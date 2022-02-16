from .base import Route, UrlManager
from routes.utility.users import UserManager
from .utility.sendgrid import sg
from sendgrid.helpers.mail import Mail, Email, To, Content
from functools import reduce
import falcon
import secrets
from .spectree import spectree
from .profile import Referral, get_referral_code
from spectree import Response
from pydantic import BaseModel
import time

xor_check = lambda x: reduce(lambda y, z: z ^ y, x)

def xor_generate(token: bytes):
    token = bytearray(token)
    token.append(xor_check(token))
    return bytes(token)

def check_xor(token: bytes) -> bool:
    return xor_check(token) == 0

class InvitationsGetReq(BaseModel):
    token: str

class InvitationsGetResp(BaseModel):
    valid: bool = True
    email: str

class InvitationsGetMessage(BaseModel):
    valid: bool = False
    expired: bool

class InvitationsPostReq(BaseModel):
    token: str
    password: str

class InvitationsPostResp(BaseModel):
    success: bool = True

class InvitationsPostMessage(BaseModel):
    valid: bool = False
    expired: bool

class EmailTokenUtilities:
    def __init__(self, dbs: dict):
        self.dbs = dbs
    
    def build_invitation_link(self, email):
        # Create singleton token
        token = secrets.token_hex()
        token = xor_generate(bytes.fromhex(token)).hex()
        self.dbs['invitations'].set(token, email, expire=24 * 60 * 60 * 7, retry=True)

        base_url = UrlManager().get_server_url()
        return base_url + 'invitation?token=' + token

    def build_forgot_password_link(self, email):
        # Create singleton token
        token = secrets.token_hex()
        token = xor_generate(bytes.fromhex(token)).hex()
        self.dbs['forgot_password'].set(token, email, expire=60*60, retry=True)

        base_url = UrlManager().get_server_url()
        return base_url + 'reset-password?token=' + token


class Invitations(Route):
    @spectree.validate(query=InvitationsGetReq, resp=Response(HTTP_200=InvitationsGetResp, HTTP_403=InvitationsGetMessage))
    def on_get(self, req, resp):
        token = req.params['token']
        email = self.verify_token(token)
        if email:
            resp.media = {'valid': True, 'email': email}
            resp.status = falcon.HTTP_200
        else:
            resp.media = {'valid': False, 'expired': check_xor(bytes.fromhex(token))}
            resp.status = falcon.HTTP_403

    @spectree.validate(json=InvitationsPostReq, resp=Response(HTTP_200=InvitationsPostResp, HTTP_403=InvitationsPostMessage))
    def on_post(self, req, resp):
        token = req.media['token']
        email = self.verify_token(token)

        if not email:
            resp.status = falcon.HTTP_403
            resp.media = {'valid': False, 'expired': check_xor(bytes.fromhex(token))}
            return

        password = req.media['password']
        UserManager(self.dbs).set_password(email, password)
        del self.dbs['invitations'][token]

        #.Referrals
        user = self.dbs['users'][email]
        if user.get('referral', None) and user['referral'] in self.dbs['referrals']:
            referrer = self.dbs['referrals'].get(user['referral'], [])
            referrer.append(Referral(username=email, code=get_referral_code(email), timestamp=time.time(),referral_tier_id=user.get('referral_tier_id','init')))
            self.dbs['referrals'][user['referral']] = referrer
        #;

        resp.media = {'success': True}

    def verify_token(self, token):
        if self.dbs['invitations'].get(token, False):
            return self.dbs['invitations'][token]

import smtplib, ssl
from bs4 import BeautifulSoup

class InvitationEmail:
    def __init__(self):
        self.port = 465
        self.smtp_server = "smtp.gmail.com"
        self.sender_email = "sender@onebutton.trade"
        self.password = "Suaja2=A312"

    def send_message(self, receiver_email, link, name, template, subject):
        with open('routes/templates/%s.html' % template, 'r') as html:
            soup = BeautifulSoup(html, features='lxml')

        if not name:
            name = 'user'

        soup_str = str(soup)
        soup_str = soup_str.replace('[[name]]', name)
        soup_str = soup_str.replace('[[link]]', link)
        
        #message["Reply-to"] = "team@onebutton.trade"

        body = Content('text/html', soup_str)
        mail = Mail(Email(self.sender_email), To(receiver_email), subject, body)
        response = sg.client.mail.send.post(request_body=mail.get())
