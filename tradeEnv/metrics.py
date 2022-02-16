import datetime
import time
import numpy as np
import pyti
import json
import uuid
import copy
import logging
from typing import List, Dict
from .trade_api import TradeAPIRegistry
from .maths import *
from .markets import Market
from .utils import candletype_from_span, to_internal_market, span_from_candletype, EventEmitter
from .meta import looseclass
from routes.logging import getLogger


def filter_kwargs(kwargs, keys=[]):
    return {key: value for key, value in kwargs.items() if key in keys}


"""
Dev notes:
The pyti ported indicators often follow the pattern:
`ind(gw(n*2,...), n)[-n:]`
This is to ensure pyti has enough samples to calculate and return reliable results, as they aren't tuned in the same way as our own implementations.
"""


class TechnicalIndicators:
    """
    standard parameters:
        n:          int             (amount of samples to use, exact definition changes with function)
        offset:     int or None     (offset calculation by `k` timesteps)
        dkey:       str             ('close', 'open', etc; might not work for all indicators)
        return_all: bool            (returns array of full window data; might be ignored in some cases)

    All oscillators are bound between 0. & 1.
    """

    def __init__(self, env):
        self.env = env

    # Get Window helper
    def _gw(self, window, **kwargs):
        return self.env.get_window(window, **filter_kwargs(kwargs, ['offset', 'dkey']))

    # Return all helper
    def _ra(self, data, **kwargs):
        if kwargs.get('return_all', False) is True:
            return data
        else:
            return data[-1]

    def ema(self, n=14, alpha=.5, **kwargs):
        data = ewma_vectorized_safe(self._gw(n, **kwargs), alpha)
        return self._ra(data, **kwargs)

    def macd(self, long_window=26, short_window=12, **kwargs):
        # TODO: Refactor? a=.5?
        short_alpha = ema_days2alpha(short_window)
        long_alpha = ema_days2alpha(long_window)

        data = self._gw(long_window, **kwargs)
        short_data = ewma_vectorized_safe(data, short_alpha)
        long_data = ewma_vectorized_safe(data, long_alpha)
        # print(short_data.shape, long_data.shape, data.shape, long_window, short_window)
        macd_data = short_data - long_data
        return self._ra(macd_data, **kwargs)

    def macd_signal(self, signal_window=9, long_window=26, short_window=12, **kwargs):
        signal_alpha = ema_days2alpha(signal_window)  # TODO: change a
        macd_data = self.macd(long_window, short_window,
                              **{'return_all': True, **kwargs})
        signal = ewma_vectorized_safe(macd_data, signal_alpha)
        return self._ra(signal, **kwargs)

    def aroon_down(self, n=14, **kwargs):
        data = self._gw(n*2, **kwargs)
        data = pyti.aroon_down(data, n)[-n:]
        return self._ra(data, **kwargs)

    def aroon_up(self, n=14, **kwargs):
        data = self._gw(n*2, **kwargs)
        data = pyti.aroon_up(data, n)[-n:]
        return self._ra(data, **kwargs)

    def aroon_osc(self, n=14, **kwargs):
        data = self._gw(n*2, **kwargs)
        data = pyti.aroon_oscillator(data, n)[-n:] / 100.
        return self._ra(data, **kwargs)

    def rsi(self, n=14, **kwargs):
        data = rsi(self._gw(n*2+1, **kwargs), n)
        return self._ra(data, **kwargs)

    def william_r(self, n=14, **kwargs):
        data = pyti.williams_percent_r(self._gw(
            n*2, dkey='high'), self._gw(n*2, dkey='low'), self._gw(n*2, dkey='close'), n)[-n:]
        return self._ra(data, **kwargs)

    def true_range(self, n=14, **kwargs):
        data = pyti.true_range(self._gw(n*2, **kwargs), n)[-n:]
        return self._ra(data, **kwargs)

    def volatility(self, n=14, **kwargs):
        data = pyti.volatility(self._gw(n*2, **kwargs), n)[-n:]
        return self._ra(data, **kwargs)

    def hull_ma(self, n=14, **kwargs):
        data = pyti.hull_moving_average(self._gw(n*2, **kwargs), n)[-n:]
        return self._ra(data, **kwargs)

    def chande_osc(self, n=14, **kwargs):
        data = pyti.chande_momentum_oscillator(self._gw(n*2, n), n)[-n:] / 100.
        return self._ra(data, **kwargs)

    def detrended_osc(self, n=14, **kwargs):
        data = pyti.detrended_price_oscillator(self._gw(n*2, n), n)[-n:] / 100.
        return self._ra(data, **kwargs)

    # Note: should be pretty close to an EMA a = 1/(n * 2 - 1)
    def triangular_ma(self, n=14, **kwargs):
        data = pyti.triangular_moving_average(self._gw(n*2+1, **kwargs), n)[-n:]
        return self._ra(data, **kwargs)

    # TODO: fixup pyti to remove const variables (ultimate_oscillator etc)

    """
    NOTE: do not support return_all
    """

    # True strength
    def tsi(self, **kwargs):
        data = pyti.true_strength_index(self._gw(40, **kwargs))
        return data[-1]

    def money_flow(self, **kwargs):
        return pyti.money_flow(self.env.current_v(), self.env.current_v('high'), self.env.current_v('low'), self.env.current_v('volume'))

    def stoch_rsi(self, n=14, **kwargs):
        data = self.rsi(n=n, return_all=True, **kwargs)
        return (data[-1] - min(data)) / (max(data) - min(data))

    def lwma(self, window=26, **kwargs):
        view = self._gw(window, **kwargs)
        return np.average(view, weights=np.arange(1, view.shape[0]+1, 1))

    # du/dt -> LWMA
    def d_lwma(self, window=26, **kwargs):
        view = self._gw(window, **kwargs)
        return np.average(np.ediff1d(view), weights=np.arange(1, view.shape[0], 1))

    def moving_avg(self, window=26, **kwargs):
        return np.mean(self._gw(window, **kwargs))

    # Average differential
    def d_moving_avg(self, window=26, **kwargs):
        dmav = np.mean(np.ediff1d(self._gw(window, **kwargs))) + \
            1e-6  # avoid zero
        return dmav


