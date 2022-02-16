from routes.stripehooks import remove_subscription
from datetime import date, datetime
import time
import stripe
import falcon
import hashlib
import re
from spectree import Response
from routes.utility.solana_api import SolanaApi

from routes.utility.ob_token import OBTokenTransaction, OBToken, get_price_in_usd
from routes.utility.obt_holding_ranks import OBTHoldingRanks
from routes.utility.otp import OTP
from routes.utility.crypto import CipherAES

from .spectree import spectree
from .base import Route, auth_guard, email_validation_auth_guard
from routes.utility.users import UserManager

from pydantic import BaseModel, constr
from typing import List, Optional, Any, Dict


class UserChangeNameReq(BaseModel):
    name: str


class UsersPostResp(BaseModel):
    success: bool = True


class AdminDeleteUsersPostResp(BaseModel):
    success: bool = True


class UsersErrorPostMessage(BaseModel):
    message: str


class DeleteUserPostReq(BaseModel):
    reasons: Optional[List]
    feedback: Optional[str]


class RequestWithdrawReq(BaseModel):
    address: str
    amount: float
    otp_code: str
    password: str


class EnableOTPReq(BaseModel):
    otp_code: str
    secret_question: str
    secret_question_answer: str


class UserChangePasswordReq(BaseModel):
    old_password: str
    new_password: Optional[constr(min_length=8)]
    confirmation_password: Optional[constr(min_length=8)]


class UserChangeName(Route):
    @auth_guard
    @spectree.validate(json=UserChangeNameReq, resp=Response(HTTP_200=UsersPostResp))
    def on_post(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]
        updated_name = req.media['name']
        profile['name'] = updated_name
        self.dbs['users'][username] = profile
        resp.media = {'success': True}


def hash_f(data):
    blk = hashlib.blake2b(salt=b'\xdb\xe5m\x95\xd47P\xb2\x1e!w\xa2,\xb2{3')
    blk.update(data.encode())
    return blk.digest()


class UserChangePassword(Route):
    @auth_guard
    @spectree.validate(json=UserChangePasswordReq, resp=Response(HTTP_200=UsersPostResp, HTTP_400=UsersErrorPostMessage))
    def on_post(self, req, resp):
        username = self.get_username(req).unwrap()
        old_pass = req.media['old_password']
        new_pass = req.media['new_password']
        confirm_new_pass = req.media['confirmation_password']

        if new_pass != confirm_new_pass:
            resp.status = falcon.HTTP_400
            resp.media = {'message': "Passwords do not match"}

        if new_pass == old_pass:
            resp.status = falcon.HTTP_400
            resp.media = {'message': "Old and new passwords are the same"}
            return

        regex_list = [(re.compile('(?=.*\\d)'), "Please include at least one number in your password"),
                      (re.compile('(?=.*[A-Z])'), "Please include at least one uppercase in your password"),
                      (re.compile('(?=.*\\w)'), "Please include at least one symbol in your password")]

        for r in regex_list:
            if not r[0].match(new_pass):
                resp.status = falcon.HTTP_400
                resp.media = {'message': r[1]}
                return

        user = self.dbs['users'][username]

        if user['pass'] != hash_f(old_pass):
            resp.status = falcon.HTTP_400
            resp.media = {'message': "Incorrect Credentials. Please try again."}
            return

        user['pass'] = hash_f(new_pass)
        self.dbs['users'][username] = user
        resp.media = {'success': True}


