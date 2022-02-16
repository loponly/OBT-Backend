from tradeEnv.exchanges import *
from result import Result, Err, Ok
from typing import *
from datetime import datetime
import urllib
import time
import hashlib
import hmac

from tradeEnv.api_adapter import ApiAdapter, Order, binance_map
from tradeEnv.utils import to_internal_market


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


