# from tradeEnv.exchanges.BinanceFutures import BinanceFutures
from json.decoder import JSONDecodeError
from re import T
from tradeEnv.exchanges import *
from tradeEnv.exchanges.BitPandaPro import BitpandaPro
from tradeEnv.exchanges.Bitvavo import BitvavoAPI
from types import MethodType
from routes.db import get_tmp_cache
from tradeEnv.utils import span_from_candletype, to_internal_market
from tradeEnv.api_adapter import ApiAdapter, Order, binance_map, binance_us_map, kraken_map
from .meta import create_deco_meta
from typing import Any, Dict, List, Set, Type, Union,  Optional
from typing_extensions import Literal
from pydantic import BaseModel
from result import Ok, Err, Result
from datetime import datetime
import requests
import uuid
import time
import urllib
import hashlib
import hmac
import base64


Exchanges = ('Binance', 'Kraken', 'Bitvavo')
ExchangeType = Literal[Exchanges]


StrDict = Dict[str, str]
SignedReq = Tuple[str, str, StrDict]
IntIsh = Union[str, int]
CandleType = constr(regex=r'^[0-9]+(w|d|h|m|s)$')
RawCandleDataType = Optional[Dict[IntIsh, Dict[str, float]]]

ApiKeysType = conlist(str, min_items=2, max_items=3)

SideType = Literal['BUY', 'SELL']
PriceDict = BalanceDict = Dict[str, float]
TxID = Union[str, int]


class LimitOrderStatus(BaseModel):
    exec_vol: float
    exec_fract: float
    price: float
    date: int


class TradeFilterData(BaseModel):
    minLot: Optional[float]
    minNot: Optional[float]
    lotDenom: Optional[int]
    priceDenom: Optional[int]


class AbstractTradeAPI(metaclass=pydanticMeta):
    hooks = []
    _map = {}

    def __init__(self, logger=None):
        self.logger = logger

    def get_logger(self):
        if getattr(self, 'logger', None):
            return self.logger
        logger = logging.getLogger()
        logger.addHandler(logging.NullHandler())
        return logger

    def sign_api(self,          api_keys: ApiKeysType, query: StrDict = {}, body: str = '', headers: StrDict = {}, urlpath: str = '') -> SignedReq: pass
    def balance(self,           api_keys: ApiKeysType) -> Result[BalanceDict, str]: pass
    def check_auth(self,        api_keys: ApiKeysType) -> bool: pass
    def stoploss_order(self,    api_keys: ApiKeysType, market: str, amount: float, stop_price: float) -> Result[str, str]: pass
    def limit_order(self,       api_keys: ApiKeysType, market: str, side: SideType, amount: float, price: float) -> Result[str, str]: pass
    def limit_details(self,     api_keys: ApiKeysType, market: str, txid: TxID) -> Result[LimitOrderStatus, str]: pass
    def cancel_order(self,      api_keys: ApiKeysType, market: str, txid: TxID) -> Result[str, str]: pass
    def market_order(self,      api_keys: ApiKeysType, market: str, side: SideType, amount: float) -> Result[Order, str]: pass
    def update_ohlc(self,       market: str, candle_type: CandleType, start_time: IntIsh) -> Result[RawCandleDataType, str]: pass
    def filters(self) -> Dict[str, TradeFilterData]: pass
    def market_prices(self) -> PriceDict: pass

    @property
    def clientOrderId(self)->str:
        if self._map.get('apiAgentCode'):
            return f"X-{self._map['apiAgentCode']}-{str(uuid.uuid4().hex[:16])}"
        return str(uuid.uuid4().hex[:16])

class CachedWrapper:
    def __init__(self, api_base, expire=60 * 5):
        self.api_base = api_base
        self.cache = get_tmp_cache('cached_api')
        self.expire = expire

    def __getattr__(self, name: str) -> Any:
        attr = self.api_base.__getattribute__(name)
        if isinstance(attr, MethodType):
            return self.cache.memoize(tag='cached_api', expire=self.expire)(attr)
        else:
            return attr