class DeleteUser(Route):

    def save_delete_log(self, req, username, time_added):
        self.dbs['deleted_users_log'][username] = {'username': username, 'reasons': req.media.get('reason', ''),
                                                   'feedback': req.media.get('feedback', ''), 'time_added': time_added,
                                                   'time_deleted': int(time.time())}

    @auth_guard
    @spectree.validate(json=DeleteUserPostReq, resp=Response(HTTP_200=AdminDeleteUsersPostResp, HTTP_400=UsersErrorPostMessage))
    def on_post(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]
        bot_ids = profile['bots']

        for i in bot_ids:
            if self.dbs['bots'][i]['enabled']:
                resp.status = falcon.HTTP_400
                resp.media = {'message': 'User has active bots and cannot be deactivated'}
                return

        subscription_id = profile.get('payment', {}).get('subscription_id', '')
        if subscription_id:
            try:
                stripe.Subscription.modify(subscription_id, cancel_at=int(datetime.now().timestamp())+180, proration_behavior=None)
            except Exception as e:
                pass
            remove_subscription(self.dbs, profile)

        with self.dbs['users'].transact(retry=True):
            profile = self.dbs['users'][username]
            profile['deactivated'] = True
            self.dbs['users'][username] = profile
            self.save_delete_log(req, username, profile.get('time_added'))

        resp.media = {'success': True}


class TokenBalance(Route):
    @email_validation_auth_guard
    def on_get(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]
        if not profile.get('OTP_config', {}).get('is_verified'):
            resp.media = {'error': f"Please enable your 2 factor authentication."}
            resp.status = falcon.HTTP_400
            return

        profile = UserManager(self.dbs).ensure_token_address(profile, username)
        address = profile['obt_token']['address'].pub
        token_manager = OBTokenTransaction(self.dbs)
        token_manager.check_user(username)
        resp.media = {'wallet_address': address,
                      'obt_usd_price': get_price_in_usd(self.dbs), 
                      'balance': {
                          'active': profile['obt_token']['balance']/OBToken.token_decimal,
                          'pending': token_manager.token.get_balance(address)/OBToken.token_decimal}
                      }

        resp.status = falcon.HTTP_200


class TokenTransaction(Route):

    @email_validation_auth_guard
    def on_get(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]
        if not profile.get('OTP_config', {}).get('is_verified'):
            resp.media = {'error': f"Please enable your 2 factor authentication."}
            resp.status = falcon.HTTP_400
            return

        profile = UserManager(self.dbs).ensure_token_address(profile, username)
        address = profile['obt_token']['address'].pub
        resp.media = {'result': []}
        if username in self.dbs['token_transactions']:
            resp.media = {'result': self.dbs['token_transactions'][username]}
        resp.status = falcon.HTTP_200


class RequestWithdraw(Route):

    @email_validation_auth_guard
    @spectree.validate(json=RequestWithdrawReq)
    def on_post(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]
        if not UserManager(self.dbs).check_auth(username, req.media['password']):
            resp.media = {'error': f"Invalid password, please try again"}
            resp.status = falcon.HTTP_400
            return

        if not profile.get('OTP_config', {}).get('is_verified'):
            resp.media = {'error': f"Please enable your 2 factor authentication first."}
            resp.status = falcon.HTTP_400
            return

        if not profile.get('obt_token'):
            resp.media = {'error': 'Please enable your 2 factor authentication.'}
            resp.status = falcon.HTTP_400
            return

        if not OTP.verify(profile['OTP_config'].get('secret'), req.media['otp_code']):
            resp.media = {'error': f"Please provide a valid 2 factor authentication code"}
            resp.status = falcon.HTTP_400
            return

        amount = int(req.media['amount'])*OBToken.token_decimal
        active_balance = profile['obt_token']['balance']

        if profile['obt_token'].get('NFT',{}).get('unlock_date'):
            if int(datetime.now().timestamp()) < profile['obt_token']['NFT']['unlock_date']:
                active_balance -= profile['obt_token'].get('NFT',{}).get('lock_amount',0)


        if amount > active_balance:
            resp.media = {'error': f"Your requested withdrawal amount exceeds the available balance of {active_balance/OBTokenTransaction.min_transaction_amount} OBT"}
            resp.status = falcon.HTTP_400
            return

        if amount < OBTokenTransaction.min_transaction_amount:
            resp.media = {'error': f"The requested withdrawal amount should be at least {OBTokenTransaction.min_transaction_amount/OBToken.token_decimal} OBT "}
            resp.status = falcon.HTTP_400
            return

        if profile['obt_token'].get('withdraw', {}).get('is_requested'):
            resp.media = {'status': 'You have already submitted the request.'}
            resp.status = falcon.HTTP_200
            return

        profile['obt_token']['withdraw'] = {
            'is_requested': True,
            'address': req.media['address'],
            'amount': amount
        }
        self.dbs['users'][username] = profile
        resp.media = {'status': 'Your request is successfully submitted'}
        resp.status = falcon.HTTP_200


