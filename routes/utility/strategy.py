from routes.db import get_strategy_map

class StrategyFactory:
    def __init__(self, uid, dbs):
        self.uid = uid
        self.dbs = dbs

    def get_name(self):
        proto = self.get_proto()
        puid = self.get_proto(as_uid=True)

        return getattr(proto, 'title', puid)
    
    def get_params(self):
        strat_raw = self.dbs['strats'].get(self.uid, None) 
        strat = self.get_proto()
        if type(strat_raw) == dict:
            params = strat_raw.get("params", {})
            params = {**strat.defaults, **params}
        else:
            params = strat.defaults
        return params

    # Recursive resolving
    def _get_proto(self, uid, as_uid=False):
        strat = get_strategy_map(add_disabled=True).get(uid, None)
        if strat is None:
            config = self.dbs['strats'].get(uid, None)
            if config is None:
                return None

            if type(config) != dict:
                if as_uid:
                    return uid
                else:
                    return config

            return self._get_proto(config['proto'], as_uid=as_uid)

        if as_uid:
            return uid

        return strat

    def get_proto(self,as_uid=False):
        return self._get_proto(self.uid, as_uid=as_uid)

    def get_candles(self, default=None):
        strat_proto = self.get_proto()
        candles = getattr(strat_proto, 'candles', None)

        # Some [deprecated] strategies only show candle settings after initialization
        if not candles:
            strat = self.construct(None)
            candles = getattr(strat, 'candles', None)

        return candles or default

    def construct(self, env):
        return self.get_proto()(env, **self.get_params())
