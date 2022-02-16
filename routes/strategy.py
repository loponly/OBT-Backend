import uuid

from routes.utility.solana_api import SolanaApi
from .base import Route, auth_guard
from routes.utility.users import UserManager

from .db import get_strategy_map
from .utils import assert_type, map_dict, getr
from .utility.public_bot_stats import PublicBotStats
from .utility.strategy import StrategyFactory

from .spectree import spectree
from spectree import Response
from pydantic import BaseModel
from typing import List, Optional
from .exchange import similar_trading_pairs, Exchange


class StrategyOptionsPostReq(BaseModel):
    uuid: str


class StrategyOptionsPostResp(BaseModel):
    constraints: dict
    defaults: dict
    descriptions: dict
    types: dict
    names: dict
    description: str
    image: str


class AvailableStrategiesGetResp(BaseModel):
    prototypes: List[dict]
    promotional_status_priority: List[str]


class StrategiesPutReq(BaseModel):
    proto: str
    params: dict
    uuid: Optional[str]


class StrategiesPutResp(BaseModel):
    uuid: str


class StrategiesPostReq(BaseModel):
    uuid: str


class StrategiesPostResp(BaseModel):
    name: str
    params: dict
    proto: str
    returns: int


class StrategiesDeleteReq(BaseModel):
    uuid: str

class StrategyOptions(Route):
    @auth_guard
    @spectree.validate(json=StrategyOptionsPostReq, resp=Response(HTTP_200=StrategyOptionsPostResp))
    def on_post(self, req, resp):
        self.mark_activity(req)
        username = self.get_username(req).unwrap()
        UserManager(self.dbs).assert_strat_allowed(username, req.media['uuid'])
        strat_config = StrategyFactory(req.media['uuid'], self.dbs)
        proto = strat_config.get_proto()
   
        d ={
            'constraints': proto.proto_params,
            'defaults': strat_config.get_params(),
            'descriptions': proto.descriptions,
            'types': proto.param_types,
            'names': proto.display_names,
            'description': proto.strategy_description,
            'image': proto.strategy_image,
        }

        profile = self.dbs['users'][username]
        strategy_name = strat_config.get_name().lower().split(' ')[0]
        all_images_owned = SolanaApi(self.dbs).get_image_urls(profile.get('obt_token',{}).get('NFT',{}).get('token_address',{}))
        if all_images_owned:
            d['image'] = all_images_owned.get(strategy_name,{}).get(profile.get('obt_token',{}).get('NFT',{}).get('token_images',{}).get(strategy_name,''),proto.strategy_image)
        resp.media = d 


