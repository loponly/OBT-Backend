import time
from routes.profile import Referral, get_referral_code
def add_referral(referrer, new_user):
    refs = dbs['referrals'][get_referral_code(referrer.lower())]
    refs.append(Referral(username=new_user, code=get_referral_code(new_user), timestamp=time.time()))
    dbs['referrals'][get_referral_code(referrer.lower())] = refs