class BinanceAPI(AbstractTradeAPI):
    name = 'Binance'
    _map = binance_map

    def _is_err(self, data: Any) -> bool:
        return 'code' in data

    def _parse_err(self, error: int = 0, msg: Optional[str] = None) -> Result[None, str]:
        # Unauth., Bad time, Bad Sig, Not found, Bad key fmt, Bad MBX Key
        if error in {-1002, -1021, -1022, -1099, -1131, -2014, -2015}:
            return Err('failed-exchange-auth')
        # Any trade-ish specific issue
        if error in {-2010, -2011, -2013, -3001, -3002, -3004, -3005, -3006, -3007, -3008, -3009, -3010, -3011, -3012, -3020, -3022, -1015, -1010, -1013}:
            if error in {-1010, -2010, -2011}:
                self.get_logger().warning(f"Failed order action: {error} {msg}")

            if msg == "Account has insufficient balance for requested action.":
                return Err('insufficient-balance')

            return Err('failed-exchange-funds')
        # Any type of system inavailability
        if error in {-1003, -1004, -1007, -1015, -3044}:
            return Err('failed-exchange-ratelimit')

        if error != 0:
            self.get_logger().warning(f"Unkown exchange error occured: {error} {msg}")

        return Err('failed-exchange-call')

    def sign_api(self, api_keys: ApiKeysType, query: StrDict = {}, body: str = '', headers: StrDict = {}, urlpath: str = '',from_broker:bool=False,sign_with_broker:bool=True) -> SignedReq:
        super().sign_api(api_keys, query=query, body=body, headers=headers, urlpath=urlpath)
        query = {'recvWindow': 60000, 'timestamp': int(time.time()) * 1000, **query}

    
        if self._map.get('apiAgentCode') and sign_with_broker:
            if from_broker:
                query['apiAgentCode'] = self._map['apiAgentCode']
            else:
                query['newClientOrderId'] = self.clientOrderId
                if query.get('clientOrderId'):
                    query['newClientOrderId'] = query['clientOrderId'] 
                    del query['clientOrderId']
                
        qstr = urllib.parse.urlencode(query)

        query['signature'] = hmac.new(api_keys[1].encode(), msg=(qstr + body).encode(), digestmod=hashlib.sha256).hexdigest()
        headers['X-MBX-APIKEY'] = api_keys[0]
        return urllib.parse.urlencode(query), body, headers

    def update_ohlc(self, market: str, candle_type: CandleType, start_time: IntIsh) -> Result[RawCandleDataType, str]:
        super().update_ohlc(market, candle_type, start_time)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        market = to_internal_market(market)
        opts = {'candlesType': candle_type, 'MarkID': market, 'cursor_start': int(float(start_time) * 1000 + 1)}
        api.updateEndpoint('api/v3/klines?symbol={MarkID}&interval={candlesType}&limit=1000&startTime={:cursor}', opts)

        if not api.data.get('markets', None):
            return Err('no-new-candles')

        return Ok(api.data['markets'][market]['candles'])

    def balance(self, api_keys: ApiKeysType) -> Result[BalanceDict, str]:
        super().balance(api_keys)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        query, body, headers = self.sign_api(api_keys)
        req = api.call('sapi/v1/capital/config/getall?%s' % query, data=body or None, method='GET', headers=headers)
        data = req.json()
        if self._is_err(data):
            self.get_logger().debug(f'Failed to get balance {data}')
            return self._parse_err(data['code'], data.get('msg', None))

        ret = {x['coin']: float(x['free']) for x in data}
        return Ok(ret)

    def check_auth(self, api_keys: ApiKeysType) -> bool:
        super().check_auth(api_keys)
        bal = self.balance(api_keys)
        return bal.is_ok()
    
    def check_user_isRebate(self, api_keys: ApiKeysType)->Result[bool,str]:
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        query, body, headers = self.sign_api(api_keys,from_broker=True)
        req = api.call('sapi/v1/apiReferral/ifNewUser?%s' % query, data=body or None, method='GET', headers=headers)
        data = req.json()
        if self._is_err(data):
            self.get_logger().debug(f'Failed to get balance {data}')
            return self._parse_err(data['code'], data.get('msg', None))

        return Ok(data['rebateWorking'])
        
    def stoploss_order(self, api_keys: ApiKeysType, market: str, amount: float, stop_price: float,clientOrderId:str = None) -> Result[str, str]:
        super().stoploss_order(api_keys, market, amount, stop_price)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        marketid = to_internal_market(market)
        query = {'symbol': marketid, 'side': 'SELL', 'type': 'STOP_LOSS_LIMIT', 'quantity': amount, 'stopPrice': stop_price, 'price': stop_price, 'timeInForce': 'GTC', 'newOrderRespType': 'FULL'}
        if clientOrderId:
            query['clientOrderId'] = clientOrderId
        query, body, headers = self.sign_api(api_keys, query=query)
        req = api.call('api/v3/order', data=query, method='POST', headers=headers)
        data = req.json()
        self.get_logger().debug(f'Attempted STOPLOSS trade: {req.text}, {req.reason}, {req.status_code}, {query}, {body}, {headers}')
        if self._is_err(data):
            return self._parse_err(data['code'], data.get('msg', None))
        if data.get('clientOrderId'):
            return Ok(data['clientOrderId'])
        return Ok(data['orderId'])

    def limit_order(self, api_keys: ApiKeysType, market: str, side: SideType, amount: float, price: float,clientOrderId:str = None) -> Result[str, str]:
        super().limit_order(api_keys, market, side, amount, price)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        marketid = to_internal_market(market)
        # TODO: discuss LIMIT_MAKER
        query = {'symbol': marketid, 'side': side, 'type': 'LIMIT', 'quantity': amount, 'price': price, 'newOrderRespType': 'FULL', 'timeInForce': 'GTC'}
        if clientOrderId:
            query['clientOrderId'] = clientOrderId
        query, body, headers = self.sign_api(api_keys, query=query)
        req = api.call('api/v3/order', data=query, method='POST', headers=headers)
        data = req.json()
        self.get_logger().debug(f'Attempted {side} LIMIT trade: {req.text}, {req.reason}, {req.status_code}, {query}, {body}, {headers}')
        if self._is_err(data):
            return self._parse_err(data['code'], data.get('msg', None))
        if data.get('clientOrderId'):
            return Ok(data['clientOrderId'])
        return Ok(data['orderId'])

    def __update_order_id(self,txid:TxID) -> bool:
        if self._map.get('apiAgentCode') in txid:
            return {'origClientOrderId':txid}
        return {'orderId':txid}

    def limit_details(self, api_keys: ApiKeysType, market: str, txid: TxID) -> Result[LimitOrderStatus, str]:
        super().limit_details(api_keys, market, txid)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        query = {'symbol': to_internal_market(market)} 
        query.update(self.__update_order_id(txid))
        query, body, headers = self.sign_api(api_keys, query=query,sign_with_broker=False)
        req = api.call(f'api/v3/order?{query}', method='GET', headers=headers)
        self.get_logger().debug(f'Attempted get details on order {txid}: {req.text}, {req.reason}, {req.status_code}, {query}, {body}, {headers}')
        data = req.json()
        if self._is_err(data):
            return self._parse_err(data['code'], data.get('msg', None))
        out = {
            'exec_vol': float(data['executedQty']),
            'exec_frac': float(data['executedQty']) / float(data['origQty']),
            'price': float(data['price']),
            'date': int(data['updateTime']) // 1000,
        }

        return Ok(out)

    def cancel_order(self, api_keys: ApiKeysType, market: str, txid: TxID) -> Result[str, str]:
        super().cancel_order(api_keys, market, txid)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        query = {'symbol': to_internal_market(market)}
        query.update(self.__update_order_id(txid))
        query, body, headers = self.sign_api(api_keys, query=query)
        req = api.call('api/v3/order', data=query, method='DELETE', headers=headers)
        self.get_logger().debug(f'Attempted to cancel on order {txid}: {req.text}, {req.reason}, {req.status_code}, {query}, {body}, {headers}')
        data = req.json()

        if self._is_err(data):
            return self._parse_err(data['code'], data.get('msg', None))
        else:
            return Ok('success')

    def market_order(self, api_keys: ApiKeysType, market: str, side: SideType, amount: float,clientOrderId:str = None) -> Result[Order, Optional[str]]:
        super().market_order(api_keys, market, side, amount)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        marketid = to_internal_market(market)
        query = {'symbol': marketid, 'side': side, 'type': 'MARKET', 'quantity': amount, 'newOrderRespType': 'FULL'}
        if clientOrderId:
            query['clientOrderId'] = clientOrderId
        query, body, headers = self.sign_api(api_keys, query=query)
        req = api.call('api/v3/order', data=query, method='POST', headers=headers)
        data = req.json()
        self.get_logger().debug(f'Attempted {side} MARKET trade: {req.text}, {req.status_code}, {query}, {body}, {headers}')
        if self._is_err(data):
            return self._parse_err(data['code'], data.get('msg', None))

        priceamount = 0.
        aux = {'fee': 0.}
        for fill in data['fills']:
            if fill['commissionAsset'] in market:
                if aux.get('fee_asset') != fill['commissionAsset']:
                    aux['fee'] = 0.
                aux['fee_asset'] = fill['commissionAsset']
                aux['fee'] += float(fill['commission'])
            else:
                # If everything is payed in BNB use that as the asset
                if 'fee_asset' not in aux or aux.get('fee_asset') == fill['commissionAsset']:
                    aux['fee_asset'] = fill['commissionAsset']
                    aux['fee'] += float(fill['commission'])

            priceamount += float(fill['price']) * float(fill['qty'])
        # Weighted average of fill price over qtys
        aux['price'] = priceamount / float(data['executedQty'])
        aux['amount'] = float(data['executedQty'])

        order = Order(self.name, marketid, data['transactTime'], side, 'MARKET', aux['price'], aux['fee'], amount, aux['fee_asset'])
        return Ok(order)

    def filters(self) -> Dict[str, TradeFilterData]:
        super().filters()
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        api.updateEndpoint('api/v3/exchangeInfo')
        return api.data['filters']

    def market_prices(self) -> PriceDict:
        super().market_prices()
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        req_info = api.call('api/v3/exchangeInfo', method='GET')
        req_price = api.call('api/v3/ticker/price', method='GET')
        exchange_info = req_info.json()
        price_data = req_price.json()
        symbol_dict = {
            market['symbol']: f"{market['baseAsset']}:{market['quoteAsset']}"
            for market in exchange_info['symbols']
        }

        return {symbol_dict[x['symbol']]: float(x['price']) for x in price_data}

    def account_api_restrictions(self, api_keys: ApiKeysType) -> Result:
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        query, body, headers = self.sign_api(api_keys,sign_with_broker=False)
        req = api.call('sapi/v1/account/apiRestrictions?%s' % query, data=body or None, method='GET', headers=headers)
        data = req.json()
        if self._is_err(data):
            self.get_logger().debug(f'Failed to get balance {data}')
            return Err(data)
            # return self._parse_err(data['code'], data.get('msg', None))
        if not data.get('tradingAuthorityExpirationTime', False):
            return Err('failed-expired')

        return Ok(datetime.fromtimestamp(int(data['tradingAuthorityExpirationTime']/1000)))


