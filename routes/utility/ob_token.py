import time
import secrets
import logging
import requests
import os
import numpy as np
from datetime import datetime
from result import Result, Ok, Err
from sendgrid.helpers.mail import Mail

from routes.policy import HoldingTiers, ReferralTiers, SubscriptionFeePerTrade, SubscriptionPolicy
from routes.utility.acl import ACLManager


from .token import OBToken
from .crypto import CipherAES
from .sendgrid import sg


def notify(username: str, data: dict) -> bool:
    try:
        message = Mail(from_email='sender@onebutton.trade', to_emails=[(username, data['profile']['name'])])
        message.dynamic_template_data = data.get('body', {})
        message.template_id = data['template_id']
        response = sg.send(message)

        if response.status_code >= 400:
            return False
        return True
    except Exception as e:
        raise e


def get_market_price(market="BNBBUSD"):
    resp = requests.get(f'https://api.binance.com/api/v3/avgPrice?symbol={market}')
    return float(resp.json()['price'])


def _get_price_in_bnb() -> float:
    resp = requests.get('https://api.pancakeswap.info/api/v2/tokens/0x8da6113655309f84127e0837fcf5c389892578b3')
    return float(resp.json().get('data', {}).get('price_BNB', 0))


def get_price_in_bnb(dbs) -> float:
    historical_prices = dbs['cache'].get('obt:obt_price_history', [])
    # TODO: use timestamps:target and use zawy's LWMA
    if 'obt:last_obt_price' not in dbs['cache']:
        current_price = _get_price_in_bnb()

        dbs['cache'].set('obt:last_obt_price', current_price, expire=3600 * 8, retry=True)
        historical_prices.append(current_price)
        historical_prices = historical_prices[-3 * 4:]  # Take data from the last 4 days max
        dbs['cache']['obt:obt_price_history'] = historical_prices

    price = np.average(historical_prices, weights=np.arange(1, len(historical_prices)+1))
    return price




def get_price_in(dbs,cur) -> float:
    bnb_price = get_price_in_bnb(dbs)
    cur_bnb_price = dbs['cache'].get(f'obt:{cur}_bnb_price')
    if not cur_bnb_price:
        cur_bnb_price = get_market_price(f"BNB{cur}")
        dbs['cache'].set(f'obt:{cur}_bnb_price', cur_bnb_price, expire=3600, retry=True)

    return bnb_price * cur_bnb_price



def get_price(dbs,currency:str)->float:
    # TODO: Need to implement other currency pairs
    if currency in ['USDT','BUSD','USD']:
        return get_price_in(dbs,'BUSD')
    if currency in ['EUR']:
        return get_price_in(dbs,'EUR')
    
    return get_price_in(dbs,currency)


def get_price_in_usd(dbs)->float:
    return get_price(dbs,currency='USDT')
