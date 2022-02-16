from tradeEnv.neural import PEPGBase
from tradeEnv.strategy import *
from .disk import DillCache, DillDisk, Fanout
from typing import *
import requests
import os
import re


# Note: Must be run at startup before using the DBs
def check_db(path):
    db = DillCache(path)
    v = db.get('__version__', 0)
    if v == 0:
        # Summary:
        # With the new DillCache DB values are compressed with zstd
        # In the migration we iterate over the original Cache and convert each element to DillCache
        # Assumptions:
        # - zstd will crash if we go over an old-format value
        # - Any unconverable values can be removed
        print(f'Migrating {path} from version {v} to 1')
        from diskcache import Cache
        odb = Cache(path)
        converted = 0
        for key in odb:
            try:
                _ = db[key]
            except SystemExit:
                raise
            except:
                converted += 1
                try:
                    obj = odb[key]
                    db[key] = obj
                except SystemExit:
                    raise
                except:
                    print(f"Failed to convert {path}:{key}, DELETING")
                    try:
                        del odb[key]
                    except:
                        pass
        db['__version__'] = 1
        print(f"Attempted to convert {converted} entries")

    # Post conversion logs
    print(f"Database {path} Checked:", [str(warn) for warn in db.check(fix=True, retry=True)])
    return db


db_names = ['globals', 'strats', 'auth', 'ttl', 'users', 'bots', 'minimum_orders', 'invitations', 'requests',
            'bot_profits', 'profile_profits', 'bot_portfolios', 'profile_portfolios', 'forgot_password',
            'balance_in_use', 'notifications', 'admin_bot_stats', 'referrals', 'referrals_hash_map','deleted_users_log', 'exchanges',
            'models', 'cache', 'token_transactions','nft_token_bots']

global_db = 'globals'
base_db_path = 'store/db/'


def get_db_path(name):
    return os.path.abspath(os.path.join(base_db_path, name))


def get_db(d):
    return DillCache(get_db_path(d))


def get_dbs():
    dbs = {d: DillCache(get_db_path(d)) for d in db_names}
    return dbs


def check_dbs():
    for d in db_names:
        check_db(get_db_path(d))

    dbs = get_dbs()
    # data_version 1 (Migrate portfolios to seperate DBs)
    if dbs['users'].get('__data_version__', 0) < 1:
        print(f'Attempting to update data version for users to 1')
        with dbs['users'].transact(retry=True):
            for username in dbs['users']:
                user = dbs['users'][username]
                print(f'\tConverted {username}')
                if 'portfolios' in user:
                    dbs['profile_portfolios'][username] = user.get('portfolios', {})
                    del user['portfolios']
                if 'notifications' in user:
                    dbs['notifications'][username] = user.get('notifications', {})
                    del user['notifications']
                dbs['users'][username] = user
            dbs['users']['__data_version__'] = 1

    if dbs['bots'].get('__data_version__', 0) < 1:
        print(f'Attempting to update data version for bots to 1')
        with dbs['bots'].transact(retry=True):
            for botid in dbs['bots']:
                bot = dbs['bots'][botid]
                if 'portfolios' not in bot:
                    continue
                print(f'\tConverted {botid}')
                dbs['bot_portfolios'][botid] = bot['portfolios']
                del bot['portfolios']
                dbs['bots'][botid] = bot
            dbs['bots']['__data_version__'] = 1

    # data_version 2 (Make usernames case-insensitive; lower)
    if dbs['users'].get('__data_version__', 0) < 2:
        print(f'Attempting to update data version for users to 2')

        def convert_key(db, okey, nkey):
            try:
                dbs[db][nkey] = dbs[db][okey]
                del dbs[db][okey]
            except KeyError:
                print(f"Failed to convert {db} {okey} to new key format")
        with dbs['users'].transact(retry=True):
            for username in dbs['users']:
                nusername = username.lower()
                if nusername == username:
                    continue

                convert_key('users', username, nusername)
                convert_key('notifications', username, nusername)
                convert_key('profile_portfolios', username, nusername)
                convert_key('balance_in_use', username, nusername)
                convert_key('profile_profits', username, nusername)

                print(f'\tConverted {username} (v2)')
            dbs['users']['__data_version__'] = 2

    if dbs['bots'].get('__data_version__', 0) < 2:
        with dbs['bots'].transact(retry=True):
            for b in dbs['bots']:
                bot = dbs['bots'][b]
                bot['user'] = bot['user'].lower()
                dbs['bots'][b] = bot
                print(f'\tConverted {b} (v2)')
        dbs['bots']['__data_version__'] = 2

    if dbs['strats'].get('__data_version__', 0) < 2:
        with dbs['strats'].transact(retry=True):
            if '__disabled_strats__' in dbs['strats']:
                dbs['models']['__disabled_strats__'] = dbs['strats']['__disabled_strats__']
            p = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
            for x in dbs['strats']:
                if not p.match(x):
                    dbs['models'][x] = dbs['strats'][x]
                    del dbs['strats'][x]
        dbs['strats']['__data_version__'] = 2


# Similar to `Cache()` but without creating new caches in different processes
def get_tmp_cache(subvolume='default'):
    config = get_db('globals')
    cache_dir = None
    if 'tmpcache' in config:
        cache_dir = os.path.abspath(config['tmpcache'])

    c = Fanout(cache_dir, disk=DillDisk, sqlite_synchronous=2)
    config['tmpcache'] = c.directory
    return c.cache(subvolume)


gcache = get_tmp_cache()

# TODO: move these cached values to seperate module and export gcache?


@gcache.memoize(expire=60, tag='strats')
def get_strategy_map(add_disabled=False):
    m = {}
    if add_disabled or os.environ.get('ENVIRONMENT', 'dev') in ['dev', 'staging','feature']:
        m = {
            'Buy and Hold': BuyAndHold,
            'Self-Learning': PEPGBase
        }

    # Add dynamic strategies (non-uuid entries)
    d = get_db('models')
    disabled_set = d.get('__disabled_strats__', set())
    for k in d:
        if not add_disabled and k in disabled_set:
            continue

        m[k] = d[k]

    return m


@gcache.memoize(expire=60 * 60 * 24 * 7, tag='currency_exchange_rates')
def get_exchange_rates():
    #! Rate limit of 1k/mo
    req = requests.get('https://openexchangerates.org/api/latest.json?app_id=a9ab492475c34054be41a75c9a6e1f4e')
    data = req.json()
    assert data.get('rates', None), "Failed to retrieve currency exchange rates"
    return data['rates']
