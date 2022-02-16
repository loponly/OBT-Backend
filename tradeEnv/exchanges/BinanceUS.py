from result import Result, Err, Ok
from typing import *

from tradeEnv.exchanges import *
from tradeEnv.exchanges.Binance import BinanceAPI
from tradeEnv.api_adapter import ApiAdapter, binance_us_map

class BinanceUSAPI(BinanceAPI):
    name = 'Binance.US'
    _map = binance_us_map 
    def balance(self, api_keys: ApiKeysType) -> Result[BalanceDict, str]:
        super().balance(api_keys)
        api = ApiAdapter(self._map, 'tmp', hooks=self.hooks)
        query, body, headers = self.sign_api(api_keys)
        req = api.call('api/v3/account?%s' % query, data=body or None, method='GET', headers=headers)
        data = req.json()
        if self._is_err(data):
            self.get_logger().debug(f'Failed to get balance {data}')
            return self._parse_err(data['code'], data.get('msg', None))

        ret = {x['asset']: float(x['free']) for x in data['balances']}
        return Ok(ret)

