from functools import reduce
import pydantic
from typing import *

from routes.policy import HoldingTiers, NFTTiers, SubscriptionFeePerTrade, SubscriptionPolicy, Policy
from routes.utility.solana_api import SolanaApi


class ACLManager:
    def __init__(self, dbs):
        self.dbs = dbs


    def get_acl(self, key) -> List[Policy]:
        assert key in self.dbs['globals'], f"No policy acl named {key}"
        return self.dbs['globals'][key]

    def get_acl_keys(self, key,key_name:str="sub") -> List[str]:
        assert key in self.dbs['globals'], f"No policy acl named {key}"
        return [p.get()[key_name] for p in self.dbs['globals'][key]]

    def add_acl(self, acl, key=None, force=False) -> bool:
        if not key:
            key = acl[-1]._key

        if key in self.dbs['globals'] and not force:
            return False

        self.dbs['globals'][key] = acl
        return True

    def find_policy(self, acl_key, element, condition=lambda pol: True):
        if acl_key not in self.dbs['globals']:
            return None

        acl = self.dbs['globals'][acl_key]
        for policy in acl:
            try:
                if not condition(policy):
                    continue

                if policy.validate(element):
                    return policy
            except (KeyError, pydantic.ValidationError) as e:
                pass

        return acl[-1]

    def get_policy(self,acl_key,sub:str):
        if acl_key not in self.dbs['globals']:
            return None

        
        acl = self.dbs['globals'][acl_key]
        for policy in acl:
            if policy.sub == sub:
                return policy
        return None

    def get_current_holding_tier(self,acl_key,element)->HoldingTiers:
        holding_tiers = self.dbs['globals'][acl_key]
        current_tier = holding_tiers[0]
        for h in holding_tiers:
            if not h.validate(element):
                break
            current_tier = h

        skin_tier = self.get_skins_tier(element.get('obt_token',{}).get('NFT',{}).get('token_address',{}))
        current_tier.allowed_bots = current_tier.allowed_bots + reduce(lambda p,d: p+ skin_tier[d].get('tier',{}).get('allowed_bots',0),skin_tier,0)
        
        return current_tier

    def get_current_nft_tier(self,element)->NFTTiers:
        nft_tiers = self.dbs['globals'][NFTTiers._key]
        current_tier = nft_tiers[0]
        for h in nft_tiers:
            if h.validate(element):
                return h
        
        return current_tier

    def get_skins_tier(self,token_addresses:dict):

        _return = {}
        if not token_addresses:
            return _return
        for d in token_addresses:
            token_info = SolanaApi(self.dbs).get_token_info(d)
            if token_info:
                _name = token_info.get('attributes',[{}])[-1].get('value','')
                if _name:
                    _name = ' '.join(_name.split(' ')[:-1]).lower()
                    _return[d] = {'name':_name,'tier':self.get_current_nft_tier(_name).get()}

        return _return


    def override_policy(self,element,policy_dict:dict):
        
        if policy_dict['sub'] != SubscriptionFeePerTrade.sub:
            return policy_dict
        
        holding_tier = self.get_current_holding_tier(acl_key=HoldingTiers._key,element=element)

        policy_dict['allowed_bots'] = holding_tier.get().get('allowed_bots',policy_dict['allowed_bots'])
        policy_dict['max_total_in_use'] = holding_tier.get().get('max_total_in_use',policy_dict['max_total_in_use'])
        policy_dict['adjusted_pct_per_trade'] = policy_dict['pct_per_trade'] - policy_dict['pct_per_trade'] * holding_tier.discount_pct
        return policy_dict
        
        