class BinanceUSAPI(BinanceAPI):
    name = 'Binance.US'
    _map = binance_us_map


class KrakenAPI(AbstractTradeAPI):
    name = 'Kraken'
    _map = kraken_map

    def _is_err(self, data: Any) -> bool:
        return 'error' in data and bool(data['error'])

    def _parse_err(self, error: List[str] = ['unkown']) -> Result[None, str]:
        err: Set[str] = set(error)
        if err.issubset({'EGeneral:Permission denied', 'EAPI:Invalid key', 'EAPI:Invalid signature', 'EAPI:Invalid nonce'}):
            return Err('failed-exchange-auth')
        if 'EOrder:Insufficient funds' in err:
            return Err('insufficient-balance')
        if err.issubset({'EOrder:Cannot open position', 'EOrder:Cannot open opposing position', 'EOrder:Margin allowance exceeded', 'EOrder:Insufficient margin', 'EOrder:Trading agreement required'}):
            return Err('failed-exchange-funds')
        if err.issubset({'EAPI:Rate limit exceeded', 'EOrder:Rate limit exceeded', 'EGeneral:Temporary lockout'}):
            return Err('failed-exchange-ratelimit')
        if error != ['unkown']:
            self.get_logger().warning(f"Unkown exchange error occured: {error}")
        return Err('failed-exchange-call')

    @staticmethod
    def asset_ob2kraken(asset: str, reverse=False):
        asset_map = {"XBT": 'XXBT', "BTC": 'XXBT', 'EUR': 'ZEUR', 'LTC': 'XLTC', 'ETH': 'XETH'}
        if reverse:
            asset_map = dict([(v, k) for k, v in asset_map.items()])
            asset_map = {**asset_map, 'XBT': 'BTC'}
        return asset_map.get(asset, asset)

    @staticmethod
    def market_ob2kraken(market: str):
        pair = market.split(':')

        if pair[1] in ['USDT', 'USDC', 'EUR']:
            return to_internal_market(market)
        else:
            base = KrakenAPI.asset_ob2kraken(pair[0])
            if base != pair[0]:
                return f'{base}{KrakenAPI.asset_ob2kraken(pair[1])}'
            else:
                return f'{base}{pair[1]}'

    def sign_api(self, api_keys: ApiKeysType, query: StrDict = {}, body: str = '', headers: StrDict = {}, urlpath: str = '') -> SignedReq:
        query['nonce'] = int(time.time()*100)
        headers['API-Key'] = api_keys[0]
        postdata = urllib.parse.urlencode(query)

        # Unicode-objects must be encoded before hashing
        encoded = (str(query['nonce']) + postdata).encode()
        message = urlpath.encode() + hashlib.sha256(encoded).digest()
        signature = hmac.new(base64.b64decode(api_keys[1]), message, hashlib.sha512)
        sigdigest = base64.b64encode(signature.digest())
        headers['API-Sign'] = sigdigest.decode()

        return '', urllib.parse.urlencode(query), headers

    def update_ohlc(self, market: str, candle_type: CandleType, start_time: IntIsh) -> Result[RawCandleDataType, str]:
        super().update_ohlc(market, candle_type, start_time)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        market = KrakenAPI.market_ob2kraken(market)
        candle_decoded = span_from_candletype(candle_type)
        opts = {'MarkID': market, 'candlesTypeSeconds': str(int(candle_decoded / 60)), 'cursor_start': int(float(start_time) + 1)}
        api.updateEndpoint('public/OHLC?pair={MarkID}&interval={candlesTypeSeconds}&since={:cursor}', opts)
        if not api.data.get('markets', None):
            return self._parse_err()

        # Take first value (returning markets might not be the same as our calculated market)
        return Ok(next(iter(api.data['markets'].values()))['candles'])

    def balance(self, api_keys: ApiKeysType) -> Result[BalanceDict, str]:
        super().balance(api_keys)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        query, body, headers = self.sign_api(api_keys, urlpath='/0/private/Balance')
        data = api.call('private/Balance', data=body or None, method='POST', headers=headers)

        data = data.json()
        if self._is_err(data):
            self.get_logger().debug(f'Failed to get balance {data}')
            return self._parse_err(data['error'])

        ret = {KrakenAPI.asset_ob2kraken(k, reverse=True): float(v) for k, v in data.get('result', {}).items()}
        return Ok(ret)

    def check_auth(self, api_keys: ApiKeysType) -> bool:
        super().check_auth(api_keys)
        # TODO: should be faster than this
        bal = self.balance(api_keys)
        return bal.is_ok()

    def stoploss_order(self, api_keys: ApiKeysType, market: str, amount: float, stop_price: float) -> Result[str, str]:
        super().stoploss_order(api_keys, market, amount, stop_price)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        marketid = KrakenAPI.market_ob2kraken(market)
        query = {'pair': marketid, 'type': 'sell', 'ordertype': 'stop-loss', 'volume': amount, 'price': stop_price}
        query, body, headers = self.sign_api(api_keys, query=query, urlpath='/0/private/AddOrder')
        req = api.call('private/AddOrder', data=body,  method='POST', headers=headers)
        self.get_logger().debug(f'Attempted LIMIT trade: {req.text}, {req.status_code}, {query}, {body}, {headers}')
        data = req.json()
        if self._is_err(data):
            return self._parse_err(data['error'])

        return Ok(data['result']['txid'][0])

    def limit_order(self, api_keys: ApiKeysType, market: str, side: SideType, amount: float, price: float) -> Result[str, str]:
        super().limit_order(api_keys, market, side, amount, price)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        marketid = KrakenAPI.market_ob2kraken(market)
        query = {'pair': marketid, 'type': side.lower(), 'ordertype': 'limit', 'volume': amount, 'price': price}
        query, body, headers = self.sign_api(api_keys, query=query, urlpath='/0/private/AddOrder')
        req = api.call('private/AddOrder', data=body,  method='POST', headers=headers)
        self.get_logger().debug(f'Attempted {side} LIMIT trade: {req.text}, {req.status_code}, {query}, {body}, {headers}')
        data = req.json()
        if self._is_err(data):
            return self._parse_err(data['error'])

        return Ok(data['result']['txid'][0])

    def limit_details(self, api_keys: ApiKeysType, market: str, txid: TxID) -> Result[LimitOrderStatus, str]:
        super().limit_details(api_keys, market, txid)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        query = {'txid': txid}
        query, body, headers = self.sign_api(api_keys, query=query, urlpath='/0/private/QueryOrders')
        req = api.call('private/QueryOrders', data=body,  method='POST', headers=headers, weight=2)
        self.get_logger().debug(f'Attempted get details on order {txid}: {req.text}, {req.reason}, {req.status_code}, {query}, {body}, {headers}')
        data = req.json()
        if self._is_err(data):
            return self._parse_err(data['error'])

        data = data['result']
        # Take first (and hopefully only) order
        data = data[list(data.keys())[0]]
        out = {
            'exec_vol': float(data['vol_exec']),
            'exec_frac': float(data['vol_exec']) / float(data['vol']),
            'price': float(data['limitprice']),
        }

        # TODO: extract last update date from kraken api
        return Ok(out)

    def cancel_order(self, api_keys: ApiKeysType, market: str, txid: TxID) -> Result[str, str]:
        super().cancel_order(api_keys, market, txid)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        query = {'txid': txid}
        query, body, headers = self.sign_api(api_keys, query=query, urlpath='/0/private/CancelOrder')
        req = api.call('private/CancelOrder', data=body,  method='POST', headers=headers)
        self.get_logger().debug(f'Attempted to cancel on order {txid}: {req.text}, {req.reason}, {req.status_code}, {query}, {body}, {headers}')
        data = req.json()
        if self._is_err(data) or data['result']['count'] < 1:
            return self._parse_err(data['error'])

        return Ok('success')

    def market_order(self, api_keys: ApiKeysType, market: str, side: SideType, amount: float) -> Result[Order, str]:
        super().market_order(api_keys, market, side, amount)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        marketid = KrakenAPI.market_ob2kraken(market)
        query = {'pair': marketid, 'type': side.lower(), 'ordertype': 'market', 'volume': amount, 'oflags': 'fciq', 'trading_agreement': 'agree'}
        query, body, headers = self.sign_api(api_keys, query=query, urlpath='/0/private/AddOrder')
        req = api.call('private/AddOrder', data=body,  method='POST', headers=headers)
        self.get_logger().debug(f'Attempted {side} MARKET trade: {req.text}, {req.status_code}, {query}, {body}, {headers}')
        datai = req.json()
        if self._is_err(datai):
            return self._parse_err(datai['error'])

        for _ in range(3):  # Retries
            txid = datai['result']['txid'][0]
            query = {'txid': txid, 'trades': True}
            query, body, headers = self.sign_api(api_keys, query=query, urlpath='/0/private/QueryOrders')
            req = api.call('private/QueryOrders', data=body,  method='POST', headers=headers, weight=2)
            dataj = req.json()
            self.get_logger().debug(f'Retrieved Order for MARKET trade: {req.text}, {req.status_code}, {query}, {body}, {headers}')
            if self._is_err(dataj):
                self.get_logger().warning('Got error in between market order, likely desync!')
                return self._parse_err(dataj['error'])

            if txid not in dataj['result']:
                self.get_logger().warning(f'txid {txid} not found, retrying in a seconds')
                time.sleep(1)
                continue

            tx = dataj['result'][txid]
            order_time = dataj['result'][txid].get('opentm', time.time())

            aux = {'fee': float(tx['fee']) + 0.01,  # Fix weird rounding on kraken
                   'fee_asset': market.split(':')[1],
                   'price': float(tx['price']),
                   'amount': float(tx['vol_exec'])
                   }

            order = Order(self.name, market, dataj['result'][txid].get('closetm', time.time()) * 1000, side.lower(), 'market', aux['price'], aux['fee'], aux['amount'], aux['fee_asset'])
            return Ok(order)
        # Ledger
        #query = {'start': order_time}
        #query, body, headers = self.sign_api(api_keys, urlpath='/0/private/Ledgers')
        #req = api.call('private/Ledgers', data=body,  method='POST', headers=headers)
        #datak = req.json()
        #time = 0
        #self.get_logger().debug(f'Retrieved Ledger for MARKET trade: {req.text}, {req.status_code}, {query}, {body}, {headers}')

        # if self._is_err(datak):
        #    self.get_logger().warning(f'Potential desync, AddOrder called, but failed to get ledger')
        #    return self._parse_err(datak['error'])

        #d = datak['result']['ledger']
        #ledges = filter(lambda k: d[k]['refid'] == tradeid and float(d[k]['fee']) != 0, d)
        #ledge = d[next(ledges)]
        #aux['fee'] = float(ledge['fee'])
        #aux['fee_asset'] = ledge['asset']
        #time = ledge['time']
        return self._parse_err()

    def _pair_conversion_map(self, api) -> Dict[str, str]:
        req = api.call('public/AssetPairs', method='GET')
        self.get_logger().debug(f'Retrieved ExchangeInfo: {req.text}, {req.status_code}')
        exchange_info = req.json()['result']
        pairs = {}

        def convert_name(s):
            p = s.split('/')
            p = list(map(lambda s: KrakenAPI.asset_ob2kraken(s, reverse=True), p))
            return ':'.join(p)

        for ticker in exchange_info:
            pair = exchange_info[ticker].get('wsname', None)
            if pair == None:
                continue
            pairs[ticker] = convert_name(pair)
            altname = exchange_info[ticker].get('altname', None)
            if altname and altname != ticker:
                pairs[altname] = pairs[ticker]

        return pairs

    def filters(self) -> Dict[str, TradeFilterData]:
        super().filters()
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        pairs = self._pair_conversion_map(api)
        api.updateEndpoint('public/AssetPairs')
        ret = api.data['filters']

        # Copy filters for Kraken names to OB names
        for m in list(ret):
            if m in pairs:
                ret[to_internal_market(pairs[m])] = ret[m]
        return ret

    def market_prices(self) -> PriceDict:
        super().market_prices()
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        pairs = self._pair_conversion_map(api)
        # TODO: split request into parts?
        # Filter out unused fiat 
        req_pairs = list(filter(lambda pair: all(map(lambda m: not pair.endswith(m), ['CAD', 'GBP','JPY', 'AUD', 'XBT', 'ETH'])), pairs))

        req = api.call(f"public/Ticker?pair={','.join(req_pairs)}")
        ret = {}

        self.get_logger().debug(f'Retrieved Tickers: {req.text}, {req.status_code}')
        try:
            price_data = req.json()['result']
            for market in price_data:
                ret[pairs[market]] = float(price_data[market]['p'][0])
        except JSONDecodeError:
            self.get_logger().debug(f'Retrieved Tickers: {req.text}, {req.status_code}')

        return ret


