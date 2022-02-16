from distutils.log import log
import logging
import falcon
import time
from routes.utility.solana_api import SolanaApi

from routes.utility.public_bot_stats import timestamp_round_to_hour
from routes.realtime import get_env
from routes.utils import atomic_memoize
from .base import Route, auth_guard
from .utility.strategy import StrategyFactory

from .spectree import spectree
from spectree import Response
from pydantic import BaseModel
from typing import Dict, List, Optional, Any


class BotPortfolioGetReq(BaseModel):
    days: Optional[int]
    botid: str


class BotPortfolioGetResp(BaseModel):
    result: Dict[int, float]


class BotPortfolioGetMessage(BaseModel):
    success: bool = False
    message: str


class TradeLogModel(BaseModel):
    date: Optional[int]
    price: float
    side: str = 'actually called type'
    amount: float
    pair: Optional[str]
    order_type: Optional[str]
    fee: Optional[float]
    fee_asset: Optional[str]
    balance: Optional[float]
    change: Optional[float]


class OpenOrderModel(BaseModel):
    side: str
    org_vol: float
    volume: float
    price: float
    createtime: int
    expire_time: int
    txid: str
    order_type: Optional[str]


class BotStatsModel(BaseModel):
    uid: str
    startingBalance: List[float]
    curBalance: float
    tokBalance: float
    portfolioValue: float
    max_balance: float
    min_balance: float
    trade_log: List[TradeLogModel]
    in_fees: float
    open_orders: Dict[str, OpenOrderModel]
    stop_loss: Optional[Dict[str, Any]]
    nickname: Optional[str]
    exchange: str
    strategy: str
    strategy_name: Optional[str]
    market: str
    candles: str
    ml_boost: bool
    start_time: int
    stop_time: Optional[int]
    enabled: bool
    bah_roi: Optional[float]
    avg_fee: float
    avg_roi_trade: float
    avg_roi_month: float
    description: Optional[str]
    image: Optional[str]



class GetBotStatsPostReq(BaseModel):
    uid: str


class GetBotStatsPostResp(BaseModel):
    __root__: BotStatsModel


class GetBotStatsPostMessage(BaseModel):
    message: str


class GetBotStatsGetReq(BaseModel):
    enabled: bool


class GetBotStatsGetResp(BaseModel):
    __root__: Dict[str, BotStatsModel]


class BotPortfolio(Route):
    @auth_guard
    @spectree.validate(resp=Response(HTTP_400=BotPortfolioGetMessage))
    def on_get(self, req, resp):
        days = req.params.get('days', None)
        botid = req.params.get('botid', None)
        with_price = req.params.get('with_price', None)
        profile = self.get_profile(req).unwrap()
        if not botid or botid not in profile['bots']:
            resp.media = {'success': False, 'message': 'Invalid bot id'}
            resp.status = falcon.HTTP_400
            return

        # TODO: 1 day from the last datapoint?
        result = self.dbs['bot_portfolios'].get(botid, {})
        if days:
            time = get_start_from_days(int(days))
            temp = list(filter(lambda x: x >= time, result))
            result = {t: result[t] for t in temp}

        if req.params.get('is_percentage', 'false') != 'false' and result:
            _init_balance = result[list(result)[0]]
            result = {k: round(((v-_init_balance)/v)*100, 2) for k, v in result.items()}

        if with_price:
            bot = self.dbs['bots'][botid]
            rte = get_env(bot.get('exchange', 'Binance'), bot.get('market', 'BTC:EUR'), '1h')
            price_data = rte.mi.historical_to_dict(with_price)
            result = {timestamp_round_to_hour(k): {'bot_portfolio': v, 'token_price': price_data.get(timestamp_round_to_hour(k), 0)} for k, v in result.items()}
            if bot.get('starting_price') and bot.get('bah_roi') and len(result) > 0:
                result[max(result)]['token_price'] = bot['bah_roi'] * bot['starting_price'] + bot['starting_price']
        resp.media = {'result': result}
        resp.status = falcon.HTTP_200


def get_start_from_days(days):
    period = days * 24 * 60 * 60
    return int(time.time()) - period


