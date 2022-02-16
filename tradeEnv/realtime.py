import re
from tradeEnv.trade_api import AbstractTradeAPI, TradeAPIRegistry
from .metrics import SimulMetrics, UserMetrics, MarketInfo, LimitOrder, StopLossOrder
from .maths import eps
from .trade_filters import init_filter
from .utils import EventEmitter, to_internal_market
from result import Ok, Result
from typing import List
import time
import uuid


def mut_on_trade(user: UserMetrics, kwargs):
    """
    Standalone alternative to SimuMetrics._on_trade \n
    Mutates `bot`, requires manual *put* to the database
    """
    if len(user.trade_log) > 0:
        old_balance = user.trade_log[-1]['balance']
        # eps for numerical stability
        change = (user.portfolioValue - old_balance + eps) / (old_balance + eps)
    else:
        change = 0

    info = {**kwargs, 'balance': user.portfolioValue, 'change': change}
    user.trade_log.append(info)


def mut_trade(bot: dict, keys: List[str], volume: float, side: str, put_tradelog=False, tapi=None, logger=None) -> Result[dict, str]:
    """
    Standalone alternative to RealTimeEnv.{_sell, _buy} \n
    bot contains {market, exchange, state: UserMetrics, ...}  \n
    Mutates `bot`, requires manual *put* to the database
    """
    assert side.lower() in ['buy', 'sell'], "Use `sell` or `buy` as side"
    # Can't check price for limits, we'll have to rely on the exchange to reject it

    user = bot['state']
    exchange = bot['exchange']

    api = tapi or TradeAPIRegistry[exchange](logger=logger)
    aux = api.market_order(keys, bot['market'], side.upper(), volume)

    if aux.is_err():
        return aux

    aux = aux.unwrap()
    price = aux.price
    fee = aux.get_fee()
    trade_dict = aux.get_dict()
    user.tokBalance += aux.get_tok_diff()
    user.curBalance += aux.get_cur_diff()
    user.in_fees += fee 
    
    trade_dict = {'type': side.lower(), 'amount': aux.amount, 'date': int(time.time()), **trade_dict}
    if put_tradelog:
        mut_on_trade(user, trade_dict)
    return Ok(trade_dict)


