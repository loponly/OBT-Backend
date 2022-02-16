import copy
import json
from typing import Optional
import requests
from collections import OrderedDict
from jsonpath2.path import Path
from .utils import lookahead, span_from_candletype, to_internal_market
from functools import partial
from result import Ok, Err
import numpy as np
import re
import time
import os
import hmac
import hashlib
import base64
import traceback
from urllib.parse import urlencode, urlparse, quote
import logging
from diskcache import Cache


class AutoProxy:
    """
    Transparently proxies HTTP requests through an AutoProxy network (defined by `PROXY_ENTRYPOINT` env variable).
    It locally fallsback while logging if it fails to send through the AutoProxy.

    It will request the approximate network load (netload) for the target domain with every request (caching for 5s)
    If the netload is too high (>100% of expected availability) it will fallback to sending requests locally.

    Matching logic:
    * not `PROXY_ENTRYPOINT` => _fallback_request
    * AutoProxy netload > 100% =>  _fallback_request
    * else => _proxy_request
    * _proxy_request fails => _fallback_request
    """

    def __init__(self, rate_limit: int, rate_period: int, tag: Optional[str] = None, overload_delay=1.5):
        self._rate_limit = rate_limit
        self._rate_period = rate_period
        self._entrypoint = os.environ.get('PROXY_ENTRYPOINT')
        self._tag = tag
        self._fallback_counter = 0
        self._overload_delay = overload_delay
        self._fallback_queue = {}
        self._cache = Cache('store/db/api_adapter', sqlite_synchronous=2)

    def _fallback_request(self, method, url, data=None, headers={}, timeout=10, weight=1):
        # TODO: use _cache.incr for atomic/synchronized weights? (note queue needs transact)
        self._fallback_counter += weight
        self._fallback_queue[time.time() + self._rate_period] = weight
        for t, weight in list(self._fallback_queue.items()):
            if t > time.time():
                self._fallback_counter -= weight
                del self._fallback_queue[t]

        time.sleep(0.5)
        if self._fallback_counter > self._rate_limit:
            # TODO: dynamic delay?
            time.sleep(self._overload_delay)

        return requests.request(method, url, data=data, headers=headers, timeout=timeout)

    def _proxy_request(self, method, url, data=None, headers={}, timeout=10, weight=1):
        _data = data
        _headers = headers
        puri = urlparse(url)
        if type(data) == dict:
            data = urlencode(data)  # TODO: externalize this

        params = ''
        if method.upper() == 'GET' or method.upper() == 'DELETE':
            params = '&'.join(filter(lambda z: bool(z), [data, puri.query]))
            # Add ? if any params exist
            params = f'?{params}' if params else params
            data = None

        headers = {
            'X-Forward-Method': method,
            'X-Forward-Host': quote(puri.netloc),
            'X-Forward-Path': quote(f'{puri.path}{params}'),
            'X-Forward-Tag': self._tag or puri.netloc,
            'X-Shared-Key': 'Q6D4eZimsT2GB2r',
            'X-Req-Weight': str(weight),
            'X-Max-Weight': str(self._rate_limit),
            'X-Expire-Weight': str(self._rate_period),
            'Proxy-Accept-Encoding': 'gzip, deflate',
            'Proxy-User-Agent': 'python-requests/2.25.1',
            'Proxy-Accept': '*/*',
            'Proxy-Connection': 'close',
            'Proxy-Pragma': 'no-cache',
            'Proxy-Cache-Control': 'no-cache',
            'Proxy-Content-Type': 'application/x-www-form-urlencoded',
            'TE': 'Trailers',
            **{f'Proxy-{k}': v for k, v in headers.items()}
        }
        #print(headers, method, url, data, params, puri)

        try:
            # TODO: fallback on 429, 503?
            #print(data, headers)
            return requests.request('POST', self._entrypoint + '/proxy', data=data, headers=headers, timeout=timeout)
        except requests.ConnectionError:
            print("ERROR: Failed to proxy request through external endpoint, falling back")
            return self._fallback_request(method, url, _data, _headers, timeout, weight)

    def request(self, method, url, data=None, headers={}, timeout=20, weight=1): 
        if self._entrypoint is None:
            return self._fallback_request(method, url, data, headers, timeout, weight)

        puri = urlparse(url)

        # Log status of autoproxy netload
        if 'entrypoint:status' not in self._cache:
            try:
                req = requests.request('GET', f"{self._entrypoint}/status", timeout=5)
                dinfo = req.json()
                self._cache.set('entrypoint:status', dinfo, expire=2)

                dinfo = dinfo.get('ratestatus', {}).get('trackers', {}).get(puri.netloc)
                if dinfo:
                    netload = dinfo['transitiveWeight'] / ((len(dinfo['peerWeights']) + 1) * dinfo['maxWeight'])
                    if netload > 1.0:
                        # Fallback to local
                        print(f"WARNING: netload is higher than 100% ({100 * netload}%) for {puri.netloc}, falling back to local requests")
                        time.sleep(self._overload_delay)
                        return self._fallback_request(method, url, data, headers, timeout, weight)
                    elif netload > 0.9:
                        print(f"WARNING: netload is higher than 90% ({100 * netload}%) for {puri.netloc}")
                        time.sleep(self._overload_delay)
                else:
                    print(f"WARNING: Failed to get netload for {puri.netloc}")
            except SystemExit:
                raise
            except:
                traceback.print_exc()
                print("Failed to get status from AutoProxy Entrypoint")

        return self._proxy_request(method, url, data, headers, timeout, weight)