class GetProfileSummary(Route):
    @auth_guard
    def on_get(self, req, resp):
        self.mark_activity(req)
        profile = self.get_profile(req).unwrap()
        if len(profile['bots']) < 1:
            resp.media = {'roi_avg': 0, 'profit_sum': 0, 'coins': {}, 'tokens': {}}
            return

        roi_sum = 0
        roi = 0
        profit_sum = 0
        roi_count = 0
        coins_usd = {}
        tokens = {}
        for botid in profile['bots']:
            bot = self.dbs['bots'][botid]
            pair = bot['market'].split(':')
            if bot['enabled']:
                cur_in_order, tok_in_order = 0., 0.
                for order in bot['state'].open_orders.values():
                    if order.side.upper() == 'SELL':
                        tok_in_order += order.volume
                    elif order.side.upper() == 'BUY':
                        cur_in_order += order.volume * order.price

                roi_sum += (bot['state'].portfolioValue - bot['state'].startingBalance[1]) / bot['state'].startingBalance[1]
                profit_sum += bot['state'].portfolioValue - bot['state'].startingBalance[1]
                roi_count += 1
                coins_usd[pair[0]] = coins_usd.get(pair[0], 0) + (bot['state'].portfolioValue - bot['state'].curBalance - cur_in_order)
                coins_usd[pair[1]] = coins_usd.get(pair[1], 0) + bot['state'].curBalance + cur_in_order
                tokens[pair[0]] = tokens.get(pair[0], 0) + bot['state'].tokBalance + tok_in_order
                tokens[pair[1]] = tokens.get(pair[1], 0) + bot['state'].curBalance + cur_in_order

        if roi_count > 0:
            roi = roi_sum / roi_count

        resp.media = {'roi_avg': roi, 'profit_sum': profit_sum, 'coins': coins_usd, 'tokens': tokens}


class GetBotStats(Route):
    def _get_stats(self, botid,token_images:dict={},token_address:dict={}):
        bot = self.dbs['bots'][botid]
        additional = {}

        try:
            strat_meta = StrategyFactory(bot['strategy'], self.dbs)
            strategy_name = strat_meta.get_name()
            proto = strat_meta.get_proto()
            additional = {
                **additional,
                'description': proto.strategy_description,
                'image': proto.strategy_image,
            }
            all_images_owned = SolanaApi(self.dbs).get_image_urls(token_address)
            _strategy_name = strategy_name.lower().split(' ')[0]
            if all_images_owned:
                additional['image'] = all_images_owned.get(_strategy_name,{}).get(token_images.get(_strategy_name,''),proto.strategy_image)
        except SystemExit:
            raise
        except Exception as e:
            logging.error(str(e))
            strategy_name = '[Deprecated]'
        return {
            **bot['state'].to_json(),
            'strategy_name': strategy_name,
            'avg_fee': self.get_avg_fee(bot),
            'avg_roi_trade': self.get_avg_roi_trade(bot),
            'avg_roi_month': self.get_avg_roi_month(bot),
            **additional
        }

    def get_stats(self, botid):
        bot = self.dbs['bots'][botid]
        if bot.get('is_invisible'):
            return {}

        profile = self.dbs['users'][bot['user']]
        token_images = profile.get('obt_token',{}).get('NFT',{}).get('token_images',{})
        token_address=profile.get('obt_token',{}).get('NFT',{}).get('token_address',{})

        return {
            'nickname': bot.get('nickname', None),
            'exchange': bot['exchange'],
            'strategy': bot['strategy'],
            'market': bot['market'],
            'candles': bot['candles'],
            'ml_boost': bot['ml_boost'],
            'start_time': bot['start_time'],
            'stop_time': bot['stop_time'],
            'enabled': bot['enabled'],
            'bah_roi': bot.get('bah_roi', 0),  # TODO: check policy
            'stop_loss': bot.get('stop_loss'),
             **atomic_memoize(self.dbs['cache'], self._get_stats, botid=botid,token_images=token_images, token_address=token_address, _expire=60*60)
        }

    @auth_guard
    @spectree.validate(json=GetBotStatsPostReq, resp=Response(HTTP_200=GetBotStatsPostResp, HTTP_400=GetBotStatsPostMessage))
    def on_post(self, req, resp):
        self.mark_activity(req)
        profile = self.get_profile(req).unwrap()
        botid = req.media['uid']
        if botid not in profile['bots']:
            resp.media = {'message': 'No access to bot %s' % botid}
            resp.status = falcon.HTTP_400
            return

        resp.media = self.get_stats(botid)

    @auth_guard
    @spectree.validate(resp=Response(HTTP_200=GetBotStatsGetResp))
    def on_get(self, req, resp):
        self.mark_activity(req)
        profile = self.get_profile(req).unwrap()
        states = {}
        for bot in profile['bots']:
            _stats = self.get_stats(bot)
            if _stats:
                states[bot] = _stats

        resp.media = states

    def get_avg_fee(self, bot):
        trade_log = bot['state'].trade_log
        total_fee = bot['state'].in_fees
        total_amount = sum(trade['amount'] * trade['price'] for trade in trade_log)

        if total_amount == 0:
            return 0

        return total_fee / total_amount

    def get_avg_roi_trade(self, bot):
        trade_log = bot['state'].trade_log
        log_count = len(trade_log)
        if log_count == 0:
            return 0

        roi = (bot['state'].portfolioValue - bot['state'].startingBalance[1]) / bot['state'].startingBalance[1]
        return roi / log_count

    def get_avg_roi_month(self, bot):
        roi = (bot['state'].portfolioValue - bot['state'].startingBalance[1]) / bot['state'].startingBalance[1]

        duration = float((bot['stop_time'] - bot['start_time']) if (bot['stop_time'] or 1e13) < time.time() else time.time() - bot['start_time'])
        return (roi / (round(duration/60/60/24)) if duration/60/60/24 > 0.5 else 0) * 30