class RealTimeEnv(SimulMetrics):
    def __init__(self, mi: MarketInfo, trade_api: AbstractTradeAPI = None):
        super().__init__()
        self.mi = mi
        self.indexstep = len(self.mi.historical['time'])-1
        self.tapi = trade_api # Created on-demand if not defined
        self.make_trades = True
        self.debug = True

    def update(self):
        res = self.mi.update(get_state=True)
        self.indexstep = len(self.mi.historical['time'])-1   
        self.timestep = self.mi.historical['time'][self.indexstep]
        return res

    def set_user(self, user: UserMetrics, profile):
        self.profile = profile
        self.user = user
    
    def get_view(self, dkey='close'):
        return self.mi.historical[dkey]

    def _on_trade(self, **kwargs):
        # Fill in the approximate fee for limit orders
        if 'fee' not in kwargs and 'amount' in kwargs and 'price' in kwargs:
            kwargs['fee'] = kwargs['amount']*(1-self.fee)*kwargs['price']
            kwargs['fee_asset'] = self.mi.market.split(':')[-1]
        super()._on_trade(**kwargs)

    def get_api(self) -> AbstractTradeAPI:
        if getattr(self, 'tapi', None):
            return self.tapi
        exchange = self.mi.exchange
        return TradeAPIRegistry[exchange](logger=self.get_logger())

    def _stop_order(self, volume, price, endtime=None):
        tfilter = init_filter(self.mi.exchange, to_internal_market(self.mi.market))
        res = tfilter.preprocess_trade(volume, self.current_v(), price=price)
        if res.is_err():
            self.get_logger().warn(res.err())
            return None

        volume, price = res.unwrap()
        
        if self.make_trades:
            exchange = self.mi.exchange
            api = self.get_api()
            param = dict(api_keys=self.profile['exchanges'][exchange],market= self.mi.market,amount= volume,stop_price = price)
            if api._map.get('apiAgentCode'):
                param['clientOrderId'] = api.clientOrderId

            txid = api.stoploss_order(**param)
            if txid.is_err():
                err_ctx = {'type': 'STOPLOSS', 'exchange': exchange, 'side': 'sell', 'volume': self.user.curBalance, 'price': price, 'uid': self.user.uid, 'market': self.mi.market, 'asset': self.mi.market.split(':')[1]}
                return self.handle_error(txid, err_ctx,param.get('clientOrderId'))

            txid = txid.unwrap()
            if not endtime:
                endtime = self.get_timestep() + self.mi.get_candle_period() * 2
            self.user.tokBalance -= volume
            self.user.open_orders[txid] = StopLossOrder('sell', volume, price, int(time.time()), endtime, txid=txid)
            return txid 
        else:
            return super()._stop_order(volume, price, endtime=endtime)

    def _trade_limit(self, side, volume, price, endtime=None):
        assert side in ['buy', 'sell'], "Only buy & sell limit orders supported"
        
        tfilter = init_filter(self.mi.exchange, to_internal_market(self.mi.market))
        res = tfilter.preprocess_trade(volume, self.current_v(), price=price)
        if res.is_err():
            self.get_logger().warn(res.err())
            return None

        volume, price = res.unwrap()

        txid = None
        exchange = self.mi.exchange

        # Bots.IO only deals in percentages
        exchange_vol = None
        if exchange == 'BotsIO':
            if side == 'buy':
                exchange_vol = (volume / (self.user.curBalance + eps)) * 100
                if exchange_vol < 1:  # 1%
                    return None
            elif side == 'sell':
                exchange_vol = (volume / (self.user.tokBalance + eps)) * 100
                if exchange_vol < 1: # 1%
                    return None

        api = self.get_api()

        param = dict(api_keys= self.profile['exchanges'][exchange], market= self.mi.market, side=side.upper(),amount= exchange_vol or volume, price =price)
        if api._map.get('apiAgentCode'):
            param['clientOrderId'] = api.clientOrderId

        if side == 'buy':
            if self.make_trades:
                txid = api.limit_order(**param)
                if txid.is_err():
                    err_ctx = {'type': 'limit', 'exchange': exchange, 'side': side, 'volume': self.user.curBalance, 'price': price, 'uid': self.user.uid, 'market': self.mi.market, 'asset': self.mi.market.split(':')[1]}
                    return self.handle_error(txid, err_ctx,param.get('clientOrderId'))
                txid = txid.unwrap()
            self.user.curBalance -= volume * price
        elif side == 'sell':
            if self.make_trades:
                txid = api.limit_order(**param)
                if txid.is_err():
                    err_ctx = {'type': 'limit', 'exchange': exchange, 'side': side, 'volume': volume, 'price': price, 'uid': self.user.uid, 'market': self.mi.market, 'asset': self.mi.market.split(':')[0]}
                    return self.handle_error(txid, err_ctx,param.get('clientOrderId'))
                txid = txid.unwrap()
            self.user.tokBalance -= volume

        _txid = txid or str(uuid.uuid4())
        if not endtime:
            # TODO(doc): get_timestamp gives the start of the candle not the end
            endtime = self.get_timestep() + self.mi.get_candle_period() * 2
        self.user.open_orders[_txid] = LimitOrder(side, volume, price, int(time.time()), endtime, txid=txid)
        return _txid

    def handle_error(self, err, err_ctx,clientOrderId=None):
      
        err_type = err.err()
        self.get_logger().warn(f"{err_type}, {err_ctx}")
        err_ctx['error'] = err_type
        # TODO: handle all errors\
        if clientOrderId:
            self.emit(f"trade:clientOrderId-fail",cid=clientOrderId,err_type=err_type,err_ctx=err_ctx)
        self.emit(f'trade:{err_type}', err_ctx)
        self.emit('trade:fail-exchange', err_ctx)
        
        return None

    

    def check_limit_orders(self):
        exchange = self.mi.exchange
        api = self.get_api()

        if getattr(self.user, 'open_orders', None):
            for x in list(self.user.open_orders.keys()): # copy list
                y = self.user.open_orders[x]
                if y.txid:
                    o = api.limit_details(self.profile['exchanges'][exchange], self.mi.market, y.txid)
                    o = o.unwrap()
                    # Allow fractional completion status of absolute is not available
                    if o.get('exec_vol', None) is None:
                        o['exec_vol'] = y.volume * o['exec_frac']

                    finished = self._fill_limit(x, abs_fill=o['exec_vol'], date=o.get('date', None))

                    if not finished and y.expire_time <= self.timestep:
                        r = api.cancel_order(self.profile['exchanges'][exchange], self.mi.market, y.txid)
                        if r.is_err():
                            if r.err() == 'failed-exchange-ratelimit':
                                # Don't close if the cause of api failure is ratelimit
                                return
                            self.get_logger().warn(f'failed to cancel order {exchange} {self.mi.market} {y.txid} {r.err()} (still closing on our end)')
                        self._reject_limit(x)
                else:
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

    # TODO: standardize return type of _sell,_buy for Simu & RT
    def _sell(self, amount):
        amount = max(0, min(self.user.tokBalance, amount))
        price = self.current_v()

        tfilter = init_filter(self.mi.exchange, to_internal_market(self.mi.market))
        res = tfilter.preprocess_trade(amount, price)
        if res.is_err():
            self.get_logger().warn(res.err())
            return None

        volume, _ = res.unwrap()
        self.get_logger().info(f'selling {amount} at {price}')
        trade_dict = {}
        
        if self.make_trades:
            pbot = {'market': self.mi.market, 'exchange': self.mi.exchange, 'state': self.user}
            keys = self.profile['exchanges'][pbot['exchange']]
            trade_dict = mut_trade(pbot, keys, volume, 'sell', tapi=getattr(self, 'tapi', None), logger=self.get_logger())
            if trade_dict.is_err():
                err_ctx = {'type': 'market', 'exchange': self.mi.exchange, 'side': 'sell', 'volume': volume, 'price': price, 'uid': self.user.uid, 'market': self.mi.market, 'asset': self.mi.market.split(':')[0]}
                return self.handle_error(trade_dict, err_ctx)
            trade_dict = trade_dict.unwrap()
        else:
            fee = volume * price * (1 - self.fee)
            namount = volume * price - fee
            self.user.curBalance += namount
            self.user.tokBalance -= volume
            self.user.in_fees += fee
        trade_dict = {'type': 'sell', 'amount': volume, 'date': int(time.time()), **trade_dict}
        self._on_trade(**trade_dict)
        return trade_dict

    def _buy(self, amount):
        amount = max(0, min(self.user.curBalance, amount))
        price = self.current_v()

        tfilter = init_filter(self.mi.exchange, to_internal_market(self.mi.market))
        res = tfilter.preprocess_trade(amount / price, price)
        if res.is_err():
            self.get_logger().warn(res.err())
            return None

        volume, _ = res.unwrap()
        self.get_logger().info(f'buying {volume} at {price}')
        trade_dict = {}

        if self.make_trades:
            pbot = {'market': self.mi.market, 'exchange': self.mi.exchange, 'state': self.user}
            keys = self.profile['exchanges'][pbot['exchange']]
            trade_dict = mut_trade(pbot, keys, volume, 'buy', tapi=getattr(self, 'tapi', None), logger=self.get_logger())
            if trade_dict.is_err():
                err_ctx = {'type': 'market', 'exchange': self.mi.exchange, 'side': 'buy', 'volume': volume, 'price': price, 'uid': self.user.uid, 'market': self.mi.market, 'asset': self.mi.market.split(':')[1]}
                return self.handle_error(trade_dict, err_ctx)
            trade_dict = trade_dict.unwrap()
        else:
            fee = volume * (1 - self.fee)
            self.user.tokBalance += volume - fee
            self.user.curBalance -= volume * price
            self.user.in_fees += fee * price
        trade_dict = {'type': 'buy', 'amount': volume, 'date': int(time.time()), **trade_dict}
        self._on_trade(**trade_dict)
        return trade_dict