# Base class to encompass the most basic requirements for an API client


class ApiNamespace:
    def __init__(self, base_url):
        self.base = base_url
        self.retry_after = 0
        if 'kraken' in base_url:
            self.proxy = AutoProxy(4, 5)
        elif 'binance' in base_url:
            self.proxy = AutoProxy(1200, 60)
        elif 'bitpanda' in base_url:
            self.proxy = AutoProxy(200, 60)
        elif 'bitvavo' in base_url:
            self.proxy = AutoProxy(500, 60)
        else:
            self.proxy = AutoProxy(4, 5)

    def call(self, path, method, data=None, headers={}, weight=1):
        if re.match(r"\{.*?\}", path):
            raise RuntimeError("Uninterpolated items in %s" % path)

        if self.retry_after:
            # Already over rate-limit
            time.sleep(self.retry_after)
            self.retry_after = 0

        #endpoint = 'https://webhook.site/5c22d9c1-9b6e-4d01-8f45-7e38d0865636/' + path
        endpoint = self.base + '/' + path
        #print(endpoint, method, data, headers)
        req = self.proxy.request(method, endpoint, data=data, headers=headers, timeout=10, weight=weight)
        #print(req, req.text, req.headers)
        self.retry_after = req.headers.get('Retry-After', 0)

        return req


def extract_json_leaf(match):
    return str(match.full_path).split('[')[-1].replace(']', '')


def assign_to_jsonpath(root_obj, path, wildcard=None, value=None):
    asSetAdd = False
    if path.endswith('|*|'):
        asSetAdd = True
        path = path.replace('|*|', '')

    path = re.sub(r"\[(.*?)\]", '.\g<1>', path)
    parts = path.split('.')

    current = root_obj
    for p, has_more in lookahead(parts):
        p = str(p)
        if p == '*':
            if wildcard is not None:
                p = str(wildcard)
            else:
                raise RuntimeError(
                    "Requires wildcard to fill in '*' (%s, %s)" % ('.'.join(parts), value))

        if current.get(p, None) is None and has_more:
            current[p] = {}

        if isinstance(current.get(p, None), dict):
            current = current[p]
            continue

        if asSetAdd and not has_more:
            if not isinstance(current.get(p, None), set):
                current[p] = set()
            current[p].add(value)
        elif value and not has_more:
            current[p] = value
        else:
            current[p] = {}


def interpolate(istr, args={}) -> str:
    for key in args:
        istr = istr.replace("{%s}" % key, str(args[key]))

    # Take step in n-pass interpolation
    istr = re.sub(r"\{\:(.*)\}", "{\g<1>}", istr)
    return istr


def data_merge(a, b):
    """merges b into a and return merged result

    NOTE: tuples and arbitrary objects are not handled as it is totally ambiguous what should happen
    """
    key = None
    # ## debug output
    # sys.stderr.write("DEBUG: %s to %s\n" %(b,a))
    try:
        if a is None or isinstance(a, str) or isinstance(a, bytes) or isinstance(a, int) or isinstance(a, float):
            # border case for first run or if a is a primitive
            a = b
        elif isinstance(a, list):
            # lists can be only appended
            if isinstance(b, list):
                # merge lists
                a.extend(b)
            else:
                # append to list
                a.append(b)
        elif isinstance(a, dict):
            # dicts must be merged
            if isinstance(b, dict):
                for key in b:
                    if key in a:
                        a[key] = data_merge(a[key], b[key])
                    else:
                        a[key] = b[key]
            else:
                raise NotImplementedError(
                    'Cannot merge non-dict "%s" into dict "%s"' % (b, a))
        else:
            raise NotImplementedError('NOT IMPLEMENTED "%s" into "%s"' % (b, a))
    except TypeError as e:
        raise TypeError(
            'TypeError "%s" in key "%s" when merging "%s" into "%s"' % (e, key, b, a))
    return a


