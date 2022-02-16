import stripe
from pydantic import BaseModel
from typing import Dict, List,Optional
from routes.utility.token import OBToken
from routes.utils import imply
from result import Result,Ok,Err


class Policy(BaseModel):
    _key = "policy:default"

    def get(self) -> dict:
        return self.dict(exclude={'_key'})

    def validate(self, *args):
        return False


class SubscriptionFeePerTrade():
    sub = "🪐 Pay As You Go"

    pct_per_trade: float = 0.0005
    min_amount_trade: float = 100*10**18
    notice_duration_in_sec: int = 60*60*24*2
    


    def __init__(self,dbs) -> None:
        self.dbs = dbs
        self.mapper = {
            self.sub:self.upgrade,
            'Free':self.downgrade
        }

    def upgrade(self,username:str,use_obt:bool,entry)->Result:

        if not use_obt:
            return Err("Subscription could not be processed")
  
        
        profile = self.dbs['users'][username]

        if profile.get("obt_token",{}).get("balance",0) <= entry.min_amount_trade:
            return Err(f"Not enough funds to start this subscription. Requires at least of {entry.min_amount_trade//OBToken.token_decimal} {OBToken.symbol}")

        profile['payment']['policy_id'] = entry.sub
        profile['payment']['payment_type'] = 'OBT'
        profile['payment']['subscription_id'] = entry.sub
        profile['payment']['subscr_item_id'] = []

        for botid in profile['bots']:
            bot = self.dbs['bots'][botid]
            bot['billing_start_portfolio'] = bot['state'].portfolioValue
            self.dbs['bots'][botid] = bot
        if profile['payment'].get('payment_method_id'):
            del profile['payment']['payment_method_id']
        self.dbs['users'][username] = profile

        return Ok(entry.sub)

    def downgrade(self,username:str,use_obt:bool,entry)->Result:
            
        profile = self.dbs['users'][username]
        profile['payment']['policy_id'] = 'Free'
        if profile['payment'].get('subscription_id'):
            del profile['payment']['subscription_id']

        
        self.dbs['users'][username] = profile

        return Ok('Free')
class SubscriptionPolicy(Policy):
    _key = "policy:sub"

    sub: str

    allowed_bots: int
    max_total_in_use: float
    payment_fees: float = 0
    price_per_month: float = 0
    benefits: Dict[str, str] = {}
    description: str = ''
    price_ids: List[str] = []

    pct_per_trade: float = 0
    min_amount_trade: float = 0
    notice_duration_in_sec: int = 0

    payments: bool = False
    free_pro: bool = False
    pre_date: int = 0

    is_hidden: bool = False

    def get(self) -> dict:
        data = self.dict(exclude={'_key'})
        data['billingType'] = 'Free'

        if data['price_per_month'] > 0:
            data['billingType'] = 'Fixed'

        if data['payment_fees'] > 0:
            data['commissionPercentage'] = data['payment_fees']
            data['billingType'] = 'Commission'

        if data.get('pct_per_trade', 0) > 0:
            data['billingType'] = 'Per_trade'
        del data['payment_fees']
        return data

    def validate(self, element) -> bool:
        return (imply(self.payments, element['payment'].get('subscription_id', False) and element['payment'].get('policy_id', '🚀 To The Moon') == self.sub)
                and imply(self.free_pro, element['payment'].get('policy_id', 'Free') == self.sub)
                and imply(getattr(self, 'pre_date', 0) > 0, element.get('time_added', 1e13) < getattr(self, 'pre_date', 0)))

class HoldingTiers(Policy):
    _key = "policy:holding_tier"

    tier: str

    discount_pct: float

    required_obt: float

    allowed_bots: int
    max_total_in_use: float

    def get(self) -> dict:
        return self.dict(exclude={'_key'})

    def validate(self, element) -> bool:
        return element.get('obt_token', {}).get('balance', 0) >= self.required_obt*OBToken.token_decimal

# extra allowed bots
class ReferralTiers(Policy):

    _key = "policy:referral_tiers"
    
    sub: str
    reward_cash_back_pct: float
    user_split_pct: float
    other_split_pct: float

    def get(self) -> dict:
        return self.dict(exclude={'_key'})

    def validate(self, element):
        pass

class NFTTiers(Policy):

    _key = "policy:nft_tiers"

    sub:str
    skin_names: List[str]
    allowed_bots: int
    discount_pct: Optional[float]
    

    def get(self) -> dict:
        return self.dict(exclude={'_key','skin_names'})

    def validate(self, element:str):
        return element in self.skin_names


