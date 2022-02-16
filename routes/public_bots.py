import falcon
import numpy as np
from .base import Route
from .utility.public_bot_stats import PublicBotStats, stats_timeranges

from datetime import datetime
from .spectree import spectree
from spectree import Response
from pydantic import BaseModel
from typing import Dict, Optional


class CumulativeStatsModel(BaseModel):
    total_active: int
    total_trades: int
    total_profit: int


class PublicStrategiesGetReq(BaseModel):
    seconds: Optional[int]
    min_duration: Optional[int]
    market: Optional[str]
    enabled_only: Optional[bool]


class PublicROIGetReq(PublicStrategiesGetReq):
    strategy: Optional[str]


class PublicStratBotModel(BaseModel):
    exchange: str
    market: str
    start_time: int
    days_active: float
    roi: float
    status: bool


class PublicStrategyModel(BaseModel):
    created: Optional[int] = 0
    trades: int = 0
    avg_duration: float
    avg_roi_bot: float
    avg_roi_month: float
    avg_roi_trades: float
    name: str
    image: str
    description: str
    bots: Dict[str, PublicStratBotModel]


class PublicStrategiesGetResp(BaseModel):
    result: Dict[str, PublicStrategyModel]


class PublicBotStatsReq(BaseModel):
    market: Optional[str]
    start_time: Optional[int]
    active_from: Optional[int]
    is_enabled: Optional[bool]
    period: Optional[str]
    num_of_logs: Optional[int]


class PublicStrategies(Route):
    @spectree.validate(query=PublicStrategiesGetReq, resp=Response(HTTP_200=PublicStrategiesGetResp))
    def on_get(self, req, resp):
        seconds = req.params.get('seconds', None)
        if seconds:
            seconds = int(seconds)
        min_duration = req.params.get('min_duration', None)
        if min_duration:
            min_duration = int(min_duration)
        market = req.params.get('market', None)
        enabled_only = req.params.get('enabled_only', None) != 'false'
        result = PublicBotStats(self.dbs).get_botstats(seconds, enabled_only=enabled_only, market=market, min_duration=min_duration)
        resp.media = {'result': result}


class PublicROI(Route):
    @spectree.validate(query=PublicROIGetReq)
    def on_get(self, req, resp):
        seconds = req.params.get('seconds', None)
        if seconds:
            seconds = int(seconds)
        min_duration = req.params.get('min_duration', None)
        if min_duration:
            min_duration = int(min_duration)
        market = req.params.get('market', None)
        strategy_id = req.params.get('strategy', None)
        enabled_only = req.params.get('enabled_only', None) != 'false'
        result = PublicBotStats(self.dbs).get_roi_range(seconds, strategy=strategy_id, enabled_only=enabled_only, market=market, min_duration=min_duration)
        resp.media = {'result': result, 'min_timestamp': min(result) if result else None}


class HighestROI(Route):
    @spectree.validate(query=PublicROIGetReq)
    def on_get(self, req, resp):
        seconds = req.params.get('seconds', None)
        if seconds:
            seconds = int(seconds)
        min_duration = req.params.get('min_duration', None)
        if min_duration:
            min_duration = int(min_duration)
        market = req.params.get('market', None)
        strategy_id = req.params.get('strategy', None)
        enabled_only = req.params.get('enabled_only', None) != 'false'
        result = PublicBotStats(self.dbs).get_roi_range(seconds, strategy=strategy_id, enabled_only=enabled_only, market=market, min_duration=min_duration)
        resp.media = max(result.values()) if result else 0


class BotStatsCumulativeStats(Route):
    @spectree.validate(resp=Response(HTTP_200=CumulativeStatsModel))
    def on_get(self, req, resp):
        pbs = PublicBotStats(self.dbs)
        data = pbs.get_general_stats()
        summary = pbs.get_botstats_summary(None)
        total_trades = np.sum([summary[k]['trades'] for k in summary])
        total_duration = np.sum([summary[k]['avg_duration'] * summary[k]['count'] for k in summary])
        avg_monthly_roi = np.mean([summary[k]['avg_roi_month'] for k in summary])
        resp.media = {**data, 'total_trades': int(total_trades), 'total_active': int(total_duration), 'avg_monthly_roi': float(avg_monthly_roi)}


class GetBotReports(Route):

    @spectree.validate(query=PublicBotStatsReq)
    def on_get(self, req, resp):
        pb = PublicBotStats(self.dbs)
        param = dict(market=req.params.get('market', False), start_time=req.params.get('start_time', 0), active_from=req.params.get('active_from', 0), is_enabled=req.params.get('is_enabled', False))
        resp.media = {
            'balance_distrbution': pb.get_bot_balance_distrbutions(**param),
            'bot_trade_logs': pb.get_bot_trade_logs(**param),
            'get_trade_distribution': pb.get_trade_distribution(_period=req.params.get("period", "day"), **param)
        }


class GetBotBalanceDistrbution(Route):

    @spectree.validate(query=PublicBotStatsReq)
    def on_get(self, req, resp):
        param = dict(market=req.params.get('market', False), start_time=req.params.get('start_time', 0), active_from=req.params.get('active_from', 0), is_enabled=req.params.get('is_enabled', False))
        resp.media = PublicBotStats(self.dbs).get_bot_balance_distrbutions(**param)


class GetBoTradeLogs(Route):

    @spectree.validate(query=PublicBotStatsReq)
    def on_get(self, req, resp):
        param = dict(market=req.params.get('market', False), start_time=req.params.get('start_time', 0), active_from=req.params.get('active_from', 0), is_enabled=req.params.get('is_enabled', False))
        resp.media = PublicBotStats(self.dbs).get_bot_trade_logs(**param)[-int(req.params.get('num_of_logs', 50)):]


class GetTradeDistribution(Route):

    @spectree.validate(query=PublicBotStatsReq)
    def on_get(self, req, resp):
        param = dict(market=req.params.get('market', False), start_time=req.params.get('start_time', 0), active_from=req.params.get('active_from', 0), is_enabled=req.params.get('is_enabled', False))
        resp.media = PublicBotStats(self.dbs).get_trade_distribution(_period=req.params.get("period", "day"), **param)
