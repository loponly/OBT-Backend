from tradeEnv.maths import quantize_float
import time
from result import Ok, Err
from tradeEnv.api_adapter import Order
from tradeEnv.realtime import RealTimeEnv
from tradeEnv.metrics import SimulMetrics, UserMetrics
from tradeEnv.markets import SineMarket, StepWaveMarket
import numpy.testing as npt
import numpy as np
from routes.db import get_dbs
from routes.utility.users import UserManager
from routes.boot import boot_checks
from server import create_api
from falcon import testing
import os
import sys
import secrets
abs_path = os.path.realpath('..')
if abs_path not in sys.path:
    sys.path.append(abs_path)


class TestCase(testing.TestCase):
    def setUp(self):
        super().setUp()
        self.app = create_api(True)
        self.dbs = get_dbs()
        boot_checks()

        um = UserManager(self.dbs)
        um.create_user('example@example.com', 'somepasswordhash', 'Tester', silent_fail=True)

        self.token = '6e7ef4d93735e18b7d05ebfd90f39d1e8e267653f2a93f1f5453d57f9753a307'
        self.dbs['auth'][self.token] = 'tester'


class TestAuth(TestCase):
    def test_get_message(self):
        doc = {'authenticated': False}
        result = self.simulate_get('/api/v1/login')
        self.assertEqual(result.json, doc)
        result = self.simulate_get('/api/v1/login', headers={'Authorization': 'randomstr'})
        self.assertEqual(result.json, doc)

    def test_login(self):
        credentials = {'username': 'example@example.com', 'password': 'somebadpassword'}
        result = self.simulate_post('/api/v1/login', json=credentials)
        assert result.json.get('token', None) is None

        credentials = {'username': 'example@example.com', 'password': 'somepasswordhash'}
        result = self.simulate_post('/api/v1/login', json=credentials)
        assert result.json.get('token', None) != None

        result = self.simulate_get('/api/v1/login', headers={'Authorization': result.json['token']})
        self.assertEqual(result.json, {'authenticated': True})


class TestExchange(TestCase):
    def test_connection(self):
        credentials = {'exchange': 'Binance', 'api_key': 'badkey', 'api_secret': 'badsecret'}
        result = self.simulate_post('/api/v1/connectexchange', json=credentials, headers={'Authorization': self.token})
        assert result.json.get('valid', False) == False

        # TODO: valid key

# FIXME: No strategies are added by default (mock for unit?)
# class TestStrategy(TestCase):
#    def test_strategy_details(self):
#        # Get public strategies
#        strats = self.simulate_get('/api/v1/availablestrategies')
#        assert strats.json['user'] == []
#        assert type(strats.json['prototypes']) == list
#        # TODO: better schema validation?
#
#        # Get strategy parameters (should fail, only if logged in)
#        params = self.simulate_post('/api/v1/strategy')
#        assert params.status_code >= 400
#
#        # Get strategy parameters (should pass, logged in)
#        params = self.simulate_post('/api/v1/strategy', json={'uuid': 'Buy and Hold'}, headers={'Authorization': self.token})
#        assert params.status_code < 400
#        assert params.json['name'] == strats.json['prototypes'][0]['uuid']
#        self.assertEqual(list(params.json.keys()), ["name", "params", "proto", "returns"])
#
#    def test_strategy_auth(self):
#        # Create private strategy
#        strats = self.simulate_get('/api/v1/availablestrategies')
#        params = self.simulate_put('/api/v1/strategy', json={'proto': 'Buy and hold', 'name': 'very-advanced-strat', 'params': {}}, headers={'Authorization': self.token})
#        assert type(params.json['uuid']) == str
#
#        # Get details on private strategy
#        ret = self.simulate_post('/api/v1/strategy', json={'uuid': params.json['uuid']}, headers={'Authorization': self.token})
#        assert ret.status_code < 400
#
#        ret = self.simulate_post('/api/v1/strategy', json={'uuid': params.json['uuid']}, headers={'Authorization': self.otoken})
#        assert ret.status_code >= 400
#
#        # Delete strategy
#        ret = self.simulate_delete('/api/v1/strategy', json={'uuid': params.json['uuid']}, headers={'Authorization': self.otoken})
#        assert ret.status_code >= 400
#
#        ret = self.simulate_delete('/api/v1/strategy', json={'uuid': params.json['uuid']}, headers={'Authorization': self.token})
#        assert ret.status_code < 400


