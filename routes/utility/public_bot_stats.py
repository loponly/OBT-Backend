import time
import traceback
import pickle
import pandas as pd
import numpy as np
from datetime import datetime
from routes.db import get_strategy_map, get_tmp_cache
from routes.utils import incr, imply, applyr, atomic_memoize
from routes.exchange import _get_exchange_configs
from routes.realtime import get_env
from .strategy import StrategyFactory

days = 24 * 60 * 60
stats_timeranges = set([None, 1 * days, 7 * days, 30 * days, 90 * days, 180 * days])
duration_timeranges = set([7 * days, 14 * days, 30 * days, 90 * days, None])
unique_markets = set([None])
exchanges = _get_exchange_configs()
for exchange in exchanges:
    unique_markets.update(exchanges[exchange]['pairs'])


def timestamp_round_to_hour(t):
    # Rounds to nearest hour
    return int(datetime.fromtimestamp(t).replace(second=0, microsecond=0, minute=0).timestamp())


def timestamp_round_to(t,round="month"):
    round_mapper ={
        'hour': dict(second=0, microsecond=0, minute=0),
        'day':  dict(second=0, microsecond=0, minute=0,hour=0),
        'month': dict(second=0, microsecond=0, minute=0,hour=0,day=1),
    }
    return int(datetime.fromtimestamp(t).replace(**round_mapper.get(round, round_mapper["hour"])).timestamp())


def effective_bot(bot):
    """
    Filters out bots that are too new or haven't traded yet.
    """
    return ((bot.get('stop_time') or int(time.time())) - bot['start_time'] > 60 * 60 * 48) and (len(bot['state'].trade_log) != 0)


