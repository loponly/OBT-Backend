import os
import sys
rootdir = os.path.dirname(__file__) + '/..'
os.chdir(rootdir)
sys.path.append(rootdir)

from routes.db import get_dbs
from tradeEnv.trade_api import TradeAPIRegistry
from routes.utils import incr
import traceback

dbs = get_dbs()

log = []
def print_log(*args):
    global log
    log.append(args)

def fprint():
    global log
    for l in log:
        print(*l)
    log = []

def kraken_bal_compat(bal):
    km = {'ZEUR': 'EUR', 'XXBT': 'BTC', 'XLTC': 'LTC', 'XETH': 'ETH'}
    for k in list(bal.keys()):
        if k in km:
            bal[km[k]] = bal[k]
            del bal[k]

for u in dbs['users']:
    try:
        user = dbs['users'][u]
        print_log(f"Checking {u}:")
        balance = {}
        for exchange in user.get('exchanges', {}):
            api = TradeAPIRegistry[exchange]()
            bal = api.balance(user['exchanges'][exchange])
            if bal.is_ok():
                balance[exchange] = bal.ok()
                kraken_bal_compat(balance[exchange])

        print_log(f"\t Bots self-check:")
        bot_balance = {}
        for b in user.get('bots', []):
            bot = dbs['bots'][b]
            if not bot['enabled']:
                continue

            #! Don't account for open orders, exchange shouldn't include it either 
            #cur_in_order, tok_in_order = 0., 0.
            #for order in bot['state'].open_orders.values():
            #    if order.side.upper() == 'SELL':
            #        tok_in_order += order.volume
            #    elif order.side.upper() == 'BUY':
            #        cur_in_order += order.volume * order.price

            curBalance = bot['state'].curBalance # - cur_in_order 
            tokBalance = bot['state'].tokBalance # - tok_in_order 
            pair = bot['market'].split(':')
            incr(bot_balance, f"{bot['exchange']}.{pair[0]}", tokBalance) 
            incr(bot_balance, f"{bot['exchange']}.{pair[1]}", curBalance) 

            tl = bot['state'].trade_log
            bal, fees = bot['state'].startingBalance, [0,0]
            for t in tl: 
                bal[0] += -t['amount'] if t['type'].upper() == 'SELL' else t['amount']
                bal[1] += (t['amount'] * t['price']) if t['type'].upper() == 'SELL' else -(t['amount'] * t['price'])
                fees[0] += t['fee'] if bot['market'].startswith(t['fee_asset']) else 0
                fees[1] += t['fee'] if bot['market'].endswith(t['fee_asset']) else 0

            if abs(tokBalance - bal[0]) >= fees[0] + 1e-5:
                print_log(f"\t\t ✗ {u}/{b}/{pair[0]}: {tokBalance} (stored) != {bal[0]} (recalc)")

            if abs(curBalance - bal[1]) >= fees[1] + 1e-5:
                print_log(f"\t\t ✗ {u}/{b}/{pair[1]}: {curBalance} (stored) != {bal[1]} (recalc)")

        print_log(f"\t Exchange balance check:")
        for exchange in bot_balance:
            for asset in set([*list(balance.get(exchange,{}).keys()), *list(bot_balance.get(exchange,{}).keys())]):
                bb = bot_balance.get(exchange, {}).get(asset, 0)
                eb = balance.get(exchange, {}).get(asset, 0)
                if bb < 1e-8 and eb < 1e-8:
                    continue

                if eb >= bb or abs(eb - bb) < 1e-5:
                    print_log(f"\t\t ✓ {u}/{exchange}/{asset}: {eb} ({exchange}) > {bb} (bots)")
                else:
                    print_log(f"\t\t ✗ {u}/{exchange}/{asset}: {eb} ({exchange}) < {bb} (bots)")

    except Exception as e:
        print_log(f"✗ {u} failed spectacularly", e)
        traceback.print_exc()
    finally:
        # Only if we actually have something to report 
        if len(log) <= 3:
            log = []
        fprint()

