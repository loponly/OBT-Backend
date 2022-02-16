from tradeEnv.api_adapter import ApiAdapter, binance_map
from tradeEnv.backrunner import Runner
from tradeEnv.metrics import SimulMetrics
import falcon
import os
from .base import Route, add_pkg, TTLManager
from .utils import assert_type
from .utility.strategy import StrategyFactory

add_pkg()


timespan_map = {'1w': 7 * 24 * 60 * 60, '1d': 24 * 60 * 60, '4h': 4 * 60 * 60, '1h': 60 * 60, '15m': 15 * 60}
exchange_map = {'Binance': binance_map}
available_pairs = ['BTC:USDT', 'LTC:USDT', 'ETH:USDT', 'BCH:USDT', 'XRP:USDT', 'BNB:USDT', 'EOS:USDT', 'ETH:BTC', 'DOGE:USDT']

class BacktestOptions(Route):
    def on_get(self, req, resp):
        self.mark_activity(req)
        resp.media = {
            "candleSizes": list(timespan_map.keys()),
            "pairs": available_pairs
        }

class Backtest(Route):
    """
    - candles       (4h | 1d)
    - exchange      (Binance)
    - market        (BTC:USDT)
    - strategy      (RSI Threshold)

    Additional Configuration:
    + strategy
    + Runner
    """

    def on_post(self, req, resp):
        self.mark_activity(req)
        config = req.media

        assert_type(req.media['candles'], str, "Candles")
        assert_type(req.media['market'], str, "Market")
        assert_type(req.media['exchange'], str, "Exchange")
        assert_type(req.media['strategy'], str, "Strategy")

        # TODO: add a set_en

        strategy_fab = StrategyFactory(config['strategy'], self.dbs)
        config['candles'] = strategy_fab.get_candles('4h')

        config['marketid'] = ''.join(config['market'].split(':'))
        api = ApiAdapter(exchange_map[config['exchange']], '%s_%s' % (config['marketid'], config['candles']))
        env = SimulMetrics(api)
        strategy = strategy_fab.construct(env)

        runner = Runner(strategy, config)
        runner.run()
        # TODO: Parallelize

        dist = runner.get_dist()

        # Clean up all the files after 24h
        to_clean = list(filter(lambda x: type(x) == str, dist.values()))
        TTLManager(self.dbs).createTTL({'files': to_clean}, 60 * 60 * 24)

        # Response formatting
        for x in dist:
            if 'store' in dist[x]:
                dist[x] = dist[x].replace('store', '')
                if os.name == 'nt':
                    dist[x] = dist[x].replace('\\', '/')

        resp.media = dist
        resp.status = falcon.HTTP_200
