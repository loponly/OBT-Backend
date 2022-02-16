import uuid
import os
from .strategy import Strategy
from .metrics import *
from .maths import safeVal
from empyrical import sortino_ratio, sharpe_ratio, max_drawdown
import json
import traceback

class Runner:
    """
    config:
        - NAME             REQ  TYPE                INFO
        - startingBalance:      [number, number]
        - candle_span:      x   number              in seconds
        - runPeriod:            number              in seconds
        - periods:              number              amount of runPeriods to run
    """
    def __init__(self, strategy: Strategy, config, root_dir='store/unique'):
        self.config = config

        self.StartingBalance = self.config.get('startingBalance', [0, 3000])
        self.StartTime = self.config.get('startTime', 0)
        self.EndTime = self.config.get('endTime', 1e13)

        self.strategy = strategy
        self.env = self.strategy.env
        self.env.fee = self.config.get('fee', self.env.fee)

        self.uuid = uuid.uuid4()
        self.root_dir = root_dir
        self.dist = {
            'stats': os.path.join(self.root_dir, str(self.uuid) + '.stats.json'),
            'trades': os.path.join(self.root_dir, str(self.uuid) + '.trades.json'),
            'candles': os.path.join(self.env.api.root_dir, '%s_%s' % (self.config['marketid'], self.config['candles']) + '.json')
        }

    def get_metrics(self):
        return {
                **self.metric_log['stats'],
                'trade_count': len(self.env.user.trade_log),
                'buys_total': len(list(filter(lambda x: x['type'] == 'buy', self.env.user.trade_log))),
                'sells_total': len(list(filter(lambda x: x['type'] == 'sell', self.env.user.trade_log))),
                'run_time': self.metric_log['runTime'],
                'portfolio_start': self.metric_log['portfolioVal'][0],
                'portfolio_end': self.metric_log['portfolioVal'][-1],
                'portfolio_avg': np.mean(self.metric_log['portfolioVal']),
                'portfolio_dev': np.std(self.metric_log['portfolioVal']),
                'portfolio_max': np.mean(self.metric_log['maxBalance']),
                'portfolio_min': np.mean(self.metric_log['minBalance'])
            }


    def get_dist(self):
        with open(self.dist['stats'], 'w+') as fp:
            json.dump(self.get_metrics(), fp)

        with open(self.dist['trades'], 'w+') as fp:
            json.dump(self.env.user.trade_log, fp)

        return self.dist

    def run(self):
        self.env.reset(startingBalance=self.StartingBalance)
        self.metric_log = {'buys': [], 'sells': [], 'portfolioVal': [], 'maxBalance': [], 'minBalance': [], 'runTime': 0, 'stats': {}}
        self.strategy.reset()
        done = False


        while self.strategy.required_samples() >= self.env.indexstep or self.env.get_timestep() < self.StartTime:
            done = not self.env.step()
            if done:
                return

        startTime = self.env.get_timestep()
        startIndex = self.env.indexstep
        startPrice = self.env.current_v()


        while True:
            self.strategy.step()

            self.metric_log['portfolioVal'].append(self.env.portfolioValue())
            done = not self.env.step()
            if self.env.get_timestep() > self.EndTime or done:
                break

        self.dist['candles_range'] = [startIndex, self.env.indexstep] # TODO: fix when parallel dist

        try:
            self.calculate_metrics(startPrice)
        except SystemExit:
                return
        except:
            traceback.print_exc()

        # buys = filter(lambda x: x.type == 'buy', self.env.user.trade_log)
        # sells = filter(lambda x: x.type == 'sell', self.env.user.trade_log)

        self.metric_log['runTime'] = self.env.get_timestep() - startTime
        self.metric_log['maxBalance'].append(self.env.user.max_balance)
        self.metric_log['minBalance'].append(self.env.user.min_balance)

    def calculate_metrics(self, startPrice):
        self.metric_log['stats']['roi'] = float((self.metric_log['portfolioVal'][-1] - self.metric_log['portfolioVal'][0]) / self.metric_log['portfolioVal'][0])
        self.metric_log['stats']['bah_roi'] = float((self.env.current_v() - startPrice) / startPrice)
        self.metric_log['stats']['roi_delta'] = float(self.metric_log['stats']['roi'] - self.metric_log['stats']['bah_roi'])
        self.metric_log['stats']['fees'] = float(self.env.user.in_fees)

        frac_diff = np.diff(self.metric_log['portfolioVal']) / np.abs(self.metric_log['portfolioVal'][:-1])

        self.metric_log['stats']['win_avg'] = safeVal(float(np.mean(frac_diff[frac_diff > 0])))
        self.metric_log['stats']['loss_avg'] = safeVal(float(np.mean(frac_diff[frac_diff < 0])))
        # TODO: safe-ify the calculations after here
        self.metric_log['stats']['win_rate'] = safeVal(float(len(frac_diff > 0) / len(frac_diff)))

        self.metric_log['stats']['mdd'] = max_drawdown(frac_diff)
        self.metric_log['stats']['shapre'] = sharpe_ratio(frac_diff)
        self.metric_log['stats']['sortino'] = sortino_ratio(frac_diff)
        # print('buys: %.3f (total %d), sells: %.3f (total %d)' % (np.mean(ametrics['buys']), np.sum(ametrics['buys']), np.mean(ametrics['sells']), np.sum(ametrics['sells'])))
        # print('%s: %.3f, %s: %.3f, avg %.3f dev %.3f, max %.3f min %.3f' % (market.split(':')[0], np.mean(ametrics['tokBalance']), market.split(':')[1], np.mean(ametrics['curBalance']), np.mean(ametrics['portfolioVal']), np.std(ametrics['portfolioVal']), np.mean(ametrics['maxBalance']), np.mean(ametrics['minBalance'])))
