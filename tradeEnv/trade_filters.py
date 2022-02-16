from .trade_api import TradeAPIRegistry
from .maths import quantize_float
from .meta import initializer
from diskcache import Cache
from result import Ok, Err, Result

def init_filter(exchange, marketid):
    gcache = Cache('store/db/api_adapter', sqlite_synchronous=2)

    @gcache.memoize(expire=3600 * 24, tag='exchange-filters')
    def update_requirements(exchange):
        """
        Updates the internal database using data from the exchanges
        """
        api = TradeAPIRegistry[exchange]()
        return api.filters()

    @gcache.memoize(expire=3600 * 24, tag='exchange-filter')
    def get_filter(exchange, market) -> TradeFilter:
        filters = update_requirements(exchange)

        # If we can't find it just assume it's all good
        if market not in filters:
            print(f"WARNING: missing filters for {exchange} {market}")
            return TradeFilter()

        return TradeFilter(**filters[market])
    
    return get_filter(exchange, marketid)

class TradeFilter:
    @initializer
    def __init__(self, minLot=None, minNot=None, priceDenom=None, lotDenom=None, **kwargs):
        pass

    def preprocess_trade(self, volume, close, price=None) -> Result[tuple, str]:
        volume = float(volume)
        
        if price != None:
            price = float(price)
            if self.priceDenom != None:
                price = quantize_float(price, int(self.priceDenom))
            else:
                price = quantize_float(price, 5)

        if self.lotDenom != None:
            volume = quantize_float(volume, int(self.lotDenom))
        else:
            volume = quantize_float(volume, 8)

        if self.minNot != None and float(self.minNot) > volume * close:
            return Err(f"Failed min notional size test ({self.minNot} > {volume*close})")

        if self.minLot != None and float(self.minLot) > volume:
            return Err(f"Failed min lot size test ({self.minLot} > {volume})")
        
        if volume < 1e-6:
            return Err(f"Minimal volume of 1e-6 required")

        return Ok((volume, price,))

    def __str__(self) -> str:
        return f"[{', '.join([f'{x}: {self.__dict__[x]}' for x in self.__dict__])}]"