class BotsIOAPI(AbstractTradeAPI):
    name = 'BotsIO'
    base_url = 'https://signal.revenyou.io/paper/api/signal/v2'
    date_format = '%Y-%m-%d %H:%M:%S'
    _map = {}

    def __init__(self, logger=None):
        super().__init__(logger=logger)
        self.binance = BinanceAPI(logger)

    def limit_order(self, api_keys: ApiKeysType, market: str, side: SideType, amount: float, price: float) -> Result[str, str]:
        super().limit_order(api_keys, market, side, amount, price)

        base_asset, quote_asset = market.split(':')

        req = requests.request('POST', f'{self.base_url}/placeOrder', json={
            "signalProvider": api_keys[0],
            "signalProviderKey": api_keys[1],
            "extId": str(uuid.uuid4()),  # TODO import
            "exchange": "binance",
            "baseAsset": base_asset,
            "quoteAsset": quote_asset,
            "type": "limit",
            "side": side.lower(),
            "limitPrice": str(price),
            "qtyPct": str(amount),
            "ttlType": "gtc",
            "responseType": "FULL"
        })

        data = req.json()

        self.get_logger().debug(f'Attempted {side} LIMIT trade: {req.text}, {req.reason}, {req.status_code}')

        if data['success']:
            return Ok(data['orderId'])
        else:
            return Err('failed-exchange-call')

    def check_auth(self, api_keys: ApiKeysType) -> bool:
        super().check_auth(api_keys)
        req = requests.request('GET', f'{self.base_url}/getOrders', params={
            "signalProvider": api_keys[0],
            "signalProviderKey": api_keys[1],
        })
        return req.status_code < 400

    def limit_details(self, api_keys: ApiKeysType, market: str, txid: TxID) -> Result[LimitOrderStatus, str]:
        super().limit_details(api_keys, market, txid)

        req = requests.request('GET', f'{self.base_url}/getOrderInfo', params={
            "signalProvider": api_keys[0],
            "signalProviderKey": api_keys[1],
            "orderId": txid
        })

        self.get_logger().debug(f'Attempted get details on order {txid}: {req.text}, {req.reason}, {req.status_code}')

        data = req.json()
        if data['success']:
            out = {
                'exec_vol': None,
                'exec_frac': float(data['qtyExecPct']) / float(data['qtyPct']),
                'price': float(data['limitPrice']),
                'date': int(
                    datetime.strptime(
                        data['lastChangeTs'], self.date_format
                    ).timestamp()
                ),
            }

            return Ok(out)
        else:
            return Err('failed-exchange-call')

    def cancel_order(self, api_keys: ApiKeysType, market: str, txid: TxID) -> Result[str, str]:
        super().cancel_order(api_keys, market, txid)

        req = requests.request('POST', f'{self.base_url}/cancelOrder', json={
            "signalProvider": api_keys[0],
            "signalProviderKey": api_keys[1],
            "orderId": txid
        })

        self.get_logger().debug(f'Attempted to cancel on order {txid}: {req.text}, {req.reason}, {req.status_code}')

        return Ok('success') if req.json()['success'] else Err('failed-order-cancel')

    def update_ohlc(self, market: str, candle_type: CandleType, start_time: IntIsh) -> Result[RawCandleDataType, str]:
        return self.binance.update_ohlc(market, candle_type, start_time)

    def market_prices(self) -> PriceDict:
        return self.binance.market_prices()

    def _balance_pct(self, api_keys: ApiKeysType, base: str = 'USDT') -> Result[BalanceDict, str]:
        req = requests.request('GET', f'{self.base_url}/getBotAssetsPct', params={
            "signalProvider": api_keys[0],
            "signalProviderKey": api_keys[1],
            "exchange": "binance",
            "baseAsset": base
        })

        self.get_logger().debug(f'Attempted to get balance {api_keys[0]}: {req.text}, {req.reason}, {req.status_code}')
        data = req.json()
        if data['success']:
            return Ok({base: data['baseTotal']})
        else:
            return Err('failed-exchange-call')

    def _get_orders(self, api_keys: ApiKeysType):
        req = requests.request('GET', f'{self.base_url}/getOrders', params={
            "signalProvider": api_keys[0],
            "signalProviderKey": api_keys[1]
        })

        self.get_logger().debug(f'Attempted to get balance {api_keys[0]}: {req.text}, {req.reason}, {req.status_code}')
        data = req.json()
        if data['success']:
            return Ok(data['orders'])
        else:
            return Err('failed-exchange-call')

    def balance(self, api_keys: ApiKeysType) -> Result[BalanceDict, str]:
        return Ok({'BUSD': 100, 'USDT': 100})

    def filters(self) -> Dict[str, TradeFilterData]:
        filters = self.binance.filters()
        for k in filters:
            filters[k]['minLot'] = None
            filters[k]['minNot'] = None
        # BotsIO is percentage based so there aren't any filters we can use
        return {}


