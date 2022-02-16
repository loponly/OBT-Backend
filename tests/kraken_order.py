from tradeEnv.api_adapter import TradeAPI, supermap, ApiAdapter

tapi = TradeAPI(supermap['Kraken'])
api = ApiAdapter(supermap['Kraken'], 'tmp')
query = {'pair': 'BTCUSDT', 'type': 'sell', 'ordertype': 'market', 'volume': 0.001}
api_keys = ['dl3n8A9Orkij2QtTQQDcdRrHLl076sKbGw8HPn3EQb83SY5MXx1M3jeH9xKJUbSI', 'ZpygJfltvdOxw9xd3nIY7ca5tIfocrsMw3jXVHIslrvzh4GUGWSW5HzZKu9frGNo']

tapi = TradeAPI(supermap['Binance'])
api = ApiAdapter(supermap['Binance'], 'tmp')
query = {'symbol': 'BTCUSDT', 'side': 'SELL', 'type': 'MARKET', 'quantity': 0.001, 'newOrderRespType': 'FULL'}
query, body, headers = tapi.sign_api(api_keys, query=query)
req = api.call('api/v3/order', data=query, method='POST', headers=headers)
print(req.json())