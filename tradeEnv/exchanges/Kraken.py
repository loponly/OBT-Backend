from tradeEnv.exchanges import *
from result import Result, Err, Ok
from typing import *
import time
import hashlib
import hmac
import base64
from json.decoder import JSONDecodeError
import urllib

from tradeEnv.api_adapter import ApiAdapter, Order, kraken_map
from tradeEnv.utils import span_from_candletype, to_internal_market

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
            if float(tx['price']) == 0:
                time.sleep(1)
                continue

            order_time = tx.get('opentm', time.time())

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

