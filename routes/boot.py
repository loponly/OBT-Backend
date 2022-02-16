import pathlib
import secrets
from pyaxo_ng import generate_keypair
from Crypto.Random import get_random_bytes
from base64 import b64encode

from tradeEnv.api_adapter import TradeAPI
from tradeEnv.trade_api import AbstractTradeAPI
from .db import check_dbs, get_dbs, get_tmp_cache
from routes.utility.acl import ACLManager
from .policy import HoldingTiers, NFTTiers, ReferralTiers, SubscriptionFeePerTrade, SubscriptionPolicy
from .tasks import api_hook
from .utility.ob_token import OBToken
from .utility.crypto import CipherAES


def set_statuses_to_strats(dbs):
    # FIXME: this should be a DB migration
    for k in dbs['models']:
        if hasattr(dbs['models'][k], 'status'):
            continue
        else:
            strat = dbs['models'][k]
            if type(strat) == dict:
                continue
            setattr(strat, 'status', 'normal')
            dbs['models'][k] = strat


def set_promotion_order(dbs):
    # FIXME: this should be a DB migration
    valid = 'promoting_order' in dbs['globals']
    if not valid:
        dbs['globals']['promoting_order'] = ["premium", "featured", "new", "normal"]

    order = dbs['globals']['promoting_order']

    if 'premium' not in order:
        order.insert(0, "premium")
        dbs['globals']['promoting_order'] = order


def referral_default(dbs):
    from routes.utility.users import get_referral_code
    for u in dbs['users']:
        profile = dbs['users'][u]
        if not profile.get('referral_tier_id',False):
            profile['referral_tier_id'] = '20-0'
            dbs['users'][u] = profile
            print(f"{u} updateting referral tier id.")
        code = get_referral_code(u)
        if code not in dbs['referrals_hash_map']:
            dbs['referrals_hash_map'][code] = u
            print(f"{u} updateting hashmap. {code}")
            

def boot_checks():
    
    print("Running boot checks")
    
    for substore in ['envs', 'db', 'dataset', 'logs', 'models', 'unique']:
        pathlib.Path(f"store/{substore}").mkdir(parents=True, exist_ok=True)

    check_dbs()
    dbs = get_dbs()

    referral_default(dbs=dbs)

    set_statuses_to_strats(dbs)
    set_promotion_order(dbs)

    # Add subscriptions if not exist
    aclm = ACLManager(dbs)
    aclm.add_acl([
        SubscriptionPolicy(sub='🚀 To The Moon', allowed_bots=10, max_total_in_use=9999.99, payment_fees=0.3, price_ids=[], payments=True),
        SubscriptionPolicy(sub='Experimenter', allowed_bots=3, max_total_in_use=1500., price_per_month=7.99, price_ids=['price_1IvmJBCmzAW8QZBCsF9YIu6E'], payments=True),
        SubscriptionPolicy(sub='Free Pro', allowed_bots=10, max_total_in_use=9999.99, payments=False, free_pro=True),
        SubscriptionPolicy(sub='Beta', allowed_bots=2, max_total_in_use=500, pre_date=1617981797),
        SubscriptionPolicy(sub=SubscriptionFeePerTrade.sub,allowed_bots=20, 
                            max_total_in_use=9999.99, 
                            pct_per_trade=SubscriptionFeePerTrade.pct_per_trade,
                            min_amount_trade= SubscriptionFeePerTrade.min_amount_trade,payments=True,
                            notice_duration_in_sec=SubscriptionFeePerTrade.notice_duration_in_sec),
        SubscriptionPolicy(sub='Free', allowed_bots=1, max_total_in_use=500),
    ], key=SubscriptionPolicy._key,force=False)

    # Adding discount tiers 
    aclm.add_acl(
        [   
            HoldingTiers(tier='init',discount_pct=0,required_obt=float('-inf'),allowed_bots=4,max_total_in_use=9999.99)
        ],key=HoldingTiers._key,force=False
    )

    # Adding ReferralTiers 
    aclm.add_acl(
        [   
            ReferralTiers(sub='init',reward_cash_back_pct=0 , user_split_pct=1,other_split_pct=0),
            ReferralTiers(sub='10-10',reward_cash_back_pct=0.2 , user_split_pct=0.5,other_split_pct=0.5),
            ReferralTiers(sub='20-0',reward_cash_back_pct=0.2 , user_split_pct=1,other_split_pct=0),
            ReferralTiers(sub='0-20',reward_cash_back_pct=0.2 , user_split_pct=0,other_split_pct=1),
        ],key=ReferralTiers._key,force=False
    )

    # Adding ReferralTiers 
    aclm.add_acl(
        [   
            NFTTiers(sub='init',allowed_bots=0, skin_names=[]),
            NFTTiers(sub='common',allowed_bots=1, skin_names=['classical','iron','emerald','diamond']),
            NFTTiers(sub='uncommon',allowed_bots=2, skin_names=['frosty','bronze','amethyst']),
        ],key=NFTTiers._key,force=False
    )
    # Generate wallets if not exists
    if 'wallet:root_key' not in dbs['globals']:
        dbs['globals']['wallet:root_key'] = CipherAES.encrypt(secrets.token_bytes(32))
        dbs['globals']['wallet:collector'] = OBToken.generate_wallet(CipherAES.decrypt(dbs['globals']['wallet:root_key']), b'main:collector')

    get_tmp_cache('cached_api').clear()

    if not dbs['globals'].get('id_key', None):
        dbs['globals']['id_key'] = generate_keypair()
    dbs['globals']['session_mkey'] = get_random_bytes(32)
    print("Session Key: ", b64encode(dbs['globals']['session_mkey']))
    TradeAPI.hooks = [api_hook]
    AbstractTradeAPI.hooks = [api_hook]