class AvailableStrategies(Route):
    @spectree.validate(resp=Response(HTTP_200=AvailableStrategiesGetResp))
    def on_get(self, req, resp):
        self.mark_activity(req)
        strats = get_strategy_map()
        # Add to user history

        market = req.params.get('market', None)
        similar_markets = bool(req.params.get('similar_markets', True))

        pbs = PublicBotStats(self.dbs)
        result = pbs.get_botstats_summary(None, market=market)
        day_rois = pbs.get_botstats_summary(86400, market=market)
        week_rois = pbs.get_botstats_summary(604800, market=market)
        month_rois = pbs.get_botstats_summary(2592000, market=market)

        exchanges = Exchange(self.dbs).get_exchange_configs()
        exchange_markets = map_dict(lambda k,v: (k, v['pairs']), exchanges)

        user_selected_images ,nft_images = {}, {}
        if self.is_authenticated(req):
            user = self.get_username(req).unwrap()
            um = UserManager(self.dbs)
            if um.get_policy(user).sub == 'Admin':
                strats = get_strategy_map(add_disabled=True)

            profile = self.dbs['users'][user]
            nft_images = SolanaApi(self.dbs).get_image_urls(profile.get('obt_token',{}).get('NFT',{}).get('token_address',{}))
            user_selected_images = profile.get('obt_token',{}).get('NFT',{}).get('token_images',{})
            
        def __get_avg_market_roi(k,pairs):
            _return,_temp = {},{}
            for pair in pairs:
                b = pair.split(":")[0]
                _d = _temp.get(b,[])
                _d.append(getr(result, f'{k}.markets.{pair}.avg_roi', 0.))
                _temp[b] = _d

            for pair in pairs:
                b= pair.split(":")[0]
                _return[pair] = sum(_temp[b])/len(_temp[b])
            
            return _return

        proto_strats = [
            {
                "name": getattr(strats[k], "title", k),
                "status": strats[k].status,
                "uuid": k,
                'image': nft_images.get(str(getattr(strats[k], "title", k)).split(' ')[0].lower(),{}).get(user_selected_images.get(str(getattr(strats[k], "title", k)).split(' ')[0].lower()),strats[k].strategy_image),
                'description': strats[k].strategy_description,
                'markets': getattr(strats[k], 'markets', []),
                'mean_roi': result.get(k, {}).get('avg_roi_bot', 0),
                'mean_month_roi': result.get(k, {}).get('avg_roi_month', 0),
                'day_roi': day_rois.get(k, {}).get('avg_roi_bot'),
                'week_roi': week_rois.get(k, {}).get('avg_roi_bot'),
                'month_roi': month_rois.get(k, {}).get('avg_roi_bot'),
                'market_rois': {exchange: __get_avg_market_roi(k,pairs) for exchange, pairs in exchange_markets.items()} 
            }
            for k in strats
        ]

        promotional_order = self.dbs['globals']['promoting_order']

        resp.media = {
            'prototypes': proto_strats,
            'promotional_status_priority': promotional_order,
        }


class Strategies(Route):
    @spectree.validate(json=StrategiesPutReq, resp=Response(HTTP_200=StrategiesPutResp))
    def on_put(self, req, resp):
        self.mark_activity(req)
        # Take over uuid from req or generate new
        # TODO: strip unknown parts/deep validate
        # TODO: GC orphaned strategies?
        # TODO: compress default strategies
        if req.media.get('uuid', False):
            profile = self.get_profile(req).unwrap()
            assert req.media['uuid'] in profile['strats'], 'Forbidden strategy'

        uid = req.media.get('uuid', str(uuid.uuid4()))
        assert_type(uid, str, "UUID")

        if self.is_authenticated(req):
            # Add to user history
            user = self.get_username(req).unwrap()
            self.dbs['users'](user, lambda doc: doc['strats'].add(uid))

        strat_class = StrategyFactory(req.media['proto'], self.dbs).get_proto()
        assert strat_class != None, 'No such strategy'

        self.dbs['strats'][uid] = {
            "uuid": uid,
            "proto": req.media['proto'],  # Base Strategy ID
            "params": req.media['params'],  # Vue Parameters
        }

        resp.media = {
            "uuid": uid
        }

    @auth_guard
    @spectree.validate(json=StrategiesPostReq, resp=Response(HTTP_200=StrategiesPostResp))
    def on_post(self, req, resp):
        self.mark_activity(req)
        uid = req.media['uuid']
        username = self.get_username(req).unwrap()
        UserManager(self.dbs).assert_strat_allowed(username, uid)

        strat_config = StrategyFactory(uid, self.dbs)
        resp.media = {
            "name": strat_config.get_name(),
            "params": strat_config.get_params(),
            "proto": strat_config.get_proto(as_uid=True),
            "returns": 0,
        }

    @auth_guard
    @spectree.validate(json=StrategiesDeleteReq)
    def on_delete(self, req, resp):
        self.mark_activity(req)
        uid = req.media['uuid']

        # Update/check user data
        profile = self.get_profile(req).unwrap()
        assert uid in profile['strats'], 'Forbidden strategy'
        profile['strats'].remove(uid)
        self.update_profile(req, profile).unwrap()

        # Remove Strat
        del self.dbs['strats'][uid]
