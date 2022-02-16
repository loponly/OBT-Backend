import os
from falcon import testing
import time
import datetime

from tradeEnv.trade_api import TradeAPIRegistry, CachedWrapper, AbstractTradeAPI, approx_conversion_rate
from tradeEnv.trade_filters import init_filter
from tradeEnv.metrics import SimulMetrics, UserMetrics
from tradeEnv.realtime import mut_trade
from tradeEnv.utils import span_from_candletype, to_internal_market
from tradeEnv.maths import withinTolerance
from routes.logging import getLogger

def mock_bot(market: str):
    return {
            'enabled': True,
            'exchange': 'unkown',
            'market': market,
            'strategy': 'unkown',
            'state': UserMetrics('', startingBalance = [0, 15]),
            'candles': '4h',
            'user': 'unkown',
            'stop_time': None,
            'start_time': time.time(),
            'billing_start_portfolio': 15
            }

def get_test_data(exchange):
    keys = [os.environ[f'{exchange}_PUB'], os.environ[f'{exchange}_PRIV']]
    bot = mock_bot(exchange, 'ETH:USDT')
    return bot, keys

def kraken_bal_compat(bal):
    km = {'ZEUR': 'EUR', 'XXBT': 'BTC', 'XLTC': 'LTC', 'XETH': 'ETH'}
    for k in list(bal.keys()):
        if k in km:
            bal[km[k]] = bal[k]

def add_to_report(*args, filename='consistency_report.log'):
    with open(filename, 'a') as fp:
        fp.write('\t'.join(map(lambda x: str(x), args)) + '\n')

def assert_report(condition, *args, filename='consistency_report.log'):
    if not condition:
        add_to_report("Assertion Failed: ", *args, filename=filename)

def ensure_required_balance(api: AbstractTradeAPI, keys, required_balance, extra=1.1):
    """
    Checks balance & prices and will sell highest asset for the required currency balance
    Returns the still missing (if any) required balances after all the conversions
    """

    bal = api.balance(keys).unwrap()
    kraken_bal_compat(bal)
    # Check how much more we need
    bal_req_diff = {}
    for x in required_balance:
        if bal[x] < required_balance[x]:
            # Take a bit extra just in case
            bal_req_diff[x] = max((required_balance[x] * extra) - bal[x] , 0)

    # Check prices to calculate our biggest asset, and to create a routing for getting the required assets
    prices = api.market_prices()
    bal_usd_values = {}
    for x in bal:
        if bal[x] == 0.:
            continue

        v = approx_conversion_rate(x, 'USDT', prices)
        if v.is_err():
            print(f"Failed to convert {x} to USDT equivalent")
            continue
        # Remove required balance from value (we don't want to sell assets we need)
        bal_usd_values[x] = v.ok() * (bal[x] - required_balance.get(x, 0))

    print(bal, bal_req_diff, bal_usd_values)

    for asset in list(reversed(sorted(bal_usd_values, key=lambda x: bal_usd_values[x]))):
        # Sell biggest assets for the required assets until required_balance is reached
        for new_asset in bal_req_diff:
            print(f"Trying {asset} to {new_asset}")
            if bal_req_diff[new_asset] < 1e-8:
                print("Skipping: required asset within margin of error of target")
                break

            if bal[asset] < 1e-6:
                print("Skipping: not enought convertable assets to feasible make trade") 
                break

            if asset == new_asset:
                continue

            # Does market exist (direct only atm)
            c = approx_conversion_rate(asset, new_asset, prices, max_depth=0)
            print(f"{asset}:{new_asset} gives {c.value}")
            if c.is_ok():
                # Check which is quote and which is base in this exchange
                market, side, vol = f'{asset}:{new_asset}', "SELL", bal_req_diff[new_asset] / c.ok()
                if not prices.get(market):
                    market, side, vol = f'{new_asset}:{asset}', "BUY", 1./vol 

                # Can only use as much as we have
                tfilter = init_filter(api.name, to_internal_market(market))
                vol = min(bal[asset], max(vol, float(tfilter.minLot)))

                # Check if we expect order to go through
                res = tfilter.preprocess_trade(vol, (c.ok() if side == 'SELL' else 1./c.ok()))
                if res.is_err():
                    print(f"Skipping {asset}:{new_asset} ({res.err()})")
                    continue
                vol, price = res.unwrap()
                add_to_report(f"Converting {vol} {asset} to {new_asset} at {price}")

                # Place order and calculate difference
                new_asset_res = vol * (c.ok() if side == 'SELL' else 1.)
                asset_res = vol * (c.ok() if side == 'BUY' else 1.)
                order = api.market_order(keys, market, side, vol)
                if order.is_ok():
                    bal[asset] -= asset_res * 1.01 # Assume worst case (1% fee)
                    bal_req_diff[new_asset] += new_asset_res

    return bal_req_diff

