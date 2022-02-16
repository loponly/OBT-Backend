import time
from datetime import datetime, timedelta
from pydantic import BaseModel
from typing import List, Any, Tuple
from sendgrid.helpers.mail import Mail, Email, To, Content
from .sendgrid import sg
from .strategy import StrategyFactory


class BotDetails(BaseModel):
    image: str
    market: str
    profit: str
    current_balance: str
    name: str
    enabled: bool
    exchange: str
    profit_percentage: str


class BotAux(BaseModel):
    trades: List[Any]
    profit: float
    current_balance: float
    starting_balance: float


class RoutineMails():
    def __init__(self, dbs: dict):
        self.dbs = dbs

    def get_bots_details(self, bots, starting_time) -> Tuple[List[BotDetails], List[BotAux]]:
        bots_details, aux = [], []
        starting_ts = starting_time.timestamp()
        for b in bots:
            bot = self.dbs['bots'][b]
            if not bot['enabled'] and bot.get('stop_time', time.time()) < starting_ts:
                continue

            history = self.dbs['bot_portfolios'].get(b, {})
            hks = list(filter(lambda t: t > starting_ts, history))
            trades = list(filter(lambda t: t.get('date', 0) > starting_ts, bot['state'].trade_log))
            baux = BotAux(trades=trades, profit=history[hks[-1]] - history[hks[0]], current_balance=history[hks[-1]], starting_balance=history[hks[0]])

            strat_meta = StrategyFactory(bot['strategy'], self.dbs)
            bdetails = BotDetails(image=strat_meta.get_proto().strategy_image.replace('.svg', '.png'), market=bot['market'], profit=str(
                round(baux.profit, 2)), current_balance=f"{round(baux.current_balance,2)} {bot['market'].split(':')[-1]}", name=strat_meta.get_name(), enabled=bot['enabled'], exchange=bot['exchange'], profit_percentage=str(round(baux.profit/baux.starting_balance, 2) * 100))

            bots_details.append(bdetails.dict())
            aux.append(baux)

        return bots_details, aux

    def send_routine_report(self, username, starting_time, period_type='Monthly'):

        user = self.dbs['users'][username]

        bots_details, aux = self.get_bots_details(user['bots'], starting_time)
        if len(bots_details) == 0:
            return False

        message = Mail(from_email='sender@onebutton.trade', to_emails=[(username, user['name'])])
        template_data = {
            "name": user.get("name", "John"),
            "period_start": starting_time.strftime("%b %d, %Y"),
            "period_end": datetime.now().strftime("%b %d, %Y"),
            "total_profit": str(round(sum(map(lambda x: x.profit, aux)), 2)),
            "starting_balance": str(round(sum(map(lambda x: x.starting_balance, aux)), 2)),
            "ending_balance": str(round(sum(map(lambda x: x.current_balance, aux)), 2)),
            "total_trades": str(round(sum(map(lambda x: len(x.trades), aux)), 2)),
            "period_type": period_type,
            "bots": bots_details
        }
        message.dynamic_template_data = template_data
        message.template_id = 'd-c6e23719c87e48839249c898b521b7b9'
        response = sg.send(message)
        #print(f"Send report to {username}", response.body, response.headers, response.status_code, template_data)
        if response.status_code >= 400:
            return False
        return True

    def check_monthly_report(self):
        last_month = self.dbs['globals'].get('mail:timestamp_last_month_report', 0)
        now = datetime.now()
        if now.timestamp() - last_month < 2 * 24 * 60 * 60:  # Avoid sending twice
            return
        if now.day != 1:  # 1st of the month
            return

        self.dbs['globals']['mail:timestamp_last_month_report'] = now.timestamp()
        starting_time = now.replace(month=now.month-1)

        for u in self.dbs['users']:
            user = self.dbs['users'][u]
            if user.get('preferences', {}).get('mail', {}).get('performance_report') != 'monthly':
                continue

            self.send_routine_report(u, starting_time)

    def check_weekly_report(self):
        last_week = self.dbs['globals'].get('mail:timestamp_last_week_report', 0)
        now = datetime.now()
        if now.timestamp() - last_week < 2 * 24 * 60 * 60:  # Avoid sending twice
            return

        if now.weekday() != 0:  # Monday
            return

        self.dbs['globals']['mail:timestamp_last_week_report'] = now.timestamp()
        starting_time = datetime.now() - timedelta(seconds=7*24*60*60)

        for u in self.dbs['users']:
            user = self.dbs['users'][u]
            if user.get('preferences', {}).get('mail', {}).get('performance_report') != 'weekly':
                continue

            self.send_routine_report(u, starting_time, period_type='Weekly')

    def run(self):
        self.check_monthly_report()
        self.check_weekly_report()