def _recursive_conversion(frm: str, to: str, prices: PriceDict, stack: Set[str] = set(), depth_ttl=2) -> Result[float, str]:
    # TODO: generalize graph/tree search across markets
    if depth_ttl <= 0:
        return Err('Failed to find suitable conversion rate (ttl expired)')

    for market in list(prices.keys()):
        pair = market.split(':')
        if pair[1] in stack:
            continue

        if pair[0] == frm:
            if pair[1] == to:
                return Ok(prices[market])
            intermediate_to = pair[1]
            stack.add(intermediate_to)
            res = _recursive_conversion(intermediate_to, to, prices, stack, depth_ttl-1)
            stack.pop()
            if res.is_ok():
                return Ok(res.ok() * prices[market])

    return Err('Failed to find suitable conversion rate (exhaustive)')


def approx_conversion_rate(frm: str, to: str, prices: PriceDict, max_depth=2) -> Result[float, str]:
    if frm == to:
        return Ok(1.)

    # Add reversed markets for easily lookup
    def reverse_market(k, v):
        return (':'.join(reversed(k.split(':'))), 1/(v + 1e-12),)
    prices = {**prices, **dict([reverse_market(k, prices[k]) for k in prices])}

    key = f'{frm}:{to}'
    if key in prices:
        return Ok(prices[key])

    return _recursive_conversion(frm, to, prices, stack={frm}, depth_ttl=max_depth)