class PublicBotStats:
    def __init__(self, dbs: dict):
        self.dbs = dbs
        self.mem_cache = {}

    def get_general_stats(self):
        return atomic_memoize(self.dbs['cache'], self._get_general_stats)

    def _get_general_stats(self):
        total_profit = 0
        for b in self.dbs['bots']:
            bot = self.dbs['bots'][b]
            total_profit += bot['state'].portfolioValue - bot['state'].startingBalance[1]
            
        return {"total_profit": int(total_profit)}

    def get_roi_in_period(self, botid, start_ts, end_ts=1e99):
        portfolios = self.dbs['bot_portfolios'].get(botid, {})
        filtered_p = list(filter(lambda x: x >= start_ts and x <= end_ts, list(portfolios)))
        if not filtered_p:
            return 0

        first_p = portfolios[min(filtered_p)]
        last_p = portfolios[max(filtered_p)]

        return (last_p - first_p) / first_p

    def __get_roi_in_range(self, botid, start_ts, end_ts=1e99) -> dict:
        portfolios = self.dbs['bot_portfolios'].get(botid, {})
        filtered_p = list(filter(lambda x: x >= start_ts and x <= end_ts, list(portfolios)))
        if not filtered_p:
            return {}
        return {k: ((v-portfolios[filtered_p[0]])/v)*100 for k, v in portfolios.items()}

    def update_all(self):
        for market in unique_markets:
            for timerange in stats_timeranges:
                for duration in duration_timeranges:
                    for e in [True, False]:
                        self.get_botstats(timerange, market=market, enabled_only=e, min_duration=duration, overwrite=True)

    def get_botstats(self, start_seconds, market=None, offset=None, enabled_only=False, min_duration=7 * days, overwrite=False):
        assert start_seconds in stats_timeranges, "Only specified timeranges are cached"
        assert min_duration in duration_timeranges, "Only specified timeranges are cached"
        assert market in unique_markets, "Only markets that are active are allowed"
        return atomic_memoize(self.dbs['cache'], self._get_botstats_by_timerange, start_seconds, market=market, offset=offset, enabled_only=enabled_only, min_duration=min_duration, _overwrite=overwrite)

    def _get_botstats_by_timerange(self, start_seconds, market=None, offset=None, enabled_only=False, min_duration=7 * days):
        # TODO: load and cache rt enviroments for market data & produce BaH from start_seconds till stop_time or time()
        if not start_seconds:
            # From start
            start_seconds = time.time()

        min_duration = min_duration or 0

        ts = time.time()
        sts = ts - start_seconds
        ets = 1e99
        strat_names = get_strategy_map()

        result = {}

        if offset:
            ets = ts - float(offset)

        for b in list(self.dbs['bots']):
            bot = self.dbs['bots'][b]

            if not imply(enabled_only, bot['enabled']):
                continue

            if not imply(market, market == bot['market']):
                continue

            if not effective_bot(bot) or (bot['stop_time'] or 1e19) < sts:
                continue

            s = StrategyFactory(bot['strategy'], self.dbs).get_proto(as_uid=True)
            if s not in strat_names:
                continue

            duration = (bot['stop_time'] - bot['start_time']) if (bot['stop_time'] or 1e19) < ts else ts - bot['start_time']

            if duration < min_duration:
                continue

            # End of Filters
            market_key = (bot['exchange'], bot['market'], bot['candles'])
            if market_key in self.mem_cache:
                env = self.mem_cache[market_key]
            else:
                env = get_env(*market_key)
                self.mem_cache[market_key] = env

            # Skip if market data is unavailable
            if not env:
                continue

            incr(result, f'{s}.count')
            if bot['start_time'] > sts and imply(bot['stop_time'], (bot['stop_time'] or 0) < ets):
                incr(result, f'{s}.created')

            tradesc = len(
                list(filter(lambda x: float(x['date']) > sts and float(x['date']) < ets, bot['state'].trade_log)))

            incr(result, f'{s}.trades', tradesc)
            incr(result, f'{s}.avg_duration', duration)

            # Calculate prices at start and end of bot, to get an approximate BaH ROI
            start_index = np.argmin(np.abs(env.mi.historical['time'] - max(sts, bot['start_time'])))
            if not bot['stop_time']:
                stop_index = -1
            else:
                stop_index = np.argmin(np.abs(env.mi.historical['time'] - bot['stop_time']))
            start_price = env.mi.historical['close'][start_index]
            stop_price = env.mi.historical['close'][stop_index]
            bah_roi = (stop_price - start_price) / start_price

            roi = self.get_roi_in_period(b, sts, ets)
            incr(result, f'{s}.markets.{bot["market"]}.avg_roi', roi)
            incr(result, f'{s}.markets.{bot["market"]}.count')
            incr(result, f'{s}.avg_roi_bot', roi)
            incr(result, f'{s}.avg_roi_trades', roi)
            months_for_roi = (60 * 60 * 24 * 30) / duration

            bot_month_roi = ((1 + roi) ** months_for_roi) - 1
            bah_bot_month_roi = ((1 + bah_roi) ** months_for_roi) - 1
            incr(result, f'{s}.avg_roi_month', bot_month_roi)
            incr(result, f'{s}.avg_bah_roi_month', bah_bot_month_roi)

            applyr(result, f'{s}.bots.{b}', lambda x: x, {'exchange': bot['exchange'], 'market': bot['market'],
                                                          'start_time': bot['start_time'], 'trades_made': tradesc,
                                                          'days_active': duration, 'roi': roi, 'status': bot['enabled'],
                                                          'bot_month_roi': bot_month_roi, 'bah_roi': bah_roi})

        for k in result:
            # Resolve all per-bot averages
            for sk in result[k]:
                if sk.startswith('avg'):
                    count_key = 'trades' if sk.endswith('trades') else 'count'
                    if result[k][count_key] == 0:
                        result[k][sk] = 0
                    else:
                        result[k][sk] /= result[k][count_key]

            # Resolve all per-market averages
            for m in result[k]['markets']:
                result[k]['markets'][m]['avg_roi'] /= result[k]['markets'][m]['count']

            result[k]['name'] = getattr(strat_names[k], 'title', k)
            result[k]['image'] = strat_names[k].strategy_image
            result[k]['description'] = strat_names[k].strategy_description

        return result

    def _get_botstats_summary(self, start_seconds, market=None, offset=None, enabled_only=False, min_duration=7 * days):
        full_stats = self.get_botstats(start_seconds, market=market, offset=offset, enabled_only=enabled_only, min_duration=min_duration)
        for k in list(full_stats):
            del full_stats[k]['bots']

        return full_stats

    def get_botstats_summary(self, start_seconds, market=None, offset=None, enabled_only=False, min_duration=7 * days, overwrite=False):
        assert start_seconds in stats_timeranges, "Only specified timeranges are cached"
        assert min_duration in duration_timeranges, "Only specified timeranges are cached"
        assert market in unique_markets, "Only markets that are active are allowed"
        return atomic_memoize(self.dbs['cache'], self._get_botstats_summary, start_seconds, market=market, offset=offset, enabled_only=enabled_only, min_duration=min_duration, _overwrite=overwrite)

    def get_roi_range(self, *args, **kwargs):
        return atomic_memoize(self.dbs['cache'], self._get_roi_range, *args, **kwargs)

    def _get_roi_range(self, start_seconds, strategy=None, market=None, offset=None, enabled_only=False, min_duration=1 * days):
        assert start_seconds in stats_timeranges, "Only specified timeranges are cached"
        assert market in unique_markets, "Only markets that are active are allowed"
        if not start_seconds:
            # From start
            start_seconds = time.time()

        min_duration = min_duration or 0

        ts = time.time()
        sts = ts - start_seconds
        ets = 1e99

        _result = {}

        if offset:
            ets = ts - float(offset)

        for bot_id in self.dbs['bots']:
            bot = self.dbs['bots'][bot_id]

            if not imply(strategy, strategy == bot['strategy']):
                continue
            if not imply(enabled_only, bot['enabled']):
                continue
            if not imply(market, market == bot['market']):
                continue
            duration = (bot['stop_time'] - bot['start_time']) if (bot['stop_time'] or 1e19) < ts else ts - bot['start_time']
            if duration < min_duration:
                continue
            roi_range = self.__get_roi_in_range(bot_id, sts, ets)

            if not roi_range:
                continue
            for k, v in roi_range.items():
                _k = timestamp_round_to_hour(k)
                _result[_k] = _result.get(_k, [])
                _result[_k].append(v)

        _result = {k: sum(v)/len(v) for k, v in _result.items()}

        return _result

    def cache_bot_trade_logs(self,reset=False):
        trade_logs = self.dbs['globals'].get('trade_logs',{})
       
        last_max_trade_timestamp = max(trade_logs) if trade_logs and not reset else 0

        strat_names = get_strategy_map()

        for b in self.dbs['bots']:
            bot = self.dbs['bots'][b]
            s = StrategyFactory(bot['strategy'], self.dbs)
            if s.get_proto(as_uid=True) not in strat_names:
                continue
            
            i = 0
            for tt_log in reversed(bot['state'].trade_log):
                if tt_log.get('date',0) <= last_max_trade_timestamp:
                    break
                i += 1
            
            if i > 0:
                for t_log in bot['state'].trade_log[-i:]:
                    t = int(t_log.get('date',0))
                    trade_logs[t] = {
                        "date": t,
                        "price": t_log["price"],
                        "type": t_log["type"],
                        "change": t_log["change"],
                        "strategy_name": s.get_name(),
                        "hour": timestamp_round_to(t,round="hour"),
                        "day": timestamp_round_to(t,round="day"),
                        "month": timestamp_round_to(t,round="month"),
                        "market": bot['market'],
                        "start_time": int(bot['start_time']),
                        "stop_time": bot.get('stop_time'),
                        "enabled":bot.get("enabled",False)
                    }
                    
        self.dbs['globals']['trade_logs'] = trade_logs


    
    def _get_bot_trade_logs(self,market,start_time,active_from,is_enabled):

        self.cache_bot_trade_logs(reset=False)
        trade_logs = self.dbs['globals'].get('trade_logs',{})

        trade_logs_timestamp = list(trade_logs.keys())
        trade_logs_timestamp.sort(reverse=True)
        _return = []
        for k in trade_logs_timestamp:
            t_log = trade_logs[k]
            if not imply(is_enabled,t_log['enabled']):
                continue

            if not imply(start_time,t_log['start_time'] < int(start_time)):
                continue

            if not imply(market,market==t_log['market']):
                continue
            
            stop_time = t_log['stop_time'] if t_log.get('stop_time') else time.time()

            if not imply(active_from,(int(stop_time) - t_log['start_time']) < int(active_from)):
                continue
            
            _return.append(t_log)

        return _return

    def _get_bot_balance_distrbutions(self,market,start_time,active_from,is_enabled=True):


        coins_usd = {}
    
        for botid in self.dbs['bots']:
            bot = self.dbs['bots'][botid]
            if not imply(is_enabled,bot['enabled']):
                continue

            if not imply(start_time,int(bot['start_time']) < int(start_time)):
                continue

            if not imply(market,market==bot['market']):
                continue
            
            stop_time = bot['stop_time'] if bot.get('stop_time') else time.time()

            if not imply(active_from,int(stop_time) - int(bot['start_time']) < int(active_from)):
                continue

            pair = bot['market'].split(':')
            
            coins_usd[pair[0]] = coins_usd.get(pair[0], 0) + (bot['state'].portfolioValue - bot['state'].curBalance)
            coins_usd[pair[1]] = coins_usd.get(pair[1], 0) + bot['state'].curBalance
        
            if bot['enabled']:
                cur_in_order = 0
                for order in bot['state'].open_orders.values():
                    if order.side.upper() == 'BUY':
                        cur_in_order += order.volume * order.price
                coins_usd[pair[0]] -= cur_in_order
                coins_usd[pair[0]] += cur_in_order

        _fiat =  ['USDT','USD','BUSD','EUR'] 
        total = sum(coins_usd.values())
        if total==0:
            return {'FIAT':0,'CRYPTO':0}
        
        fiat_total = sum(map(lambda x:coins_usd[x],filter(lambda x: x if x in _fiat else False,coins_usd)))
        return {'FIAT':fiat_total/total,'CRYPTO':(total-fiat_total)/total}
    
    def _get_trade_distribution(self,*args,_period="day",**kwargs):
        tradelogs = self._get_bot_trade_logs(*args,**kwargs)
        _return  = {}
        if not tradelogs:
            return _return

        df=pd.DataFrame(tradelogs)
        _data = df.sort_values(by=_period).groupby([_period,'type'])[_period].count().to_dict()

        for k in _data:
            _t ,side = k
            _return[_t] = _return.get(_t,{})
            _return[_t][side] = _data[k]

        return _return

    
    def get_trade_distribution(self,*args,**kwargs):
        return atomic_memoize(self.dbs['cache'],self._get_trade_distribution,*args,**kwargs)
    
    def get_bot_balance_distrbutions(self,*args,**kwargs):
        return atomic_memoize(self.dbs['cache'],self._get_bot_balance_distrbutions,*args,**kwargs)

    def get_bot_trade_logs(self,*args,**kwargs):
        return atomic_memoize(self.dbs['cache'],self._get_bot_trade_logs,*args,**kwargs)