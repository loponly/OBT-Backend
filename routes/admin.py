import re
from falcon.status_codes import HTTP_500

import pandas as pd
from routes.profile import Referral
from routes.utility.solana_api import SolanaApi
from tradeEnv.metrics import MarketInfo
from tradeEnv.api_adapter import ApiAdapter
from .invitations import EmailTokenUtilities, InvitationEmail
from itertools import islice
from .logging import RotatingFanoutHandler
from io import BytesIO
from zipfile import ZipFile, ZIP_LZMA
import falcon
import falcon.request_helpers
import os
import io
import time
import random
import json
import zstd
import hashlib
from itertools import islice

from .base import Route, StandardResponse
from .db import get_tmp_cache
from routes.utility.acl import ACLManager
from .utils import editable_keys, map_dict
from .policy import HoldingTiers, NFTTiers, SubscriptionPolicy
from .realtime import get_env

from .utility.strategy import StrategyFactory
from routes.utility.users import UserManager

from Crypto.Random import get_random_bytes
from Crypto.Cipher import AES
from base64 import b64decode, b64encode
from pyaxo_ng import AxolotlConversation

from spectree import Response
from .spectree import spectree

from pydantic import BaseModel, constr
from typing import List, Optional, Any, Dict

from routes.exchange import Exchange

class TradeLogModel(BaseModel):
    date: Optional[int]
    price: float
    side: str = 'actually called type'
    amount: float
    pair: Optional[str]
    order_type: Optional[str]
    fee: Optional[float]
    fee_asset: Optional[str]
    balance: Optional[float]
    change: Optional[float]


class OpenOrderModel(BaseModel):
    side: str
    org_vol: float
    volume: float
    price: float
    createtime: int
    expire_time: int
    txid: str
    order_type: Optional[str]


class AdminStatsModel(BaseModel):
    user: str
    uid: str
    startingBalance: List[float]
    curBalance: float
    tokBalance: float
    portfolioValue: float
    max_balance: float
    min_balance: float
    trade_log: List[TradeLogModel]
    in_fees: float
    open_orders: Dict[str, OpenOrderModel]
    stop_loss: Optional[Dict[str, Any]]
    nickname: Optional[str]
    exchange: str
    strategy: str
    strategy_name: Optional[str]
    market: str
    candles: str
    ml_boost: bool
    trades_made: int
    start_time: int
    activated: int
    stop_time: Optional[int]
    enabled: bool
    bah_roi: Optional[float]
    avg_fee: float
    avg_roi_trade: float
    avg_roi_month: float
    description: Optional[str]
    image: Optional[str]


class ExchangeModel(BaseModel):
    pairs: List[str]
    candles: Optional[List[str]]


class AdminExchangeReq(BaseModel):
    __root__: Dict[str, ExchangeModel]


class AdminDisable2FAReq(BaseModel):
    username: str
    is_disabled: bool


class GetAdminStatsGetResp(BaseModel):
    __root__: Dict[str, AdminStatsModel]


class AdminUsersGetResp(BaseModel):
    __root__: List[Any]


class AdminUsersPostReq(BaseModel):
    email: str
    name: str


class AdminUsersPostResp(BaseModel):
    success: bool = True


class AdminUsersPostMessage(BaseModel):
    success: bool = False
    message: str


class AdminStrategiesPutReq(BaseModel):
    original_id: str
    new_config: Dict[str, Any]


class AdminStratTransportPostReq(BaseModel):
    uuid: str


class AdminRequestsPostReq(BaseModel):
    email: str


class AdminRequestsPostResp(BaseModel):
    success: bool = True


class AdminRequestsPostMessage(BaseModel):
    message: str


class RequestModel(BaseModel):
    email: str
    name: str
    period: int


class AdminRequestsGetResp(BaseModel):
    result: List[RequestModel]


class AdminDenyRequestsPostReq(BaseModel):
    email: str


class AdminDenyRequestsPostResp(BaseModel):
    success: bool = True


class InvitationModel(BaseModel):
    email: str
    name: str
    period: int


class AdminInvitationsGetResp(BaseModel):
    __root__: List[InvitationModel]


class AdminDeletedLogModel(BaseModel):
    username: str
    time_added: int
    time_deleted: int
    reasons: Optional[List]
    feedback: Optional[str]


class AdminExchangeMaketResp(BaseModel):
    __root__: Dict[str, Any]


class AdminResurrectBotPostReq(BaseModel):
    bot_id: str


class AdminResurrectBotPostResp(BaseModel):
    success: bool = True


class AdminDeletedLogResp(BaseModel):
    __root__: List[AdminDeletedLogModel]


class AdminInvitationsPostReq(BaseModel):
    email: str


class AdminInvitationsPostResp(BaseModel):
    success: bool = True


class AdminDeleteUsersPostReq(BaseModel):
    email: str


class UserChangePasswordReq(BaseModel):
    old_password: str
    new_password: Optional[constr(min_length=8)]
    confirmation_password: Optional[constr(min_length=8)]