TradeAPIRegistry: Dict[str, Type[AbstractTradeAPI]] = {
    'Binance.US': BinanceUSAPI,
    'Binance': BinanceAPI,
    'Kraken': KrakenAPI,
    'BotsIO': BotsIOAPI,
    'BitpandaPro': BitpandaPro,
    'Bitvavo': BitvavoAPI,
}


class FilledOrder(BaseModel):
    exchange: str
    pair: str
    date: Union[float, int]
    side: SideType
    order_type: Literal['MARKET', 'LIMIT']
    price: float
    volume: float
    fee: float
    fee_asset: str

    def get_fee(self):
        if not self.fee_asset:
            return 0

        if self.fee_asset == 'KFEE':
            return self.fee / 100

        api = CachedWrapper(TradeAPIRegistry[self.exchange]())
        prices = api.market_prices()
        _pair = self.pair.split(':')
        ratio = approx_conversion_rate(self.fee_asset, _pair[1], prices)
        if ratio.is_err():
            return 0

        ratio = ratio.unwrap()
        return self.fee * ratio

    def get_tok_diff(self):
        vol = self.volume

        if self.side == 'SELL':
            vol = -vol

        if self.pair.startswith(self.fee_asset):
            vol -= self.fee

        return vol

    def get_cur_diff(self):
        vol = self.volume * self.price

        if self.side == 'BUY':
            vol = -vol

        if self.pair.endswith(self.fee_asset):
            vol -= self.fee

        return vol
