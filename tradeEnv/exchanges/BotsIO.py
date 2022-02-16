from tradeEnv.exchanges.Binance import BinanceAPI
from tradeEnv.exchanges import *
from result import Result, Err, Ok
from datetime import datetime
from typing import *
import requests
import uuid


class BotsIOAPI(AbstractTradeAPI):
    name = 'BotsIO'
    base_url = 'https://signal.revenyou.io/paper/api/signal/v2'
    date_format = '%Y-%m-%d %H:%M:%S'
    _map = {}

    def __init__(self, logger=None):
        super().__init__(logger=logger)
        self.binance = BinanceAPI(logger)

    def limit_order(self, api_keys: ApiKeysType, market: str, side: SideType, amount: float, price: float) -> Result[str, str]:
        super().limit_order(api_keys, market, side, amount, price)

        base_asset, quote_asset = market.split(':')

        req = requests.request('POST', f'{self.base_url}/placeOrder', json={
            "signalProvider": api_keys[0],
            "signalProviderKey": api_keys[1],
            "extId": str(uuid.uuid4()),  # TODO import
            "exchange": "binance",
            "baseAsset": base_asset,
            "quoteAsset": quote_asset,
            "type": "limit",
            "side": side.lower(),
            "limitPrice": str(price),
            "qtyPct": str(amount),
            "ttlType": "gtc",
            "responseType": "FULL"
        })

        data = req.json()

        self.get_logger().debug(f'Attempted {side} LIMIT trade: {req.text}, {req.reason}, {req.status_code}')

        if data['success']:
            return Ok(data['orderId'])
        else:
            return Err('failed-exchange-call')

    def check_auth(self, api_keys: ApiKeysType) -> bool:
        super().check_auth(api_keys)
        req = requests.request('GET', f'{self.base_url}/getOrders', params={
            "signalProvider": api_keys[0],
            "signalProviderKey": api_keys[1],
        })
        return req.status_code < 400

    def limit_details(self, api_keys: ApiKeysType, market: str, txid: TxID) -> Result[LimitOrderStatus, str]:
        super().limit_details(api_keys, market, txid)

        req = requests.request('GET', f'{self.base_url}/getOrderInfo', params={
            "signalProvider": api_keys[0],
            "signalProviderKey": api_keys[1],
            "orderId": txid
        })

        self.get_logger().debug(f'Attempted get details on order {txid}: {req.text}, {req.reason}, {req.status_code}')

        data = req.json()
        if data['success']:
            out = {
                'exec_vol': None,
                'exec_frac': float(data['qtyExecPct']) / float(data['qtyPct']),
                'price': float(data['limitPrice']),
                'date': int(
                    datetime.strptime(
                        data['lastChangeTs'], self.date_format
                    ).timestamp()
                ),
            }

            return Ok(out)
        else:
            return Err('failed-exchange-call')

    def cancel_order(self, api_keys: ApiKeysType, market: str, txid: TxID) -> Result[str, str]:
        super().cancel_order(api_keys, market, txid)

        req = requests.request('POST', f'{self.base_url}/cancelOrder', json={
            "signalProvider": api_keys[0],
            "signalProviderKey": api_keys[1],
            "orderId": txid
        })

        self.get_logger().debug(f'Attempted to cancel on order {txid}: {req.text}, {req.reason}, {req.status_code}')

        return Ok('success') if req.json()['success'] else Err('failed-order-cancel')

    def update_ohlc(self, market: str, candle_type: CandleType, start_time: IntIsh) -> Result[RawCandleDataType, str]:
        return self.binance.update_ohlc(market, candle_type, start_time)

    def market_prices(self) -> PriceDict:
        return self.binance.market_prices()

    def _balance_pct(self, api_keys: ApiKeysType, base: str = 'USDT') -> Result[BalanceDict, str]:
        req = requests.request('GET', f'{self.base_url}/getBotAssetsPct', params={
            "signalProvider": api_keys[0],
            "signalProviderKey": api_keys[1],
            "exchange": "binance",
            "baseAsset": base
        })

        self.get_logger().debug(f'Attempted to get balance {api_keys[0]}: {req.text}, {req.reason}, {req.status_code}')
        data = req.json()
        if data['success']:
            return Ok({base: data['baseTotal']})
        else:
            return Err('failed-exchange-call')

    def _get_orders(self, api_keys: ApiKeysType):
        req = requests.request('GET', f'{self.base_url}/getOrders', params={
            "signalProvider": api_keys[0],
            "signalProviderKey": api_keys[1]
        })

        self.get_logger().debug(f'Attempted to get balance {api_keys[0]}: {req.text}, {req.reason}, {req.status_code}')
        data = req.json()
        if data['success']:
            return Ok(data['orders'])
        else:
            return Err('failed-exchange-call')

    def balance(self, api_keys: ApiKeysType) -> Result[BalanceDict, str]:
        return Ok({'BUSD': 100, 'USDT': 100})

    def filters(self) -> Dict[str, TradeFilterData]:
        filters = self.binance.filters()
        for k in filters:
            filters[k]['minLot'] = None
            filters[k]['minNot'] = None
        # BotsIO is percentage based so there aren't any filters we can use
        return {}
