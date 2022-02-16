from .maths import legacy_bound_norm, meandev_norm, sigmoid
from .strategy import StatefulStrategy
import torch
import os
import numpy as np
import pickle

from .utils import add_pkg
add_pkg()


class NeuralStrategy(StatefulStrategy):
    def __init__(self, env, **kwargs):
        super().__init__(env, **kwargs)
        path = os.path.join('./store/models', self.hp['model'] + '.pt')
        assert os.path.isfile(path), "Bad Model"
        self.model = torch.load(path)
        self.candles = getattr(self.model, 'candles', '4h')
        self.threshold_fulltrade = None # or None
        self.is_tanh = False

    def try_trade(self, p, trade='sell', dtype='percent'):
        if trade == 'sell':
            x = self.sell(p, dtype=dtype)
        elif trade == 'buy':
            x = self.buy(p, dtype=dtype)
        else:
            print('warning: unknown trade "%s"' % trade)

    def try_action(self, action):
        if len(action) == 3 or len(action) == 2:
            idx = action.argmax()
            prob = action[idx]

            # Assume Tanh (redistribute to sigmoid)
            if self.is_tanh: 
                prob = sigmoid(np.sinh(prob))

            if self.threshold_fulltrade:
                prob = 1. if prob > self.threshold_fulltrade else 0.
            
            if idx == 0: # BUY
                self.try_trade(prob, trade='buy') # 50%
            elif idx == 1: # SELL
                self.try_trade(prob, trade='sell') # 50%
            else: # HOLD
                pass
        else:
            raise NotImplementedError("Failed to parse action")

    def step(self):
        pass


# TODO rename
class PEPGBase(NeuralStrategy):
    title = 'Pioneer'
    strategy_description = 'The first generation of AI strategies that started the OB Trader expedition to the Moon. 80% of the existing strategies are products of Pioneer\'s evolution.'
    status = 'normal'
    strategy_image = 'https://raw.githubusercontent.com/ob-trading/OB-Family/d61272d29f27dcf30321b7649e9920df67cdc5e0/Pioneer.svg'
    markets = ["BTC:USDT","LTC:USDT","BNB:USDT","ETH:USDT"]
    defaults = {
        'model': 'Filter75-D3-V3',
    }

    proto_params = {
        'model': ['Filter75-D2-V2', 'Filter75-D3-V3', 'GRU32-5-D3-V3', 'GRU24-3-D3-V4-1h', 'GRU24-3-D3-V4']
    }

    descriptions = {
        'model': 'Type of model to use (D# is the data version, V# is model version)',
    }

    display_names = {
        'model': 'Model'
    }

    def __init__(self, env, **kwargs):
        super().__init__(env, **kwargs)

        # if 'GRU' in self.hp['model']:
        #     self.is_tanh = True

        if hasattr(self.model, 'reset'):
            self.model.reset()

    def loads(self, data: bytes):
        try:
            if 'GRU' in self.hp['model']:
                self.model.hn = pickle.loads(data)

            if 'LSTM' in self.hp['model']:
                data = pickle.loads(data)
                self.model.hn = data[0]
                self.model.fn = data[1]
        except:
            print(self.hp['model'], 'skipping loads because of errors')

    def dumps(self):
        if 'GRU' in self.hp['model']:
            return pickle.dumps(self.model.hn)

        if 'LSTM' in self.hp['model']:
            return pickle.dumps([self.model.hn, self.model.fn])

        return b''

    def observe(self):
        if '-D1-' in self.hp['model']:
            macdsig = legacy_bound_norm(self.env.ti.macd_signal(
                return_all=True) - self.env.ti.macd(return_all=True))
            ema_diff = np.ediff1d(self.env.ti.ema(30, 1/30, return_all=True))
            ema_diff = legacy_bound_norm(ema_diff)
            data = np.concatenate((
                self.env.ti.rsi(n=14, return_all=True),
                [self.env.ti.stoch_rsi()],
                macdsig,
                ema_diff,
                [np.log(self.env.user.curBalance + 1)],
                [np.log(self.env.user.tokBalance * self.env.current_v() + 1)]
            ))
            data = torch.FloatTensor(data)
        elif '-D2-' in self.hp['model']:
            diff = np.ediff1d(self.env.get_window(window=129))
            diff = meandev_norm(diff)
            data = torch.zeros((len(diff) + 2,))
            data[:128] = torch.from_numpy(diff)
            data[128] = np.log(self.env.user.curBalance + 1)
            data[129] = np.log(self.env.user.tokBalance * self.env.current_v() + 1)
        elif '-D3-' in self.hp['model']:
            close_data = np.ediff1d(self.env.get_window(window=65, dkey='close'))
            close_data = meandev_norm(close_data)

            volume_data = np.ediff1d(self.env.get_window(window=65, dkey='volume'))
            volume_data = meandev_norm(volume_data)

            data = torch.zeros((len(volume_data) + len(close_data) + 2,))
            data[:64] = torch.from_numpy(close_data)
            data[64:128] = torch.from_numpy(volume_data)

            price = self.env.current_v()
            sum_balance = self.env.user.tokBalance * price + self.env.user.curBalance
            data[128] = self.env.user.curBalance / sum_balance
            data[129] = (self.env.user.tokBalance * self.env.current_v()) / sum_balance

        return data

    def step(self):
        obs = self.observe()

        with torch.no_grad():
            action = self.model.forward(obs)
            action = action.detach().numpy()

        super().try_action(action)

class TimeSeriesPredictor:
    def __init__(self, env, **kwargs):
        self.modelp = kwargs['model']
        path = os.path.join('./', kwargs['model'] + '.pt')
        assert os.path.isfile(path), "Bad Model"
        self.model = torch.load(path)

    def observe(self):
        if '-R1-' in self.modelp:
            maxv = 40.0
            closed = np.ediff1d(self.env.get_window(window=2, dkey='close')) / 40.0
            lowd = np.ediff1d(self.env.get_window(window=2, dkey='low')) / 40.0
            highd = np.ediff1d(self.env.get_window(window=2, dkey='low')) / 40.0
            opend = np.ediff1d(self.env.get_window(window=2, dkey='open')) / 40.0
            volumed = np.log1p(self.env.current_v(vtype='volume')) / 2.0

            data = np.hstack((closed, lowd, highd, opend, volumed)) 
            data = torch.tensor(data, requires_grad=False,  dtype=torch.float32)

        return data

