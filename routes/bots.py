from itertools import islice
from datetime import timedelta

from routes.utility.fee_per_trade import  OBTFeePerTrade
from routes.utility.ob_token import  get_price_in_usd

from routes.utility.token import OBToken
from .logging import RotatingFanoutHandler
from typing import Dict, List, Optional, Any, Union
from pydantic import BaseModel, confloat
from spectree import Response
from .spectree import spectree
from tradeEnv.metrics import UserMetrics
from tradeEnv.trade_api import CachedWrapper, TradeAPIRegistry, approx_conversion_rate
from tradeEnv.utils import span_from_candletype, to_internal_market
from falcon.status_codes import HTTP_400, HTTP_500
from tradeEnv.trade_filters import init_filter
import falcon
import uuid
import time
from .utils import assert_type
from .base import Route, StandardResponse, add_pkg, auth_guard
from routes.utility.acl import ACLManager
from routes.utility.users import UserManager
from .policy import HoldingTiers, SubscriptionFeePerTrade, SubscriptionPolicy
from .realtime import get_env
from .exchange import Exchange
from .utility.strategy import StrategyFactory
from .logging import getLogger
add_pkg()


class BotsByExchange(BaseModel):
    exchange: str
    active: List[str]
    archived: List[str]


class BotsCategorizedGetResp(BaseModel):
    __root__: List[BotsByExchange]


class CreateBotGetResp(BaseModel):
    __root__: List[str]


class CreateBotDeleteReq(BaseModel):
    uid: str


class CreateBotDeleteResp(BaseModel):
    success: bool = True


class CreateBotDeleteMessage(BaseModel):
    success: bool = False
    message: str


class StopLossModel(BaseModel):
    stop: confloat(gt=0.01, le=0.99)
    # E.g. stop=0.5, trailing=true; if 1.1 roi new stop-loss will be 0.6
    trailing: bool


class CreateBotPostReq(BaseModel):
    exchange: str
    market: str
    candles: Optional[str]
    nickname: Optional[str]
    strategy: str
    balance: confloat(gt=0., lt=1e10)
    duration: Optional[int]
    ml_boost: Optional[bool]
    stop_loss: Optional[StopLossModel]
    telegram_token: Optional[str]
    telegram_sent_username: Optional[str]
    twitter_tokens: Optional[List[str]]


class BotUpdateDetailsReq(BaseModel):
    uid: str
    stop_loss: Optional[Union[StopLossModel, Dict[bool, bool]]]


class CreateBotPostResp(BaseModel):
    success: bool = True
    uid: str


class PositionSizePostReq(BaseModel):
    pair: str
    exchange: str


class PositionSizePostResp(BaseModel):
    amount: float


class BotLogPostReq(BaseModel):
    uid: str


class BotVisiblePostReq(BaseModel):
    uid: str
    is_invisible: Optional[bool]


class BotLogPostResp(BaseModel):
    __root__: Dict[str, Optional[List[Dict[str, str]]]]


class BotVisibleResp(BaseModel):
    __root__: Dict[str, str]


class GetBotsCategorized(Route):
    @auth_guard
    @spectree.validate(resp=Response(HTTP_200=BotsCategorizedGetResp))
    def on_get(self, req, resp):
        self.mark_activity(req)
        profile = self.get_profile(req).unwrap()

        d = []
        for k in Exchange(self.dbs).get_exchange_configs().keys():
            d.append({'exchange': k, 'active': [], 'archived': []})

        for b in profile['bots']:
            bot = self.dbs['bots'][b]
            if bot.get('is_invisible', False):
                continue
            classtag = 'active' if bot['enabled'] else 'archived'
            for k in d:
                if k['exchange'] == bot['exchange']:
                    k[classtag].append(b)

        resp.media = d