class OBTokenTransaction:
    min_transaction_amount = 10*10**18

    def __init__(self, dbs, logger=None) -> None:
        self.dbs = dbs
        contract_addr = os.environ.get('CONTRACT_ADDR', '0xEfCA9db9712A8C4ce987a70Ef7D60c43B5F29a68')
        self.token = OBToken(self.dbs['globals']['wallet:collector'], contract_addr)
        self.logger = logger
        self.acl = ACLManager(self.dbs)

    def get_logger(self):
        if getattr(self, 'logger', None):
            return self.logger
        logger = logging.getLogger()
        logger.addHandler(logging.NullHandler())
        return logger

    def ready_for_transfer(self, obt_token):
        if not obt_token.get('transfer_fee_trx'):
            trx = self.token.transfer_gas_fee(obt_token['address'].pub)
            if trx.is_ok():
                obt_token['transfer_fee_trx'] = trx.ok()
                return False
            self.get_logger().error(trx.err())

        if not obt_token.get('approved_trx') and self.token.is_transaction_confirmed(obt_token.get('transfer_fee_trx')):
            trx = self.token.approve(obt_token['address'])
            if trx.is_ok():
                obt_token['approved_trx'] = trx.ok()
                return False
            self.get_logger().error(trx.err())

        if obt_token.get('approved_trx') and self.token.is_transaction_confirmed(obt_token['approved_trx']) and self.token.is_transaction_confirmed(obt_token.get('transfer_fee_trx')):
            return True

        return False

    def save_transaction(self, user, transaction_data):
        if transaction_data:
            transaction = self.dbs['token_transactions'].get(user, [])
            transaction.append(transaction_data)
            self.dbs['token_transactions'][user] = transaction

    def check_user(self, u):
        with self.dbs['users'].lock(u, timeout=10.) as lock:
            profile = self.dbs['users'][u]
            if not profile.get('obt_token'):
                return

            obt_token = profile['obt_token']
            if not obt_token.get('address'):
                return

            # TODO: refactor save_changes as a context manager?
            def save_changes(transaction_data=None):
                self.save_transaction(u, transaction_data)
                self.dbs['users'][u] = profile

            fee = int(self.token._estimate_transaction_fee()['value']/get_price_in_bnb(self.dbs)) * 2
            if obt_token.get('withdraw'):
                __withdraw_amount = obt_token['withdraw'].get('amount', 0) - fee
                if __withdraw_amount + fee > obt_token['balance'] or __withdraw_amount + fee < self.min_transaction_amount:
                    obt_token['error'] = 'Amount to withdraw not valid'
                    obt_token['withdraw']['is_requested'] = False
                    obt_token['withdraw']['amount'] = 0
                    __withdraw_amount = 0

                elif obt_token['withdraw'].get('is_requested'):
                    collector_wallet = self.dbs['globals']['wallet:collector']
                    # TODO: transaction can be reverted while the raw transaction went through
                    trx = self.token.transfer(collector_wallet.pub, collector_wallet.priv, obt_token['withdraw']['address'], __withdraw_amount)
                    if trx.is_ok():
                        obt_token['balance'] -= obt_token['withdraw']['amount']
                        obt_token['withdraw']['is_requested'] = False
                        obt_token['withdraw']['amount'] = __withdraw_amount
                        obt_token['withdraw']['last_trxHash'] = trx.ok()
                        save_changes(transaction_data={
                            'date': time.time(),
                            'hash_id': trx.ok(),
                            'type': 'WITHDRAW',
                            'amount': str(__withdraw_amount/self.token.token_decimal),
                            'fee': fee/self.token.token_decimal,
                            'from_address': obt_token['address'].pub,
                            'to_address': obt_token['withdraw']['address'],
                            'price': get_price_in_usd(self.dbs)

                        })
                        notify(username=u,
                               data={
                                        'template_id': 'd-c5bddc735df54c4396d151540d192fc9',
                                        'profile': profile,
                                        'body': {
                                            'date': f"{datetime.now():%d-%m-%Y %H:%M:%S}",
                                            'amount': str(__withdraw_amount/self.token.token_decimal),
                                            'wallet_id':  obt_token['withdraw']['address'],
                                            'transaction_id': trx.ok()
                                        }
                               })
                        return
                    self.get_logger().error(trx.err())

            # Check if there is any transfer in progress (only start a new one after the previous has finished)
            if 'deposit_transfer' not in obt_token:
                # TODO(Casper): Fix race-condition where another endpoint overwrites 'deposit_transfer' and check_user is called again from different thread, user would lose their deposit
                # Check if user has deposited minimum amount of tokens
                wallet_balance = self.token.get_balance(obt_token['address'].pub)
                obt_token['pending_balance'] = wallet_balance
                if wallet_balance < self.min_transaction_amount:
                    save_changes()
                    return

                # First check if all pre-transfer actions are done (gas + approve)
                if not self.ready_for_transfer(obt_token):
                    save_changes()
                    return

                # Create transfer to colllector wallet
                trx = self.token.transfer_from(obt_token['address'].pub, wallet_balance)
                if trx.is_ok():
                    obt_token['deposit_transfer'] = {'date': time.time(), 'amount': wallet_balance, 'fee': fee, 'to_address': obt_token['address'].pub, 'txid': trx.ok()}
                    save_changes()
            elif self.token.is_transaction_confirmed(obt_token['deposit_transfer']['txid']):
                tx = obt_token['deposit_transfer']
                del obt_token['deposit_transfer']
                _amount = int(tx['amount'] - tx['fee'])
                obt_token['balance'] += _amount
                save_changes(transaction_data={
                    'date': time.time(),
                    'type': 'DEPOSIT',
                    'amount': str(_amount/self.token.token_decimal),
                    'fee': tx['fee']/self.token.token_decimal,
                    'to_address': obt_token['address'].pub,
                    'txid': tx['txid'],
                    'price': get_price_in_usd(self.dbs)
                })
                notify(username=u,
                       data={
                           'template_id': 'd-4ea3c958e46a45a7baee9cdef8ca0075',
                           'profile':  profile,
                           'body': {
                               'date': f"{datetime.now():%d-%m-%Y %H:%M:%S}",
                               'amount': str(_amount/self.token.token_decimal),
                               'transaction_id': tx['txid']
                           }
                       })
                return

    def get_nft_token_discount(self,bot,profile):
        nft_token = bot.get('nft_token',{})
        if not nft_token:
            return 0
        if nft_token.get('address') not in profile.get('obt_token',{}).get('NFT',{}).get('token_address',{}):
            nft_token_address=nft_token.get('address')
            if nft_token_address:
                all_locked_nft_addresses = self.dbs['globals'].get('nft_bot:addresses',{})
                if all_locked_nft_addresses.get(nft_token_address):
                    del all_locked_nft_addresses[nft_token_address]
                    self.dbs['globals']['nft_bot:addresses']  = all_locked_nft_addresses
            return 0
        if nft_token.get('skin'):
            return self.acl.get_current_nft_tier(nft_token.get('skin')).discount_tier
        return 0

    def deduct_bot_trade_fee(self, email:str, amount:float,currency:str,bot)->Result:
        profile = self.dbs['users'][email]
        if profile['payment'].get('policy_id',None) != SubscriptionFeePerTrade.sub:
            return Err('not-allowed-policy_id')
        

        entry = self.acl.get_policy(SubscriptionPolicy._key,SubscriptionFeePerTrade.sub)

        if not entry:
            return Err(f'No policy {SubscriptionFeePerTrade.sub}.')


        if profile.get('obt_token', {}).get('balance', None) is None:
              return Err('Please enable obt_token wallet.')

        price = get_price(self.dbs,currency)
        discount_pct = self.acl.get_current_holding_tier(HoldingTiers._key,profile).discount_pct
        
        discount_pct += self.get_nft_token_discount(bot,profile)

        amount = (amount/price)*self.token.token_decimal*entry.pct_per_trade * (1-discount_pct)
        profile['obt_token']['balance'] -= amount

        self.save_transaction(user=email,transaction_data={
            'date': time.time(),
            'hash_id':'',
            'type': 'FEE-SUBSCRIPTION',
            'amount': 0,
            'fee': amount/self.token.token_decimal,
            'from_address': '',
            'to_address': '',
            'price': price,
            'discount_pct': discount_pct
        })

        self.dbs['users'][email] = profile
        self.add_referral_rewards(amount,email)
        
        return Ok(amount/self.token.token_decimal)
    
    def add_referral_rewards(self,amount,email):
        profile = self.dbs['users'][email]
        ref_tier_p = self.acl.get_policy(ReferralTiers._key,profile.get('referral_tier_id','init'))

        if ref_tier_p.reward_cash_back_pct <= 0 and ref_tier_p.reward_cash_back_pct>1:
            return 

        if profile.get('referral') not in self.dbs['referrals_hash_map']:
            return 
        
        if profile.get('referral') not in self.dbs['referrals']:
            return 

        referrer_username =  self.dbs['referrals_hash_map'][profile['referral']]

        if referrer_username not in self.dbs['users']:
            return 


        reward_amount = amount * ref_tier_p.reward_cash_back_pct
        _split_amount = reward_amount*ref_tier_p.other_split_pct
        if _split_amount > 0:
            profile['obt_token']['balance'] += _split_amount
            self.dbs['users'][email] = profile
            
            self.save_transaction(user=email,transaction_data={
                'date': time.time(),
                'hash_id':'',
                'type': 'REFERRAL-REWARD-CASHBACK',
                'amount': _split_amount/self.token.token_decimal,
                'fee': 0,
                'from_address': '',
                'to_address': '',
                'from_referral_tier_id':ref_tier_p.sub,
                'from_user':referrer_username
            })

        _split_amount = reward_amount*ref_tier_p.user_split_pct
        if _split_amount > 0:
            referrer_profile = self.dbs['users'][referrer_username]
            referrer_profile['obt_token']['balance'] += _split_amount

            self.dbs['users'][referrer_username] = referrer_profile

            self.save_transaction(user=referrer_username,transaction_data={
                'date': time.time(),
                'hash_id':'',
                'type': 'REFERRAL-REWARD-CASHBACK',
                'amount': _split_amount/self.token.token_decimal,
                'fee': 0,
                'from_address': '',
                'to_address': '',
                'from_referral_tier_id':ref_tier_p.sub,
                'from_user':email
            })
        

    def run(self):
        users = self.dbs['users']
        for u in users:
            self.check_user(u)


