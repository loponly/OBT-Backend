from os import link
from routes.base import Route, UrlManager, auth_guard
from routes.policy import ReferralTiers
from routes.utility.acl import ACLManager
from routes.utility.users import get_referral_code
from typing import Optional, Set
from pydantic import BaseModel
import time
import base64


def encode_base(data:str):
    return base64.b64encode(data.encode()).decode()

def decoded_base(hash_data:bytes):
    return base64.b64decode(hash_data).decode()

class Referral(BaseModel):
    username: str
    code: str
    timestamp: float
    referral_tier_id: Optional[str]
    

    def get(self) -> dict:
        return self.dict(exclude={'username'})

class ReferralInfo(Route):
    def find_referrers(self, codes: Set[str]):
        codes = codes.copy()
        referrers = {}
        for k in self.dbs['users']:
            if len(codes) == 0:
                break

            code = get_referral_code(k)
            if code in codes:
                codes.remove(code)
                yield code, k

    @auth_guard
    def on_get(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.get_profile(req).unwrap()
        code = get_referral_code(username)
        urlm = UrlManager()

        # Create if not exists, so we can check if it exists when signing up a user 
        if code not in self.dbs['referrals']:
            self.dbs['referrals'][code] = []

        if code not in self.dbs['referrals_hash_map']:
            self.dbs['referrals_hash_map'][code] = username

        referrals = self.dbs['referrals'][code]
        details = list(map(lambda r: r.get(), referrals))
        
        acl = ACLManager(self.dbs)

        
        link = f"{urlm.get_server_url()}sign-up?ref={code}"
        referral  = {row.sub:{
                    "link": f"{link}&s={encode_base(row.sub)}",
                    **row.get()
                    } for row in acl.get_acl(ReferralTiers._key)}
        

        resp.media = {
                "refferal":referral,
                "details": details,
                }