class CreateBot(Route):
    @auth_guard
    @spectree.validate(resp=Response(HTTP_200=CreateBotGetResp))
    def on_get(self, req, resp):
        self.mark_activity(req)
        profile = self.get_profile(req).unwrap()
        resp.media = list(profile['bots'])

    @auth_guard
    @spectree.validate(json=CreateBotDeleteReq, resp=Response(HTTP_200=CreateBotDeleteResp, HTTP_400=CreateBotDeleteMessage))
    def on_delete(self, req, resp):
        self.mark_activity(req)
        username = self.get_username(req).unwrap()

        uid = req.media['uid']
        close_open_orders = req.media.get('close_open_orders', False)
        sell_active = req.media.get('sell_active', False)

        bots = self.dbs['users'][username].get('bots')
        if uid not in bots:
            resp.media = {'success': False, 'message': 'Bot not found for this user'}
            resp.status = falcon.HTTP_400
            return

        bot = self.dbs['bots'][uid]
        exchange = bot['exchange']
        market = bot['market']
        profile = self.get_profile(req).unwrap()
        
        warning = {}
        try:
            # TODO: schedule task instead of this sync shit
            if close_open_orders or sell_active:
                env = get_env(exchange, market, bot['candles'])
                env.set_user(bot['state'], profile)
                env.nstep()  # Update limit order status from the exchange

                if close_open_orders:
                    open_orders = bot['state'].open_orders
                    for order in list(open_orders.keys()):
                        # Cancel on the exchange, and mark as such on our side
                        env.get_api().cancel_order(profile['exchanges'][exchange], market, open_orders[order].txid)
                        env._reject_limit(open_orders[order].txid)

                if sell_active:
                    env.sellp(1.)
        except SystemExit:
            raise
        except:
            warning = {'warning': 'Failed to sell/close positions, please manually verify your desired position on the exchange'}

        bot['enabled'] = False
        bot['stop_time'] = time.time()

        self.dbs['bots'][uid] = bot

        resp.media = {'success': True, **warning}

    @auth_guard
    @spectree.validate(json=CreateBotPostReq, resp=Response(HTTP_200=CreateBotPostResp, HTTP_400=StandardResponse, HTTP_500=StandardResponse))
    def on_post(self, req, resp):
        self.mark_activity(req)
        username = self.get_username(req).unwrap()

        exchange = req.media['exchange']
        market = req.media['market']  # BTCUSDT
        candles = req.media.get('candles', '4h')
        strategy = req.media['strategy']
        balance = req.media['balance']
        ml_boost = req.media.get('ml_boost', False)
        stop_loss = req.media.get('stop_loss', None)
        nickname = req.media.get('nickname', None)
        # limit of active bot count
        profile = self.get_profile(req).unwrap()
        acl = ACLManager(self.dbs)
        policy: SubscriptionPolicy = acl.find_policy(SubscriptionPolicy._key, profile)
        # TODO: check max_balance_allowed

        if stop_loss:
            stop_loss['starting_portfolio'] = balance
            stop_loss['highest_portfolio'] = balance
        # if stop_loss and 'stop_loss' not in policy.benefits:
        #     resp.status = falcon.HTTP_400
        #     resp.media = {'error': 'Stop-loss not available for the current tier', 'code': 'benefit-not-in-tier'}
        #     return

        bot_count = sum(bool(self.dbs['bots'][b]['enabled']) for b in profile['bots'])
        allowed_bots = policy.allowed_bots
        if policy.sub == SubscriptionFeePerTrade.sub:
            holding_tier = acl.get_current_holding_tier(HoldingTiers._key,profile)
            allowed_bots = holding_tier.get().get('allowed_bots',allowed_bots)
            max_total_in_use = holding_tier.get().get('max_total_in_use',policy.max_total_in_use)

        
        assert bot_count + 1 <= allowed_bots, "Failed to create a bot: maximum active bot count reached"

        exchanges = Exchange(self.dbs).get_exchange_configs()
        assert exchange in exchanges, "Exchange not supported"
        assert market in exchanges[exchange]['pairs'], "Market pair not supported"
        assert candles in exchanges[exchange]['candles'], "Candles not supported"

        # Overwrite candles if strategy enforces it
        strat_factory = StrategyFactory(strategy, self.dbs)
        candles = strat_factory.get_candles(candles)
        strategy_status = strat_factory.get_proto().status

        UserManager(self.dbs).assert_strat_allowed(username, strategy, strategy_status)

        logger = getLogger('api.v1.createbot')

        tfilter = init_filter(exchange, to_internal_market(market))
        api = TradeAPIRegistry[exchange]()
        api = CachedWrapper(api, expire=60 * 60)
        prices = api.market_prices()
        current_price = approx_conversion_rate(*market.split(':'), prices)
        if current_price.is_err():
            logger.debug(f'Market unavailable {current_price.err()} on {market} {exchange}')
            resp.status = HTTP_500
            resp.media = {"error": "This market is currently unavailable, please try again later"}
            return

        current_price = current_price.unwrap()
        if tfilter.preprocess_trade(balance/current_price, current_price).is_err():
            logger.debug(f'Failed simulated trace {balance} at {current_price} on {market} {exchange} ({filter})')
            resp.status = HTTP_400
            resp.media = {"error": "Failed to create a bot: provided position size is too small for the selected market"}
            return

        uid = str(uuid.uuid4())

        self.dbs['users'](username, lambda user: user['bots'].add(uid))
        state = UserMetrics(uid, [0, balance])
        self.dbs['bots'][uid] = {
            'enabled': True,
            'is_invisible': False,
            'ml_boost': ml_boost,
            'exchange': exchange,
            'market': market,
            'strategy': strategy,
            'state': state,
            'candles': candles,
            'features': ['candle'],
            'user': username,
            'nickname': nickname,
            'stop_time': None,
            'start_time': time.time(),
            'stop_loss': stop_loss,
            'billing_start_portfolio': balance,
            'telegram_token': req.media.get('telegram_token', None),
            'telegram_sent_username': req.media.get('telegram_sent_username', None),
            'twitter_tokens': req.media.get('twitter_tokens', None)
        }

        resp.media = {'success': True, 'uid': uid}

    @auth_guard
    @spectree.validate(json=BotUpdateDetailsReq, resp=Response(HTTP_200=CreateBotDeleteResp, HTTP_400=StandardResponse, HTTP_500=StandardResponse))
    def on_put(self, req, resp):
        # Validate-then-update
        profile = self.get_profile(req).unwrap()
        policy: SubscriptionPolicy = ACLManager(self.dbs).find_policy(SubscriptionPolicy._key, profile)
        # TODO: check max_balance_allowed
        uid = req.media['uid']
        stop_loss = req.media.get('stop_loss')
        # Validate
        if uid not in profile['bots']:
            resp.status = falcon.HTTP_400
            resp.media = {'error': 'No bot found with that id'}
            return

        if stop_loss != None and 'stop_loss' not in policy.benefits:
            resp.status = falcon.HTTP_400
            resp.media = {'error': 'Stop-loss not available for the current tier', 'code': 'benefit-not-in-tier'}
            return

        # Update
        bot = self.dbs['bots'][uid]
        if stop_loss != None:
            bot['stop_loss'] = stop_loss
            bot['stop_loss']['starting_portfolio'] = bot['state'].portfolioValue
            bot['stop_loss']['highest_portfolio'] = bot['state'].portfolioValue

        self.dbs['bots'][uid] = bot
        resp.media = {'success': True}