class EstimateFee(Route):

    @email_validation_auth_guard
    def on_get(self, req, resp):
        obt = OBTokenTransaction(self.dbs)
        resp.media = {
            'Total_Fee_BNB': (obt.token._estimate_transaction_fee()["value"]/OBToken.token_decimal)*4,
            'Total_Fee_OBT': obt.min_transaction_amount/OBToken.token_decimal
        }
        resp.status = falcon.HTTP_200


class EnableOTP(Route):
    @email_validation_auth_guard
    def on_post(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]
        if not profile.get('OTP_config', {}).get('secret'):
            profile['OTP_config'] = {
                'secret': OTP.generate_secret(),
                'is_verified': False
            }
            self.dbs['users'][username] = profile

        resp.media = {'secret': OTP.get_secret_str(profile['OTP_config']['secret'])}
        resp.status
        return

    @email_validation_auth_guard
    @spectree.validate(json=EnableOTPReq)
    def on_put(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]

        if len(req.media['secret_question']) < 0:
            resp.media = {"error": "if secret_question is missing or its length is 0"}
            resp.status = falcon.HTTP_400
            return

        if len(req.media['secret_question_answer']) < 0:
            resp.media = {"error": "if secret_question_answer is missing or its length is 0"}
            resp.status = falcon.HTTP_400
            return

        if not profile.get('OTP_config', {}).get('secret'):
            resp.media = {"error": "Please enable your 2 factor authentication first."}
            resp.status = falcon.HTTP_400
            return

        if OTP.verify(profile.get('OTP_config', {}).get('secret'), req.media['otp_code']):
            otp_cfg = profile.get('OTP_config', {})
            otp_cfg['is_verified'] = True
            otp_cfg['secret_question'] = CipherAES.encrypt(req.media['secret_question'].encode())
            otp_cfg['secret_question_answer'] = CipherAES.encrypt(req.media['secret_question_answer'].encode())
            profile['OTP_config'] = otp_cfg
            self.dbs['users'][username] = profile
            resp.media = {'enabled': True}
            resp.status = falcon.HTTP_200
            return

        resp.media = {'error': 'Worng OTP code!'}
        resp.status = falcon.HTTP_200

    @email_validation_auth_guard
    def on_get(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]
        if not profile.get('OTP_config', {}).get('is_verified'):
            resp.media = {'enabled': False}
            resp.status = falcon.HTTP_200
            return

        resp.media = {'enabled': True}
        resp.status = falcon.HTTP_200


class OTPQuestions(Route):
    def __init__(self, dbs: dict):
        super().__init__(dbs)

    @email_validation_auth_guard
    def on_get(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]
        otp_cfg = profile.get('OTP_config', {})
        if not otp_cfg.get('secret') or not otp_cfg.get('secret_question'):
            resp.media = {"error": "Please enable your 2 factor authentication first."}
            resp.status = falcon.HTTP_400
            return

        resp.media = {'secret_question': CipherAES.decrypt(profile['OTP_config']['secret_question']).decode()}
        resp.status = falcon.HTTP_200

class UserOBTRank(Route):

    @auth_guard
    def on_get(self, req, resp):
        username = self.get_username(req).unwrap()
        profile = self.dbs['users'][username]
        address = profile.get('obt_token',{}).get('address',None)
        if not address:
            resp.media = {'error':'No wallet address to check.'}
            resp.status = falcon.HTTP_404
            return


        ranks = self.dbs['globals'].get('OBTHoldingRanks:ranks')            
        if not ranks:
            OBTHoldingRanks(self.dbs).refresh_all_the_ranks()
            ranks = self.dbs['globals'].get('OBTHoldingRanks:ranks')            
        
        resp.media = {'rank': ranks.get(address.pub)}
        resp.status = falcon.HTTP_200