class TestMaths(testing.TestCase):
    def test_quantize(self):
        assert quantize_float(0.1 + 0.2, 4) == 0.3
        assert quantize_float(1e-9, 5) == 0
        assert quantize_float(1.999999999, 3) == 1.999
        assert quantize_float(0.19999999 + 0.2999999, 3) == 0.499

        x = np.random.randint(10000, size=(16, 16, 16)).astype(np.float64)
        xr = x + np.random.rand(16, 16, 16)
        print(x, xr, quantize_float(xr, 0))
        npt.assert_array_equal(x, quantize_float(xr, 0))
        npt.assert_array_equal(x / 10, quantize_float(xr / 10, 1))
        npt.assert_array_equal(x / 1e2, quantize_float(xr / 1e2, 2))


class TradeEnvTestCase(testing.TestCase):
    def setUp(self):
        super().setUp()
        self.mi = SineMarket(samples=500)
        self.env = SimulMetrics(indexstep=150, mi=self.mi)


class TestMarkets(TradeEnvTestCase):
    def test_generated(self):
        assert self.env.step() == True

    def test_plot(self):
        fig = self.mi.plot_market(candles=100)
        fig.savefig('plot-sine.png')

        tmi = StepWaveMarket(samples=500, alpha=1)
        tfig = tmi.plot_market(candles=20)
        tfig.savefig('plot-t.png')

    def test_offset(self):
        for i in [40, 82]:
            for ei in [140, 362, 225, 400]:
                self.env.indexstep = ei
                cv = self.env.get_view()[-i:]
                cg = self.env.get_window(i)
                # print(self.env.indexstep, self.mi._calc_window(40, -self.env.indexstep), self.mi._calc_window(40))
                cr = self.mi.get_window(i, offset=-self.env.indexstep)
                npt.assert_array_equal(cv, cr)
                npt.assert_array_equal(cv, cg)
                assert len(cv) == i

                # Different from the others (not offset by indexstep)
                cgr = self.mi.get_window(i)
                assert len(cgr) == i

    def test_window_check(self):
        x = self.mi.historical['time']
        woa1 = self.mi._calc_window(40, -150), 40
        woa2 = self.mi._calc_window(80, 50), 80
        for woa in [woa1,  woa2]:
            wo, a = woa
            w, o = wo
            assert len(x[w:o]) == a

    #! WARN: exact float comparisons follow INCLUDING floating-point errors
    def test_market_orders(self):
        tmi = StepWaveMarket(samples=500, alpha=1)
        tenv = SimulMetrics(indexstep=151, mi=tmi, startingBalance=[0, 10], fee=0.999, limit=5)
        assert tenv.buyp(1.) != None
        assert tenv.user.curBalance == 0.
        assert tenv.user.tokBalance == 9.99
        assert tenv.step() == True
        assert tenv.sellp(1.) != None
        assert tenv.user.curBalance == 19.96002
        assert tenv.user.tokBalance == 0.
        assert tenv.step() == True
        assert tenv.buyp(0.5) != None
        assert tenv.user.curBalance == 9.98001
        assert tenv.user.tokBalance == 9.97002999

        assert tenv.buyp(0.5) == None  # FAILED trade (below limit)
        assert tenv.user.curBalance == 9.98001
        assert tenv.user.tokBalance == 9.97002999

    def test_limit_orders(self):
        tmi = StepWaveMarket(samples=500, alpha=1)
        tenv = SimulMetrics(indexstep=150, mi=tmi, startingBalance=[0, 10], fee=0.999, limit=5)
        assert tenv.buy_limitp(1., 0.95) != None
        assert len(tenv.user.open_orders) == 1
        assert tenv.user.curBalance == 0.
        assert tenv.user.tokBalance == 0.
        assert tenv.step() == True
        assert tenv.user.tokBalance == 10.515789473684212  # (10 / 0.95) * 0.999
        assert tenv.user.curBalance == 0.
        assert tenv.sell_limitp(1., 2.05) != None
        assert len(tenv.user.open_orders) == 1
        assert tenv.user.tokBalance == 0.
        assert tenv.user.curBalance == 0.
        assert tenv.step() == True
        assert tenv.user.tokBalance == 0.
        assert tenv.user.curBalance == 21.53581105263158  # ((10 / 0.95) * 0.999) * 2.05 * 0.999

        assert tenv.buy_limitp(1., 0.94) != None  # Failed trade (price not reached)
        assert len(tenv.user.open_orders) == 1
        assert tenv.user.tokBalance == 0.
        assert tenv.user.curBalance == 0.
        assert tenv.portfolioValue() == 21.53581105263158
        assert tenv.step() == True
        assert tenv.user.tokBalance == 0.
        assert tenv.user.curBalance == 21.53581105263158

        assert tenv.buyp(1.) != None
        assert tenv.user.tokBalance == 21.514275241578947  # p * 0.999
        assert tenv.user.curBalance == 0.

        assert tenv.sell_limitp(1., 2.06) != None  # Failed trade (price not reached)
        assert len(tenv.user.open_orders) == 1
        assert tenv.user.tokBalance == 0.
        assert tenv.user.curBalance == 0.
        assert tenv.portfolioValue() == 21.514275241578947
        assert tenv.step() == True
        assert tenv.user.tokBalance == 21.514275241578947
        assert tenv.user.curBalance == 0.
        assert tenv.portfolioValue() == 21.514275241578947 * 2

        assert tenv.sellp(1.) != None
        assert tenv.user.tokBalance == 0.
        assert tenv.user.curBalance == 42.985521932674736

        assert tenv.buy_limitp(.5, 0.99) != None  # Partial balance trade
        assert len(tenv.user.open_orders) == 1
        assert tenv.user.tokBalance == 0.
        assert tenv.user.curBalance == 21.492760966337368
        assert tenv.portfolioValue() == 42.985521932674736
        assert tenv.step() == True
        assert tenv.user.tokBalance == 21.68814970239498
        assert tenv.user.curBalance == 21.492760966337368


