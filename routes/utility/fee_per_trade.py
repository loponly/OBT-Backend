

import time
from result import Err,Ok,Result
from routes.policy import  SubscriptionFeePerTrade, SubscriptionPolicy
from datetime import datetime
from routes.realtime import close_bots
from routes.utility.notifications import NotificationHandler
from routes.utility.ob_token import OBTokenTransaction,notify
from routes.utility.strategy import StrategyFactory


class OBTFeePerTrade(OBTokenTransaction):

    def __init__(self, dbs, logger=None) -> None:
        super().__init__(dbs, logger=logger)

    def check_notice_period(self,username):
        profile = self.dbs['users'][username]
        
        if profile['payment'].get('policy_id',None) != SubscriptionFeePerTrade.sub:
            return False

        acl = self.dbs['globals'][SubscriptionPolicy._key]
        for entry in acl:
            if entry.sub == profile['payment'].get('policy_id',None):
                break
        else:
            return False

        # if profile['obt_token'].get('balance',0) > entry.min_amount_trade:
        if profile['obt_token'].get('balance',0) > 0:
            if profile['obt_token'].get('notice_start_time'):
                del profile['obt_token']['notice_start_time']
                self.dbs['users'][username] = profile
            return False

        now_timestamp = time.time()
        notify_handler =NotificationHandler(dbs=self.dbs)
        if not profile['obt_token'].get('notice_start_time'):   
            profile['obt_token']['notice_start_time'] = now_timestamp
            end_date = f"{datetime.fromtimestamp(profile['obt_token']['notice_start_time'] + SubscriptionFeePerTrade.notice_duration_in_sec):%d-%m-%Y %H:%M:%S}"
            
            notify(username=username,data={
                'template_id':'d-762a52a2975942ac8a766beb4e9cc3c9',
                'profile':profile,
                'body':{
                    'date': end_date,
                    'plan':SubscriptionFeePerTrade.sub,
                #  'notice_period' : f"{(now_timestamp - profile['obt_token']['notice_start_time'])//3600}"
                }
            })   
            
                
            notify_handler.add_balance_notification(username=username,
                                                                        data={
                                                                            "title":f"Insufficient OBT Balance for your Fee per trade plan ends {end_date}",
                                                                            "body":f"Your balance has hit 0 OBT. Make sure to deposit more OBT in your wallet in order to continue using the {SubscriptionFeePerTrade.sub} plan. If your OBT balance remains negative in the next ~48h we will automatically downgrade your subscription plan and archive all of your currently active bots."
                                                                        })


            self.dbs['users'][username] = profile

        if profile['obt_token']['notice_start_time'] + entry.notice_duration_in_sec <= time.time():
            close_postion = False
            for b in profile['bots']:
                bot = self.dbs['bots'][b]
                if bot['enabled']:
                    strat_meta = StrategyFactory(bot['strategy'], self.dbs)
                    strategy_name = strat_meta.get_name()
                    notify_handler.add_balance_notification(username=username,
                                                            data={
                                                                "title":f"Your {strategy_name} {bot['market']} bot was archived due to insufficient OBT balance",
                                                                "body":f"Your bot {strategy_name} ({b}) on {bot['exchange']} {bot['market']} has been archived due to insufficient OBT balance in your OB wallet"
                                                            })
                    close_postion = True
                    
            if close_postion:
                close_bots(self.dbs,profile)
                SubscriptionFeePerTrade(self.dbs).downgrade(username,True,True)

            return close_postion

        return False

    def estimate_OBT_bot_fee(self,bot):
        if not bot.get('state'):
            return 0
        trade_log = bot['state'].trade_log
        total_transaction = len(trade_log)
        if total_transaction == 0:
            return 0

        avg_fee = sum(trade.get('OBT_fee',0) for trade in trade_log)/total_transaction

        if avg_fee <= 0:
            return 0

        return avg_fee
    
    def run(self):
        users = self.dbs['users']
        for u in users:
            self.check_notice_period(u)