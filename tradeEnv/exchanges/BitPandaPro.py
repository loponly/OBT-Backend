import re
import json
import time
import datetime

import requests
from tradeEnv.utils import span_from_candletype, to_internal_market
import urllib


from typing import Any, List, Set, Dict

from typing_extensions import Literal
from result import Result, Err, Ok
from collections import OrderedDict

from tradeEnv.api_adapter import ApiAdapter, Order, candles_ms2s, denoms_from_step
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


bitpandapro_map = {
    'name': 'BitpandaProApi',
    'exchange_name': 'BitpandaPro',
    'base': 'https://api.exchange.bitpanda.com',
    'cursor_limit': 1000,
    'cursor_stop': 1000000
}


OrderType = Literal['LIMIT', 'MARKET', 'STOP']


class BitpandaPro(AbstractTradeAPI):
    name = 'BitpandaPro'
    _map = bitpandapro_map

    def _is_err(self, data: Any) -> bool:
        return 'error' in data and bool(data['error'])

    def _parse_err(self, error: List[str] = ['unkown']) -> Result[None, str]:
        err: Set[str] = set(error)
        if err.issubset({'MISSING_PERMISSION', 'MISSING_CREDENTIALS', 'INVALID_CREDENTIALS',
                         'INVALID_APIKEY', 'INVALID_AUTHORIZED_PARTY', 'INVALID_SCOPES', 'INVALID_SUBJECT',
                         'INVALID_DEVICE_ID', 'INVALID_IP_RESTRICTION', 'APIKEY_REVOKED', 'APIKEY_EXPIRED',
                         'SESSION_EXPIRED', 'CLIENT_IP_BLOCKED', 'ILLEGAL_CHARS', 'INVALID_ACCOUNT_ID'}):
            return Err('failed-exchange-auth')
        if err.issubset({'INSUFFICIENT_FUNDS', 'INSUFFICIENT_LIQUIDITY'}):
            return Err('insufficient-balance')
        if err.issubset({'BAD_AMOUNT_PRECISION', 'BAD_PRICE_PRECISION', 'ALREADY_IN_PROGRESS',
                         'INVALID_AMOUNT', 'INVALID_AFFILIATE_TAG', 'IN_MAINTENANCE', 'INVALID_UNIT', 'INVALID_PERIOD'
                         'INVALID_ORDER_REQUEST', 'INVALID_ORDER_TYPE', 'INVALID_CLIENT_UUID', 'INVALID_TIME', 'INVALID_DATE',
                         'INVALID_CURRENCY', 'INVALID_LIMIT', 'INVALID_QUERY', 'INVALID_SIDE',
                         'INVALID_ACCOUNT_HISTOsRY_FROM_TIME', 'INVALID_ACCOUNT_HISTORY_MAX_PAGE_SIZE',
                         'INVALID_CANDLESTICKS_GRANULARITY', 'INVALID_CANDLESTICKS_UNIT', 'NEGATIVE_AMOUNT', 'NEGATIVE_PRICE',
                         'ORDER_NOT_FOUND', 'INVALID_INSTRUMENT_CODE'}):
            return Err('failed-exchange-funds')
        if err.issubset({'TRANSACTION_RATE_LIMIT_REACHED'}):
            return Err('failed-exchange-ratelimit')
        elif err.issubset({'INVALID_AFFILIATE_TAG'}):
            return Err({'failed-exchange-internal'})
        if error != ['unkown']:
            self.get_logger().warning(f"Unkown exchange error occured: {error}")

        return Err('failed-exchange-call')
    ### PUBLIC API'S ###

    def update_ohlc(self, market: str, candle_type: CandleType, start_time: IntIsh) -> Result[RawCandleDataType, str]:
        super().update_ohlc(market, candle_type, start_time)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        TimeGranularity = {'m': 'MINUTES',
                           'h': 'HOURS',
                           'd': 'DAYS',
                           'w': 'WEEKS',
                           'M': 'MONTHS'}
        candle_decoded = re.match(r'(^[0-9]+)(w|d|h|m)$', candle_type)
        start_time = max(int(start_time), 1420112436)+1

        def get_ohlc(start_time: IntIsh, end_time: IntIsh) -> List[dict]:
            end_time = datetime.datetime.fromtimestamp(int(end_time), datetime.timezone.utc)
            start_time = datetime.datetime.fromtimestamp(int(start_time), datetime.timezone.utc)
            opts = {
                'unit':     TimeGranularity[str(candle_decoded.group(2))],
                'period':   int(candle_decoded.group(1)),
                'from':     start_time.isoformat(),
                "to":       end_time.isoformat(),
            }
            url_string = 'public/v1/candlesticks/{MarkID}?{query}'.format(MarkID=market.replace(':', '_'), query=urllib.parse.urlencode(opts))
            req = api.call(url_string, method='GET')

            if req.status_code != 200:
                return []
            return req.json()
        start_time = datetime.datetime.fromtimestamp(int(start_time))
        ohlc_datas = []
        while start_time <= datetime.datetime.now():
            end_time = (start_time + datetime.timedelta(**{TimeGranularity[str(candle_decoded.group(2))].lower(): 1500}))

            _d = get_ohlc(start_time.timestamp(), end_time.timestamp())
            ohlc_datas.extend(_d)
            start_time = end_time
        _return = {}
        _span_minutes = span_from_candletype(candle_type)
        for d in ohlc_datas:
            if 'time' not in d:
                continue
            _timestamp = int(datetime.datetime.strptime(d['time'], "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(datetime.timezone.utc).timestamp())+1
            if _timestamp % _span_minutes == 0:
                _return[_timestamp] = {
                    'open':     d['open'],
                    'high':     d['high'],
                    'low':      d['low'],
                    'close':    d['close'],
                    'volume':   d['volume']
                }

        _return = OrderedDict(sorted(_return.items()))
        return Ok(_return)

    def market_prices(self) -> PriceDict:
        super().market_prices()
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        req = api.call('public/v1/market-ticker', method='GET')
        exchange_info = req.json()
        pairs = {}
        for ticker in exchange_info:
            if ticker.get('state', '') == 'ACTIVE':
                pairs[ticker['instrument_code'].replace('_', ':')] = float(ticker['last_price'])

        return pairs

    def __filters(self) -> Result[str, TradeFilterData]:
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        res = api.call('public/v1/instruments')
        if res.status_code != 200:
            return Err(res.status_code)
        if 'error' in res.json():
            return Err(res.json())
        _return = {}
        # lotDenom: minAmountincemental base
        # priceDenom: minAmountIncremental quote
        # minLot: min amount in base
        # minNot:  min amount in quote
        for row in res.json():
            if row.get('state', '') == 'ACTIVE':
                _return[f"{row['base']['code']}{row['quote']['code']}"] = {
                    'lotDenom': row['amount_precision'],
                    'priceDenom': row['quote']['precision'],
                    'minNot': float(row['min_size']),
                }

        return Ok(_return)

    def filters(self) -> Dict[str, TradeFilterData]:
        super().filters()
        _filter = self.__filters()
        if _filter.is_err():
            return {}
        return _filter.ok()

    ### PRIVATE API'S ###

    def sign_api(self, api_keys: ApiKeysType, query: StrDict = {}, body: str = '', headers: StrDict = {}, urlpath: str = '') -> Dict:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_keys[0]}',
            'bp-affiliate-tag': 'OBTrader'
        }

        return headers

    def balance(self, api_keys: ApiKeysType) -> Result[BalanceDict, str]:
        super().balance(api_keys)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)

        headers = self.sign_api(api_keys, urlpath='')
        res = api.call('public/v1/account/balances',  method='GET', headers=headers)
        _retrun = res.json()
        if self._is_err(_retrun):
            return self._parse_err([_retrun['error']])
        _retrun = {d['currency_code']: float(d['available']) for d in _retrun['balances']}
        return Ok(_retrun)

    def check_auth(self, api_keys: ApiKeysType) -> bool:
        super().check_auth(api_keys)
        # TODO: should be faster than this
        return self.balance(api_keys).is_ok()

    def __create_order(self, api_keys: ApiKeysType, market: str,  orderType: OrderType, side: SideType, amount: float, price: float) -> requests.models.Response:
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        urlpath = 'public/v1/account/orders'
        marketid = market.replace(':', '_')
        _filter = self.filters().get(market.replace(':', ''), {'lotDenom': 0, 'priceDenom': 0, 'minNot': 10.0})
        amount = int(amount) if _filter['lotDenom'] == 0 else amount
        price = int(price) if _filter['priceDenom'] == 0 else price
        price = str(price)
        query = {'instrument_code': marketid,
                 'side': side,
                 'type': orderType,
                 'amount': str(amount),
                 }
        if orderType == 'LIMIT':
            query['price'] = price
        if orderType == 'STOP':
            query['price'] = price
            query['trigger_price'] = price
        req = api.call(path=urlpath, data=json.dumps(query),  method='POST', headers=self.sign_api(api_keys, urlpath=urlpath))

        self.get_logger().debug(f'Attempted {orderType} {side} order: {req.text}, {req.reason}, {req.status_code},{json.dumps(query)}')
        return req

    def limit_order(self, api_keys: ApiKeysType, market: str, side: SideType, amount: float, price: float) -> Result[str, str]:
        super().limit_order(api_keys, market, side, amount, price)

        res = self.__create_order(api_keys, market, 'LIMIT', side, amount, price)
        if res.status_code != 201:
            return self._parse_err([res.json()['error']])
        data = res.json()
        if self._is_err(data):
            return self._parse_err([data['error']])

        return Ok(data['order_id'])

    def stoploss_order(self, api_keys: ApiKeysType, market: str, amount: float, stop_price: float) -> Result[str, str]:
        super().stoploss_order(api_keys, market, amount, stop_price)

        res = self.__create_order(api_keys=api_keys, market=market, orderType='STOP', side='SELL', amount=amount, price=stop_price)
        if res.status_code != 201:
            return self._parse_err([res.json()['error']])
        data = res.json()
        if self._is_err(data):
            return self._parse_err([data['error']])
        return Ok(data['order_id'])


    def limit_details(self, api_keys: ApiKeysType, market: str, txid: TxID, is_full_response=False) -> Result[LimitOrderStatus, str]:
        super().limit_details(api_keys, market, txid)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        urlpath = f'public/v1/account/orders/{txid}'
        headers = self.sign_api(api_keys, urlpath=urlpath)
        req = api.call(path=urlpath, method='GET', headers=headers)
        self.get_logger().debug(f'Attempted get details on order {txid}: {req.text}, {req.reason}, {req.status_code},{headers}')
        data = req.json()
        if req.status_code != 200:
            return Err(req.status_code)
        if self._is_err(data):
            return self._parse_err([data['error']])
        if is_full_response:
            return Ok(data)
        data = data['order']
        out = {
            'date': data.get('time_last_updated', data['time']),
            'exec_vol': float(data['filled_amount']),
            'exec_frac':  float(data['filled_amount'])/float(data['amount']) if float(data['filled_amount']) > 0 else 0,
            'price': float(data['price']),
        }

        out['date'] = int(datetime.datetime.strptime(out['date'], "%Y-%m-%dT%H:%M:%S.%fZ").timestamp())
        return Ok(out)

    def cancel_order(self, api_keys: ApiKeysType, market: str, txid: TxID) -> Result[str, str]:
        super().cancel_order(api_keys, market, txid)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        urlpath = f'public/v1/account/orders/{txid}'

        headers = self.sign_api(api_keys, urlpath=urlpath)
        req = api.call(path=urlpath, method='DELETE', headers=headers)
        self.get_logger().debug(f'Attempted to cancel on order {txid}: {req.text}, {req.reason}, {req.status_code},{headers}')
        if req.status_code != 204:
            return self._parse_err([req.json()['error']])
        return Ok('success')

    def market_order(self, api_keys: ApiKeysType, market: str, side: SideType, amount: float) -> Result[Order, str]:
        super().market_order(api_keys, market, side, amount)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)

        res = self.__create_order(api_keys, market, 'MARKET', side, amount, 0)
        if res.status_code != 201:
            return self._parse_err([res.json()['error']])
        data = res.json()
        if self._is_err(data):
            return self._parse_err([data['error']])
        txid = data['order_id']

        aux = self.__filled_order(api_keys=api_keys, market=market, txid=txid)
        if aux.is_err():
            return aux
        aux = aux.ok()
        order = Order(self.name, to_internal_market(market), aux['timestamp'], side, 'MARKET', aux['price'], float(aux['fee']), aux['amount'], aux['fee_asset'])
        return Ok(order)

    def __filled_order(self, api_keys: ApiKeysType, market: str, txid: str, retry: int = 2) -> Result:
        res = self.limit_details(api_keys=api_keys, market=market, txid=txid, is_full_response=True)
        if res.is_err():
            return res
        data = res.ok()
        aux = {'fee': 0,
               'fee_asset': '',
               'price': float(data['order']['price']),
               'amount': float(data['order']['filled_amount']),
               'timestamp': data['order'].get('time_last_updated', data['order']['time']),
               }
        for d in data['trades']:
            # TODO: handle edge-case of different `fee_currency`s
            aux['fee'] += float(d['fee']['fee_amount'])
            aux['fee_asset'] = d['fee']['fee_currency']

        aux['timestamp'] = int(datetime.datetime.strptime(aux['timestamp'], "%Y-%m-%dT%H:%M:%S.%fZ").timestamp())*1000
        if aux['amount'] == 0:
            if retry > 0:
                time.sleep(1)
                aux = self.__filled_order(api_keys, market, txid, retry=retry-1)
                return aux # No Result wrapper as it's done by the deepest call
            else:
                self.get_logger().debug(f'Attempted to make a market order {txid}: {aux}')
                return Err('failed-exchange-funds')
        return Ok(aux)