class LimitOrder:
    side: str
    volume: float
    price: float
    createtime: int
    expire_time: int
    org_vol: float
    txid: str = None
    order_type = 'LIMIT'

    def __init__(self, side, volume, price, createtime, expire_time, txid=None):
        self.side = side
        self.org_vol = volume
        self.volume = volume
        self.price = price
        self.createtime = createtime
        self.expire_time = expire_time
        self.txid = txid
        self.order_type = self.__class__.order_type


class StopLossOrder(LimitOrder):
    order_type = 'STOPLOSS'


@looseclass
class UserMetrics:
    """Structure for storing user/bot/session data"""
    uid: str
    curBalance: float
    tokBalance: float
    max_balance: float
    min_balance: float
    last_trade_attempt: int = None
    portfolioValue: float = 0.01
    in_fees: float = 0.0
    startingBalance: List[float] = [0, 3000]
    trade_log: List[Dict] = []
    open_orders: Dict[str, LimitOrder] = {}

    def __init__(self, uid: str, startingBalance: List[float] = [0, 3000]):
        self.uid = uid
        self.startingBalance = startingBalance
        self.curBalance = startingBalance[1]
        self.tokBalance = startingBalance[0]
        self.last_trade_attempt = None
        self.portfolioValue = startingBalance[1]
        self.max_balance = 0
        self.min_balance = 1e9
        self.trade_log = []
        self.in_fees = 0
        self.open_orders = {}

    def to_json(self):
        if not getattr(self, 'open_orders', None):
            self.open_orders = {}

        x = copy.deepcopy(self.__dict__)
        # When exporting to users ignore negatives (assumes small negative balances because of exchange differences)
        x['curBalance'] = max(0, x['curBalance'])
        x['tokBalance'] = max(0, x['tokBalance'])
        x['open_orders'] = dict([(k, x['open_orders'][k].__dict__) for k in list(x['open_orders'].keys())])

        return x


