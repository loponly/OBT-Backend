def reactivate(dbs, bots):
    for b in bots:
        bot = dbs['bots'][b]
        bot['enabled'] = True
        dbs['bots'][b] = bot

def check_reactivate(dbs, username, bots):
    from tradeEnv.trade_api import TradeAPIRegistry
    from routes.logging import getLogger
    user = dbs['users'][username]
    fbots = list(filter(lambda b: dbs['bots'][b]['enabled'], user['bots']))
    bots = list(bots)
    bots.extend(fbots)
    bots = set(bots)
    assets_required = {}
    exchange = None
    assets_available = None
    for b in bots:
        assert b in user['bots']
        bot = dbs['bots'][b]
        exchange = exchange or bot['exchange']
        assert bot['exchange'] == exchange, "Trying to reactivate from different exchanges not implemented"
        if not assets_available:
            api = TradeAPIRegistry[exchange](logger=getLogger('reactivate'))
            assets_available = api.balance(user['exchanges'][exchange]).unwrap()
        pair = bot['market'].split(':') # TODO: check open orders
        assets_required[pair[1]] = assets_required.get(pair[1], 0) + bot['state'].curBalance
        assets_required[pair[0]] = assets_required.get(pair[0], 0) + bot['state'].tokBalance
    for asset in assets_required:
        assert asset in assets_available, "Required asset not available from exchange"
        assert not assets_required[asset] > assets_available[asset], f"Not enough assets for {asset} ({assets_required[asset]} > {assets_available[asset]}"


def bots_summary(dbs, username=None, bots=[]):
    assert username or bots, "Should have at least one search param"
    if username:
        user = dbs['users'][username]
        fbots = list(filter(lambda b: dbs['bots'][b]['enabled'], user['bots']))
        bots = list(bots)
        bots.extend(fbots)
        bots = set(bots)
    top = {}
    for b in bots:
        bot = dbs['bots'][b]
        print(f"{b} ({bot['enabled']}): {bot['market']} {bot['state'].tokBalance} tok, {bot['state'].curBalance} cur")

