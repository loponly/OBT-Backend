import abc
import time
from datetime import datetime
from pydantic.tools import T
from sendgrid.helpers.mail import Mail
from .sendgrid import sg
from .notifications import NotificationHandler
from .pollcron import TimedExecUtils
from tradeEnv.trade_api import BinanceAPI

class NotifyAlert():
    def __init__(self, dbs: dict):
        self.dbs = dbs

    def notify(self, username: str, data: dict) -> bool:

        try:
            message = Mail(from_email='sender@onebutton.trade', to_emails=[(username, data['profile']['name'])])
            message.dynamic_template_data = data.get('body', {})
            message.template_id = data.get('template_id')
            response = sg.send(message)

            if response.status_code >= 400:
                return False
            return True
        except Exception as e:
            return False

    def run(self):
        pass


class NotifyMailPreferences(NotifyAlert):
    notify_categories = {
        'trade:insufficient-balance': {
            'subject': 'Your One Button Trader {market} {bot type} bot didn’t have enough balance to place an order',
            'title': 'Your {market} {bot type} bot failed to place an order on {exchange} due to insufficient balance',
            'body': 'You can use the body sent into the notification + ”\nIf your bot actually has enough balance, please contact our support team for further assistance'
        },
        'trade:stoploss': {
            'subject': 'Your One Button Trader {cur} {market} {bot type} bot has hit its stop-loss at {token price}',
            'title': "Stop-loss triggered at {token price} on {market} for your {bot type} bot",
            'body': "{body}"

        },
        'trade:failed-exchange-auth': {
            'subject': 'Your One Button Trader bot failed to place an order on {exchange}',
            'title': 'Your bot failed to place an order on {exchange} due to faulty API credentials',
            'body': 'During one of your recent bot trade attempts on {exchange} the API credentials connected to your One Button Trader account were rejected. In order to ensure that your bots continue trading double-check whether all the needed permissions for trading are granted. Alternatively, you can re-create your API credentials and re-connect them to your One Button Account.If everything seems to be properly configured, please contact our support team for further assistance'

        },
    }

    def __init__(self, dbs: dict):
        super().__init__(dbs)

    def notify_mail_preferences(self, username: str, notify_category: str, param: dict = {}):
        profile = self.dbs['users'][username]
        if self.notify_categories.get(notify_category, False) and profile.get('preferences', {}).get('mail', {}).get(notify_category):
            _body = {}
            for k, v in self.notify_categories[notify_category].items():
                _body[k] = v.format(**param)

            self.notify(username,  data={'body': _body,
                                         'profile': {'name': username.split('@')[0]},
                                         'template_id': 'd-6b99f14f149a45a3989feac7c255610d'
                                         })

    def run(self):
        pass


class NotifyBinanceAPIExpire(NotifyAlert):
    __global__name: str = f'NotifyBinanceAPIExpire:timestamp_last_check_user_binance_api'
    __check_days: list = [5, 3, 1]
    __refresh_version: int = 2
    object_name: str = 'binance_apiRestrictions'

    def __init__(self, dbs: dict):
        super().__init__(dbs)

    def refresh_all_current_binance_expire_date(self, is_auto_manually: bool = True):
        _check = 0
        if is_auto_manually:
            _check = self.dbs['globals'].get('NotifyAlert:is_refresh_all_current_user_binance_expire_date', self.__refresh_version)
            if _check > self.__refresh_version:
                return

        _api = BinanceAPI()
        for username in self.dbs['users']:
            profile = self.dbs['users'][username]
            api_key = profile.get('exchanges', {}).get('Binance')
            if not isinstance(api_key, list):
                if profile.get(self.object_name):
                    profile[self.object_name].update({'expire_datetime': None})
                    self.dbs['users'][username] = profile
                continue

            res = _api.account_api_restrictions(api_key)
            if res.is_ok():
                profile[self.object_name] = profile.get(self.object_name, {})
                profile[self.object_name]['expire_datetime'] = res.ok()
                self.dbs['users'][username] = profile

        self.dbs['globals']['NotifyAlert:is_refresh_all_current_user_binance_expire_date'] = _check + 1

    def __check_user(self, username):

        profile = self.dbs['users'][username]
        _expire_date = profile.get(self.object_name, {}).get('expire_datetime')

        if not _expire_date:
            return
        try:
            _expire_date=int(_expire_date)
        except TypeError:
            return

        _days_until_expire = int((int(_expire_date)-datetime.now()).days)
        if not _days_until_expire in self.__check_days:
            return

        if int(profile[self.object_name].get(f'is_notify_{_days_until_expire}', 0)) + 2 * 24 * 60 * 60 < time.time():
            return

        _days_until_expipre_str = f'{_days_until_expire} {"days" if _days_until_expire>1 else "day"}'

        if self.notify(username=username, data={'body': {'days_until_expire': _days_until_expipre_str,
                                                         },
                                                'profile': profile,
                                                'template_id': 'd-a37fe09362a94fa5ab5d09f7949223e6'
                                                }):
            NotificationHandler(self.dbs).add_binance_api_expire(username, {
                'title': f'⚠️ Your account and bots will stop working correctly in {_days_until_expipre_str}',
                'body': f'In {_days_until_expire}, Binance will deactivate the “<b>Enable Spot & Margin Trading</b>“ option on your connected API credentials. In order to allow your account and bots to function properly, make sure to update your “<b>Enable Spot & Margin Trading</b>“ option or create a new set of API credentials and reconnect them to your OB account. You can learn more about the restriction <a href=”https://www.binance.com/en/support/announcement/11e4c2f44e7a47b9b5fc0e479c0b256f” target=”_blank”>here</a>'
            })
            profile[self.object_name][f'is_notify_{_days_until_expire}'] = time.time() 
            self.dbs['users'][username] = profile

    def check_binance_api_expire(self):

        # last_check = self.dbs['globals'].get(self.__global_str_name, 0)
        # now = time.time()
        # if now - last_check < 24 * 60 * 60:
        #     return
        # self.dbs['globals'][self.__global_str_name] = now

        for username in self.dbs['users']:
            self.__check_user(username)

    def run(self):
        self.refresh_all_current_binance_expire_date(is_auto_manually=True)
        self.check_binance_api_expire()
