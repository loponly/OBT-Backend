import time
import traceback
from routes.stripehooks import create_usage_record, remove_subscription
from routes.db import get_exchange_rates
from routes.policy import SubscriptionPolicy
from routes.utility.acl import ACLManager

class ProfileProfitCalc():
    def __init__(self, dbs):
        self.dbs = dbs

    def get_profile_profitability(self):
        for username in self.dbs['users']:
            try:
                profile = self.dbs['users'][username]
                billing_profit = 0
                all_time_profit = 0

                for b in profile['bots']:
                    bot = self.dbs['bots'][b]

                    if bot['enabled']:
                        portfolios = self.dbs['bot_portfolios'].get(b, {})
                        portfolios[int(time.time())] = bot['state'].portfolioValue

                        first_portfolio = portfolios[list(portfolios)[0]]
                        roi = (bot['state'].portfolioValue - first_portfolio) / first_portfolio
                        bot['roi'] = roi
                        # Resolve bots first for potential race conditions (TODO: entry lock?)
                        self.dbs['bots'][b] = bot
                        self.dbs['bot_portfolios'][b] = portfolios

                    start_portfolio = bot.get('billing_start_portfolio', bot['state'].portfolioValue)
                    billing_profit += bot['state'].portfolioValue - start_portfolio
                    all_time_profit += bot['state'].portfolioValue - bot['state'].startingBalance[1]

                profile_profit = self.dbs['profile_profits'].get(username, {})
                profile_profit[int(time.time())] = all_time_profit
                self.dbs['profile_profits'][username] = profile_profit

                policy = ACLManager(self.dbs).find_policy(SubscriptionPolicy._key, profile)
                # if the commission fee is 0 or negligible don't send a usage record
                profile['payment'] = profile.get('payment', {})
                if policy.payment_fees < 1e-8:
                    profile['payment']['last_profit_calculated'] = billing_profit
                    profile['payment']['last_profit_time'] = int(time.time())
                    continue 
                
                # conversion USD -> EUR
                rates = get_exchange_rates()
                eur_profit = rates['EUR'] * billing_profit
                try:
                    create_usage_record(profile, self.dbs, eur_profit)
                    profile['payment']['billing_amount_eur'] = eur_profit * policy.payment_fees
                    profile['payment']['billing_amount'] = billing_profit * policy.payment_fees
                    profile['payment']['last_profit_calculated'] = billing_profit
                    profile['payment']['last_profit_time'] = int(time.time())
                except SystemExit:
                    return
                except Exception:
                    traceback.print_exc()
                    if (profile['payment']['next_billing_date'] or 0) + 7 * 24 * 60 * 60 < int(time.time()):
                        remove_subscription(self.dbs, profile)
                self.dbs['users'][username] = profile
            except SystemExit:
                return
            except:
                traceback.print_exc()
