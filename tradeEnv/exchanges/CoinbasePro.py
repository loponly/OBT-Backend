from os import path
import time
import datetime
import math
import requests


from tradeEnv.utils import span_from_candletype, to_internal_market
import urllib
import json
import hmac
import hashlib
import base64


from typing import Any, List, Set, Dict
from requests import auth
from result import Result, Err, Ok
from collections import OrderedDict
from typing_extensions import Literal

from tradeEnv.api_adapter import ApiAdapter, Order
from tradeEnv.exchanges import (
    AbstractTradeAPI,
    SignedReq,
    StrDict,
    ApiKeysType,
    CandleType,
    IntIsh,
    TxID,
    RawCandleDataType,
    BalanceDict,
    SideType,
    LimitOrderStatus,
    TradeFilterData,
    PriceDict
)

OrderType = Literal['limit', 'stop', 'market']

CoinbasePro_map = {
    'name': 'CoinbaseProAPI',
    'exchange_name': 'CoinbasePro',
    'base': 'https://api.pro.coinbase.com',
    'cursor_limit': 1000,
    'cursor_stop': 1000000,
    'pairs': ['BTC:EUR', 'LTC:EUR', 'ETH:EUR', 'EOS:EUR', 'ETC:EUR', 'ADA:EUR'],
    'pair_splitter': ('-', ':')
}


def resample_ohlcv(ohlcv: List[list], to_timeframe: CandleType) -> List[list]:
    import pandas as pd
    columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
    df = pd.DataFrame(ohlcv,  columns=columns)
    df['timestamp'] = [datetime.datetime.fromtimestamp(int(x)) for x in df['timestamp']]
    df.set_index('timestamp', inplace=True)

    df = df.resample(to_timeframe).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    })
    df['timestamp'] = df.index
    df['timestamp'] = [int(datetime.datetime.timestamp(x)) for x in df['timestamp']]
    df = df[columns]

    return df.values.tolist()


