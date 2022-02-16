import urllib.parse
import psutil
import time
import multiprocessing
from typing import *
from result import Result
from routes.db import get_dbs, get_db
from setproctitle import setproctitle, getproctitle
from routes.utility.solana_api import SolanaApi

from routes.utility.fee_per_trade import OBTFeePerTrade
from routes.utility.obt_holding_ranks import OBTHoldingRanks

from .base import TTLManager 
from .realtime import RealTimeEvl
from .exchange import Exchange
from .logging import getLogger
from .utility.intercom import OBIntercomDataFeed
from .utility.ob_token import OBTokenTransaction
from .utility.notify_alert import NotifyBinanceAPIExpire
from .utility.portfolio_fetch import PortfolioFetch
from .utility.bot_profit_calc import ProfileProfitCalc
from .utility.balance_in_use import BalanceInUse
from .utility.admin_bot_stats import AdminBotStats
from .utility.public_bot_stats import PublicBotStats
from .utility.mailing import RoutineMails
from .utils import no_except


def dayify(t): return int(t/(24 * 60 * 60))
def safe_incr(d, k): return d.__setitem__(k, d.get(k, 0) + 1)
def xavg(x, y, a): return a * x + (1-a) * y


a = 0.995  # Smoothing factor


def log_resource_usage():
    db = get_db('globals')

    cpu_cumm = 0.
    rss = 0
    for p in psutil.process_iter():
        try:
            cpu_cumm += p.cpu_percent()
            rss += p.memory_info()[0]
        except:
            pass

    prev = db.get('stat_resources', {})

    data = {'mem': rss, 'cpu': cpu_cumm, 'max_mem': max(prev.get('max_mem', 0), rss), 'max_cpu': max(prev.get('max_cpu', 0), cpu_cumm), 'avg_mem': xavg(prev.get('avg_mem', 0), rss, a), 'avg_cpu': xavg(prev.get('avg_cpu', 0), cpu_cumm, a), 'timestamp': time.time()}

    # Reset max values after 24h
    if dayify(prev.get('timestamp', 0)) != dayify(data['timestamp']):
        data['max_cpu'] = cpu_cumm
        data['max_mem'] = rss

    db['stat_resources'] = data


def watchdog():
    setproctitle(f'{getproctitle()} watchdog')

    # Run all tasks & make sure they stay online
    logger = getLogger('tasks')

    tasks = dict((f.__name__, f) for f in [evl, update_stats, update_public_stats])
    pids: Dict[str, multiprocessing.Process] = {}
    # TODO: bad_counter = {}

    # Handle exit without lingering threads
    import atexit

    def exit_handler():
        print("Terminating tasks...")
        for task in pids:
            print(f"Terminating {task} ({pids[task].pid})")
            pids[task].terminate()

    atexit.register(exit_handler)

    while True:
        for k in tasks:
            if k in pids:
                # Task already exists, just check condition
                if not pids[k].is_alive() or pids[k].exitcode != None:
                    # Task was terminated at some point, restart+log
                    l = logger.getChild(k)
                    l.warn(f"Process {pids[k].pid} ({k}, errc: {pids[k].exitcode}) was terminated, restarting...")
                    # Will restart in next run
                    del pids[k]
            else:
                # Create new task
                logger.info(f"Starting task {k}")
                p = multiprocessing.Process(target=tasks[k], name=k)
                pids[k] = p
                p.start()

        time.sleep(10)


def evl():
    setproctitle(f'{getproctitle()} evl')
    dbs = get_dbs()
    ttlm = TTLManager(dbs)
    exchanges = Exchange(dbs).get_exchange_configs()
    rt = RealTimeEvl(dbs, exchanges)

    target_delay = 60
    last_time = time.time()
    while True:

        no_except(ttlm.checkTTL)
        no_except(rt.loop)
        no_except(log_resource_usage)

        # Adaptive Delay
        actual_delay = max((last_time + target_delay) - time.time(), 0)
        if actual_delay <= 0:
            print('WARNING: rt delay <= 0, event loop took too long')
        time.sleep(actual_delay)

        dbs['globals']['stat_rt'] = {'last_loop_time': target_delay - actual_delay, 'delay': time.time() - last_time, 'timestamp': int(time.time())}
        print(f'RT evl time: {int(target_delay - actual_delay)}s delay: {time.time() - last_time}')
        last_time = time.time()


def update_public_stats():
    dbs = get_dbs()
    target_delay = 6 * 3600
    base_title = getproctitle()
    while True:
        start_time = time.time()
        
        setproctitle(f'{base_title} public_bot_stats')
        no_except(PublicBotStats(dbs).update_all)

        delay = target_delay + start_time - time.time()
        dbs['globals']['stat_public_stats_update'] = {'last_loop_time': time.time() - start_time, 'delay': delay, 'timestamp': int(time.time())}
        delay = max(delay, 0)
        time.sleep(int(delay))

def update_stats():
    dbs = get_dbs()
    target_delay = 3600
    base_title = getproctitle()
    while True:
        start_time = time.time()

        #setproctitle(f'{base_title} portfolio_fetch')
        #for user in dbs['users']:
        #    if isinstance(user, Result):
        #        del dbs['users'][user]
        #        continue
        #    no_except(PortfolioFetch(dbs).get_portfolio, user, dbs['users'][user]['exchanges'])
        #    time.sleep(1)  # Minize impact on API

        setproctitle(f'{base_title} profile_profit_calc')
        no_except(ProfileProfitCalc(dbs).get_profile_profitability)
        setproctitle(f'{base_title} balance_in_use')
        no_except(BalanceInUse(dbs).calc_balance)
        setproctitle(f'{base_title} check_ending_balance')
        no_except(BalanceInUse(dbs).check_ending_balance)
        setproctitle(f'{base_title} admin_bot_stats')
        no_except(AdminBotStats(dbs).update_admin_bot_stats)
        setproctitle(f'{base_title} routine_mails')
        no_except(RoutineMails(dbs).run)
        setproctitle(f'{base_title} NotifyBinanceAPIExpire')
        no_except(NotifyBinanceAPIExpire(dbs).run)
        setproctitle(f'{base_title} OBIntercomDataFeed')
        no_except(OBIntercomDataFeed(dbs).run)
        setproctitle(f'{base_title} OBTokenTransaction')
        no_except(OBTokenTransaction(dbs).run)
        setproctitle(f'{base_title} OBTFeepPerTrade')
        no_except(OBTFeePerTrade(dbs).run)
        setproctitle(f'{base_title} OBTHoldingRanks')
        no_except(OBTHoldingRanks(dbs).refresh_all_the_ranks)
        setproctitle(f'{base_title} update_all_users_owned_nfts')
        no_except(SolanaApi(dbs).update_all_users_owned_nfts)

        delay = target_delay + start_time - time.time()
        dbs['globals']['stat_stats_update'] = {'last_loop_time': time.time() - start_time, 'delay': delay, 'timestamp': int(time.time())}
        delay = max(delay, 0)
        time.sleep(int(delay))


def api_hook(url):
    # print(url)
    db = get_db('globals')
    prev = db.get('stat_api', {})
    if dayify(prev.get('timestamp', 0)) != dayify(time.time()):
        prev['today'] = {}

    o = urllib.parse.urlparse(url)
    host = o.netloc
    fullp = o.netloc + o.path

    for x in ['today', 'alltime']:
        pd = prev.get(x, {})
        safe_incr(pd, host)
        safe_incr(pd, fullp)
        prev[x] = pd

    prev['timestamp'] = int(time.time())

    db['stat_api'] = prev
