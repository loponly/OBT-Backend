import re
import time
import datetime
from hashlib import blake2b
from functools import partial
from typing import *
from routes.db import get_strategy_map

from routes.utils import get_short_hash
from routes.policy import SubscriptionPolicy
from routes.db import get_strategy_map
from .acl import ACLManager
from .payment.payment_utility import PaymentUtils
from .ob_token import OBToken
from .crypto import CipherAES

get_referral_code = partial(get_short_hash, salt=b'referrals')

# TODO: split up into different modules
class UserManager:
    schema = {
        'exchanges': {},
        'bots': set(),
        'strats': set()
    }

    def __init__(self, dbs):
        self.dbs = dbs

    def ensure_token_address(self, profile, username):
        if not profile.get('obt_token'):
            profile['obt_token'] = {
                'address': OBToken.generate_wallet(CipherAES.decrypt(self.dbs['globals']['wallet:root_key']), username.encode()),
                'transfer_fee_trx': None,
                'approved_trx': None,
                'withdraw': {'is_requested': False, 'address': None, 'amount': 0},
                'balance': 0
            }
            self.dbs['users'][username] = profile

        return profile

    def create_user(self, username, password, name, data={}, overwrite=False, silent_fail=False):
        valid = overwrite or username not in self.dbs['users']
        if silent_fail and not valid:
            return
        else:
            assert re.fullmatch(r'[^@]+@[^@]+\.[^@]+', username), "Email is not valid"
            assert valid, "User already exists"

        if not data.get('referral_tier_id',None):
            data['referral_tier_id'] = 'init'

        profile = {
            **self.schema,
            'pass': self.hash_f(password) if password != '' else None,
            'name': name,
            'time_added': int(time.time()),
            'last_active': int(time.time()),
            'preferences': {'notif_categories': ['trade']},
            'deactivated': False,
            'payment': {
                'enabled': False,
                'customer_id': None,
                'subscription_id': None,
                'subscr_item_id': None,
                'payment_method_id': None,
                'next_billing_date': None,
                'billing_amount': None,
                'last_profit_calculated': None
            },
            **data
        }
        profile = self.ensure_token_address(profile, username)
        self.dbs['users'][username] = profile

        try:
            PaymentUtils(self.dbs).add_customer(username)
        except SystemExit:
            raise
        except Exception as e:
            if not silent_fail:
                raise

    def get_policy(self, username) -> SubscriptionPolicy:
        aclm = ACLManager(self.dbs)
        return aclm.find_policy(SubscriptionPolicy._key, self.dbs['users'][username])

    def set_password(self, username, password):
        assert self.dbs['users'][username], "User does not exist"
        assert not self.dbs['users'][username]['pass'], "Password already exists"

        self.dbs['users'](username, lambda profile: profile.__setitem__('pass', self.hash_f(password)))

    def reset_password(self, username, password):
        assert self.dbs['users'][username], "User does not exist"
        self.dbs['users'](username, lambda profile: profile.__setitem__('pass', self.hash_f(password)))

    def hash_f(self, data):
        # TODO: fix up salt & pepper
        blk = blake2b(salt=b'\xdb\xe5m\x95\xd47P\xb2\x1e!w\xa2,\xb2{3')
        blk.update(data.encode())
        return blk.digest()

    def assert_auth(self, username, password):
        assert self.check_auth(username, password), "Failed to authenticate"

    def check_auth(self, username, password):
        return username in self.dbs['users'] and self.dbs['users'][username][
            'pass'
        ] == self.hash_f(password)

    def assert_strat_allowed(self, username, uid, strategy_status=False):
        assert username in self.dbs['users'], "User doesn't exist"
        policy = self.get_policy(username)
        if uid not in get_strategy_map(add_disabled=True).keys():
            profile = self.dbs['users'][username]
            assert uid in profile['strats'], 'Forbidden strategy'
        if strategy_status == 'premium':
            assert 'premium_bots' in policy.benefits, 'Please upgrade your plan'

    def get_bots(self, email):
        return self.dbs['users'][email]['bots']

    def get_bot_balance(self, email):
        user_bots = self.get_active_bots(email)
        balance = 0
        for botid in user_bots:
            bot = self.dbs['bots'][botid]
            balance += bot['state'].portfolioValue

        return balance

    def get_portfolio(self, email):
        portfolios = self.dbs['profile_portfolios'].get(email, {})
        temp = list(portfolios.items())
        if not temp:
            return {}
        return temp[-1][1]

    def get_active_bots(self, email):
        user_bots = self.get_bots(email)
        result = {}
        for b in user_bots:
            bot = self.dbs['bots'][b]
            if not bot['enabled']:
                continue
            result[b] = bot
        return result

    def get_archived_bots(self, email):
        user_bots = self.get_bots(email)
        result = {}
        for b in user_bots:
            bot = self.dbs['bots'][b]
            if bot['enabled']:
                continue
            result[b] = bot
        return result

    def get_referrals(self, email: str) -> list:
        result = []

        for ref in self.dbs['referrals'].get(get_referral_code(email), []):
            ref_profile = self.dbs['users'].get(ref.username, {})
            
            if ref.username not in self.dbs['users']:
                continue

            result.append(
                {'email': ref.username,
                 'name': ref_profile.get('name'),
                 'subscription_plan': self.get_policy(ref.username).sub,
                 'referral_tier_id': ref_profile.get('referral_tier_id'),
                 'number_of_active_bots': len(self.get_active_bots(ref.username)),
                 'number_of_archived_bots': len(self.get_archived_bots(ref.username)),
                 'exchanges': [name for name in ref_profile.get('exchanges')],
                 'account_creation_date': int(ref_profile.get('time_added', datetime.datetime.now().timestamp)),
                 'balance_in_use': self.get_bot_balance(ref.username)
                 }
            )

        return result        

    def get_otp_config(self, profile: dict) -> dict:
        if not profile.get('OTP_config'):
            return {}

        OTP_config = profile['OTP_config']
        return {
            'enabled': OTP_config.get('is_verified', False),
            'secret_question': CipherAES.decrypt(OTP_config['secret_question']).decode() if OTP_config.get('secret_question') else '',
            'secret_question_answer':  CipherAES.decrypt(OTP_config['secret_question_answer']).decode() if OTP_config.get('secret_question_answer') else '',
        }
