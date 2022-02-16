import os
import sys
abs_path = os.path.realpath('..')
#print(abs_path)
if not abs_path in sys.path:
    sys.path.append(abs_path)

os.environ['BINANCE_PUB']  = 'UWvl04EPtLv1cCHI2oNodLlRFQvsEhAGEnXkf92OV09hl3BXLw2Pos5htNS7SHue'
os.environ['BINANCE_PRIV'] = 'tizXXrtjpFeOZ6CylzwtDum8765M2SZUST3dJfbi4teqdye2Q9kwt7bu3XrnWF4x'
os.environ['BINANCEUS_PUB'] = '7aSGEDTn9tM14dRRDpdUzmM4IW6G3OnxatFgUHOZmu5x5dT1DXp3O1AaOxnYXwt9'
os.environ['BINANCEUS_PRIV'] = 'IYmMFErma2frO9c1JUv7qrIk1Qtqnbv1DuuR85z5ceey5IY7Y7bpuCJXlrV9p7cM'

"""
Tests for limit orders; requires BINANCE_PUB & BINANCE_PRIV with >=11$
"""
from falcon import testing
from tradeEnv.trade_api import BinanceAPI, BinanceUSAPI
import sys, logging
logger=logging.getLogger('root')
logger.addHandler(logging.StreamHandler(stream=sys.stdout))
logger.setLevel(logging.DEBUG)


class BinanceCase(testing.TestCase):
    def setUp(self):
        super().setUp()
        self.binance_keys = [os.environ['BINANCE_PUB'], os.environ['BINANCE_PRIV']]

    def test_limit_order_binance(self):
        print('========================')
        print('WARNING: if you see this you might want to check your open orders, they aren\'t always automatically canceled')
        print('=========================')

        market = 'BTC:USDT'
        side = 'buy'
        price = 30000
        amount = 0.0011

        api = BinanceAPI(logger=logger)
        txid = api.limit_order(self.binance_keys, market, side.upper(), amount, price)
        assert txid.is_ok(), "Transaction couldn't be placed"
        txid = txid.ok()

        o = api.limit_details(self.binance_keys, market, txid)
        assert o.is_ok(), "Order details couldn't be fetched (requires manual cancelation: {txid})"

        r = api.cancel_order(self.binance_keys, market, txid)
        assert r.is_ok(), f"Order coulnd't be canceled (requires manual cancelation: {txid})"


class BinanceUSCase(testing.TestCase):
    def setUp(self):
        super().setUp()
        self.binance_keys = [os.environ['BINANCEUS_PUB'], os.environ['BINANCEUS_PRIV']]

    def test_limit_order_binance(self):
        print('========================')
        print(
            'WARNING: if you see this you might want to check your open orders, they aren\'t always automatically canceled')
        print('=========================')

        market = 'BTC:USDT'
        side = 'buy'
        price = 30000
        amount = 0.0011

        api = BinanceUSAPI(logger=logger)
        txid = api.limit_order(self.binance_keys, market, side.upper(), amount, price)
        assert txid.is_ok(), "Transaction couldn't be placed"
        txid = txid.ok()

        o = api.limit_details(self.binance_keys, market, txid)
        assert o.is_ok(), "Order details couldn't be fetched (requires manual cancelation: {txid})"

        r = api.cancel_order(self.binance_keys, market, txid)
        assert r.is_ok(), f"Order coulnd't be canceled (requires manual cancelation: {txid})"