class MarketInfo(Market):
    exchange: str
    candle_type: str
    last_state: Dict[str, float]
    historical: Dict[str, np.ndarray]

    def __init__(self, market, candle_type, last_state, historical, exchange=None):
        super().__init__()
        self.market = market
        self.candle_type = candle_type
        self.last_state = last_state
        self.historical = historical
        self.exchange = exchange

    def update(self, get_state=False):
        if not hasattr(self, 'exchange') or self.exchange is None:
            self.exchange = 'Binance'

        logger = getLogger('metrics.update')
        api = TradeAPIRegistry[self.exchange](logger)
        candles = api.update_ohlc(self.market, self.candle_type, self.historical.get('time', [0])[-1])
        if candles.is_err():
            return False, False

        candles = candles.unwrap()

        if len(candles) > 0:
            self.historical['time'] = np.hstack((self.historical.get('time', np.array([], dtype=np.long)), [int(float(x)) for x in candles]))
            for dkey in candles[max(candles.keys())]:
                self.historical[dkey] = np.hstack((self.historical.get(dkey, []), [float(candles[t][dkey]) for t in candles]))

        # TODO: get market state
        # Signal whether data was updated
        return len(candles) > 0, False

    @staticmethod
    def from_download(market, candle_type, exchange='Binance'):
        mi = MarketInfo(market, candle_type, {}, {}, exchange=exchange)
        mi.update()
        return mi

    @staticmethod
    def from_api_adapter(api):
        marketid = list(api.data['markets'].keys())[0]
        candles = api.data['markets'][marketid]['candles']

        historical = {'time': np.array([int(x) for x in candles], dtype=np.long)}
        for dkey in candles[max(candles.keys())]:
            historical[dkey] = np.array([float(candles[t][dkey]) for t in candles], dtype=np.float32)

        time_diff = int(historical['time'][1] - historical['time'][0])
        candle_type = candletype_from_span(time_diff)
        return MarketInfo(marketid, candle_type, {}, historical)

    def to_json(self):
        return json.dumps({'markets': {to_internal_market(self.market): {'candles': self.ohlc_json()}}})

    def historical_to_dict(self, price_info='close'):
        return {k: v for k, v in zip(list(self.historical.get('time', [])), list(self.historical.get(price_info, self.historical.get('close', []))))}


