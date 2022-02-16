from routes.db import get_dbs
from .logging import getLogger
import os
import re
from itertools import groupby
from typing import List, Optional, Union, Any
from result import Result, Ok, Err
from pydantic import BaseModel
from spectree import Response
from .spectree import spectree
from tradeEnv.trade_api import CachedWrapper, TradeAPIRegistry
from tradeEnv.exchanges.CoinbasePro import CoinbasePro_map
from .utility.notify_alert import NotifyBinanceAPIExpire
import falcon
from .base import Route, StandardResponse, add_pkg, auth_guard
add_pkg()


class ConnectExchangeGetResp(BaseModel):
    __root__: List[str]


class ConnectExchangeDeleteReq(BaseModel):
    exchange: str


class ConnectExchangePostReq(BaseModel):
    exchange: str
    api_key: Optional[str]
    api_secret: Optional[str]


class ConnectExchangePostResp(BaseModel):
    valid: bool = True


class ConnectExchangePostMessage(BaseModel):
    valid: bool = False
    message: Optional[str]


class ConnectExchangePutReq(BaseModel):
    exchange: str
    api_key: Optional[str]
    api_secret: Optional[str]


class ConnectExchangePutResp(BaseModel):
    success: bool = True


class ConnectExchangePutMessage(BaseModel):
    success: bool = False
    message: Optional[str]


class ExchangeDataPostReq(BaseModel):
    exchange: str
    asset: str


class ExchangeDataPostResp(BaseModel):
    total: float
    free: float


class Exchange:
    default_exchange_config = {'Binance': {
        'pairs': ['BTC:USDT', 'LTC:USDT', 'ETH:USDT', 'XMR:USDT', 'BNB:USDT', 'BTC:BUSD', 'ETH:BUSD', 'BNB:BUSD', 'EOS:BUSD', 'DOT:BUSD', 'ETC:USDT', 'XRP:USDT', 'ADA:USDT', 'BCH:USDT', 'TRX:USDT', 'LINK:USDT', 'XLM:USDT', 'VET:USDT', 'UNI:USDT', 'SOL:USDT', 'MATIC:USDT', 'KSM:USDT'],
        'candles': ['4h', '1h']
    }, 'Kraken': {
        'pairs': ['BTC:EUR', 'BTC:USDT', 'LTC:EUR', 'LTC:USDT', 'ETH:EUR', 'ETH:USDT'],
        'candles': ['4h', '1h']
    }, 'BitpandaPro': {
        'pairs': ['BTC:EUR', 'LTC:EUR', 'ETH:EUR', 'EOS:EUR', 'ADA:EUR', "XRP:EUR", "BCH:EUR", "MIOTA:EUR", "DOT:EUR", "LINK:EUR"],
        'candles': ['4h', '1h']
    }, 'Binance.US': {
        'pairs': ['BTC:USDT', 'LTC:USDT', 'ETH:USDT', 'BNB:USDT', 'BTC:BUSD', 'ETH:BUSD', 'BNB:BUSD', 'EOS:BUSD', 'ETC:USDT', 'ADA:USDT', 'BCH:USDT', 'XLM:USDT', 'VET:USDT', 'UNI:USDT'],
        'candles': ['4h', '1h']
    }, 'Bitvavo': {
        'pairs': ['BTC:EUR', 'LTC:EUR', 'ETH:EUR', 'EOS:EUR', 'ETC:EUR', 'ADA:EUR'],
        'candles': ['4h', '1h']
    }}

    def __init__(self, dbs) -> None:
        self.dbs = dbs

    def set_exchange_market(self, exchange_name: str, markets: list, operation: str) -> Result[Any, str]:
        try:
            exchange = self.dbs['exchanges'].get(exchange_name)
            if not exchange:
                return
            pairs = set(exchange.get('pairs', []))
            _operation = {
                'add': pairs.add,
                'remove': pairs.remove
            }
            for m in markets:
                _operation[operation](m)
            exchange['pairs'] = list(pairs)
            self.dbs['exchanges'][exchange_name] = exchange
            self.dbs['globals']['RealTimeEvl:is_reload_env'] = True

            return Ok(pairs)
        except KeyError as e:
            return Err(f'{str(e)} {exchange} {markets} {operation}')

    def get_exchange_configs(self):
        available_exchanges = {}
        # FIXME(Casper): Why not iterate over database and only fill default if nothing is found? 
        for exchange, subconfig in self.default_exchange_config.items():
            if exchange not in self.dbs['exchanges']:
                self.dbs['exchanges'][exchange] = subconfig
                available_exchanges[exchange] = subconfig
            else:
                available_exchanges[exchange] = self.dbs['exchanges'][exchange]

        if os.environ.get('ENVIRONMENT', "dev") == 'staging':
            available_exchanges['Binance']['pairs'].append('DOGE:BUSD')

        return available_exchanges


# NOTE: Exchange(dbs).get_exchange_configs() should be preferred
def _get_exchange_configs():
    # FIXME(Casper): get_dbs has high latency, so should be done somewhere higher up the chain
    return Exchange(get_dbs()).get_exchange_configs()


