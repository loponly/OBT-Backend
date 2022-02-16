import os
import sys
import pickle

abs_path = os.path.realpath('../..')
if abs_path not in sys.path:
    sys.path.append(abs_path)

os.chdir('../..')

# Just binance atm
for x in ['BTC:USDT', 'LTC:USDT', 'ETH:USDT', 'BNB:USDT']:
     for y in ['4h', '1h', '15m']:
             with open(f'store/envs/Binance_{x}_{y}.pkl', 'rb') as fi, open(f'store/dataset/{"".join(x.split(":"))}_{y}.json', 'w+') as fo:
                     s = pickle.load(fi)
                     print(fo.write(s.mi.to_json()))