class ProfileBots(Route):
    def __init__(self, dbs: dict):
        self.dbs = dbs

    @auth_guard
    def on_delete(self, req, resp):
        profile = self.get_profile(req).unwrap()

        close_open_orders = req.media.get('close_open_orders', False)
        sell_active = req.media.get('sell_active', False)

        for uid in profile['bots']:
            with self.dbs['bots'].transact(retry=True):
                bot = self.dbs['bots'][uid]
                exchange = bot['exchange']
                market = bot['market']

                # TODO: schedule task instead of this sync shit
                if close_open_orders or sell_active:
                    env = get_env(exchange, market, bot['candles'])
                    env.set_user(bot['state'], profile)
                    env.nstep()  # Update limit order status from the exchange

                if close_open_orders:
                    open_orders = bot['state'].open_orders
                    for order in list(open_orders.keys()):
                        # Cancel on the exchange, and mark as such on our side
                        env.get_api().cancel_order(profile['exchanges'][exchange], market, open_orders[order].txid)
                        env._reject_limit(open_orders[order].txid)

                if sell_active:
                    env.sellp(1.)

                bot['enabled'] = False
                bot['stop_time'] = time.time()

                self.dbs['bots'][uid] = bot


class PositionSize(Route):
    @spectree.validate(json=PositionSizePostReq, resp=Response(HTTP_200=PositionSizePostResp))
    def on_post(self, req, resp):
        self.mark_activity(req)
        pair = req.media['pair']
        exchange = req.media['exchange']

        filter = init_filter(exchange, to_internal_market(pair))
        minimum = filter.minNot or 10.

        resp.media = {'amount': minimum}


