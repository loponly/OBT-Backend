from typing import Optional
import falcon

from routes.utility.google_auth import GoogleOATH
from routes.utility.otp import OTP
from routes.utility.users import UserManager
from .base import StandardResponse, Route, TTLManager
from .spectree import spectree
from spectree import Response

from pydantic import BaseModel


class AuthPostReq(BaseModel):
    username: str
    password: str
    opt_code: Optional[str]

class AuthPostResp(BaseModel):
    token: str
    user: str
    name: str

class AuthPostMessage(BaseModel):
    message: str


class AuthGetResp(BaseModel):
    authenticated: bool = True

class AuthGetMessage(BaseModel):
    authenticated: bool = False

class CheckOtpReq(BaseModel):
    username: str
    password: str

class CheckOtpResp(BaseModel):
    enabled: bool

class Authenticate(Route):
    @spectree.validate(resp=Response(HTTP_200=AuthGetResp, HTTP_403=AuthGetMessage))
    def on_get(self, req, resp):
        self.mark_activity(req)
        if self.is_authenticated(req):
            resp.media = {'authenticated': True}
            resp.status = falcon.HTTP_200
        else:
            resp.media = {'authenticated': False}
            resp.status = falcon.HTTP_403

    @spectree.validate(json=AuthPostReq, resp=Response(HTTP_200=AuthPostResp, HTTP_400=StandardResponse))
    def on_post(self, req, resp):
        self.mark_activity(req)
        user = req.media['username'].strip().lower()
        password = req.media['password']
        profile = self.dbs['users'][user]

        if self.is_authenticated(req):
            resp.media = {'error': 'already authenticated'}
            resp.status = falcon.HTTP_400
            return

        if not UserManager(self.dbs).check_auth(user, password):
            resp.media = {'error': 'Failed to login: bad password/username'}
            resp.status = falcon.HTTP_400
            return

        if profile.get('deactivated', None):
            resp.media = {'error': 'Failed to login: user is deactivated'}
            resp.status = falcon.HTTP_400
            return

        if profile.get('OTP_config', {}).get('is_verified') and not profile.get('OTP_config',{}).get('is_disabled'):
            if not OTP.verify(profile['OTP_config'].get('secret'), req.media.get('otp_code')):
                resp.media = {'error': f"Please provide a valid 2 factor authentication code"}
                resp.status = falcon.HTTP_400
                return

            # Give user token
        resp.media = {
            'token': self.set_token(user),
            # Send name or email for frontend
            'user': user,
            'name': profile.get('name', 'User')
        }


class CheckAutheticationOTP(Route):
    def __init__(self, dbs: dict):
        super().__init__(dbs)

    @spectree.validate(json=CheckOtpReq, resp=Response(HTTP_200=CheckOtpResp, HTTP_400=StandardResponse))
    def on_post(self, req, resp):
        self.mark_activity(req)
        user = req.media['username'].strip().lower()
        password = req.media['password']
        if not UserManager(self.dbs).check_auth(user, password):
            resp.media = {'error': 'Failed to login: bad password/username'}
            resp.status = falcon.HTTP_400
            return

        profile = self.dbs['users'][user]

        if self.is_authenticated(req):
            resp.media = {'error': 'already authenticated'}
            resp.status = falcon.HTTP_400
            return

        if profile.get('deactivated'):
            resp.media = {'error': 'Failed to login: user is deactivated'}
            resp.status = falcon.HTTP_400
            return

        if profile.get('OTP_config', {}).get('is_verified') and not profile.get('OTP_config',{}).get('is_disabled'):
            print(profile.get('OTP_config',{}).get('is_disabled'))
            resp.media = {'enabled': True}
            resp.status = falcon.HTTP_200
            return

        resp.media = {'enabled': False}
        resp.status = falcon.HTTP_200
        return


class GoogleAuthenticate(Route):

    def on_get(self, req, resp):
        self.mark_activity(req)
        try:
            google_info = GoogleOATH().callback(req)
            email = google_info.get('email')
            full_name = google_info.get('name')

            if not self.dbs['users'].get(email):
                user_manager = UserManager(self.dbs)
                user_manager.create_user(email, '', full_name)

            profile = self.dbs['users'][email]
            if profile.get('deactivated', None):
                resp.media = {'error': 'Failed to login: user is deactivated'}
                resp.status = falcon.HTTP_400
                return

            token = self.set_token(email)
            resp.status = falcon.HTTP_200

        except Exception as e:
            resp.media = {'error': str(e)}
            resp.status = falcon.HTTP_500

        raise falcon.HTTPPermanentRedirect(f'https://beta.obtrader.ml/login?token={token}&user={email}&name={full_name}')

    def on_post(self, req, resp):
        self.mark_activity(req)
        if self.is_authenticated(req):
            resp.media = {'error': 'already authenticated'}
            resp.status = falcon.HTTP_400
            return

        authorization_url, state = GoogleOATH().flow.authorization_url()
        resp.media = {'authorization_url': authorization_url, 'state': state}
        resp.status = falcon.HTTP_200
