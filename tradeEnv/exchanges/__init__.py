import logging
from result import Ok, Err, Result
from typing import * 
from typing_extensions import Literal
from pydantic import BaseModel, conlist, constr, validate_arguments
from tradeEnv.meta import create_deco_meta
from tradeEnv.api_adapter import Order
import uuid

pydanticMeta = create_deco_meta([validate_arguments])

StrDict = Dict[str, str]
SignedReq = Tuple[str, str, StrDict]
IntIsh = Union[str, int]
CandleType = constr(regex=r'^[0-9]+(w|d|h|m|s)$')
RawCandleDataType = Optional[Dict[IntIsh, Dict[str, float]]]

ApiKeysType = conlist(str, min_items=2, max_items=3)

SideType = Literal['BUY', 'SELL']
PriceDict = BalanceDict = Dict[str, float]
TxID = Union[str, int]


class LimitOrderStatus(BaseModel):
    exec_vol: float
    exec_fract: float
    price: float
    date: int


class TradeFilterData(BaseModel):
    minLot: Optional[float]
    minNot: Optional[float]
    lotDenom: Optional[int]
    priceDenom: Optional[int]


class AbstractTradeAPI(metaclass=pydanticMeta):
    hooks = []

    def __init__(self, logger=None):
        self.logger = logger

    def get_logger(self):
        if getattr(self, 'logger', None):
            return self.logger
        logger = logging.getLogger()
        logger.addHandler(logging.NullHandler())
        return logger

    def sign_api(self,          api_keys: ApiKeysType, query: StrDict = {}, body: str = '', headers: StrDict = {}, urlpath: str = '') -> SignedReq: pass
    def balance(self,           api_keys: ApiKeysType) -> Result[BalanceDict, str]: pass
    def check_auth(self,        api_keys: ApiKeysType) -> bool: pass
    def stoploss_order(self,    api_keys: ApiKeysType, market: str, amount: float, stop_price: float) -> Result[str, str]: pass
    def limit_order(self,       api_keys: ApiKeysType, market: str, side: SideType, amount: float, price: float) -> Result[str, str]: pass
    def limit_details(self,     api_keys: ApiKeysType, market: str, txid: TxID) -> Result[LimitOrderStatus, str]: pass
    def cancel_order(self,      api_keys: ApiKeysType, market: str, txid: TxID) -> Result[str, str]: pass
    def market_order(self,      api_keys: ApiKeysType, market: str, side: SideType, amount: float) -> Result[Order, str]: pass
    def update_ohlc(self,       market: str, candle_type: CandleType, start_time: IntIsh) -> Result[RawCandleDataType, str]: pass
    def filters(self) -> Dict[str, TradeFilterData]: pass
    def market_prices(self) -> PriceDict: pass
    @property
    def clientOrderId(self)->str:
        if self._map.get('apiAgentCode'):
            return f"X-{self._map['apiAgentCode']}-{str(uuid.uuid4().hex[:16])}"
        return str(uuid.uuid4().hex[:16])