def check_consistency(api: AbstractTradeAPI, bot, keys):
    """
    Checks market order, fee percentage and final balance vs expected balance
    """
    add_to_report(f"=====Report Start {api.name} {datetime.datetime.now():%Y-%m-%d}=======\n")

    res = ensure_required_balance(api, keys, {'USDT': 11.0})
    add_to_report(f"Missing balance for tests?:", res)

    tfilter = init_filter(api.name, to_internal_market(bot['market']))

    prices = api.market_prices()
    start_balance = api.balance(keys).unwrap()
    kraken_bal_compat(start_balance)
    print(start_balance)
    pair = bot['market'].split(':')
    conversion = approx_conversion_rate(pair[0], pair[1], prices, max_depth=0)
    conversion = conversion.unwrap()

    add_to_report(f"Staring with {start_balance[pair[1]]}{pair[1]} and {start_balance[pair[0]]}{pair[0]}")
    add_to_report(f"Approximate conversion rate of {conversion}")

    bvol = 11.0 / conversion
    
    res = tfilter.preprocess_trade(bvol, conversion)
    bvol, price = res.unwrap()

    border = api.market_order(keys, bot['market'], 'BUY', bvol)
    border = border.unwrap()
    
    add_to_report(f"\nBUY Order diff:")
    add_to_report(f"\t\t{pair[0]}: order of {bvol} resulted locally in {border.get_tok_diff()}")
    add_to_report(f"\t\t{pair[1]}: order of {-bvol * border.price} resulted locally in {border.get_cur_diff()}")
    add_to_report(f"\t\tFee: {border.get_fee()} {pair[1]} ({border.get_fee() / abs(border.get_cur_diff()) * 100}%)")
    assert_report(withinTolerance(border.get_tok_diff(), bvol, 0.01), f"Token addition is not within 1% of {bvol} ({border.get_tok_diff()})")
    assert_report(withinTolerance(border.get_cur_diff(), -bvol * border.price, 0.01), f"Currency substraction is not within 1% of {-11.0} ({border.get_cur_diff()})")
    #assert all(map(lambda fee: withinTolerance(border['fee'] / (border['amount'] * border['price']), fee), [0.0026, 0.26, 0.075, 0.060])), "Not all fees are in our registry"

    sorder = api.market_order(keys, bot['market'], 'SELL', border.amount)
    sorder = sorder.unwrap()

    add_to_report(f"\nSELL Order diff:")
    add_to_report(f"\t\t{pair[0]}: order of {-bvol} resulted locally in {sorder.get_tok_diff()}")
    add_to_report(f"\t\t{pair[1]}: order of {bvol * sorder.price} resulted locally in {sorder.get_cur_diff()}")
    add_to_report(f"\t\tFee: {sorder.get_fee()} {pair[1]} ({sorder.get_fee() / abs(sorder.get_cur_diff()) * 100}%)")
    assert_report(withinTolerance(sorder.get_cur_diff(), bvol * sorder.price, 0.01), f"Currency addition is not within 1% of {11.0} ({sorder.get_cur_diff()})")
    assert_report(withinTolerance(sorder.get_tok_diff(), -bvol, 0.01), f"Token substraction is not within 1% of {-11.0 / conversion} ({sorder.get_tok_diff()})")
    assert_report(withinTolerance(border.get_fee(), sorder.get_fee(), 0.01), f"Fee between buy/sell trade not equal (within 1% margin of error)")
  
    time.sleep(2.0) # wait, exchanges (binance) don't always update immediately

    end_balance = api.balance(keys).unwrap()
    kraken_bal_compat(end_balance)
    print(end_balance)

    price_loss = sorder.price / border.price # Fractional loss by spread

    exch_tok_diff = float(end_balance[pair[0]]) - float(start_balance[pair[0]])
    bot_tok_diff = border.get_tok_diff() + sorder.get_tok_diff() 
    exch_cur_diff = float(end_balance[pair[1]]) - float(start_balance[pair[1]])
    bot_cur_diff = border.get_cur_diff() + sorder.get_cur_diff()

    fee_for = lambda t, rf: rf[1] if rf[0] == t else 0.

    spread_loss = border.amount * border.price - sorder.amount * sorder.price
    add_to_report(f"\nSpread loss: {spread_loss} {pair[1]}")
    add_to_report(f"Fee loss: {border.get_fee() + sorder.get_fee()} {pair[1]}")

    add_to_report(f"\nExchange fee (assuming spread correct and fee in quote currency): {abs(exch_cur_diff) - spread_loss} {pair[1]}")
    add_to_report(f"Exchange spread (assuming fee correct): {abs(exch_cur_diff) - (fee_for(pair[1], border.get_raw_fee()) + fee_for(pair[1], sorder.get_raw_fee()))} {pair[1]}")

    add_to_report(f"\nExchange {pair[0]} diff: {exch_tok_diff} (compared to {bot_tok_diff} locally)")
    assert_report(abs(exch_tok_diff - bot_tok_diff) < 1e-4, f"Token balance difference on exchange ({exch_tok_diff}) does not equal the local difference ({bot_tok_diff})")

    add_to_report(f"Exchange {pair[1]} diff: {exch_cur_diff} (compared to {bot_cur_diff} locally)")
    assert_report(abs(exch_cur_diff - bot_cur_diff) < 1e-4 or exch_cur_diff > bot_cur_diff, f"Currency balance difference on exchange ({exch_cur_diff}) does not equal the local difference ({bot_cur_diff})")
    
    print(border, sorder)
    expected_loss = ((price_loss-1) * border.price * bvol) - fee_for(pair[1], sorder.get_raw_fee()) - fee_for(pair[1], border.get_raw_fee())
    add_to_report(f"Bot vs expected change: {bot_cur_diff} {pair[1]} (compared to {expected_loss} locally)")
    assert_report(abs(bot_cur_diff - expected_loss) < 1e-4, f"Bot price loss does not correlate with bot simulated loss (might be false positive; assumes exchange puts all fees in quote currency)")
    add_to_report(f"Exchange vs expected change: {exch_cur_diff} {pair[1]} (compared to {expected_loss} locally)")
    assert_report(abs(exch_cur_diff - expected_loss) < 1e-2, f"Exchange price loss does not correlate with bot simulated loss (might be false positive; assumes exchange puts all fees in quote currency)")

    return border, sorder

