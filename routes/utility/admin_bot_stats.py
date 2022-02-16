import time
from routes.db import get_strategy_map 
from routes.policy import SubscriptionPolicy
from routes.utils import incr
from .strategy import StrategyFactory
from routes.utility.acl import ACLManager

class AdminBotStats:
    def __init__(self, dbs: dict):
        self.dbs = dbs

    def update_by_subcription(self, subscription: str = None):
        statskey = f'stats_{subscription}' if subscription != None else 'stats'
        aclm = ACLManager(self.dbs)
        strat_names = get_strategy_map().keys()

        result = {}

        for username in self.dbs['users']:
            user = self.dbs['users'][username]
            policy = aclm.find_policy(SubscriptionPolicy._key, user)
            if subscription != None and policy.sub != subscription:
                continue

            incr(result, 'total_exchanges', len(user['exchanges']))
            incr(result, 'total_users')


            for b in user['bots']:
                bot = self.dbs['bots'][b]
                strat = bot['strategy']
                strat = StrategyFactory(strat, self.dbs).get_proto(as_uid=True)
                if strat not in strat_names:
                    continue

                incr(result, 'total_bots')
                if not bot['enabled']:
                    continue

                for log in bot['state'].trade_log:
                    if not log.get('type', None) or not log.get('date', None):
                        continue

                    if int(time.time()) - log['date'] <= 24 * 60 * 60:
                        incr(result, f'trades_made_1d.{strat}.{log["type"].lower()}')
                incr(result, f'total_balance.{strat}', bot['state'].portfolioValue)
                incr(result, f'total_active_bots.{strat}')

        result['avg_balance_bot'] = {}
        for name in strat_names:
            result['avg_balance_bot'][name] = result.get('total_balance', {}).get(name, 0) / result.get('total_active_bots', {}).get(name, 1) if result.get('total_active_bots', {}).get(name, False) else 0

        stats = self.dbs['admin_bot_stats'].get(statskey, {})
        stats[int(time.time())] = result
        self.dbs['admin_bot_stats'][statskey] = stats


    def update_admin_bot_stats(self):
        aclm = ACLManager(self.dbs)
        acl  = aclm.get_acl(SubscriptionPolicy._key)
        self.update_by_subcription()
        for policy in acl:
            self.update_by_subcription(policy.sub)