def get_similar_markets():
    _exchanges = _get_exchange_configs()
    a = [i.get('pairs') for i in _exchanges.values()]
    a = list(set([item for sublist in a for item in sublist]))
    b = list(filter(lambda x: x.split(':')[1] in ['USDT', 'BUSD', 'EUR'], a))

    each_word = sorted([x.split(':') for x in b])

    grouped = [list(value) for key, value in groupby(each_word, lambda x: x[:-1])]

    result = []
    for group in grouped:
        temp = []
        for i in range(len(group)):
            temp.append(":".join(group[i]))
        result.append(temp)
    similar_pairs = list(filter(lambda x: len(x) != 1, result))
    return similar_pairs


similar_trading_pairs = get_similar_markets()


class GetExchanges(Route):
    def on_get(self, req, resp):
        self.mark_activity(req)
        resp.media = Exchange(self.dbs).get_exchange_configs()

class ConnectExchange(Route):
    @auth_guard
    @spectree.validate(resp=Response(HTTP_200=ConnectExchangeGetResp))
    def on_get(self, req, resp):
        self.mark_activity(req)
        profile = self.get_profile(req).unwrap()
        resp.media = list(profile['exchanges'].keys())

    @auth_guard
    @spectree.validate(json=ConnectExchangeDeleteReq)
    def on_delete(self, req, resp):
        self.mark_activity(req)
        profile = self.get_profile(req).unwrap()
        exchange = req.media['exchange']
        assert exchange in profile['exchanges'].keys(), "Exchange not connected"
        del profile['exchanges'][exchange]
        if exchange == 'Binance' and profile.get(NotifyBinanceAPIExpire.object_name):
            del profile[NotifyBinanceAPIExpire.object_name]

        self.update_profile(req, profile).unwrap()

    @auth_guard
    @spectree.validate(json=ConnectExchangePostReq, resp=Response(HTTP_200=ConnectExchangePostResp, HTTP_400=ConnectExchangePostMessage))
    def on_post(self, req, resp):
        self.mark_activity(req)
        exchange = req.media['exchange']

        logger = getLogger('api.connectexchange')

        api = TradeAPIRegistry[exchange](logger=logger)
        api = CachedWrapper(api)

        api_keys = self.get_api_keys(req)
        valid = api.check_auth(api_keys)

        if not valid:
            resp.status = falcon.HTTP_400
            resp.media = {'valid': False}
        else:
            profile = self.get_profile(req).unwrap()

            if exchange in profile['exchanges']:
                resp.status = falcon.HTTP_400
                resp.media = {'valid': False, 'message': 'API Key/Secret already exist'}
                return

            profile['exchanges'][exchange] = api_keys

            if exchange == 'Binance':
                res = api.account_api_restrictions(api_keys)
                profile[NotifyBinanceAPIExpire.object_name] = {'expire_datetime': res.ok() or res.err()}

            self.update_profile(req, profile).unwrap()
            resp.media = {'valid': True}

    @auth_guard
    @spectree.validate(json=ConnectExchangePutReq, resp=Response(HTTP_200=ConnectExchangePutResp, HTTP_400=ConnectExchangePutMessage))
    def on_put(self, req, resp):
        self.mark_activity(req)
        exchange = req.media['exchange']

        api = TradeAPIRegistry[exchange]()
        api = CachedWrapper(api)
        api_keys = self.get_api_keys(req)

        valid = api.check_auth(api_keys)

        if not valid:
            resp.status = falcon.HTTP_400
            resp.media = {'success': False}
            return

        profile = self.get_profile(req).unwrap()

        if exchange not in profile['exchanges']:
            resp.status = falcon.HTTP_400
            resp.media = {'success': False, 'message': 'Exchange not found'}
            return

        profile['exchanges'][exchange] = api_keys
        if exchange == 'Binance':
            res = api.account_api_restrictions(api_keys)
            profile[NotifyBinanceAPIExpire.object_name] = {'expire_datetime': res.ok() or res.err()}

        self.update_profile(req, profile).unwrap()

        resp.media = {'success': True}

    def get_api_keys(self, req):
        api_keys = []
        access_token = req.media.get('access_token', False)
        if access_token:
            api_keys.append(access_token.strip())
            api_keys.append('')
            return api_keys

        api_keys.append(req.media['api_key'].strip())
        api_keys.append(req.media['api_secret'].strip())

        api_passphrase = req.media.get('api_passphrase', False)
        if api_passphrase:
            api_keys.append(api_passphrase.strip())

        return api_keys


class ExchangeData(Route):
    @auth_guard
    @spectree.validate(json=ExchangeDataPostReq, resp=Response(HTTP_200=ExchangeDataPostResp, HTTP_400=StandardResponse))
    def on_post(self, req, resp):
        self.mark_activity(req)
        exchange = req.media['exchange']
        asset = req.media.get('asset', 'USDT')
        profile = self.get_profile(req).unwrap()

        api = TradeAPIRegistry[exchange]()
        api = CachedWrapper(api, expire=60)
        data = api.balance(profile['exchanges'][exchange])

        if data.is_err():
            user = self.get_username(req).unwrap()
            resp.status = falcon.HTTP_400
            resp.media = {'error': f"Failed to get balance for {asset} for {user}, check your API key", 'code': data.err()}
            # should probably be changed to an error message
            return

        data = data.unwrap()
        all_bots = self.get_bots(req).unwrap()
        bots = []
        for b in all_bots:
            if not b['enabled'] or b['exchange'] != exchange or not b['market'].endswith(asset):
                continue
            bots.append(b)
        in_use = sum(bot['state'].curBalance for bot in bots)

        b = float(data.get(asset, 0))
        resp.media = {'total': b, 'free': b - in_use}
