from routes.policy import  ReferralTiers
from routes.profile import  decoded_base
from routes.utility.acl import ACLManager
from .base import Route
import os
import falcon
from .utility.sendgrid import sg

from .invitations import InvitationEmail, EmailTokenUtilities
from routes.utility.users import UserManager
from .db import get_tmp_cache

from .spectree import spectree
from spectree import Response
from pydantic import BaseModel, constr
from typing import Optional

import logging
from sentry_sdk.integrations.logging import LoggingIntegration

class RequestsPostReq(BaseModel):
    email: str
    name: Optional[str]
    maillist: Optional[bool]
    referral: Optional[constr(max_length=64)]

class RequestsPostResp(BaseModel):
    success: bool = True

class ForgotPasswordPostReq(BaseModel):
    email: str

class ForgotPasswordPostResp(BaseModel):
    success: bool = True

class ResetPasswordGetReq(BaseModel):
    token: str

class ResetPasswordGetResp(BaseModel):
    valid: bool = True

class ResetPasswordGetMessage(BaseModel):
    valid: bool = False
    expired: bool

class ResetPasswordPostReq(BaseModel):
    token: str
    password: str

class ResetPasswordPostResp(BaseModel):
    success: bool = True

class ResetPasswordPostMessage(BaseModel):
    valid: bool = False
    expired: bool

class VersionGetResp(BaseModel):
    __root__: str

sentry_logging = LoggingIntegration(
    level=logging.INFO,        # Capture info and above as breadcrumbs
    event_level=logging.ERROR  # Send errors as events
)

sentry_logger = logging.getLogger('sentry')
sentry_logger.addHandler(sentry_logging._handler)
sentry_logger.addHandler(sentry_logging._breadcrumb_handler)

class Requests(Route):
    @spectree.validate(json=RequestsPostReq, resp=Response(HTTP_200=RequestsPostResp))
    def on_post(self, req, resp):
        email = req.media['email'].strip().lower()
        name = req.media.get('name', 'Anonymous').strip()
        maillist = req.media.get('maillist', True)
        referral = req.media.get('referral', None)
        referral_tier_id = decoded_base(req.media.get('referral_tier_id','')).strip() if req.media.get('referral_tier_id','') != None else None
        extra_data = {}
        if referral:
            referral = referral.strip()
            if referral in self.dbs['referrals']:
                extra_data['referral'] = referral.strip()
            else:
                resp.status_code = falcon.HTTP_400
                resp.media = {'error': 'Failed to find referral code, please keep it empty if unused'}
                return
        
        if referral_tier_id not in ACLManager(self.dbs).get_acl_keys(ReferralTiers._key):
            referral_tier_id = 'init'

        extra_data['referral_tier_id'] = referral_tier_id

        link = EmailTokenUtilities(self.dbs).build_invitation_link(email)
        InvitationEmail().send_message(email, link, name, 'user_invitation', 'Welcome to OB Trader 🚀🤖')
        
        user_manager = UserManager(self.dbs)
        user_manager.create_user(email, '', name, data=extra_data)

        if maillist:
            try:
                data = {'contacts': [{
                    "email": email,
                    "first_name": name.split(' ')[0],
                    "last_name": " ".join(name.split(' ')[1:])
                }]}
                response = sg.client.marketing.contacts.put(request_body=data)
                if response.status_code >= 400:
                    print("Couldn't add user to sendgrid", response.body, response.status_code)
            except SystemExit as e:
                raise e
            except Exception:
                sentry_logger.exception("Failed sendgrid mailinglist addition on signup")

        resp.media = {'success': True}


class ForgotPassword(Route):
    @spectree.validate(json=ForgotPasswordPostReq, resp=Response(HTTP_200=ForgotPasswordPostResp))
    def on_post(self, req, resp):
        email = req.media['email'].strip().lower()
        assert email in self.dbs['users'], "This email address is not registered in the system"

        name = self.dbs['users'][email]['name']
        link = EmailTokenUtilities(self.dbs).build_forgot_password_link(email)
        InvitationEmail().send_message(email, link, name, 'forgot_password', 'OB Password Recovery')

        resp.media = {'success': True}

from .invitations import check_xor
class ResetPassword(Route):
    @spectree.validate(query=ResetPasswordGetReq, resp=Response(HTTP_200=ResetPasswordGetResp, HTTP_403=ResetPasswordGetMessage))
    def on_get(self, req, resp):
        token = req.params['token']
        email = self.verify_token(token)
        if email:
            resp.media = {'valid': True}
            resp.status = falcon.HTTP_200
        else:
            resp.media = {'valid': False, 'expired': check_xor(bytes.fromhex(token))}
            resp.status = falcon.HTTP_403

    @spectree.validate(json=ResetPasswordPostReq, resp=Response(HTTP_200=ResetPasswordPostResp, HTTP_403=ResetPasswordPostMessage))
    def on_post(self, req, resp):
        token = req.media['token']
        email = self.verify_token(token)

        if not email:
            resp.status = falcon.HTTP_403
            resp.media = {'valid': False, 'expired': check_xor(bytes.fromhex(token))}
            return

        password = req.media['password']
        UserManager(self.dbs).reset_password(email, password)
        del self.dbs['forgot_password'][token]
        resp.media = {'success': True}

    def verify_token(self, token):
        if self.dbs['forgot_password'].get(token, False, retry=True):
            return self.dbs['forgot_password'][token]


import subprocess
_cache = get_tmp_cache()

@_cache.memoize(expire=24 * 3600, tag='be_version')
def get_version():
    if os.environ.get('VERSION', None):
        return os.environ['VERSION']
    p = subprocess.run(["curl -s https://raw.githubusercontent.com/i404788/git-quick-version/master/quick-version.sh | sh"], shell=True, stdout=subprocess.PIPE, text=True)
    return p.stdout.strip() or ''

class Version(Route):
    @spectree.validate(resp=Response(HTTP_200=VersionGetResp))
    def on_get(self, req, resp):
        resp.media = get_version()