class ApiAdapter(ApiNamespace):
    def __init__(self, map, fname=None, root_dir='store/dataset', hooks=[]):
        super().__init__(map['base'])

        self.root_dir = root_dir
        self.map = map.copy()
        self.fname = fname or self.map['name']
        self.fpath = self.get_full_path('%s.json' % fname)
        self.hooks = hooks
        self.data = {}
        if os.path.isfile(self.fpath):
            with open(self.fpath, 'r') as fp:
                self.data = json.load(fp)
                print('Loaded %s.json' % self.fname)

    def get_exchange_name(self):
        return self.map['exchange_name']

    def get_full_path(self, fname):
        return os.path.abspath(os.path.join(self.root_dir, fname))

    def move(self, name):
        old_fname = self.get_full_path('%s.json' % self.fname)

        self.fname = self.get_full_path('%s.json' % name)
        if os.path.isfile(old_fname):
            os.rename(old_fname, self.fname)

    def autosave(self):
        with open(self.fname, 'w') as fp:
            json.dump(self.data, fp)

    def call(self, path, data=None, method=None, headers={}, weight=1):
        if self.data.get('bearertoken', False):
            headers['Accept'] = 'application/json'
            headers['Authorization'] = 'Bearer %s' % self.data['bearertoken']

        for hook in self.hooks:
            if callable(hook):
                hook(self.base + '/' + path)

        return super().call(path, method or 'GET', data=data, headers=headers, weight=weight)

    def goThroughCusor(self, ourl, arguments={}, silent=True):
        url = interpolate(ourl, arguments)
        stop = False
        limit = self.map['cursor_limit']
        stop = self.map['cursor_stop']
        data = {}
        last_it = 0
        for it in range(0, stop, limit):
            # No limit specified, set it to fallback options
            if '{len}' not in url and it != 0:
                # TODO: custom fallback exprs
                fallbackExprs = arguments.get('cursor_expr') or ['$[*][0]', '$.result[@[keys()][0]][*][0]']
                for strexpr in fallbackExprs:
                    expr = Path.parse_str(strexpr)
                    matches = list(expr.match(data))
                    if matches:
                        it = max(int(x.current_value) for x in matches) + 1
                        # TODO: optimize kraken (currently minimum of 2 requests)
                        if last_it == it or arguments.get('cursor_start', 1e99) <= it:
                            return data
                        else:
                            last_it = it

                        if not silent:
                            print('Fallback cursor: %s %d' % (expr, it))

                        break

            if it == 0 and arguments.get('cursor_start', False):
                it = arguments.get('cursor_start', 0)

            cursor_url = interpolate(url, {'cursor': it, 'len': limit})
            req = self.call(cursor_url)

            if req.status_code != 200:  # Empty answer
                if not silent:
                    print(f'Cursor stopped by remote peer: {cursor_url} (status {req.status_code})')
                return data
            else:
                ndata = req.json()
                if not ndata:
                    if not silent:
                        print(f'Cursor stopped by remote peer: {cursor_url} (no data)')
                    return data
                # Response type is array
                if not data and isinstance(ndata, list):
                    data = []
                data = data_merge(data, ndata)
        if not silent:
            print('Cursor stopped by stop-limit %d (not full dataset)' % stop)
        return data

    def updateEndpoint(self, url, arguments={}, silent=True, autosave=False):
        if '{:cursor}' in url:
            rdata = self.goThroughCusor(url, arguments, silent)
        else:
            nurl = interpolate(url, arguments)
            rdata = self.call(nurl).json()

        if 'error' in rdata and rdata['error'] != []:
            return 0
        
        endpoint_meta = self.map['endpoints'][url]
        for f in endpoint_meta.get('preprocess',[]):
            rdata = f(rdata)

        cid = {}  # {namespace: [id]}
        for key in endpoint_meta['map']:
            expr = Path.parse_str(endpoint_meta['map'][key])
            matches = list(expr.match(rdata))
            # print(len(matches))
            for i, match in enumerate(matches):
                # Check if key or value ({*} or [*])
                if '{*}' in key:
                    # Store cid for wildcards
                    #maybe_id = extract_json_leaf(match)
                    if not cid.get(key):
                        cid[key] = []
                    cid[key].append(match.current_value)
                else:
                    # Lookup cid (PrimeKey) from previous iterations
                    wildcardkey = None
                    exprkey = key.split('[*]')[0] + '{*}'
                    if cid.get(exprkey) and len(cid[exprkey]) > i:
                        wildcardkey = cid[exprkey][i]
                    else:
                        if not silent:
                            print(cid)
                            print('Potential no-key %d/%d, %s' %
                                  (i, len(cid[exprkey]), exprkey))
                    resolved_key = interpolate(key, arguments)
                    assign_to_jsonpath(self.data, resolved_key, value=match.current_value, wildcard=wildcardkey)

        if endpoint_meta.get('postprocess', None):
            endpoint_meta['postprocess'](self.data, url, arguments)

        if autosave:
            self.autosave()
        return len(rdata)


