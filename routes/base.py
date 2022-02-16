from tradeEnv.strategy import *
import time
import secrets
import falcon
import shutil
import os
import sys
import uuid
from result import Result, Ok, Err
from typing import Dict, Optional, Union
from pydantic import BaseModel
from base64 import b64encode, b64decode

from .db import *
from .utils import no_except


def add_pkg():
    abs_path = os.path.realpath('..')
    if abs_path not in sys.path:
        sys.path.append(abs_path)


add_pkg()


def auth_ttl(token, dbs=None):
    if token in dbs['auth']:
        del dbs['auth'][token]


class EncryptionManager:
    def __init__(self, dbs: dict, ridentity: str, timeout=5):
        self.dbs: Dict[str, DillCache] = dbs
        self.id = self.dbs['globals']['id_key'].pub
        self.rid = ridentity
        self.timeout = timeout

    def __enter__(self):
        key = f'axolotl:{self.rid}'
        self.lock = self.dbs['globals'].lock(key, timeout=self.timeout)
        self.lock.__enter__()
        self.conv = self.dbs['globals'][key]
        return self

    def __exit__(self, *args):
        key = f'axolotl:{self.rid}'
        self.dbs['globals'][key] = self.conv
        del self.conv
        self.lock.__exit__(*args)

    def get_identity_header(self) -> dict:
        return {'X-Identity': b64encode(self.id).decode()}

    def create_timesig(self) -> bytes:
        timestamp = str(int(time.time())).encode()
        return b64encode(self.conv.encrypt(timestamp))

    def check_timesig(self, timesig, stale=3600, ftl=60) -> int:
        timestamp = self.conv.decrypt(b64decode(timesig)).decode()
        assert time.time() < int(timestamp) + stale, "Stale message"
        assert int(timestamp) < time.time() + ftl, "FTL message"
        return int(timestamp)

    def encrypt_body(self, obj: bytes) -> str:
        return b64encode(self.conv.encrypt(obj))

    def decrypt_body(self, obj: bytes) -> bytes:
        return self.conv.decrypt(b64decode(obj))


class StandardResponse(BaseModel):
    success: Optional[Union[str, bool]]
    debug: Optional[str]
    info: Optional[str]
    warning: Optional[str]
    error: Optional[str]


def auth_guard(func):
    def _auth_guard(self, req, resp):
        auth = self.get_username(req)
        if auth.is_err():
            return self.err_as_resp(auth, resp)
        else:
            return func(self, req, resp)
    return _auth_guard


class Route:
    def __init__(self, dbs: dict):
        self.dbs = dbs

    def secure(self, remote_identity: str):
        return EncryptionManager(self.dbs, remote_identity)

    def is_authenticated(self, req) -> bool:
        return bool(self.dbs['auth'].get(req.auth or '', None))

    def get_auth(self, req) -> Result[str, str]:
        if hasattr(req, 'auth') and req.auth != None:
            return Ok(req.auth)
        return Err('Failed to authenticate')

    def err_as_resp(self, err, resp):
        resp.media = {'error': err.err()}
        resp.status = falcon.HTTP_400

    def mark_activity(self, req):
        if not bool(self.dbs['auth'].get(req.auth or '', None)):
            return

        token = req.auth
        username = self.dbs['auth'][token]
        user = self.dbs['users'][username]
        # Only update if last activity was a while ago
        if time.time() - user.get('last_active', 0) > 3 * 60:
            user['last_active'] = int(time.time())
            self.dbs['users'][username] = user
            self.dbs['auth'].touch(token, expire=24 * 60 * 60)

    def get_username(self, req) -> Result[str, str]:
        token = self.get_auth(req)
        if token.is_err():
            return token
        username = self.dbs['auth'].get(token.unwrap())
        if not username:
            return Err("Authentication token not valid")
        return Ok(username)

    def get_profile(self, req) -> Result[dict, str]:
        username = self.get_username(req)
        if username.is_err():
            return username
        profile = self.dbs['users'].get(username.unwrap())
        if not profile:
            return Err("Profile not found")
        return Ok(profile)

    def update_profile(self, req, profile) -> Result[None, str]:
        username = self.get_username(req)
        if username.is_err():
            return username
        self.dbs['users'][username.unwrap()] = profile
        return Ok(None)

    def get_bots(self, req) -> Result[List[dict], str]:
        profile = self.get_profile(req)
        if profile.is_err():
            return profile
        ids = profile.unwrap()['bots']
        return Ok([self.dbs['bots'][bid] for bid in ids])

    def set_token(self, user: str) -> str:
        # Create singleton token
        token = secrets.token_hex()
        self.dbs['auth'].set(token, user, expire=24 * 60 * 60, retry=True)
        # Remove auth after 1w
        TTLManager(self.dbs).createTTL({'lambda': [{'args': (token,), 'f': auth_ttl}]}, ttl=604800)
        return token


def removeFiles(arr):
    for file in arr:
        if file and type(file) == str:
            shutil.rmtree(file, ignore_errors=True)


class TTLManager:
    def __init__(self, dbs):
        self.dbs = dbs

    # Creates a timer for when files/objects expire
    # description: {files: string[], lambda: func[]]}
    def createTTL(self, description, ttl=3600):
        self.dbs['ttl'][str(uuid.uuid4())] = {
            'expires': int(time.time()) + ttl,
            **description
        }

    def runLambdas(self, arr):
        for func in arr:
            if func:
                args = func.get('args', ())
                f = func.get('f', None)
                try:
                    if f and callable(f):
                        f(*args, dbs=self.dbs)
                    else:
                        print("WARN: got TTL Lambda without valid function")
                except SystemExit:
                    raise
                except Exception as e:
                    print("ERR: Failed TTL Lambda:", e)

    def checkTTL(self):
        del_list = []
        for x in list(self.dbs['ttl']):
            item = no_except(lambda: self.dbs['ttl'][x])
            if not item:
                del_list.append(x)
                continue

            if int(time.time()) > item['expires']:
                removeFiles(item.get('files', []))
                self.runLambdas(item.get('lambda', []))
                del_list.append(x)

        for d in del_list:
            del self.dbs['ttl'][d]


class UrlManager():
    def get_server_url(self):
        urls = {
            'dev': 'http://localhost:8000/',
            'staging': 'https://beta.obtrader.ml/',
            'feature': 'https://feature.obtrader.ml/',
            'prod': 'https://app.onebutton.trade/'
        }

        return urls.get(os.environ.get('ENVIRONMENT', "dev"), 'Invalid environment variable')
