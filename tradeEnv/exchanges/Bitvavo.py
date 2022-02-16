import time
import re
import datetime

import requests
from tradeEnv.utils import span_from_candletype, to_internal_market
import urllib
import json
import hmac
import hashlib


from typing import Any, List, Dict, Optional
from typing_extensions import Literal
from result import Result, Err, Ok
from collections import OrderedDict

from functools import partial
from tradeEnv.api_adapter import *
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


Bitvavo_map = {
    'name': 'BitvavoAPI',
    'exchange_name': 'Bitvavo',
    'base': 'https://api.bitvavo.com/v2',
    'cursor_limit': 1000,
    'cursor_stop': 1000000,
    'endpoints': {
        '{MarkID}/candles?interval={candlesType}&start={:cursor}': {
            'map': OrderedDict({
                'markets[{MarkID}].candles{*}':           '$[*][0]',
                'markets[{MarkID}].candles[*].open':      '$[*][1]',
                'markets[{MarkID}].candles[*].high':      '$[*][2]',
                'markets[{MarkID}].candles[*].low':       '$[*][3]',
                'markets[{MarkID}].candles[*].close':     '$[*][4]',
                'markets[{MarkID}].candles[*].volume':    '$[*][5]',
            }),
            'preprocess': [lambda l: list(reversed(l))],
            'postprocess': partial(pipeline, [pop_last, candles_ms2s])
        }
    }
}


OrderType = Literal['limit', 'market', 'stopLossLimit']