class BotLogs(Route):
    @auth_guard
    @spectree.validate(json=BotLogPostReq, resp=Response(HTTP_200=BotLogPostResp))
    def on_post(self, req, resp):
        self.mark_activity(req)
        uid = req.media['uid']

        profile = self.get_profile(req).unwrap()
        if uid not in profile['bots']:
            resp.media = {'success': False, 'message': 'Bot not found'}
            resp.status = falcon.HTTP_400
            return

        rfh = RotatingFanoutHandler()
        path = rfh.search_leaf(uid)
        if len(path) < 1 or len(path) > 1:
            resp.media = {'info': 'No logs available yet'}
            resp.status = falcon.HTTP_400
            return

        ldb = rfh.db.cache(path[0])
        keys = list(islice(ldb.iterkeys(reverse=True), 1000))
        data = dict([(key, ldb[key]) for key in keys])

        for k in data:
            if data[k] is None:
                continue

            for i in range(len(data[k])):
                del data[k][i]['process']

        resp.media = data


class BotVisible(Route):

    @auth_guard
    @spectree.validate(json=BotVisiblePostReq, resp=Response(HTTP_200=BotVisibleResp))
    def on_post(self, req, resp):
        uid = req.media['uid']
        bot = self.dbs['bots'].get(uid, False)
        if not bot:
            resp.media = {'error': f'Bod id {uid} is not exists!'}
            resp.status = falcon.HTTP_400
            return

        bot['is_invisible'] = req.media.get('is_invisible', True)
        self.dbs['bots'][uid] =bot
        resp.media = {'is_invisible': bot['is_invisible']}


class EstimateOBTFee(Route):

    @auth_guard
    def on_get(self,req,resp):
        profile = self.get_profile(req).unwrap()

        bots = profile.get('bots',[])
        if not bots:
            resp.media ={'avg_fee': 0,'estimate_duration':f"{0} seconds"}
            return
        
        obt_trx_instance = OBTFeePerTrade(self.dbs)

        count_bots = len(bots)
        total_fee,total_duration = 0,0
        for b in bots:
            bot = self.dbs['bots'][b]
            total_fee += obt_trx_instance.estimate_OBT_bot_fee(bot)
            total_duration +=span_from_candletype(bot.get('candels','1h'))
        
        avg_fee = total_fee/count_bots
        avg_duration = total_duration/ count_bots

        if avg_fee == 0 or avg_duration == 0:
            resp.media ={'avg_fee': 0,'estimate_duration':f"{0} second"}
            return


        balance=profile.get('obt_token',{}).get('balance',0)//OBToken.token_decimal
        estimate_duration = (balance/avg_fee)*avg_duration
        resp.media = {'avg_fee': f"{avg_fee:.2} OBT",'estimate_duration': str(timedelta(seconds=estimate_duration)),'avg_duration':avg_duration,'estimate_duration_sec':estimate_duration}
        return
        