stex_map = {
    'name': 'StexAPI',
    'base': 'https://api3.stex.com',
    'method': 'GET',
    'cursor_limit': 999,
    'cursor_stop': 1000000,
    'endpoints': {
        'public/ticker': {
            'map': OrderedDict({
                'token|*|':           'data[*].currency_code',
                'token|*|':           'data[*].market_code',
                'markets{*}':         'data[*].id',
                'markets[*].TokTick': 'data[*].currency_code',
                'markets[*].CurTick': 'data[*].market_code',
                'markets[*].MarkID':     'data[*].id',
                'markets[*].Activity':   'data[*].count',
                'markets[*].Timestamp':  'data[*].timestamp',
            }),
        },
        'public/chart/{MarkID}/{candlesType}?timeStart=1&timeEnd=30832277800': {
            'map': OrderedDict({
                'markets[{MarkID}].candles{*}':           'data[*].time',
                'markets[{MarkID}].candles[*].open':      'data[*].open',
                'markets[{MarkID}].candles[*].close':     'data[*].close',
                'markets[{MarkID}].candles[*].low':       'data[*].low',
                'markets[{MarkID}].candles[*].high':      'data[*].high',
                'markets[{MarkID}].candles[*].volume':    'data[*].volume',
            }),
        },
        'public/trades/{MarkID}?sort=DESC&offset={:cursor}&limit={:len}': {
            'map': OrderedDict({
                'markets[{MarkID}].trades{*}':            'data[*].id',
                'markets[{MarkID}].trades[*].Timestamp':  'data[*].timestamp',
                'markets[{MarkID}].trades[*].Type':       'data[*].type',
                'markets[{MarkID}].trades[*].Amount':     'data[*].amount',
                'markets[{MarkID}].trades[*].Price':      'data[*].price',
            })
        }
    }
}

# tradingview_map = {
#     'name': 'TradingView',
#     'base': 'https://', # No API endpoint available
#     'method': 'GET',
#     'cursor_limit': 999,
#     'cursor_stop': 1000000,
#     'endpoints': {
#         '/symbol_info?group=broker_crypto':  OrderedDict({
#             'token|*|': 'symbol[*]'
#         }),
#         'history?symbol={TokID}&resolution={candlesType}&from=0&to=30832277800': OrderedDict({
#             'markets[{TokID}].candles{*}':            't[*]',
#             'markets[{TokID}].candles[*].open':       'o[*]',
#             'markets[{TokID}].candles[*].close':      'c[*]',
#             'markets[{TokID}].candles[*].low':        'l[*]',
#             'markets[{TokID}].candles[*].high':       'h[*]',
#             'markets[{TokID}].candles[*].volume':     'v[*]',
#         })
#         'authorize?login={username}&password={password}': OrderedDict({
#             'bearertoken': 'd.access_token',
#             'authexpire': 'd.expiration'
#         })
#     }
# }

def pop_last(data, url, arguments):
    if data.get('markets', None):
        c = data['markets'][arguments['MarkID']]['candles']
        d = max(map(lambda x: int(x), c.keys()))
        del c[str(d)]