@looseclass
class SimulMetrics(EventEmitter):
    ti: TechnicalIndicators
    mi: MarketInfo
    indexstep: int = 0

    def __init__(self, api=None, indexstep: int = 0, startingBalance: List[float] = [0, 3000], fee=0.999, limit=10, mi: Market = None, logger=None):
        super().__init__()
        self.api = api
        self.fee = fee
        self.limit = limit
        if mi:
            self.mi = mi
        elif self.api:
            self.mi = MarketInfo.from_api_adapter(self.api)

        self.user = UserMetrics('sim', startingBalance)

        self.ti = TechnicalIndicators(self)
        self.debug = False
        self.logger = logger

        self.indexstep = indexstep
        self.max_buy = 1.0
        self.reset(indexstep, startingBalance=startingBalance)

    def reset(self, new_indexstep=None, startingBalance: List[float] = [0, 3000]):
        """Reset simulation with certain start conditions"""
        self.user.startingBalance = startingBalance
        self.user.curBalance = startingBalance[1]
        self.user.tokBalance = startingBalance[0]
        self.user.max_balance = 0
        self.user.min_balance = 1e9
        self.user.trade_log = []
        if new_indexstep:
            self.indexstep = new_indexstep

        if hasattr(self, 'mi'):
            self.timestep = self.mi.historical['time'][self.indexstep]

    # ====== Data manipulation ======
    def __len__(self):
        return len(self.mi)

    def get_logger(self):
        if getattr(self, 'logger', None):
            return self.logger
        logger = logging.getLogger()
        logger.addHandler(logging.NullHandler())
        return logger

    def portfolioValue(self):
        """Get portfolio value in the main currency of the market"""
        openBalance = 0
        price = self.current_v()
        if getattr(self.user, 'open_orders', None):
            for x in self.user.open_orders:
                y = self.user.open_orders[x]
                if y.side.upper() == 'SELL':
                    # Use current price as the order-price is not the actual value
                    openBalance += y.volume * price
                elif y.side.upper() == 'BUY':
                    # Use order price if it was used to calculate the volume
                    openBalance += y.volume * y.price
        return self.user.curBalance + (price * self.user.tokBalance) + openBalance

    def step(self):
        """Go to next sample in data"""
        self.indexstep += 1
        return self.nstep()

    def get_timestep(self, index=None):
        """Get current time in seconds"""
        if index is None:
            index = self.indexstep
        ct = max(0, index)
        return int(self.mi.historical['time'][ct])

    def time_at_relative_candle(self, delta_candles=1):
        return self.get_timestep() + self.mi.get_candle_period() * delta_candles

    def get_formatted_time(self, index=None):
        """Get current datetime as a ISO formatted datetime"""
        if index is None:
            index = self.indexstep
        return datetime.datetime.fromtimestamp(self.get_timestep(index)).strftime('%Y-%m-%d %H:%M:%S')

    # e.g. sum = lambda x, p, s : x + p
    # Deprecated use numpy + get_window
    def _reduce(self, op, window, offset, vtype):
        s = 0  # Store
        p = 0  # Prev
        for t in range(window, -1, -1):
            # Define which sample to use
            current = self.current_v(
                vtype=vtype, indexstep=self.indexstep - t - offset)
            s = op(current, p, s)
            p = current
        return s

    def current_v(self, vtype='close', indexstep=None):
        ct = max(0, indexstep) if indexstep else self.indexstep
        return float(self.mi.historical[vtype][ct])

    def get_view(self, dkey='close'):
        """Get all samples"""
        return self.mi.historical[dkey][:self.indexstep]

    def get_window(self, window, offset=None, dkey='close'):
        # TODO: check if offset is correct (cross-reference get_view())
        return self.mi.get_window(window, offset=-self.indexstep, dkey=dkey)

    def _on_trade(self, **kwargs):
        balance = self.portfolioValue()
        if len(self.user.trade_log) > 0:
            old_balance = self.user.trade_log[-1]['balance']
            # eps for numerical stability
            change = (balance - old_balance + eps) / (old_balance + eps)
        else:
            change = 0
        info = {'date': self.get_timestep(), 'price': self.current_v(), **kwargs, 'balance': balance, 'change': change}
        self.emit('trade:filled', info)
        if kwargs.get('order_type') == StopLossOrder.order_type:
            self.emit('trade:stoploss', info, self)

        # self.user.trade_log.append(info)

    # ======== Market Orders ==============
    # Options: percent, points (amount when converted to currency), raw
    def sell(self, value, dtype='percent'):
        """Proxy for different sell variants"""
        type_map = {'percent': self.sellp,
                    'points': self._sell_points, 'raw': self._sell}
        return type_map[dtype](value)

    def buy(self, value, dtype='percent'):
        """Proxy for different buy variants"""
        type_map = {'percent': self.buyp,
                    'points': self._buy, 'raw': self._buy}
        return type_map[dtype](value)

    def _sell_points(self, amount):
        """Sell tokens in amount of currency"""
        return self._sell(amount / self.current_v())

    def _sell(self, amount):
        """Sell amount of tokens"""
        amount = max(0, min(self.user.tokBalance, amount))
        if amount * self.current_v() < self.limit:
            return None

        namount = amount * self.current_v() * self.fee
        self._on_trade(type="sell", amount=amount)
        self.user.in_fees += (1 - self.fee) * self.current_v() * amount
        self.user.curBalance += (amount * self.current_v()) * self.fee
        self.user.tokBalance -= amount
        return namount

    def _buy(self, amount):
        """Buy for amount of currency"""
        amount = max(0, min(self.user.curBalance, amount))
        if amount < self.limit:
            return None

        namount = amount * self.fee
        self._on_trade(type="buy", amount=amount / self.current_v())
        self.user.in_fees += (1 - self.fee) * amount
        self.user.tokBalance += (amount / self.current_v()) * self.fee
        self.user.curBalance -= amount
        return namount

    def sellp(self, percentage):
        """Sell a percentage of available balance"""
        if percentage < 0.01:
            return None

        total = self.user.tokBalance * percentage
        return self._sell(total)

    def buyp(self, percentage):
        """Buy a percentage of available balance"""
        if percentage < 0.01:
            return None

        percentage = min(self.max_buy, percentage)
        total = self.user.curBalance * percentage
        return self._buy(total)

    # ========= Limit orders ================

    def sell_limitp(self, percentage, price, endtime=None):
        volume = self.user.tokBalance * percentage
        self.get_logger().info(f'selling {percentage:5.3f}% ({volume}) at {price}')
        return self.sell_limit(volume, price, endtime)

    def buy_limitp(self, percentage, price, endtime=None):
        volume = (self.user.curBalance * percentage) / price
        self.get_logger().info(f'buying {percentage:5.3f}% ({volume}) at {price}')
        return self.buy_limit(volume, price, endtime)

    def sell_limit(self, volume, price, endtime=None):
        # print(volume, self.user.tokBalance)
        volume = max(0, min(self.user.tokBalance, volume))
        return self._trade_limit('sell', volume, price, endtime)

    def buy_limit(self, volume, price, endtime=None):
        # print(volume, self.user.curBalance)
        volume = max(0, min(self.user.curBalance / price, volume))
        return self._trade_limit('buy', volume, price, endtime)

    def _stop_order(self, volume, price, endtime=None):
        if volume * price < self.limit:
            return None

        self.user.tokBalance -= volume
        txid = str(uuid.uuid4())
        if not endtime:
            endtime = self.get_timestep() + self.mi.get_candle_period()
        self.user.open_orders[txid] = StopLossOrder('sell', volume, price, self.get_timestep(), endtime)
        return txid

    def _trade_limit(self, side, volume, price, endtime=None):
        if not getattr(self.user, 'open_orders', None):
            self.user.open_orders = {}

        cv = volume * price
        if cv < self.limit:
            return None  # f'limit {cv} ({side}, {volume}, {price}, {self.user.curBalance}, {self.user.tokBalance})'

        if side == 'buy':
            self.user.curBalance -= cv
        elif side == 'sell':
            self.user.tokBalance -= volume
        else:
            raise NotImplementedError('Invalid side')

        # Dummy txid
        txid = str(uuid.uuid4())
        if not endtime:
            endtime = self.get_timestep() + self.mi.get_candle_period()
        self.user.open_orders[txid] = LimitOrder(side, volume, price, self.get_timestep(), endtime)
        return txid

    def _fill_limit(self, txid, delta_fill=None, abs_fill=None, date=None):
        if txid not in self.user.open_orders:
            return False

        assert delta_fill != None or abs_fill != None, "fill_limit requires either delta_fill or abs_fill"

        tx = self.user.open_orders[txid]
        if abs_fill != None:
            #(tx.org_vol - tx.vol) - (tx.org_vol - abs_fill)
            delta_fill = abs_fill - (tx.org_vol - tx.volume)

        if tx.volume < delta_fill:
            self.get_logger().warn(f"WARN: stored {tx.volume} volume lower than {delta_fill} filled")

        if delta_fill < 0:
            self.get_logger().warn(f"WARN: stored delta_fill < 0 ({delta_fill})")

        tx.volume = tx.volume - delta_fill

        if tx.side == 'buy':
            self.user.tokBalance += delta_fill * self.fee
        elif tx.side == 'sell':
            self.user.curBalance += delta_fill * tx.price * self.fee

        # Transaction done
        if tx.volume <= 1e-8:
            self._on_trade(type=tx.side, amount=tx.org_vol, price=tx.price, order_type=getattr(tx, 'order_type', 'LIMIT'), date=int(date or time.time()))
            self.user.in_fees += tx.org_vol*(1-self.fee)*tx.price
            del self.user.open_orders[txid]
            return True

        return False

    def _reject_limit(self, txid, date=None):
        if txid not in self.user.open_orders:
            return

        tx = self.user.open_orders[txid]

        # Create a trade for the partially filled order
        vol = tx.org_vol - tx.volume
        if vol > 1e-8:
            self._on_trade(type=tx.side, amount=vol, price=tx.price, order_type=getattr(tx, 'order_type', 'LIMIT'), date=int(date or time.time()))
            self.user.in_fees += vol*(1-self.fee)*tx.price

        # Revert balance deduction
        if tx.side == 'buy':
            self.user.curBalance += tx.volume * tx.price
        elif tx.side == 'sell':
            self.user.tokBalance += tx.volume
        else:
            raise NotImplementedError("Invalid side")

        del self.user.open_orders[txid]

    def check_limit_orders(self):
        high = self.current_v(vtype='high')
        low = self.current_v(vtype='low')
        if getattr(self.user, 'open_orders', None):
            for x in list(self.user.open_orders.keys()):
                y = self.user.open_orders[x]
                if type(y) == StopLossOrder:
                    if low < y.price:
                        self._fill_limit(x, delta_fill=y.volume, date=self.get_timestep())

                        self.emit("trade:stoploss", y, self)
                    elif y.expire_time <= self.timestep:
                        self._reject_limit(x, date=y.expire_time)
                else:
                    if (y.side == 'buy' and low <= y.price) or (y.side == 'sell' and high >= y.price):
                        self._fill_limit(x, delta_fill=y.volume, date=self.get_timestep())

                    if y.expire_time <= self.timestep:
                        self._reject_limit(x, date=y.expire_time)
                        continue

    def nstep(self):
        """no-step, step without changing the index"""
        self.timestep = self.mi.historical['time'][self.indexstep]

        # Process limit orders
        self.check_limit_orders()

        # Update user info
        portVal = self.portfolioValue()
        if portVal > self.user.max_balance:
            self.user.max_balance = portVal
        if portVal < self.user.min_balance:
            self.user.min_balance = portVal

        self.user.portfolioValue = self.portfolioValue()
        return self.indexstep < len(self.mi.historical['time']) - 1