class AdminDeleteUsersPostResp(BaseModel):
    success: bool = True


class AdminDeleteUsersPostMessage(BaseModel):
    message: str


class AdminRevokeInvitationPostReq(BaseModel):
    email: str


class AdminRevokeInvitationPostResp(BaseModel):
    success: bool = True


class AdminRevokeInvitationPostMessage(BaseModel):
    message: str


class BotModel(BaseModel):
    uid: str
    user: Optional[str]
    strat_name: Optional[str]
    exchange: str
    market: str
    current_balance: float
    starting_balance: List[float]
    activated: int
    roi: float
    trades_made: int


class AdminBotListGetResp(BaseModel):
    __root__: Dict[str, BotModel]


class AdminAnalyticsGetResp(BaseModel):
    values: Dict[str, Any]


class AdminStatsGetResp(BaseModel):
    __root__: dict


class AdminPromotingOrderGetResp(BaseModel):
    promoting_order: List[str]


class AdminPromotingOrderReq(BaseModel):
    promoting_order: List[str]


class AdminPromotingOrderPostResp(BaseModel):
    success: bool = True

# Models end


def encrypt_aes_json(data: bytes, key: bytes) -> dict:
    nonce = get_random_bytes(16)
    cipher = AES.new(key, AES.MODE_SIV, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(data)

    json_k = ['nonce', 'ciphertext', 'tag']
    json_v = [b64encode(x).decode('utf-8') for x in (nonce, ciphertext, tag)]
    return dict(zip(json_k, json_v))


def decrypt_aes_json(obj: dict, key: bytes) -> bytes:
    nonce = b64decode(obj['nonce'].encode())
    ct = b64decode(obj['ciphertext'].encode())
    tag = b64decode(obj['tag'].encode())

    try:
        cipher = AES.new(key, AES.MODE_SIV, nonce=nonce)
        plaintext = cipher.decrypt_and_verify(ct, tag)
    except (ValueError, KeyError):
        return None

    return plaintext


class MetaAuth(Route):
    def on_post(self, req, resp):
        print(f'Got new admin from {req.remote_addr}')
        plaintext = decrypt_aes_json(req.media, self.dbs['globals']['session_mkey'])

        if not plaintext:
            print('Bad message')
            # Obfuscate 1-bit oracle
            time.sleep(random.random() * 2.)
            resp.media = {'error': 'Invalid key'}
            resp.status = falcon.HTTP_403
            return

        # {'keys': [...], 'identity': ''}
        data = json.loads(plaintext.decode())
        keys = [b64decode(key) for key in data['keys']]

        oKeys, oResolve = AxolotlConversation.new_from_x3dh(mode=True)  # Recipient
        oConv = oResolve(*keys)
        self.dbs['globals'][f'axolotl:{data["identity"]}'] = oConv

        obj = {
            'keys': [b64encode(key).decode() for key in oKeys],
            'identity': b64encode(self.dbs['globals']['id_key'].pub).decode()
        }

        resp.media = encrypt_aes_json(json.dumps(obj).encode(), self.dbs['globals']['session_mkey'])


cache = get_tmp_cache('admin')

# Enables PFS-E2EE using pyaxo-ng
# Decrypts underlying stream (the content-type is for the decrypted data)


def pfs_encrypted(func):
    def inner(self, req, resp):
        req.identity = req.headers['X-Identity'.upper()]

        # Read stream *before* decrypting (locks database)
        raw_data = None
        if not req.bounded_stream.is_exhausted:
            raw_data = req.bounded_stream.read()

        with self.secure(req.identity) as em:
            em.check_timesig(req.headers['X-Timesig'.upper()].encode())
            # Decrypt stream
            if raw_data != None:
                dec_data = em.decrypt_body(raw_data)
                req._bounded_stream = falcon.request_helpers.BoundedStream(io.BytesIO(dec_data), len(dec_data))

        func(self, req, resp)

        if resp.media != None:
            resp.data = json.dumps(resp.media).encode()
            resp._media = None

        if resp.data:
            with self.secure(req.identity) as em:
                resp.data = em.encrypt_body(resp.data)

    return inner


class AdminInfo(Route):
    @pfs_encrypted
    def on_get(self, req, resp):
        resp.data = json.dumps({'time': int(time.time())}).encode()


class AdminUsers(Route):
    @pfs_encrypted
    @spectree.validate(resp=Response(HTTP_200=AdminUsersGetResp))
    def on_get(self, req, resp):
        result = []
        for email in self.dbs['users']:
            profile = self.dbs['users'][email]
            if not bool(profile.get('pass', False)) or profile.get('deactivated', None):
                continue
            um = UserManager(self.dbs)
            user = {}
            user['email'] = email
            user['name'] = profile.get('name', '--')
            user['exchanges'] = list(profile['exchanges'].keys())
            user['bots_count'] = len(um.get_active_bots(email))

            bots_info = {}
            for b, bot in um.get_active_bots(email).items():
                bots_info[b] = {}
                bots_info[b]['roi'] = bot.get('roi', 0)
                bots_info[b]['last_trade_attempt'] = getattr(bot['state'], 'last_trade_attempt', 0)
                bots_info[b]['candles'] = bot['candles']

            user['bots'] = bots_info
            user['referrals'] = um.get_referrals(email)
            user['bot_balance'] = um.get_bot_balance(email)
            user['balance'] = um.get_portfolio(email)
            
            # Clean raw values for admin
            user['token'] = map_dict(lambda k,v: (k, v.pub if k == 'address' else v), profile.get('obt_token', {}))
            user['token'] = map_dict(lambda k,v: (k, (v / 10 ** 18) if k in ['balance', 'pending_balance'] else v), user['token'])

            user['token_transactions'] = self.dbs['token_transactions'].get(email, [])
            user['OTP'] = um.get_otp_config(profile)
            user['time_added'] = profile.get('time_added', 0)
            user['last_active'] = int(time.time()) - profile.get('last_active', 0)
            user['subscription'] = um.get_policy(email).sub
            user['no_NFT_tokens'] = len(list(filter(lambda _a: _a in self.dbs['nft_token_bots'], profile.get('obt_token',{}).get("NFT",{}).get('token_address',[]))))
            result.append(user)

        resp.media = result

    @pfs_encrypted
    @spectree.validate(json=AdminUsersPostReq, resp=Response(HTTP_200=AdminUsersPostResp, HTTP_400=AdminUsersPostMessage))
    def on_post(self, req, resp):
        email = req.media['email']
        name = req.media['name']

        if len(email) > 50 or len(name) > 50:
            resp.media = {'success': False, 'message': 'Email/name too long'}
            resp.status = falcon.HTTP_400
            return

        user_manager = UserManager(self.dbs)
        user_manager.create_user(email, '', name, silent_fail=True)

        link = EmailTokenUtilities(self.dbs).build_invitation_link(email)
        InvitationEmail().send_message(email, link, name, 'user_invitation', 'Welcome to OB Trader 🚀🤖')

        resp.media = {'success': True}


class AdminStrategies(Route):
    @pfs_encrypted
    def on_post(self, req, resp):
        code = None
        name = None
        for part in req.media:
            if part.name == 'strategydefinition':
                code = b''
                while True:
                    chunk = part.stream.read(8192)
                    if not chunk:
                        break

                    code += chunk
                    if len(chunk) > 20000000:
                        resp.media = {'error': 'Content too large'}
                        resp.status = falcon.HTTP_413
                        return
                del chunk

            elif part.name == 'modelfile':
                if part.filename:
                    # TODO: make model location dynamic?
                    with open(os.path.join('./store/models/', part.secure_filename), 'wb+') as dest:
                        part.stream.pipe(dest)

            elif part.name == 'name':
                if not part.text:
                    resp.media = {'error': 'No name provided'}
                    resp.status = falcon.HTTP_400
                    return
                name = part.text

        # For use with DillDisk (input: Dill, DillDisk expects Zstd(Dill))
        assert code != None, "No strategy file provided"
        assert len(code) > 10, "Strategy file too small"
        self.dbs['models'].unsafe_set(name, zstd.compress(code, 3, 1), retry=True)

    @pfs_encrypted
    @spectree.validate(json=AdminStrategiesPutReq, resp=Response(HTTP_200=StandardResponse))
    def on_put(self, req, resp):
        oid = req.media['original_id']
        nconfig = req.media['new_config']

        strat = self.dbs['models'][oid]
        for k in nconfig:
            if k in ['name', 'enabled']:
                continue
            setattr(strat, k, nconfig[k])

        self.dbs['models'][nconfig['name']] = strat

        disabled_set = self.dbs['models'].get('__disabled_strats__', set())
        disabled_set.discard(oid)
        if not nconfig['enabled']:
            disabled_set.add(nconfig['name'])

        self.dbs['models']['__disabled_strats__'] = disabled_set

        if oid != nconfig['name']:
            del self.dbs['models'][oid]

        resp.media = {'info': 'Succesfully updated strategy'}

    @pfs_encrypted
    def on_get(self, req, resp):
        # Add dynamic strategies (non-uuid entries)
        # TODO: memoize?
        disabled = self.dbs['models'].get('__disabled_strats__', set())

        # print(m)
        o = []
        m = self.dbs['models']
        for k in m:
            keys = editable_keys(m[k].__dict__)
            d = {b: m[k].__dict__[b] for b in keys}
            # print(keys, d)
            d['name'] = k
            d['enabled'] = k not in disabled
            o.append(d)

        resp.media = o

    @pfs_encrypted
    def on_delete(self, req, resp):
        uid = req.media['uuid']
        del self.dbs['models'][uid]
        # TODO: disable bots associated?


class AdminUserSubscriptions(Route):
    @pfs_encrypted
    def on_post(self, req, resp):
        data = req.media

        assert bool(data.get('user', None)), "No user specified"
        user = data['user']
        with self.dbs['users'].transact(retry=True):
            p = self.dbs['users'][user]
            npolicy = data.get('policy_id')
            nsubscr = data.get('subscription_id')
            if type(nsubscr) == str:
                p['payment']['subscription_id'] = nsubscr

            if type(npolicy) == str:
                p['payment']['policy_id'] = npolicy

            if data.get('free_pro', None) != None:
                p['payment']['free_pro'] = bool(data['free_pro'])

            self.dbs['users'][user] = p
        resp.media = {'success': True}


class AdminSubscriptionConfig(Route):
    @pfs_encrypted
    def on_get(self, req, resp):
        acl = self.dbs['globals'][SubscriptionPolicy._key]
        acl = list(map(lambda entry: entry.dict(exclude={'_key'}), acl))
        resp.media = acl

    @pfs_encrypted
    def on_post(self, req, resp):
        data = req.media
        acl = []
        for entry in data:
            #! Note: variables not defined in SubscriptionPolicy ignored
            acl.append(SubscriptionPolicy(**entry))

        self.dbs['globals'][SubscriptionPolicy._key] = acl
        resp.media = {'success': True}

class AdminHoldingTiersConfig(Route):

    @pfs_encrypted
    def on_get(self, req, resp):
        acl = self.dbs['globals'][HoldingTiers._key]
        acl = list(map(lambda entry: entry.dict(exclude={'_key'}), acl))
        resp.media = acl

    @pfs_encrypted
    def on_post(self, req, resp):
        data = req.media
        acl = []
        for entry in data:
            #! Note: variables not defined in SubscriptionPolicy ignored
            acl.append(HoldingTiers(**entry))

        self.dbs['globals'][HoldingTiers._key] = acl
 
        resp.media = {'success': True}

class AdminNFTTiersConfig(Route):

    @pfs_encrypted
    def on_get(self, req, resp):
        acl = self.dbs['globals'][NFTTiers._key]
        acl = list(map(lambda entry: entry.dict(exclude={'_key'}), acl))
        resp.media = acl

    @pfs_encrypted
    def on_post(self, req, resp):
        data = req.media
        acl = []
        for entry in data:
            #! Note: variables not defined in SubscriptionPolicy ignored
            acl.append(NFTTiers(**entry))
        self.dbs['globals'][NFTTiers._key] = acl

        for u in self.dbs['users']:
            profile = self.dbs['users'][u]
            token_addresses =profile.get('obt_token',{}).get("NFT",{}).get('token_address')
            if token_addresses:
                skin_tier = ACLManager(self.dbs).get_skins_tier(token_addresses,_overwrite=True)
                SolanaApi(self.dbs).get_all_token_infos(token_addresses,skin_tier,_overwrite=True)
 
        resp.media = {'success': True}


class AdminSubscriptionBenefits(Route):
    @pfs_encrypted
    def on_get(self, req, resp):
        acl = self.dbs['globals'][SubscriptionPolicy._key]
        benefit_keys = set()
        for pol in acl:
            for k in getattr(pol, 'benefits', {}).keys():
                benefit_keys.add(k)
        resp.media = {'keys': list(benefit_keys)}


class AdminStrategyTransport(Route):
    @pfs_encrypted
    def on_post(self, req, resp):
        if type(req.media) == dict:
            uuid = req.media['uuid']
            data = BytesIO()
            zipf = ZipFile(data, mode='w', compression=ZIP_LZMA)

            cstrat = self.dbs['models'].unsafe_get(uuid, retry=True)
            bstrat = zstd.decompress(cstrat)

            zipf.writestr(f'{uuid}.dill', bstrat)

            # TODO: add pytorch version for compat?
            strat = self.dbs['models'][uuid]
            model_file = getattr(strat, 'model_file', None)
            if model_file != None:
                model_path = os.path.join('./store/models/', model_file + '.pt')
                with open(model_path, 'rb') as f:
                    zipf.writestr(f'{model_file}.pt', f.read())

            zipf.close()
            resp.data = data.getvalue()
            resp.content_type = 'application/octet-stream'
        else:
            for part in req.media:
                if part.name == 'bundle':
                    tmp = BytesIO()
                    part.stream.pipe(tmp)
                    zipf = ZipFile(tmp, mode='r', compression=ZIP_LZMA)
                    for name in zipf.namelist():
                        if '.dill' in name:
                            bstrat = zipf.read(name)
                            self.dbs['models'].unsafe_set(name.split('.')[0], zstd.compress(bstrat, 3, 1), retry=True)
                            continue

                        if '.pt' in name:
                            zipf.extract(name, path='./store/models/')
                            continue

            resp.media = {'success': True}


class AdminRequests(Route):
    @pfs_encrypted
    @spectree.validate(json=AdminRequestsPostReq, resp=Response(HTTP_200=AdminRequestsPostResp, HTTP_400=AdminRequestsPostMessage))
    def on_post(self, req, resp):
        email = req.media['email']

        user_request = self.dbs['requests'].get(email, None)
        if not user_request:
            resp.status = falcon.HTTP_400
            resp.media = {'message': 'There is no request for this user'}
            return

        name = user_request['name']
        user_manager = UserManager(self.dbs)
        user_manager.create_user(email, '', name, silent_fail=True)

        link = EmailTokenUtilities(self.dbs).build_invitation_link(email)
        InvitationEmail().send_message(email, link, name, 'user_invitation', 'Welcome to OB Trader 🚀🤖')

        del self.dbs['requests'][email]

        resp.media = {'success': True}

    @pfs_encrypted
    @spectree.validate(resp=Response(HTTP_200=AdminRequestsGetResp))
    def on_get(self, req, resp):
        requests = self.dbs['requests']
        result = [{
            'email': email,
            'name': requests[email]['name'],
            'period': int(time.time()) - requests[email]['time']
        } for email in requests]
        resp.media = {'result': result}


class AdminDenyRequests(Route):
    @pfs_encrypted
    @spectree.validate(json=AdminDenyRequestsPostReq, resp=Response(HTTP_200=AdminDenyRequestsPostResp))
    def on_post(self, req, resp):
        email = req.media['email']
        del self.dbs['requests'][email]

        resp.media = {'success': True}


class AdminInvitations(Route):
    @pfs_encrypted
    @spectree.validate(resp=Response(HTTP_200=AdminInvitationsGetResp))
    def on_get(self, req, resp):
        result = []
        for email in self.dbs['users']:
            profile = self.dbs['users'][email]
            if bool(profile.get('pass', False)):
                continue

            user = {
                'email': email,
                'name': profile.get('name', '--'),
                'period': int(time.time()) - profile.get('time_added', 0)
            }

            result.append(user)

        resp.media = result

    @pfs_encrypted
    @spectree.validate(json=AdminInvitationsPostReq, resp=Response(HTTP_200=AdminInvitationsPostResp))
    def on_post(self, req, resp):
        email = req.media['email']
        profile = self.dbs['users'][email]
        name = profile.get('name', 'user')

        assert not bool(profile.get('pass', False)), 'This user is already active'

        link = EmailTokenUtilities(self.dbs).build_invitation_link(email)
        InvitationEmail().send_message(email, link, name, 'user_invitation', 'Welcome to OB Trader 🚀🤖')
        profile['time_added'] = int(time.time())
        self.dbs['users'][email] = profile

        resp.media = {'success': True}


class AdminDeleteUsers(Route):
    @pfs_encrypted
    @spectree.validate(json=AdminDeleteUsersPostReq, resp=Response(HTTP_200=AdminDeleteUsersPostResp, HTTP_400=AdminDeleteUsersPostMessage))
    def on_post(self, req, resp):
        email = req.media['email']
        profile = self.dbs['users'][email]
        bot_ids = profile['bots']

        for i in bot_ids:
            if self.dbs['bots'][i]['enabled']:
                bot = self.dbs['bots'][i]
                exchange = bot['exchange']
                market = bot['market']

                env = get_env(exchange, market, bot['candles'])
                env.set_user(bot['state'], profile)
                env.nstep()  # Update limit order status from the exchange

                open_orders = bot['state'].open_orders
                for order in list(open_orders.keys()):
                    # Cancel on the exchange, and mark as such on our side
                    env.get_api().cancel_order(profile['exchanges'][exchange], market, open_orders[order].txid)
                    env._reject_limit(open_orders[order].txid)

                env.sellp(1.)

                bot['enabled'] = False
                bot['stop_time'] = time.time()
                self.dbs['bots'][i] = bot

        with self.dbs['users'].transact(retry=True):
            profile = self.dbs['users'][email]
            profile['deactivated'] = True
            self.dbs['users'][email] = profile
        resp.media = {'success': True}


class AdminDeleteUsersLog(Route):
    @pfs_encrypted
    @spectree.validate(resp=Response(HTTP_200=AdminDeletedLogResp))
    def on_get(self, req, resp):
        result = []

        for k in self.dbs['deleted_users_log']:
            log = {**self.dbs['deleted_users_log'][k], 'username': k}
            result.append(log)

        resp.media = result


class AdminRevokeInvitation(Route):
    @pfs_encrypted
    @spectree.validate(json=AdminRevokeInvitationPostReq, resp=Response(HTTP_200=AdminRevokeInvitationPostResp, HTTP_400=AdminRevokeInvitationPostMessage))
    def on_post(self, req, resp):
        email = req.media['email']
        profile = self.dbs['users'][email]
        if profile.get('pass', False):
            resp.status = falcon.HTTP_400
            resp.media = {'message': 'This user is active'}
            return

        # get token and expire it
        for token in self.dbs['invitations']:
            if self.dbs['invitations'][token] == email:
                del self.dbs['invitations'][token]
                break

        del self.dbs['users'][email]
        resp.media = {'success': True}


class AdminBotList(Route):
    def get_stats(self, botid):
        bot = self.dbs['bots'][botid]
        profile = self.dbs['users'][bot['user']]
        additional = {}
        additional['bah_roi'] = bot.get('bah_roi', 0)

        try:
            strat_meta = StrategyFactory(bot['strategy'], self.dbs)
            strategy_name = strat_meta.get_name()
            proto = strat_meta.get_proto()
            additional = {
                **additional,
                'description': proto.strategy_description,
                'image': proto.strategy_image,
            }
        except SystemExit:
            raise
        except Exception:
            strategy_name = '[Deprecated]'
        return {
            **bot['state'].to_json(),
            'user': bot.get('user', ' --'),
            'nickname': bot.get('nickname', None),
            'exchange': bot['exchange'],
            'strategy': bot['strategy'],
            'strategy_name': strategy_name,
            'market': bot['market'],
            'candles': bot['candles'],
            'ml_boost': bot['ml_boost'],
            'trades_made': len(bot['state'].trade_log),
            'start_time': bot['start_time'],
            'activated': round((int(time.time()) - bot['start_time']) / 60 / 60 / 24),  # in days
            'stop_time': bot['stop_time'],
            'enabled': bot['enabled'],
            'stop_loss': bot.get('stop_loss'),
            'avg_fee': self.get_avg_fee(bot),
            'avg_roi_trade': self.get_avg_roi_trade(bot),
            'avg_roi_month': self.get_avg_roi_month(bot),
            'last_trade_attempt': bot['state'].to_json().get('last_trade_attempt', None) if bot.get('state', False) else None,
            'candles': bot['candles'],
            **additional
        }

    def get_avg_fee(self, bot):
        trade_log = bot['state'].trade_log
        total_fee = bot['state'].in_fees
        total_amount = sum(trade['amount'] * trade['price'] for trade in trade_log)

        if total_amount == 0:
            return 0

        return total_fee / total_amount

    def get_avg_roi_trade(self, bot):
        trade_log = bot['state'].trade_log
        log_count = len(trade_log)
        if log_count == 0:
            return 0

        roi = (bot['state'].portfolioValue - bot['state'].startingBalance[1]) / bot['state'].startingBalance[1]
        return roi / log_count

    def get_avg_roi_month(self, bot):
        roi = (bot['state'].portfolioValue - bot['state'].startingBalance[1]) / bot['state'].startingBalance[1]

        duration = float(
            (bot['stop_time'] - bot['start_time']) if (bot['stop_time'] or 1e13) < time.time() else time.time() - bot[
                'start_time'])
        return (roi / (round(duration / 60 / 60 / 24)) if duration / 60 / 60 / 24 > 0.5 else 0) * 30

    @pfs_encrypted
    @spectree.validate(resp=Response(HTTP_200=AdminBotListGetResp))
    def on_get(self, req, resp):
        result = {}
        offset = int(req.params.get('offset', 0))
        count = int(req.params.get('count', 10))
        i = 0
        for b in self.dbs['bots']:
            # TODO: create meta-index/orderedset __disabled_bots__?
            bot = self.dbs['bots'][b]

            if not bot['enabled']:
                continue

            # Valid but might not be the right page
            i += 1
            if i <= offset:
                continue

            strat_factory = StrategyFactory(bot['strategy'], self.dbs)

            temp = {
                'uid': b,
                'user': bot.get('user', '--'),
                'strat_name': strat_factory.get_name(),
                'exchange': bot['exchange'],
                'market': bot['market'],
                'current_balance': bot['state'].portfolioValue,
                'starting_balance': bot['state'].startingBalance,
                'activated': round((int(time.time()) - bot.get('start_time', 0)) / 60 / 60 / 24),   # in days
                'roi': (bot['state'].portfolioValue - bot['state'].startingBalance[1]) / bot['state'].startingBalance[1],
                'trades_made': len(bot['state'].trade_log),
                'last_trade_attempt': bot['state'].to_json().get('last_trade_attempt', None) if bot.get('state', False) else None,
                'candles': bot['candles'],
            }

            result[b] = temp

            if len(result) >= count:
                break

        resp.media = result

    @pfs_encrypted
    def on_post(self, req, resp):
        uid = req.media['uid']
        resp.media = self.get_stats(uid)


class AdminResurrectBot(Route):
    @pfs_encrypted
    @spectree.validate(json=AdminResurrectBotPostReq, resp=Response(HTTP_200=AdminResurrectBotPostResp))
    def on_post(self, req, resp):
        bot_id = req.media.get('bot_id', None)
        bot = self.dbs['bots'].get(bot_id, {})

        if not bot:
            assert False, 'Bot does not exists'
        if bot['enabled']:
            assert False, 'Bot is enabled'
        else:
            bot['enabled'] = True
            bot['stop_time'] = None
            self.dbs['bots'][bot_id] = bot
            resp.media = {'success': True}


class AdminBotListInactive(Route):
    @pfs_encrypted
    @spectree.validate(resp=Response(HTTP_200=AdminBotListGetResp))
    def on_get(self, req, resp):
        all_bots = self.dbs['bots']

        result = {}
        offset = int(req.params.get('offset', 0))
        count = int(req.params.get('count', 10))
        i = 0
        for b in all_bots:
            # TODO: create meta-index/orderedset __disabled_bots__?
            bot = self.dbs['bots'][b]

            if bot['enabled']:
                continue

            # Valid but might not be the right page
            i += 1
            if i <= offset:
                continue

            strat_factory = StrategyFactory(bot['strategy'], self.dbs)
            temp = {
                'uid': b,
                'user': bot.get('user', '--'),
                'strat_name': strat_factory.get_name(),
                'exchange': bot['exchange'],
                'market': bot['market'],
                'current_balance': bot['state'].portfolioValue,
                'starting_balance': bot['state'].startingBalance,
                'activated': round((int(time.time()) - bot.get('start_time', 0)) / 60 / 60 / 24),   # in days
                'roi': (bot['state'].portfolioValue - bot['state'].startingBalance[1]) / bot['state'].startingBalance[1],
                'trades_made': len(bot['state'].trade_log),
                'last_trade_attempt': bot['state'].to_json().get('last_trade_attempt', None) if bot.get('state', False) else None,
                'candles': bot['candles'],
            }
            result[b] = temp

            if len(result) >= count:
                break

        resp.media = result


class AdminAnalytics(Route):
    @pfs_encrypted
    @spectree.validate(resp=Response(HTTP_200=AdminAnalyticsGetResp))
    def on_post(self, req, resp):
        days = req.media.get('days', None)
        subscription = req.media.get('subscription', None)
        statskey = f'stats_{subscription}' if subscription != None else 'stats'

        stats = self.dbs['admin_bot_stats'].get(statskey, {})

        if not days:
            resp.media = {'values': stats}
            return

        amount = int(days) * 24
        temp = list(stats.items())
        values = temp[-amount:]

        resp.media = {'values': dict(values)}

@cache.memoize(expire=60, tag='stat_keys')
def get_stat_keys(db):
    return [k for k in db if k.startswith('stat_')]

def get_stats(db):
    keys = get_stat_keys(db)
    return {k.replace('stat_', ''): db[k] for k in keys}


class AdminStats(Route):
    @pfs_encrypted
    @spectree.validate(resp=Response(HTTP_200=AdminStatsGetResp))
    def on_get(self, req, resp):
        resp.media = get_stats(self.dbs['globals'])


class AdminLogs(Route):
    @pfs_encrypted
    def on_get(self, req, resp):
        rfh = RotatingFanoutHandler()
        resp.media = rfh.db['config']

    @pfs_encrypted
    def on_post(self, req, resp):
        limit = req.media.get('limit', 1000)

        rfh = RotatingFanoutHandler()
        path = rfh.search_leaf(req.media['leaf'])
        if len(path) < 1:
            resp.media = {'info': 'Failed to find leaf (no logs)'}
            return

        if len(path) > 1:
            resp.media = {'info': 'Multiple leafs found, make your query more specific'}
            return

        ldb = rfh.db.cache(path[0])
        keys = list(islice(ldb.iterkeys(reverse=True), limit))
        resp.media = dict([(key, ldb[key]) for key in keys])


class AdminPromotingOrder(Route):
    @pfs_encrypted
    @spectree.validate(resp=Response(HTTP_200=AdminPromotingOrderGetResp))
    def on_get(self, req, resp):
        promoting_order = self.dbs['globals']['promoting_order']
        resp.media = {'promoting_order': promoting_order}
        resp.status = falcon.HTTP_200

    @pfs_encrypted
    @spectree.validate(json=AdminPromotingOrderReq, resp=Response(HTTP_200=AdminPromotingOrderPostResp))
    def on_post(self, req, resp):
        self.dbs['globals']['promoting_order'] = req.media['promoting_order']
        resp.media = {'success': True}


class AdminExchangeMarket(Route):
    @pfs_encrypted
    @spectree.validate(resp=Response(HTTP_200=AdminExchangeMaketResp))
    def on_get(self, req, resp):
        _result = Exchange(self.dbs).get_exchange_configs()
        resp.media = _result
        resp.status = falcon.HTTP_200

    @pfs_encrypted
    @spectree.validate(json=AdminExchangeReq, resp=Response(HTTP_202=AdminExchangeMaketResp))
    def on_post(self, req, resp):
        exchange_ins = Exchange(self.dbs)
        exchanges = exchange_ins.get_exchange_configs()
        for _exchange, _info in req.media.items():
            if _exchange not in exchanges:
                resp.media = {'error': f'Exchange not found {_exchange}'}
                resp.status = falcon.HTTP_404
                return
            rs = exchange_ins.set_exchange_market(_exchange, _info.get('pairs', []), "add")
            if rs.err():
                resp.media = {'error': rs.err()}
                resp.status = falcon.HTTP_302
                return
        resp.media = {'pairs': list(rs.ok())}
        resp.status = falcon.HTTP_202

    @pfs_encrypted
    @spectree.validate(json=AdminExchangeReq, resp=Response(HTTP_202=AdminExchangeMaketResp))
    def on_put(self, req, resp):
        exchange_ins = Exchange(self.dbs)
        exchanges = exchange_ins.get_exchange_configs()
        for _exchange, _info in req.media.items():
            if _exchange not in exchanges:
                resp.media = {'error': f'Exchange not found {_exchange}'}
                resp.status = falcon.HTTP_404
                return
            rs = exchange_ins.set_exchange_market(_exchange, _info.get('pairs', []), "add")
            if rs.err():
                resp.media = {'error': rs.err()}
                resp.status = falcon.HTTP_302
                return
        resp.media = {'pairs': list(rs.ok())}
        resp.status = falcon.HTTP_202

    @pfs_encrypted
    @spectree.validate(json=AdminExchangeReq, resp=Response(HTTP_202=AdminExchangeMaketResp))
    def on_delete(self, req, resp):
        exchange_ins = Exchange(self.dbs)
        exchanges = exchange_ins.get_exchange_configs()
        for _exchange, _info in req.media.items():
            if _exchange not in exchanges:
                resp.media = {'error': f'Exchange not found {_exchange}'}
                resp.status = falcon.HTTP_404
                return
            rs = exchange_ins.set_exchange_market(_exchange, _info.get('pairs', []), "remove")
            if rs.err():
                resp.media = {'error': rs.err()}
                resp.status = falcon.HTTP_302
                return
        resp.media = {'pairs': list(rs.ok())}
        resp.status = falcon.HTTP_202


class AdminBotAnalytics(Route):
    @cache.memoize(expire=60, tag='stat_bot_averages')
    def _get_bot_averages(self) -> dict:
        _trade_logs = []
        for b in self.dbs['bots']:
            bot = self.dbs['bots'][b]
            if bot['state'].trade_log:
                _trade_logs.extend([{**d, 'botId': b, 'trade': 1, 'market': bot['market']} for d in bot['state'].trade_log])
        df = pd.DataFrame(_trade_logs)
        df['date'] = pd.to_datetime(df['date'], unit='s', errors='coerce').dt.date
        df['trade_amount_quote'] = df['amount']*df['price']
        df['%_of_balance_traded_for_that_trade'] = df['trade_amount_quote']/df['balance']*100
        avg_df = df.groupby(['market', 'date'], as_index=False).sum().groupby('market').mean()
        avg_df = avg_df.add_prefix('avg_')
        return avg_df.T.to_dict()

    @pfs_encrypted
    def on_get(self, req, resp):
        resp.media = self._get_bot_averages()
        resp.status = falcon.HTTP_200


class AdminDisable2FA(Route):
    
    @pfs_encrypted
    @spectree.validate(json=AdminDisable2FAReq)
    def on_post(self, req, resp):

        username = req.media['username']
        profile = self.dbs['users'][username]
        if not profile.get('OTP_config'):
            resp.media = {'error': f'OTP is not enabled for this user:{username}'}
            resp.status = falcon.HTTP_400
            return

        del profile['OTP_config']
        self.dbs['users'][username] = profile
        resp.media = {'reset': True}
        resp.status = falcon.HTTP_200


class AdminOBTEarningAnalytics(Route):

    def on_get(self, req, resp):
        pass

from functools import partial
from routes.utils import get_short_hash

get_referral_code = partial(get_short_hash, salt=b'referrals')
class AdminReferralRequestModel(BaseModel):
    referrer_username: str
    referral_username: str
    referral_tier_id: Optional[str]

class AdminReferral(Route):

    @pfs_encrypted
    @spectree.validate(json=AdminReferralRequestModel)
    def on_post(self,req,resp):
        referral_username = req.media['referral_username']
        referrer_username = req.media['referrer_username']


        if referrer_username == referral_username:
            resp.media = {'error':'The username are same!'}
            resp.status = HTTP_500
            return
            

        for user in [referral_username,referrer_username]:
            if user not in self.dbs['users']:
                resp.media = {'error':f'The username not registred! {user}'}
                resp.status = HTTP_500
                return
                
            code = get_referral_code(user)
            if code not in self.dbs['referrals_hash_map']:
                self.dbs['referrals_hash_map'][code] =  user


        referrer_profile  = self.dbs['users'][referrer_username]
        referrer_profile['referral'] = get_referral_code(referral_username)
        referrer_profile['referral_tier_id'] =  req.media.get('referral_tier_id','20-0')
        self.dbs['users'][referrer_username] = referrer_profile


        referrals_list = self.dbs['referrals'].get(get_referral_code(referrer_username),[])
        new_ref = Referral(username=referral_username, code=get_referral_code(referral_username), timestamp=time.time(),referral_tier_id=req.media.get('referral_tier_id','init'))
        if referral_username not in [r.username for r in referrals_list]:
            referrals_list.append(new_ref)
            self.dbs['referrals'][get_referral_code(referrer_username)] = referrals_list
        else:
            _referrals_list = []
            for r in referrals_list:
                if r.username == referral_username:
                    _referrals_list.append(new_ref)
                else:
                    _referrals_list.append(r)
            
            self.dbs['referrals'][get_referral_code(referrer_username)] = _referrals_list

        

        resp.media= 'Success'

        
    

    
    # @spectree.validate(query=ReferralRequestModel)
    def on_get(self,req,resp):
        pass