def candles_ms2s(data, url, arguments):
    if data.get('markets', None):
        c = data['markets'][arguments['MarkID']]['candles']
        for x in list(c.keys()):
            c[int(x)//1000] = c.pop(x)

def denoms_from_step(data, url, arguments):
    if data.get('filters', None):
        def convert(x): return int(np.abs(np.log10(float(x))))
        for x in data['filters']:
            entry = data['filters'][x]
            for y in entry:
                entry[y] = float(entry[y])
                if y in ['lotDenom', 'priceDenom']:
                    entry[y] = convert(entry[y])


def get_binance_base_dict(exchange_name: str) -> dict:
    """
    :exchange_name - takes two types of string 'Binance' or 'Binance.US'
    """
    base_dict = {}
    base_dict['exchange_name'] = exchange_name
    if exchange_name == 'Binance':
        base_dict['name'] = exchange_name + 'API'
        base_dict['base'] = 'https://api.binance.com'
        base_dict['apiAgentCode'] = 'DVCYH86C'
    elif exchange_name == 'Binance.US':
        base_dict['name'] = exchange_name + 'API'
        base_dict['base'] = 'https://api.binance.us'
    else:
        raise NotImplementedError('NOT IMPLEMENTED "%s"' % (exchange_name))

    return base_dict

def pipeline(funcs, *args):
    for f in funcs:
        f(*args)

binance_details = {
    'method': 'GET',
    'cursor_limit': 1000,
    'cursor_stop': 1000000,
    'endpoints': {
        'api/v3/klines?symbol={MarkID}&interval={candlesType}&limit=1000&startTime={:cursor}': {
            'map': OrderedDict({
                'markets[{MarkID}].candles{*}':           '$[*][0]',
                'markets[{MarkID}].candles[*].open':      '$[*][1]',
                'markets[{MarkID}].candles[*].high':      '$[*][2]',
                'markets[{MarkID}].candles[*].low':       '$[*][3]',
                'markets[{MarkID}].candles[*].close':     '$[*][4]',
                'markets[{MarkID}].candles[*].volume':    '$[*][5]',
            }),
            # lambda data, url, arguments
            # TODO: move pop_last to trade_api?
            'postprocess': partial(pipeline, [pop_last, candles_ms2s])
        },
        'api/v3/exchangeInfo': {
            'map': OrderedDict({
                'rateLimits{*}':                    '$.rateLimits[*].rateLimitType',
                'rateLimits[*]._interval':          '$.rateLimits[*].interval',
                'rateLimits[*]._intervalNum':       '$.rateLimits[*].intervalNum',
                'rateLimits[*].limit':              '$.rateLimits[*].limit',

                'filters{*}':                        '$.symbols[*].symbol',
                # 'filter[*].rules':                  '$.symbols[*].filters',
                'filters[*].minLot':                 '$.symbols[*].filters[*][?(@.filterType = "LOT_SIZE")].minQty',
                'filters[*].minNot':                 '$.symbols[*].filters[*][?(@.filterType = "MIN_NOTIONAL")].minNotional',
                'filters[*].lotDenom':               '$.symbols[*].filters[*][?(@.filterType = "LOT_SIZE")].stepSize',
                'filters[*].priceDenom':             '$.symbols[*].filters[*][?(@.filterType = "PRICE_FILTER")].tickSize'
            }),
            'postprocess': denoms_from_step
        }
        # 'exchangeInfo': OrderedDict({
        #     'markets{*}':               'symbols[*].symbol',
        #     'markets[*].TokTick':       'symbols[*].baseAsset',
        #     'markets[*].CurTick':       'symbols[*].quoteAsset'
        # })
    }
}

binance_map = get_binance_base_dict('Binance')
binance_map.update(binance_details)
binance_us_map = get_binance_base_dict('Binance.US')
binance_us_map.update(binance_details)



kraken_map = {
    'name': 'KrakenAPI',
    'exchange_name': 'Kraken',
    'base': 'https://api.kraken.com/0',
    'cursor_limit': 1000,
    'cursor_stop': 1000000,
    'endpoints': {
        'public/OHLC?pair={MarkID}&interval={candlesTypeSeconds}&since={:cursor}': {
            'map': OrderedDict({
                'markets[{MarkID}].candles{*}':           '$.result[@[keys()][0]][*][0]',
                'markets[{MarkID}].candles[*].open':      '$.result[@[keys()][0]][*][1]',
                'markets[{MarkID}].candles[*].high':      '$.result[@[keys()][0]][*][2]',
                'markets[{MarkID}].candles[*].low':       '$.result[@[keys()][0]][*][3]',
                'markets[{MarkID}].candles[*].close':     '$.result[@[keys()][0]][*][4]',
                'markets[{MarkID}].candles[*].volume':    '$.result[@[keys()][0]][*][6]'
            }),
            'postprocess': pop_last
        },
        'public/AssetPairs': {
            'map': OrderedDict({
                'filters{*}':                        '$.result[*][?(@.ordermin)].altname',
                'filters[*].minLot':                 '$.result[*][?(@.ordermin)].ordermin',
                'filters[*].priceDenom':             '$.result[*][?(@.ordermin)].pair_decimals',
                'filters[*].lotDenom':               '$.result[*][?(@.ordermin)].lot_decimals',
            })
        }
    }
}


# TODO: ^-1 it?
supermap = {
    'Binance': binance_map,
    'Binance.US': binance_us_map,
    'Kraken': kraken_map
}

kraken_currency_map = {
    'XETH': 'ETH',
    'XXBT': 'XBT',
    'XXRP': 'XRP',
    'XLTC': 'LTC',
    'XXDG': 'XDG',
    'XXLM': 'XLM',
    'XMLN': 'MLN',
    'XREP': 'REP',

    'ZUSD': 'USD',
    'ZEUR': 'EUR'
}


class TradeAPI:
    hooks = []

    def __init__(self, map, logger=None):
        self.map = map
        self.name = self.map['name']
        self.logger = logger

    def get_logger(self):
        if getattr(self, 'logger', None):
            return self.logger
        logger = logging.getLogger()
        logger.addHandler(logging.NullHandler())
        return logger

    def sign_api(self, api_keys, query={}, body='', headers={}, urlpath=''):
        """
        Sign API call for specific exchange with keys & finalize the query 

        Note: (dict(query) -> str(urlencoded))
        """
        if self.name.startswith('Binance'):
            query = {'recvWindow': 5000, 'timestamp': int((time.time()-2) * 1000), **query}
            qstr = urlencode(query)

            query['signature'] = hmac.new(api_keys[1].encode(), msg=(qstr + body).encode(), digestmod=hashlib.sha256).hexdigest()
            headers['X-MBX-APIKEY'] = api_keys[0]
            return urlencode(query), body, headers
        elif self.name == 'KrakenAPI':
            query['nonce'] = int(time.time()*100)
            headers['API-Key'] = api_keys[0]
            postdata = urlencode(query)

            # Unicode-objects must be encoded before hashing
            encoded = (str(query['nonce']) + postdata).encode()
            message = urlpath.encode() + hashlib.sha256(encoded).digest()
            signature = hmac.new(base64.b64decode(api_keys[1]), message, hashlib.sha512)
            sigdigest = base64.b64encode(signature.digest())
            headers['API-Sign'] = sigdigest.decode()

            return '', urlencode(query), headers
        else:
            raise NotImplementedError("sign_api(): %s ohlc update not implemented" % self.name)

    def update_ohlc(self, market, candle_type, start_time):
        # Create a temporary adapter for data
        api = ApiAdapter(self.map, 'tmp', hooks=self.hooks)

        if self.name.startswith('Binance'):
            market = to_internal_market(market)
            opts = {'candlesType': candle_type, 'MarkID': market, 'cursor_start': int(start_time) * 1000 + 1}
            req = api.updateEndpoint('api/v3/klines?symbol={MarkID}&interval={candlesType}&limit=1000&startTime={:cursor}', opts)
        elif self.name == 'KrakenAPI':
            market = to_internal_market(market)
            candle_decoded = span_from_candletype(candle_type)
            opts = {'MarkID': market, 'candlesTypeSeconds': str(int(candle_decoded / 60)), 'cursor_start': int(start_time) + 1}
            req = api.updateEndpoint('public/OHLC?pair={MarkID}&interval={candlesTypeSeconds}&since={:cursor}', opts)
        if api.data.get('markets'):
            return api.data['markets'][market]['candles']
        else:
            # No update available
            return {}

    def get_balance(self, api_keys):
        api = ApiAdapter(self.map, 'tmp', hooks=self.hooks)
        ret = {}
        if self.name.startswith('Binance'):

            query, body, headers = self.sign_api(api_keys)
            data = api.call('sapi/v1/capital/config/getall?%s' % query, data=body or None, method='GET', headers=headers)
            if data.status_code >= 400:
                return None

            data = data.json()
            if type(data) != list:
                return None
            else:
                for x in data:
                    ret[x['coin']] = float(x['free'])
                return ret

        elif self.name == 'KrakenAPI':
            query, body, headers = self.sign_api(api_keys, urlpath='/0/private/Balance')
            data = api.call('private/Balance', data=body or None, method='POST', headers=headers)

            data = data.json()
            if data['error']:
                return None
            return data.get('result', [])

        else:
            raise NotImplementedError("get_balance(): %s get_balance not implemented" % self.name)

    def check_auth(self, api_keys):
        if self.name.startswith('Binance'):
            return self.get_balance(api_keys) != None
        elif self.name == 'KrakenAPI':
            return self.get_balance(api_keys) != None
        else:
            raise NotImplementedError("check_auth(): %s auth check not implemented" % self.name)
        # Code in conditions is the same but leaving it like this for now in case of extra api-s that work differently

    def get_fill_data(self, json):
        aux_data = {'fee': 0}
        if self.name.startswith('Binance'):
            priceamount = 0.
            if json['status'] != 'FILLED':
                return {'id': json['orderId']}

            for fill in json['fills']:
                if fill['commissionAsset'] in json['symbol']:
                    aux_data['fee_asset'] = fill['commissionAsset']
                    aux_data['fee'] += float(fill['commission'])
                else:
                    # If everything is payed in BNB use that as the asset
                    if 'fee_asset' not in aux_data:
                        aux_data['fee_asset'] = fill['commissionAsset']

                priceamount += float(fill['price']) * float(fill['qty'])
            aux_data['price'] = priceamount / float(json['executedQty'])
            aux_data['amount'] = float(json['executedQty'])
            return aux_data

        elif self.name == 'KrakenAPI':
            aux_data['fee'] = float(json['fee'])
            aux_data['price'] = float(json['price'])
            aux_data['amount'] = float(json['vol_exec'])
            return aux_data

        return None

    def limit_order(self, api_keys, market, side, amount, price):
        api = ApiAdapter(self.map, 'tmp', hooks=self.hooks)
        marketid = to_internal_market(market)
        if self.name.startswith('Binance'):
            # TODO: discuss LIMIT_MAKER
            query = {'symbol': marketid, 'side': side, 'type': 'LIMIT', 'quantity': amount, 'price': price, 'newOrderRespType': 'FULL', 'timeInForce': 'GTC'}
            query, body, headers = self.sign_api(api_keys, query=query)
            print(query)
            req = api.call('api/v3/order', data=query, method='POST', headers=headers)
            self.get_logger().debug(f'Attempted {side} LIMIT trade: {req.text}, {req.reason}, {req.status_code}, {query}, {body}, {headers}')
            if req.status_code < 400:
                return Ok(req.json()['orderId'])
            else:
                json = req.json()
                if json['msg'] == "Account has insufficient balance for requested action.":
                    return Err('insufficient-balance')
                return Err(None)
        elif self.name == 'KrakenAPI':
            query = {'pair': marketid, 'type': side.lower(), 'ordertype': 'limit', 'volume': amount, 'price': price}
            query, body, headers = self.sign_api(api_keys, query=query, urlpath='/0/private/AddOrder')
            req = api.call('private/AddOrder', data=body,  method='POST', headers=headers)
            self.get_logger().debug(f'Attempted {side} LIMIT trade: {req.text}, {req.status_code}, {query}, {body}, {headers}')
            if not req.json()['error']:
                return Ok(req.json()['result']['txid'][0])
            else:
                if "EOrder:Insufficient funds" in req.json()['error']:
                    return Err('insufficient-balance')
                return Err(None)

    def get_limit_details(self, api_keys, market, txid):
        api = ApiAdapter(self.map, 'tmp', hooks=self.hooks)
        marketid = to_internal_market(market)
        if self.name.startswith('Binance'):
            query = {'symbol': marketid, 'orderId': txid}
            query, body, headers = self.sign_api(api_keys, query=query)
            req = api.call(f'api/v3/order?{query}', method='GET', headers=headers)
            self.get_logger().debug(f'Attempted get details on order {txid}: {req.text}, {req.reason}, {req.status_code}, {query}, {body}, {headers}')
            if req.status_code < 400:
                data = req.json()
                out = {}
                out['exec_vol'] = float(data['executedQty'])
                out['exec_frac'] = float(data['executedQty']) / float(data['origQty'])
                out['price'] = float(data['price'])
                out['date'] = int(data['updateTime']) // 1000
                return out
            else:
                return None
        elif self.name == 'KrakenAPI':
            query = {'txid': txid}
            query, body, headers = self.sign_api(api_keys, query=query, urlpath='/0/private/QueryOrders')
            req = api.call('private/QueryOrders', data=body,  method='POST', headers=headers)
            self.get_logger().debug(f'Attempted get details on order {txid}: {req.text}, {req.reason}, {req.status_code}, {query}, {body}, {headers}')
            data = req.json()
            if not data['error']:
                data = data['result']
                # Take first (and hopefully only) order
                data = data[list(data.keys())[0]]
                out = {}
                out['exec_vol'] = float(data['vol_exec'])
                out['exec_frac'] = float(data['vol_exec']) / float(data['vol'])
                out['price'] = float(data['limitprice'])
                # TODO: extract last update date from kraken api
                return out
            else:
                return None

    def cancel_order(self, api_keys, market, txid):
        api = ApiAdapter(self.map, 'tmp', hooks=self.hooks)
        marketid = to_internal_market(market)
        if self.name.startswith('Binance'):
            query = {'symbol': marketid, 'orderId': txid}
            query, body, headers = self.sign_api(api_keys, query=query)
            req = api.call('api/v3/order', data=query, method='DELETE', headers=headers)
            self.get_logger().debug(f'Attempted to cancel on order {txid}: {req.text}, {req.reason}, {req.status_code}, {query}, {body}, {headers}')
            if req.status_code < 400:
                return True
            else:
                return None
        elif self.name == 'KrakenAPI':
            query = {'txid': txid}
            query, body, headers = self.sign_api(api_keys, query=query, urlpath='/0/private/CancelOrder')
            req = api.call('private/CancelOrder', data=body,  method='POST', headers=headers)
            self.get_logger().debug(f'Attempted to cancel on order {txid}: {req.text}, {req.reason}, {req.status_code}, {query}, {body}, {headers}')
            if not req.json()['error']:
                return req.json()['result']['count'] > 0
            else:
                return None

    def market_order(self, api_keys, market, side, amount):
        api = ApiAdapter(self.map, 'tmp', hooks=self.hooks)
        marketid = to_internal_market(market)
        if self.name.startswith('Binance'):
            query = {'symbol': marketid, 'side': side, 'type': 'MARKET', 'quantity': amount, 'newOrderRespType': 'FULL'}
            query, body, headers = self.sign_api(api_keys, query=query)
            req = api.call('api/v3/order', data=query, method='POST', headers=headers)
            self.get_logger().debug(f'Attempted {side} MARKET trade: {req.text}, {req.status_code}, {query}, {body}, {headers}')
            if req.status_code < 400:
                aux = self.get_fill_data(req.json())
                order = Order(self.name, marketid, req.json()['transactTime'], side, 'MARKET', aux['price'], aux['fee'], amount, aux['fee_asset'])
                return Ok(order)
            else:
                json = req.json()
                if json['msg'] == "Account has insufficient balance for requested action.":
                    return Err('insufficient-balance')
                return Err(None)
        elif self.name == 'KrakenAPI':
            query = {'pair': marketid, 'type': side.lower(), 'ordertype': 'market', 'volume': amount}
            query, body, headers = self.sign_api(api_keys, query=query, urlpath='/0/private/AddOrder')
            req = api.call('private/AddOrder', data=body,  method='POST', headers=headers)
            self.get_logger().debug(f'Attempted {side} MARKET trade: {req.text}, {req.status_code}, {query}, {body}, {headers}')
            if not req.json()['error']:
                txid = req.json()['result']['txid'][0]
                query = {'txid': txid, 'trades': True}
                query, body, headers = self.sign_api(api_keys, query=query, urlpath='/0/private/QueryOrders')
                req = api.call('private/QueryOrders', data=body,  method='POST', headers=headers)

                order_time = req.json()['result'][txid]['opentm']
                tradeid = req.json()['result'][txid]['trades'][0]
                aux = self.get_fill_data(req.json()['result'][txid])
                # Ledger
                query = {'start': order_time}
                query, body, headers = self.sign_api(api_keys, urlpath='/0/private/Ledgers')
                req = api.call('private/Ledgers', data=body,  method='POST', headers=headers)
                time = 0

                if not req.json()['error']:
                    d = req.json()['result']['ledger']
                    ledges = filter(lambda k: d[k]['refid'] == tradeid and float(d[k]['fee']) != 0, d)
                    ledge = d[next(ledges)]
                    aux['fee'] = float(ledge['fee'])
                    aux['fee_asset'] = ledge['asset']
                    time = ledge['time']

                order = Order(self.name, marketid, time * 1000, side.lower(), 'market', aux['price'], aux['fee'], amount, aux['fee_asset'])
                return Ok(order)
            else:
                if "EOrder:Insufficient funds" in req.json()['error']:
                    return Err('insufficient-balance')
                return Err(None)
        else:
            raise NotImplementedError(
                "market_order(): %s market order not implemented" % self.name)

    def get_portfolio(self, balance):
        api = ApiAdapter(self.map, 'tmp', hooks=self.hooks)
        portfolio = 0
        currencies = balance.keys()
        if self.name.startswith('Binance'):
            req = api.call('api/v3/ticker/price', method='GET')
            data = req.json()
            btc_portfolio = 0
            current_price = 0

            for curr in currencies:
                if curr == 'USDT':
                    portfolio += balance.get('USDT', 0)
                    continue
                if curr == 'BTC':
                    btc_portfolio += balance.get('BTC', 0)
                    continue
                for ticker in data:
                    symbol = ticker.get('symbol', '')
                    if symbol == 'BTCUSDT':
                        current_price = float(ticker.get('price', 0))
                    elif curr + 'BTC' == symbol:
                        btc_portfolio += float(ticker.get('price', 0)) * balance[curr]
                    elif 'BTC' + curr == symbol:
                        btc_portfolio += balance[curr] / float(ticker.get('price', 0))
            portfolio += btc_portfolio * current_price

        elif self.name == 'KrakenAPI':
            portfolio += balance.get('USD', 0)
            query = []
            for curr in currencies:
                if curr == 'KFEE':
                    continue
                if curr in kraken_currency_map:
                    curr = kraken_currency_map[curr]
                query.append(curr + 'USD')

            req = api.call('public/Ticker?pair=%s' % ','.join(query), method='GET')
            temp = req.json().get('result', None)
            if not temp:
                return 0

            for curr in currencies:
                if curr == 'KFEE':
                    continue
                flag = False
                for tcurr in [curr, kraken_currency_map.get(curr, None)]:
                    for fcurr in ['USD', 'ZUSD']:
                        price = temp.get(tcurr + fcurr, None)
                        if price:
                            portfolio += float(price['p'][0]) * balance[curr]
                            flag = True
                            break
                    if flag:
                        break

        return portfolio

    def get_filters(self):
        api = ApiAdapter(self.map, 'tmp', hooks=self.hooks)
        if self.name.startswith('Binance'):
            api.updateEndpoint('api/v3/exchangeInfo')
        elif self.name == 'KrakenAPI':
            api.updateEndpoint('public/AssetPairs')

        return api.data['filters']


class Order:
    def __init__(self, exchange, pair, date, side, order_type, price, fee, amount, fee_asset):
        self.exchange = exchange
        self.pair = pair
        self.date = date / 1000
        self.type = side
        self.order_type = order_type
        self.price = price
        self.fee = fee
        self.amount = amount
        self.fee_asset = fee_asset
        if self.exchange.startswith('Kraken') and self.fee_asset in kraken_currency_map:
            self.fee_asset = kraken_currency_map[self.fee_asset]
    
    def get_raw_fee(self):
        return (self.fee_asset, self.fee)

    def get_fee(self):
        """
        Converted to currency
        """
        if self.fee_asset not in self.pair:
            return 0

        if self.fee_asset == 'KFEE':
            return self.fee / 100

        if self.pair.startswith(self.fee_asset):
            return self.fee * self.price
        else:
            return self.fee

    def get_dict(self):
        result = copy.deepcopy(self.__dict__)
        del result['exchange']
        return result

    def get_tok_diff(self):
        amount = self.amount

        if self.type.lower() == 'sell':
            amount *= -1

        if self.pair.startswith(self.fee_asset):
            amount -= self.fee

        return amount

    def get_cur_diff(self):
        amount = self.amount * self.price

        if self.type.lower() == 'buy':
            amount *= -1

        if self.pair.endswith(self.fee_asset):
            amount -= self.fee

        return amount

    def __repr__(self):
        return repr(self.get_dict())