class GenericCase(testing.TestCase):
    def setUp(self):
        super().setUp()
        self.disabled = []#['binance']

    def test_check_kraken(self):
        logger = getLogger('tests.consistency.kraken')
        if 'kraken' in self.disabled:
            assert False, "Kraken disabled"

        keys = ['6LpPY/VMgMSHtbzwvSpj9l3Z3ywZUPk0NIgMYp3AWYizYfAIhHCQF4Hp', 'VZiqmLKhxyOLsEuBiOnAYtWX9Fdt0qsfsYUSd3OdxQ64G8tCoAOmS6MGanCD4vgLigacSawLPXMgeLwiDYzeIw==']
        b, s = check_consistency(TradeAPIRegistry['Kraken'](logger), mock_bot('ETH:USDT'), keys)
        assert_report(withinTolerance(b.get_fee(), abs(b.get_cur_diff()) * 0.0026), "Fee percentage is not as expected (might be false positive)")
        assert_report(withinTolerance(s.get_fee(), abs(s.get_cur_diff()) * 0.0026), "Fee percentage is not as expected (might be false positive)")


    def test_check_binance(self):
        logger = getLogger('tests.consistency.binance')
        if 'binance' in self.disabled:
            assert False, "Binance disabled"

        keys = ['XtaHzqlzITBmrLp304U7xeAWCqJgOuBq28liQyuNBQ8G6WAkxqQVQY47FNy7BHLp', 'BKW0nBsIGsh2XpGNhKw1IMHqU0CEBFMtFZkBrTyd4Tvbn1Se5Fnve1X8vqrWTMeF']
        b, s = check_consistency(TradeAPIRegistry['Binance'](logger), mock_bot('ETH:USDT'), keys)
        assert_report(withinTolerance(b.get_fee(), abs(b.get_cur_diff()) * 0.001), "Fee percentage is not as expected (might be false positive)")
        assert_report(withinTolerance(s.get_fee(), abs(s.get_cur_diff()) * 0.001), "Fee percentage is not as expected (might be false positive)")

