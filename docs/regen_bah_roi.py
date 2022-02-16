from routes.realtime import get_env
from routes.db import get_dbs
import time
import numpy as np

if __name__ == '__main__':
    env_cache = {}
    dbs = get_dbs()
    for b in dbs['bots']:
        bot = dbs['bots'][b]

        if 'starting_price' not in bot:
            print(f"Skipping {b}, no starting price found")
            continue

        env_descriptor = (bot['exchange'], bot['market'], bot['candles'])
        if env_descriptor not in env_cache:
            env_cache[env_descriptor] = get_env(*env_descriptor)

        env = env_cache[env_descriptor]
        if not env:
            print(f"Missing env {env_descriptor}, skipping {b}")
            continue

        stop_time = bot.get('stop_time') or time.time()
        start_time = bot['start_time']
        start_index = np.argmin(np.abs(env.mi.historical['time'] - start_time))
        stop_index = np.argmin(np.abs(env.mi.historical['time'] - stop_time))
        start_price = env.mi.historical['close'][start_index]
        stop_price = env.mi.historical['close'][stop_index]
        old_roi = bot.get('bah_roi')
        if start_price != bot['starting_price']:
            print(f"WARN: start prices are not eq {start_price} != {bot['starting_price']}")
        bot['starting_price'] = start_price 
        bot['bah_roi'] = (stop_price - start_price) / start_price
        print(f"Updating {b}, price at {stop_time - int(time.time())}s ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(env.mi.historical['time'][stop_index]))}) is {stop_price} for {bot['exchange']}-{bot['market']} giving {bot['bah_roi']} (prev {old_roi})")
        dbs['bots'][b] = bot