class CoinbasePro(AbstractTradeAPI):

    name = 'CoinbasePro'
    _map = CoinbasePro_map

    def _is_err(self, status_code: int) -> bool:
        return status_code in (402, 400, 401, 403, 404, 429, 500)

    def _parse_err(self, status_code: int) -> Result[None, str]:
        if status_code == 401:
            return Err('failed-exchange-auth')
        elif status_code == 400:
            return Err('insufficient-balance')
        elif status_code == 401:
            return Err('failed-exchange-funds')
        elif status_code == 429:
            return Err('failed-exchange-ratelimit')

        return Err('failed-exchange-call')

    ### PUBLIC API'S ###

    def update_ohlc(self, market: str, candle_type: CandleType, start_time: IntIsh) -> Result[RawCandleDataType, str]:
        super().update_ohlc(market, candle_type, start_time)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        now = datetime.datetime.now()
        _start_time = int(start_time)
        start_time = max(int(start_time), 1420112436) + 1
        is_resample_ohlcv = False
        allowed_granularity = [60, 300, 900, 3600, 21600, 86400]
        __minutes = span_from_candletype(candle_type)
        if __minutes not in allowed_granularity:
            is_resample_ohlcv = True
            for i, m in enumerate(allowed_granularity[1:]):
                if m >= __minutes:
                    __minutes = allowed_granularity[i]
                    break

        def get_ohlc(start_time: datetime, end_time: datetime) -> List[dict]:

            end_time = datetime.datetime.fromtimestamp(int(end_time), datetime.timezone.utc)
            start_time = datetime.datetime.fromtimestamp(int(start_time), datetime.timezone.utc)
            start_time = end_time if start_time > end_time else start_time
            opts = {
                'start': start_time.isoformat(),
                'end': end_time.isoformat(),
                'granularity': __minutes
            }
            url_string = 'products/{MarkID}/candles?{query}'.format(MarkID=market.replace(':', '-'), query=urllib.parse.urlencode(opts))
            res = api.call(url_string, method='GET')
            return res
        ohlc_datas = []
        while start_time <= now.timestamp():
            end_time = start_time + 300 * __minutes
            res = get_ohlc(start_time, end_time)
            _data = res.json()
            start_time = end_time
            if isinstance(_data, dict):
                if 'requested exceeds 300' in _data.get('message', ''):
                    time.sleep(10)
                    continue
            if self._is_err(res.status_code):
                return self._parse_err(res.status_code)
            ohlc_datas.extend(_data)
        _return = {}
        if is_resample_ohlcv and ohlc_datas:
            ohlc_datas = resample_ohlcv(ohlc_datas, to_timeframe=candle_type)
        _span_minutes = span_from_candletype(candle_type)
        for d in ohlc_datas:
            _timestamp = int(d[0])
            if _timestamp % _span_minutes == 0 and _timestamp > _start_time:
                _return[_timestamp] = {
                    'open': str(d[1]),
                    'high': str(d[2]),
                    'low': str(d[3]),
                    'close': str(d[4]),
                    'volume': str(d[5])
                }

        _return = OrderedDict(sorted(_return.items()))
        return Ok(_return)

    def market_prices(self, market: str = None) -> PriceDict:
        # :TODO Coinbase does not have full price tickker method
        super().market_prices()
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        if market:
            res = api.call(f"products/{market.replace(':','-')}/ticker", method='GET')
            if self._is_err(res.status_code):
                return self._parse_err(res.status_code)
            return {
                market: res.json()['price']
            }
        markets = api.call('products')
        if self._is_err(markets.status_code):
            return self._parse_err(markets.status_code)
        _return = {}
        for market in markets.json():
            if market['id'].replace('-', ':') in self._map['pairs']:
                res = api.call(f"products/{market['id']}/ticker", method='GET')
                _d = res.json()
                if 'price' in _d:
                    _return[market['id'].replace('-', ':')] = float(res.json()['price'])

        return _return

    def filters(self) -> Dict[str, TradeFilterData]:
        super().filters()
        def convert(x): return int(abs(math.log10(float(x))))

        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        res = api.call('products')
        if self._is_err(res.status_code):
            return self._parse_err(res.status_code)
        _return = {}
        for row in res.json():
            if row.get('status', '') == 'online':
                _return[f"{row['id'].replace('-','')}"] = {
                    'lotDenom':  convert(row['base_increment']),
                    'priceDenom': convert(row['quote_increment']),
                    'minLot': float(row['base_min_size']),
                    'minNot': float(row['min_market_funds']),
                }

        return _return

    ### PRIVATE API'S ###

    def sign_api(self, api_keys: ApiKeysType, method: str = '', query: StrDict = {}, body: StrDict = {}, headers: StrDict = {}, urlpath: str = '') -> SignedReq:
        timestamp = str(int(time.time()))
        if body:
            body = json.dumps(body, separators=(',', ':'))
        if query:
            urlpath += '?{query}'.format(query=urllib.parse.urlencode(query))
        message = ''.join([timestamp, method,
                           '/'+urlpath, (body or '')])
        message = message.encode('ascii')
        hmac_key = base64.b64decode(api_keys[1])
        signature = hmac.new(hmac_key, message, hashlib.sha256)
        signature_b64 = base64.b64encode(signature.digest()).decode('utf-8')
        headers.update({
            'CB-ACCESS-SIGN': signature_b64,
            'CB-ACCESS-TIMESTAMP': timestamp,
            'CB-ACCESS-KEY': api_keys[0],
            'CB-ACCESS-PASSPHRASE': api_keys[2],
            'Content-type': 'application/json',
        })

        return urlpath, body, headers

    def balance(self, api_keys: ApiKeysType) -> Result[BalanceDict, str]:
        super().balance(api_keys)
        urlpath = 'accounts'
        method = 'GET'
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        _, _, headers = self.sign_api(api_keys,  method=method, urlpath=urlpath)
        res = api.call(urlpath,  method=method, headers=headers)
        if self._is_err(res.status_code):
            return self._parse_err(res.status_code)
        _data = res.json()
        if not isinstance(res.json(), list):
            return Err(_data)
        _retrun = {d['currency']: float(d['available']) for d in _data}
        return Ok(_retrun)

    def check_auth(self, api_keys: ApiKeysType) -> bool:
        super().check_auth(api_keys)
        # TODO: should be faster than this
        return self.balance(api_keys).is_ok()

    def __create_order(self, api_keys: ApiKeysType, orderType: OrderType, market: str, side: SideType, amount: float, price: float) -> requests.models.Response:
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        urlpath = 'orders'
        method = 'POST'
        _filter = self.filters().get(market.replace(':', ''), {})
        price = round(price, int(_filter.get('priceDenom', 0)))
        amount = round(amount, int(_filter.get('lotDenom', 0)))
        body = {
            'product_id': market.replace(':', '-'),
            'side': side.lower(),
            'type': 'limit',
            'price': str(price),
            'size': str(amount),
        }
        if orderType == 'stop':
            body.update({
                'stop': 'loss',
                'stop_price': str(price)
            })
        if orderType == 'market':
            body.update({
                'type': 'market',
            })
            del body['price']
        _, body, headers = self.sign_api(api_keys,  method=method, urlpath=urlpath, body=body)
        res = api.call(path=urlpath,   method=method, headers=headers, data=body)
        self.get_logger().debug(f'Attempted {side} {orderType.upper()} trade: {res.text}, {res.status_code}, {body}, {headers}')

        return res

    def limit_order(self, api_keys: ApiKeysType, market: str, side: SideType, amount: float, price: float) -> Result[str, str]:
        super().limit_order(api_keys, market, side, amount, price)
        res = self.__create_order(api_keys=api_keys, orderType='limit', market=market, side=side, amount=amount, price=price)
        _data = res.json()
        if self._is_err(res.status_code):
            return self._parse_err(res.status_code)

        return Ok(_data['id'])

    def stoploss_order(self, api_keys: ApiKeysType, market: str, amount: float, stop_price: float) -> Result[str, str]:
        super().stoploss_order(api_keys, market, amount, stop_price)
        res = self.__create_order(api_keys=api_keys, orderType='stop', market=market, side='SELL', amount=amount, price=stop_price)
        _data = res.json()
        if self._is_err(res.status_code):
            return self._parse_err(res.status_code)

        return Ok(_data['id'])

    def limit_details(self, api_keys: ApiKeysType, market: str, txid: TxID, is_full_response: bool = False) -> Result[LimitOrderStatus, str]:
        super().limit_details(api_keys, market, txid)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        method = 'GET'
        urlpath = f'orders/{txid}'
        _, body, headers = self.sign_api(api_keys,  method=method, urlpath=urlpath)
        req = api.call(path=urlpath,   method=method, headers=headers, data=body)
        _data = req.json()
        if self._is_err(req.status_code):
            return self._parse_err(req.status_code)
        if is_full_response:
            return Ok(_data)
        out = {
            'date': _data.get('done_at', _data['created_at']),
            'exec_vol': float(_data['filled_size']),
            'exec_frac': float(_data['filled_size'])/float(_data['size']) if float(_data['filled_size']) > 0 else 0,
            'price': float(_data['price']) if _data.get('price', False) else float(_data['executed_value']) / float(_data['filled_size'])
        }
        out['date'] = int(datetime.datetime.strptime(out['date'], "%Y-%m-%dT%H:%M:%S.%fZ").timestamp())

        return Ok(out)

    def cancel_order(self, api_keys: ApiKeysType, market: str, txid: TxID) -> Result[str, str]:
        super().cancel_order(api_keys, market, txid)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        method = 'DELETE'
        urlpath = f'orders/{txid}'
        _, body, headers = self.sign_api(api_keys,  method=method, urlpath=urlpath)
        req = api.call(path=urlpath,   method=method, headers=headers, data=body)
        if self._is_err(req.status_code):
            return self._parse_err(req.status_code)
        _data = req.json()

        return Ok('success')

    def market_order(self, api_keys: ApiKeysType, market: str, side: SideType, amount: float) -> Result[Order, str]:
        super().market_order(api_keys, market, side, amount)
        res = self.__create_order(api_keys=api_keys, orderType='market', market=market, side=side, amount=amount, price=0)
        _data = res.json()
        if self._is_err(res.status_code):
            return self._parse_err(res.status_code)
        _data = self.limit_details(api_keys, market, _data['id'], is_full_response=True).ok()

        order_time = int(datetime.datetime.strptime(_data['done_at'], "%Y-%m-%dT%H:%M:%S.%fZ").timestamp())
        aux = {'fee': float(_data['fill_fees']),
               'fee_asset':  market.split(':')[-1],
               'price': float(_data['executed_value']) / float(_data['filled_size']),
               'amount': float(_data['filled_size'])
               }

        order = Order(self.name, to_internal_market(market), order_time * 1000, side, 'MARKET', aux['price'], aux['fee'], aux['amount'], aux['fee_asset'])
        return Ok(order)
