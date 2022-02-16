import os
import requests
from datetime import datetime
from result import Result, Ok, Err
from routes.utils import atomic_memoize
from routes.db import get_tmp_cache
from routes.utility.users import UserManager


class Intercom():

    base_url = 'https://api.intercom.io/'
    token = 'dG9rOjdmYjRjZGEwX2VjNzFfNGMyYl84Y2JmXzRmYmQ4N2UxYzQwZDoxOjA='
    __data_types = {
        'integer': int,
        'float': float,
        'string': str
    }
    hearders = {
        'Authorization': 'Bearer {token}',
        'Accept': 'application/json',
        'Content-Type': 'application/json'}

    def __init__(self, token: str = None) -> None:
        if token:
            self.token = token
        self.hearders['Authorization'] = self.hearders['Authorization'].format(token=self.token)

    def request(self, path: str, data: dict = {}, method: str = 'GET') -> Result:
        res = requests.request(url=f'{self.base_url}/{path}', json=data, method=method, headers=self.hearders)
        if res.status_code == 200 or res.status_code == 202:
            return Ok(res.json())
        return Err(res)

    def __create_data_atribute(self, **kwargs):
        data = {'model': 'contact',
                'data_type': 'string',
                **kwargs}
        return self.request(path='data_attributes', data=data, method='POST')

    def update_contacts(self, id: str, custom_attributes: dict):
        data = {'custom_attributes': custom_attributes}
        return self.request(path=f'contacts/{id}', data=data, method='PUT')

    def get_all_contacts(self) -> list:
        contacts = []
        path = 'contacts?per_page=150&starting_after={starting_after}'
        _starting_after = ''
        while True:
            res = self.request(path=path.format(starting_after=_starting_after), data={}, method='GET')
            if res.is_err():
                break
            _data = res.ok()
            contacts.extend(_data.get('data'))
            _starting_after = _data.get('pages', {}).get('next', {}).get('starting_after')
            if not _starting_after:
                break

        return contacts

    def _create_data_atributes(self, custom_attributes: dict):
        for k, v in custom_attributes.items():
            self.__create_data_atribute(**{'name': k, 'data_type': v, 'description': v.replace('_', ' ')})


class OBIntercomDataFeed:

    custom_attributes = {
        'ob_number_of_exchanges': 'integer',
        'ob_balance_in_use': 'float',
        'ob_subscription_plan': 'string',
        'ob_subscription_plan_max_balance': "float",
        'ob_subscription_plan_max_bots': "float",
        'ob_number_of_active_bots': "integer",
        'ob_number_of_archived_bots': "integer",
        'ob_profit_until_now': "integer",
        'ob_number_of_referrals': "integer",
        'ob_env': "string"
    }

    def __init__(self, dbs: dict, token: str = None) -> None:
        self.intercom = Intercom(token=token)
        self.dbs = dbs
        self.um = UserManager(dbs)
        self.cache = get_tmp_cache('stats')

    def __get_user_data(self, email):
        if not self.dbs['users'].get(email):
            return {}
        policy = self.um.get_policy(email)
        return {
            'ob_number_of_exchanges': len(self.um.get_portfolio(email=email)),
            'ob_balance_in_use': self.um.get_bot_balance(email),
            'ob_subscription_plan': policy.sub if policy else '',
            'ob_subscription_plan_max_balance': policy.max_total_in_use if policy else 0,
            'ob_subscription_plan_max_bots': policy.allowed_bots if policy else 0,
            'ob_number_of_active_bots': len(self.um.get_active_bots(email)),
            'ob_number_of_archived_bots': len(self.um.get_archived_bots(email)),
            # 'ob_profit_until_now': '',
            'ob_number_of_referrals': len(self.um.get_referrals(email)),
            'ob_env': os.environ.get('ENVIRONMENT', "dev")
        }

    def create_data_atributes(self):
        is_created = self.dbs['globals'].get('OBIntercomDataFeed:create_data_atributes', False)
        if is_created:
            return
        self.dbs['globals']['OBIntercomDataFeed:create_data_atributes'] = True
        self.intercom._create_data_atributes(self.custom_attributes)

    def feed_data(self):
        last_run = self.dbs['globals'].get('OBIntercomDataFeed:feed_data', 0)
        now = datetime.now().timestamp()
        if last_run + 24 * 60 * 60 > now:
            return
        self.dbs['globals']['OBIntercomDataFeed:feed_data'] = now

        contacts = atomic_memoize(self.cache, self.intercom.get_all_contacts)
        for u in contacts:
            _email = u.get('email')
            if not _email:
                continue
            d = self.__get_user_data(u.get('email'))
            if d and u.get('id'):
                self.intercom.update_contacts(id=u['id'], custom_attributes=d)

    def run(self):
        self.create_data_atributes()
        self.feed_data()