class MockRealtimeAPI:
    def __init__(self, mi):
        self.mi = mi

    def market_order(self, api_keys, market, side, amount):
        return Ok(Order('BinanceAPI', 'BTCUSDT', int(time.time()), side, 'MARKET', self.mi.historical['close'][-1], 0.0, amount, 'USDT'))

# Fuzzy comparison for floats (p=fraction, a=absolute)


def fuzzyp_eq(a, b, p=1e-4):
    return ((a * (1+p)) > b) and ((b * (1+p)) > a)


def fuzzya_eq(a, b, t=1e-5):
    return ((a + t) > b) and ((b + t) > a)


class RealtimeMock(testing.TestCase):
    def setUp(self):
        super().setUp()
        self.mi = SineMarket(samples=500, price_denom=2)
        self.mi.market = 'BTC:USDT'
        self.mi.exchange = 'Binance'
        self.api = MockRealtimeAPI(self.mi)
        self.env = RealTimeEnv(mi=self.mi, trade_api=self.api)
        self.env.profile = {'exchanges': {'Binance': []}}
        self.env.update()

    def test_rt_market_orders(self):
        # default SimuMetrics value
        bal = 3000
        assert self.env.buy(1.) != None
        print(self.env.user.__dict__)
        assert fuzzya_eq(self.env.user.curBalance, 0.)
        assert fuzzyp_eq(self.env.user.tokBalance, bal / self.env.current_v())
        # assert len(self.env.user.trade_log) == 1
        assert self.env.sell(1.) != None
        assert fuzzyp_eq(self.env.user.curBalance, bal)
        assert fuzzya_eq(self.env.user.tokBalance, 0., t=1e-4)  # Reduced accuracy because of quantization steps
        # assert len(self.env.user.trade_log) == 2
