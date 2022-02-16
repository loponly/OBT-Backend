from tradeEnv.trade_api import TradeAPIRegistry, approx_conversion_rate, CachedWrapper
from .notifications import NotificationHandler
import time

class PortfolioFetch():
    def __init__(self, dbs: dict):
        self.dbs = dbs
        self.error_delay = 24 * 60 * 60

    def get_portfolio(self, username, exchanges):
        portfolio = {}
        for exch in exchanges:
            api = TradeAPIRegistry[exch]()
            balance_raw = api.balance(exchanges[exch])

            # Could not fetch portfolio value
            if balance_raw.err() == 'failed-exchange-auth':
                key = f'{username}:{exch}:portfoliofetch-error'
                prev = self.dbs['globals'].get(key, {})
                if prev.get('date', None) is None:
                    prev['date'] = int(time.time())

                # We want it to reset only after 24h since the first error
                deltatime = int(time.time()) - prev['date']
                prev['counter'] = prev.get('counter', 0) + 1
                self.dbs['globals'].set(key, prev, expire=(24 * 60 * 60) - deltatime)

                # Only triggers notif on the 3rd time in 24h
                if prev.get('counter', 0) > 3:
                    NotificationHandler(self.dbs).add_error_portfolio_notification(username, exch)

                continue
                
            if balance_raw.err() != None:
                print(f"Failed to get portfolio for {username}", balance_raw.err())
                continue

            balance_raw = balance_raw.unwrap()
            balance = self.extract_balance_dicts(balance_raw)
            prices = CachedWrapper(api).market_prices()
            portfolio[exch] = 0
            for asset in balance:
                price = approx_conversion_rate(asset, 'USDT', prices)
                if price.is_err():
                    # TODO: logger
                    continue
                portfolio[exch] += price.ok() * balance[asset]

            # open orders
            all_bots = self.dbs['users'][username]['bots']
            for bot in all_bots:
                current_bot = self.dbs['bots'][bot]
                if current_bot['exchange'] != exch:
                    continue

                if hasattr(current_bot['state'], 'open_orders'):
                    current_price = approx_conversion_rate(current_bot['market'].split(':')[0], 'USDT', prices)
                    if current_price.is_err():
                        # TODO: logger
                        continue

                    for order in current_bot['state'].open_orders:
                        volume = current_bot['state'].open_orders[order].volume
                        portfolio[exch] += volume * current_price.ok()

        x = self.dbs['profile_portfolios'].get(username, {})
        x[int(time.time())] = portfolio
        self.dbs['profile_portfolios'][username] = x

    def extract_balance_dicts(self, balance_raw):
        return {
            x: float(balance_raw[x])
            for x in balance_raw
            if float(balance_raw[x]) > 0
        }