class BitvavoAPI(AbstractTradeAPI):

    name = 'Bitvavo'
    _map = Bitvavo_map

    @staticmethod
    def concat_price_by_precision(precision: int, price: float) -> float:
        import re
        number_concat = ''
        price = re.match(r'(0|([1-9]+[0-9]*))(\.(0*)([1-9]+[0-9]*)|)', str(price))
        if price:
            _n = price.group(2)
            number_concat = _n[0:precision if len(_n) >= precision else len(_n)] if _n else '0'
            number_concat += (len(_n)-precision)*'0' if len(_n)-precision > 0 else ''
            precision -= len(_n) if _n else 0
            if precision > 0:
                _n = price.group(5)
                number_concat += '.'+price.group(4) if price.group(4) is not None else ''
                number_concat += _n[0:precision if len(_n) >= precision else len(_n)] if _n else ''
            return float(number_concat)
        return price

    @staticmethod
    def concat_by_precision_number(precision: int, number: float) -> str:
        _return_number = ''
        for i, d in enumerate(str(number)):
            if precision == 0:
                break
            if d == '0' and i == 0:
                continue
            _return_number += d
            if d == '.':
                continue
            precision -= 1

        _return_number = str(float(_return_number))
        if len(_return_number) > 1:
            if _return_number[-2:] == '.0':
                return str(int(float(_return_number)))
        return _return_number

    def _is_err(self, data: Any) -> bool:
        return 'error' in data and bool(data['error'])

    def _parse_err(self, errorCode: int = 0, msg: Optional[str] = None) -> Result[None, str]:
        if errorCode in {110}:
            return Err('failed-exchange-auth')
        elif errorCode in {110, 201, 216}:
            return Err('insufficient-balance')
        elif errorCode in {205, 233}:
            self.get_logger().warning(f"Failed order action: {errorCode} {msg}")
            return Err('failed-exchange-funds')
        elif errorCode in {301, 309}:
            return Err('failed-exchange-ratelimit')
        elif errorCode != 0:
            self.get_logger().warning(f"Unkown exchange error occured: {errorCode} {msg}")

        return Err('failed-exchange-call')

    ### PUBLIC API'S ###
    def update_ohlc(self, market: str, candle_type: CandleType, start_time: IntIsh) -> Result[RawCandleDataType, str]:
        super().update_ohlc(market, candle_type, start_time)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        market = market.replace(':', '-')
        candle_span = span_from_candletype(candle_type)
        opts = {'candlesType': candle_type, 'MarkID': market, 'cursor_start': int(float(start_time) + candle_span) * 1000 + 1, 'cursor_expr': ['$[*][0]']}
        api.updateEndpoint('{MarkID}/candles?interval={candlesType}&start={:cursor}', opts, silent=False)

        if not api.data.get('markets', None):
            return Err('no-new-candles')

        return Ok(api.data['markets'][market]['candles'])

    def market_prices(self) -> PriceDict:
        super().market_prices()
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        res = api.call(f"ticker/price", method='GET')
        _data = res.json()
        if self._is_err(_data):
            return self._parse_err(_data.get('errorCode', 0))
        _return = {}
        for row in res.json():
            _return[row['market'].replace('-', ':')] = float(row['price'])
        return _return

    def filters(self, is_full_data=False) -> Dict[str, TradeFilterData]:
        '''
        Return:
            lotDenom: minAmountincemental base
            priceDenom: minAmountIncremental quote
            minLot: min amount in base
            minNot:  min amount in quote
        '''
        super().filters()
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        res = api.call('markets')
        _data = res.json()
        if self._is_err(_data):
            return self._parse_err(_data.get('errorCode', 0))
        _return = {}
        for row in _data:
            if row.get('status', '') == 'trading':
                _data = {
                    'lotDenom': int(row['pricePrecision']),
                    'priceDenom': int(row['minOrderInQuoteAsset']),
                    'minLot': float(row['minOrderInBaseAsset']),
                    'minNot': float(row['minOrderInQuoteAsset']),
                }
                if is_full_data:
                    _data.update(row)

                _return[f"{row['market'].replace('-','')}"] = _data

        return _return

    ### PRIVATE API'S ###

    def sign_api(self, api_keys: ApiKeysType,  urlpath: str = '', method: str = '', query: StrDict = {}, body: StrDict = {}, headers: StrDict = {},) -> SignedReq:
        timestamp = str(int(time.time()*1000))
        if body:
            body = json.dumps(body, separators=(',', ':'))
        if query:
            urlpath += '?{query}'.format(query=urllib.parse.urlencode(query))
        message = ''.join([timestamp,
                           method,
                           '/v2/'+urlpath,
                           (body or '')])
        signature = hmac.new(api_keys[1].encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
        headers.update({
            'Content-Type': 'Application/JSON',
            'BITVAVO-ACCESS-SIGNATURE': signature,
            'BITVAVO-ACCESS-TIMESTAMP': timestamp,
            'BITVAVO-ACCESS-KEY': api_keys[0],
            'BITVAVO-ACCESS-WINDOW': str(60000),
        })

        return urlpath, body, headers

    def balance(self, api_keys: ApiKeysType) -> Result[BalanceDict, str]:
        super().balance(api_keys)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        method = 'GET'
        urlpath, body, headers = self.sign_api(api_keys=api_keys,  method=method, urlpath='balance')
        res = api.call(path=urlpath,  method='GET', headers=headers)
        _data = res.json()
        if self._is_err(_data):
            return self._parse_err(_data.get('errorCode', 0))
        _retrun = {d['symbol']: float(d['available']) for d in _data}

        return Ok(_retrun)

    def check_auth(self, api_keys: ApiKeysType) -> bool:
        super().check_auth(api_keys=api_keys)
        return self.balance(api_keys=api_keys).is_ok()

    def __create_order(self, api_keys: ApiKeysType, market: str, orderType: OrderType, side: SideType, amount: float, price: float) -> requests.models.Response:
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        urlpath = 'order'
        method = 'POST'
        _filter = self.filters().get(market.replace(':', ''), {})
        amount = str(self.concat_by_precision_number(_filter.get('lotDenom', 0), amount))
        body = {
            'market': market.replace(':', '-'),
            'side': side.lower(),
            'orderType': orderType,
            'amount': str(amount)
        }

        if orderType == 'limit':
            body['price'] = str(self.concat_by_precision_number(_filter.get('priceDenom', 0), price))

        if orderType == 'stopLossLimit':
            price = str(self.concat_by_precision_number(_filter.get('priceDenom', 0), price))
            body.update({
                'price': price,
                'triggerAmount': price,
                'triggerType': 'price',
                'triggerReference': 'lastTrade'
            })

        _, body, headers = self.sign_api(api_keys=api_keys,  method=method, urlpath=urlpath, body=body)
        res = api.call(path=urlpath,  method=method, headers=headers, data=body)
        self.get_logger().debug(f'Attempted {side} {orderType} trade: {res.text}, {res.status_code}, {body}, {headers}')
        return res

    def limit_order(self, api_keys: ApiKeysType, market: str, side: SideType, amount: float, price: float) -> Result[str, str]:
        super().limit_order(api_keys, market, side, amount, price)
        res = self.__create_order(api_keys=api_keys, side=side, market=market, orderType='limit', amount=amount, price=price)
        _data = res.json()
        if self._is_err(_data):
            return self._parse_err(_data.get('errorCode', 0))
        return Ok(_data['orderId'])

    def stoploss_order(self, api_keys: ApiKeysType, market: str, amount: float, stop_price: float) -> Result[str, str]:
        super().stoploss_order(api_keys, market, amount, stop_price)
        res = self.__create_order(api_keys=api_keys, side='SELL', market=market, orderType='stopLossLimit', amount=amount, price=stop_price)
        _data = res.json()
        if self._is_err(_data):
            return self._parse_err(_data.get('errorCode', 0))
        return Ok(_data['orderId'])

    def limit_details(self, api_keys: ApiKeysType, market: str, txid: TxID, is_full_response: bool = False) -> Result[LimitOrderStatus, str]:
        super().limit_details(api_keys, market, txid)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        urlpath = 'order'
        method = 'GET'
        query = {
            'market': market.replace(':', '-'),
            'orderId': txid
        }
        urlpath, _, headers = self.sign_api(api_keys=api_keys,  method=method, urlpath=urlpath, query=query)
        req = api.call(path=urlpath, method=method, headers=headers)
        _data = req.json()
        if self._is_err(_data):
            return self._parse_err(_data.get('errorCode', 0))
        if is_full_response:
            return Ok(_data)
        out = {
            'date': _data['updated']//1000,
            'exec_vol': float(_data['filledAmount']),
            'exec_frac': float(_data['filledAmount'])/float(_data['amount']),
            'price': float(_data['price']) if _data.get('price', False) else float(_data['filledAmountQuote']) / float(_data['filledAmount'])
        }
        return Ok(out)

    def cancel_order(self, api_keys: ApiKeysType, market: str, txid: TxID) -> Result[str, str]:
        super().cancel_order(api_keys, market, txid)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        urlpath = 'order'
        method = 'DELETE'
        query = {
            'market': market.replace(':', '-'),
            'orderId': txid
        }
        urlpath, _, headers = self.sign_api(api_keys=api_keys,  method=method, urlpath=urlpath, query=query)
        req = api.call(path=urlpath, method=method, headers=headers)
        self.get_logger().debug(f'Attempted to cancel on order {txid}: {req.text},  {req.status_code},{headers}')
        _data = req.json()
        if self._is_err(_data):
            # Check if already canceled
            if _data.get('errorCode', 0) == 233:
                return Ok('success')

            return self._parse_err(_data.get('errorCode', 0))
        return Ok('success')

    def market_order(self, api_keys: ApiKeysType, market: str, side: SideType, amount: float) -> Result[Order, str]:
        super().market_order(api_keys, market, side, amount)
        res = self.__create_order(api_keys=api_keys, side=side, market=market, orderType='market', amount=amount, price=0)
        _data = res.json()
        if self._is_err(_data):
            return self._parse_err(_data.get('errorCode', 0))
        order_time = _data['fills'][-1]['timestamp']
        aux = {'fee': float(_data['feePaid']),
               'fee_asset': _data['feeCurrency'],
               'price': float(_data['filledAmountQuote']) / float(_data['filledAmount']),
               'amount': float(_data['filledAmount'])
               }

        order = Order(self.name, to_internal_market(market), order_time, side, 'MARKET', aux['price'], aux['fee'], aux['amount'], aux['fee_asset'])
        return Ok(order)
