import time
import os
from .notif_saving import NotificationSaving, Notification
import tweepy
from result import Ok, Err, Result
from pydantic import BaseModel
from typing import *

trunc_currencies = ['USDT', 'EUR']


def round_value(value, currency='USDT'):
    power = 2 if currency in trunc_currencies else 5
    value = int(value * (10 ** power))
    return float(value) / (10 ** power)


class TradeMessage(BaseModel):
    bot_type: str
    total_balance: Any
    market: str
    tok: str
    cur: str
    price: float
    type: str
    portfolioValue: float
    curBalance: float
    date: Any
    change: float


class NotificationHandler():
    def __init__(self, dbs: dict):
        self.dbs = dbs
        self.notif_saving = NotificationSaving(dbs)

    # personal notifications

    def add_trade_notification(self, username, bot_name, exchange, pair, side, total, price, order_type, fee, fee_asset, token_balance, curr_balance, bot_value, change):
        change = change * 100
        bought_sold = 'bought'
        if side == 'sell'.upper():
            bought_sold = 'sold'
            total = -total
        token = pair.split(':')[0]
        currency = pair.split(':')[1]
        token_total = total / price

        price = round_value(price, currency=currency)
        total = round_value(total, currency=currency)
        token_total = round_value(token_total, currency=token)
        bot_value = round_value(bot_value, currency=currency)
        change = round_value(change)
        token_balance = round_value(token_balance, currency=token)
        curr_balance = round_value(curr_balance, currency=currency)

        title = '%s %s Executed at %s %s' % (pair, side, price, currency)
        body_array = ['Bot %s %s %s on %s for total of %s %s for price of %s %s.<br>' % (bot_name, bought_sold, token, exchange, total, currency, price, currency),
                      '<b>Trade Summary</b><br>',
                      '<b>Date:</b> %s<br>' % time.strftime('%d-%m-%Y %H:%M:%S'),
                      '<b>Exchange:</b> %s<br>' % exchange,
                      '<b>Market:</b> %s<br>' % pair,
                      '<b>Side:</b> %s<br>' % side,
                      '<b>Order type:</b> %s<br>' % order_type,
                      '<b>Price:</b> %s %s<br>' % (price, currency),
                      '<b>Amount:</b> %s %s<br>' % (token_total, token),
                      '<b>Fee:</b> %s %s<br>' % (fee, fee_asset),
                      '<b>Bot:</b> %s<br>' % bot_name,
                      '<b>Current bot token balance:</b> %s %s<br>' % (token_balance, token),
                      '<b>Current bot currency balance:</b> %s %s<br>' % (curr_balance, currency),
                      '<b>Current bot value:</b> %s %s<br>' % (bot_value, currency),
                      '<b>Change since last trade:</b> %s %%' % (('+' + str(change)) if change > 0 else str(change))
                      ]
        body = ''.join(str(b) for b in body_array)
        notification = Notification(title, body, 'trade', int(time.time()), icon=token)
        self.notif_saving.save_notification(notification, username=username)

    def add_error_order_notification(self, username, bot_name, exchange, response):
        title = 'Error placing order'
        body = 'Bot %s failed to place an order. %s response: %s' % (bot_name, exchange, response)

        notification = Notification(title, body, 'system', int(time.time()), icon='error')
        self.notif_saving.save_notification(notification, username=username)

    def add_error_portfolio_notification(self, username, exchange):
        title = 'Failed to retrieve portfolio from %s. Check your API connection key' % exchange
        body = 'We encountered an issue when trying to get your portfolio from %s. Please check your API connection settings on %s' % (exchange, exchange)

        notification = Notification(title, body, 'system', int(time.time()), icon='error')
        self.notif_saving.save_notification(notification, username=username)

    def add_new_ip_notification(self, username, ip):
        title = 'Login from new IP detected'
        body = 'We detected a login from new IP: %s. If it was not you, please change your password immediately.' % ip

        notification = Notification(title, body, 'system', int(time.time()), icon='warning')
        self.notif_saving.save_notification(notification, username=username)

    def add_monthly_bot_stats_notification(self, username, link='link'):
        title = 'Monthly bot stats'
        body = 'The monthly statistics for your bots are ready. Navigate to the bot page or click the button below to view them.'

        notification = Notification(title, body, 'system', int(time.time()), icon='info', link=link)
        self.notif_saving.save_notification(notification, username=username)

    def add_trial_started_notification(self, username):
        title = 'Trial period started'
        body = 'You have started your trial period. You now have temporary access to the full OB experience.'

        notification = Notification(title, body, 'system', int(time.time()), icon='info')
        self.notif_saving.save_notification(notification, username=username)

    def add_trial_ends_soon_notification(self, username, link='link'):
        title = 'Trial period ends soon'
        body = 'Your trial period ends soon. Navigate to the subscriptions page or click the button below to continue using OB Trader.'

        notification = Notification(title, body, 'system', int(time.time()), icon='info', link=link)
        self.notif_saving.save_notification(notification, username=username)

    def add_trial_ended_notification(self, username, link='link'):
        title = 'Trial period ended'
        body = 'Your trial period ended. Navigate to the subscriptions page or click the button below to continue using OB Trader.'

        notification = Notification(title, body, 'system', int(time.time()), icon='info', link=link)
        self.notif_saving.save_notification(notification, username=username)

    def add_error_charging_notification(self, username):
        title = 'Error when charging payment'
        body = 'Failed to charge subscription payment. Please verify your payment methods.'

        notification = Notification(title, body, 'system', int(time.time()), icon='error')
        self.notif_saving.save_notification(notification, username=username)

    def add_charging_soon_notification(self, username):
        title = 'Payment due in 3 days'
        body = 'Your subscription payment is due in 3 days. The fee will be automatically withdrawn from your chosen payment service.'

        notification = Notification(title, body, 'system', int(time.time()), icon='info')
        self.notif_saving.save_notification(notification, username=username)

    def add_insufficient_balance_notification(self, username, exchange, side, bot_name, amount, asset):
        title = f'Action Required: Not Enough Assets on {exchange}'
        body = f'Bot {bot_name} does not have enough {asset} to make a {side} trade on {exchange}. It could have happened if you recently manually traded on {exchange}. Update your balance on {exchange} to have at least {amount} {asset}'

        notification = Notification(title, body, 'system', int(time.time()), icon='error')
        self.notif_saving.save_notification(notification, username=username)

    def add_binance_api_expire(self, username, data):
        title = data.get('title', '')
        body = data.get('body', '')

        notification = Notification(title, body, 'system', int(time.time()), icon='info')
        self.notif_saving.save_notification(notification, username=username)

    def add_balance_notification(self, username, data):
        title = data.get('title', '')
        body = data.get('body', '')

        notification = Notification(title, body, 'system', int(time.time()), icon='info')
        self.notif_saving.save_notification(notification, username=username)

    def add_subscription_plan_notification(self, username, data):
        title = data.get('title', '')
        body = data.get('body', '')

        notification = Notification(title, body, 'system', int(time.time()), icon='info')
        self.notif_saving.save_notification(notification, username=username)

    # global notifications

    def add_global_notification(self, title, body, category):
        notification = Notification(title, body, category, int(time.time()), glob=True)
        self.notif_saving.save_global_notification(notification)


    def notify_telegram(self, telegram_token, telegram_sent_username,  text, username):
        import telegram
        try:
            bot = telegram.Bot(token=telegram_token)
            bot.send_message(chat_id=telegram_sent_username, text=text, parse_mode=telegram.ParseMode.HTML, disable_web_page_preview=True)
        except Exception as e:
            self.notif_saving.save_notification(str(e), username=username)
            print(str(e))

    def post_tweet(self, consumer: list, access: list, message: str) -> Result:
        # Authenticate to Twitter
        try:
            auth = tweepy.OAuthHandler(*consumer)
            auth.set_access_token(*access)
            res = tweepy.API(auth).update_status(message)
            if res:
                return Ok('Tweeted!')
        except Exception as e:
            return Err(str(e))

    def twitter_message_template(self, message: TradeMessage):
        text = f"🤖 AI Type #{message['bot_type']} #{message['market'].replace(':',':#')}\n"
        if message['type'] == 'BUY':
            message['total_balance'] = message['portfolioValue']
            message['total_balance'] = 0 if message['total_balance'] == 0 else int(((message['portfolioValue'] - message['curBalance'])/message['total_balance'])*100)
            text += f"🟢 Bought #{message['tok']} at {message['price']} #{message['cur']} for {message['total_balance']}% of my balance\n"
        else:
            text += f"🔴 Sold {int(message['total_balance']*100)}% of my current #{message['tok']} at {message['price']} #{message['cur']}\n"

        text += f"📅 Date: {message['date']:%d-%m-%Y %H:%M:%S} UTC\n"
        text += f"🪙 Market: #{message['market'].replace(':',':#')}\n"
        if message["change"] != 0:
            text += f"{'⬇' if message['change'] < 0 else '⬆'} Change Since Last Trade: {'' if message['change'] < 0 else '+'}{message['change']}%"

        return text

    def telegram_message_template(self, message: TradeMessage):
        text = f"🔴 Sold {int(message['total_balance']*100)}% of my current {message['tok']} at ${message['price']}\n"
        if message['type'].upper() == 'BUY':
            message['total_balance'] = message['portfolioValue']
            message['total_balance'] = 0 if message['total_balance'] == 0 else int(((message['portfolioValue'] - message['curBalance'])/message['total_balance'])*100)
            text = f"🟢 Bought {message['tok']}  at ${message['price']} for {message['total_balance']}% of my balance\n"
        text += f"📅 Date: {message['date']:%d-%m-%Y %H:%M:%S} UTC\n"
        text += f"🪙 Market: {message['market']}\n"

        return text
