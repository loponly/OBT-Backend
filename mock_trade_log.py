import random
import time

def generateTradeLog():
    tradeLog = []
    x = 0
    while x < 5:
      type_tr = "sell" if x % 2 == 0 else "buy"
      tradeLog.append(
        {
          'botName': "MA Crossover",
          'type': type_tr,
          'market': "BTC:USDT",
          'currency': "BTC",
          'amount': (random.randint(1, 10) / 10 * 20 + 5) / 100,
          'date': int(time.time()),
          'price': (random.randint(1, 10) / 10 * 10000 + 9264.0),
          'priceCurrency': "USDT",
          'balance': x * 10,
          'fee': (random.randint(1, 10) / 10 * 16 + 5) / 100000,
          'fee_asset': "USDT",
          'change': round(random.randint(1, 10) / 10 * -10) + 0.01
        }
      )
      x += 1
    return tradeLog