# from tradeEnv.exchanges.BinanceFutures import BinanceFutures
from tradeEnv.exchanges import *
from tradeEnv.exchanges.BitPandaPro import BitpandaPro
from tradeEnv.exchanges.Bitvavo import BitvavoAPI
from tradeEnv.exchanges.Binance import BinanceAPI
from tradeEnv.exchanges.BinanceUS import BinanceUSAPI
from tradeEnv.exchanges.Kraken import KrakenAPI
from tradeEnv.exchanges.BotsIO import BotsIOAPI
from types import MethodType
from routes.db import get_tmp_cache
from typing import *
from typing_extensions import Literal
from pydantic import BaseModel
from result import Ok, Err, Result

Exchanges = ('Binance', 'Kraken', 'Bitvavo')
ExchangeType = Literal[Exchanges]

class CachedWrapper:
    def __init__(self, api_base, expire=60 * 5):
        self.api_base = api_base
        self.cache = get_tmp_cache('cached_api')
        self.expire = expire

    def __getattr__(self, name: str) -> Any:
        attr = self.api_base.__getattribute__(name)
        if isinstance(attr, MethodType):
            return self.cache.memoize(tag='cached_api', expire=self.expire)(attr)
        else:
            return attr


def _recursive_conversion(frm: str, to: str, prices: PriceDict, stack: Set[str] = set(), depth_ttl=2) -> Result[float, str]:
    # TODO: generalize graph/tree search across markets
    if depth_ttl <= 0:
        return Err('Failed to find suitable conversion rate (ttl expired)')

    for market in list(prices.keys()):
        pair = market.split(':')
        if pair[1] in stack:
            continue

        if pair[0] == frm:
            if pair[1] == to:
                return Ok(prices[market])
            intermediate_to = pair[1]
            stack.add(intermediate_to)
            res = _recursive_conversion(intermediate_to, to, prices, stack, depth_ttl-1)
            stack.pop()
            if res.is_ok():
                return Ok(res.ok() * prices[market])

    return Err('Failed to find suitable conversion rate (exhaustive)')


def approx_conversion_rate(frm: str, to: str, prices: PriceDict, max_depth=2) -> Result[float, str]:
    if frm == to:
        return Ok(1.)

    # Add reversed markets for easily lookup
    def reverse_market(k, v):
        return (':'.join(reversed(k.split(':'))), 1/(v + 1e-12),)
    prices = {**prices, **dict([reverse_market(k, prices[k]) for k in prices])}

    key = f'{frm}:{to}'
    if key in prices:
        return Ok(prices[key])

    return _recursive_conversion(frm, to, prices, stack={frm}, depth_ttl=max_depth)


TradeAPIRegistry: Dict[str, Type[AbstractTradeAPI]] = {
    'Binance.US': BinanceUSAPI,
    'Binance': BinanceAPI,
    'Kraken': KrakenAPI,
    'BotsIO': BotsIOAPI,
    'BitpandaPro': BitpandaPro,
    'Bitvavo': BitvavoAPI,
}


class FilledOrder(BaseModel):
    exchange: str
    pair: str
    date: Union[float, int]
    side: SideType
    order_type: Literal['MARKET', 'LIMIT']
    price: float
    volume: float
    fee: float
    fee_asset: str

    def get_fee(self):
        if not self.fee_asset:
            return 0

        if self.fee_asset == 'KFEE':
            return self.fee / 100

        api = CachedWrapper(TradeAPIRegistry[self.exchange]())
        prices = api.market_prices()
        _pair = self.pair.split(':')
        ratio = approx_conversion_rate(self.fee_asset, _pair[1], prices)
        if ratio.is_err():
            return 0

        ratio = ratio.unwrap()
        return self.fee * ratio

    def get_tok_diff(self):
        vol = self.volume

        if self.side == 'SELL':
            vol = -vol

        if self.pair.startswith(self.fee_asset):
            vol -= self.fee

        return vol

    def get_cur_diff(self):
        vol = self.volume * self.price

        if self.side == 'BUY':
            vol = -vol

        if self.pair.endswith(self.fee_asset):
            vol -= self.fee

        return vol
