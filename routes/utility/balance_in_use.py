import time


class BalanceInUse():
    def __init__(self, dbs: dict):
        self.dbs = dbs

    def check_ending_balance(self):
        for b in self.dbs['bots']:
            bot = self.dbs['bots'][b]
            if not bot['enabled'] and bot['state'].trade_log and bot['state'].tokBalance > 0:
                if bot['state'].trade_log[-1].get('type', '').lower() == 'sell' and bot['state'].trade_log[-1].get('balance', False):
                    _balance = bot['state'].trade_log[-1]['balance']
                    bot['state'].curBalance = _balance
                    bot['state'].tokBalance = 0
                    self.dbs['bots'][b] = bot
                    bot_portfolios = self.dbs['bot_portfolios'].get(b, {})
                    if bot_portfolios:
                        max_timestamp = max(bot_portfolios)
                        bot_portfolios[max_timestamp] = _balance
                        self.dbs['bot_portfolios'][b] = bot_portfolios

    def calc_balance(self):

        for email in self.dbs['users']:
            user = self.dbs['users'][email]
            in_use = 0
            for b in user['bots']:
                bot = self.dbs['bots'][b]
                if not bot['enabled']:
                    continue
                in_use += bot['state'].portfolioValue

            balance_in_use = self.dbs['balance_in_use'].get(email, {})
            balance_in_use[int(time.time())] = in_use
            self.dbs['balance_in_use'][email] = balance_in_use
