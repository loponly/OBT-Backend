import numpy as np
import torch
import os
import sys
import math
import dill

abs_path = os.path.realpath('../..')
if abs_path not in sys.path:
    sys.path.append(abs_path)


"""
! Notes:
! No super() allowed, use the extended class directly (see https://github.com/uqfoundation/dill/issues/300)
! Don't re-use variable names (at all) (see https://github.com/uqfoundation/dill/issues/219)
! ! meandev_norm() previously conflicted with try_trade() on `x`
! ! KeyError: '__builtins__'
! No use of tradeEnv imports, so Admin-Backend can also decode it also they can change
"""

class Strategy:
    param_types = {}
    proto_params = {}
    descriptions = {}
    display_names = {}
    strategy_description = 'No explanation found...'
    strategy_image = ''

    def validate_params(self, **kwargs):
        for key in kwargs:
            if type(kwargs[key]) != type(self.proto_params[key][0]):
                return False

            if type(self.proto_params[key][0]) == str or len(self.proto_params[key]) != 2:
                if kwargs[key] not in self.proto_params[key]:
                    return False
            else:
                if kwargs[key] > self.proto_params[key][0] or kwargs[key] < self.proto_params[key][1]:
                    return False
        return False

    def __init__(self, env, **kwargs):
        self.env = env
        self.hp = {**self.defaults, **kwargs}
        self.cast_hp()

    def cast_hp(self):
        for param in self.proto_params:
            param_el = self.proto_params[param]
            param_type = type(param_el[-1])
            param_len = len(param_el)
            self.hp[param] = param_type(self.hp[param])

            if param_type in [float, int]:

                if not math.isfinite(self.hp[param]):
                    raise TypeError(
                        'Failed to cast parameter %s to expected type' % param)

                if param_len == 2:
                    if self.hp[param] > max(param_el) or self.hp[param] < min(param_el):
                        raise TypeError('Parameter %s out of range [%.3f %.3f]' % (
                            param, min(param_el), max(param_el)))
                else:
                    if self.hp[param] not in param_el:
                        raise TypeError('Parameter %s (%s) not in %s' % (
                            param, self.hp[param], str(param_el)))
            else:
                if self.hp[param] not in param_el:
                    raise TypeError('Parameter %s (%s) not in %s' %
                                    (param, self.hp[param], str(param_el)))

            # # Convert percentage
            # if self.param_types[param] == '%':
            #     self.hp[param] = self.hp[param] / 100.

    def buy(self, value, dtype='percent'):
        self.env.buy(value, dtype)

    def sell(self, value, dtype='percent'):
        self.env.sell(value, dtype)

    def required_samples(self):
        return 201  # default

    def step(self):
        pass

    def substep(self):
        pass

    def set_env(self, env):
        self.env = env

    # NOTE: Doesn't reset env
    def reset(self):
        pass

# Interface


class StatefulStrategy(Strategy):
    def loads(self, data: bytes):
        pass

    def dumps(self):
        return b''

class NeuralStrategy(StatefulStrategy):
    def __init__(self, env, **kwargs):
        StatefulStrategy.__init__(self, env, **kwargs)
        path = os.path.join('./store/models', self.model_file + '.pt')
        assert os.path.isfile(path), "Bad Model"
        self.model = torch.load(path)
        self.candles = getattr(self.model, 'candles', '4h')

        if hasattr(self.model, 'reset'):
            self.model.reset()

    def try_trade(self, p, trade='sell', dtype='percent'):
        if trade == 'sell':
            self.sell(p, dtype=dtype)
        elif trade == 'buy':
            self.buy(p, dtype=dtype)
        else:
            print('warning: unknown trade "%s"' % trade)

    def sigmoid(self, z):
        return 1 / (1 + np.exp(-z))

    def try_action(self, action):
        if len(action) == 3 or len(action) == 2:
            idx = action.argmax()
            prob = action[idx]
            
            if prob > 6.:
                return

            # Assume Tanh (redistribute to sigmoid)
            # TODO: remove
            prob = self.sigmoid(np.sinh(prob))

            if self.hp.get('fulltrade_threshold', False):
                prob = 1. if prob > self.hp['fulltrade_threshold'] else prob
                prob = 0. if prob < 1 - self.hp['fulltrade_threshold'] else prob 

            if idx == 0:  # BUY
                self.try_trade(prob, trade='buy')
            elif idx == 1:  # SELL
                self.try_trade(prob, trade='sell')
            else:  # HOLD
                pass
        else:
            raise NotImplementedError("Failed to parse action")


_model_file = 'Filter75-D3-V4'
_path = os.path.join('../../store/models', _model_file + '.pt')
m = torch.load(_path)
del _path
try:
    _candles = m.config['candleSizes'][0]
    _data_version = m.config['data_version']
    _markets = m.config['markets']
except:
    # Older strategies have no .config
    _data_version = 3
    _candles = '4h'
    _markets = ["UNKN"]
_candles = getattr(m, 'candles', _candles)
del m

class AITemplate(NeuralStrategy):
    title = 'Unnamed strategy'
    status = 'normal'
    strategy_description = 'Strategies used by neural networks (AI) trained on cryptocurrency and other markets. The settings contain different neural network models'
    strategy_image = 'static_images/Self-learning.png'
    defaults = {
      "fulltrade_threshold": 0.99
    }
    proto_params = {
      "fulltrade_threshold": [
       0.51,
       1.0
      ]
    }
    descriptions = {
      "fulltrade_threshold": "Confidence percentage of bot portfolio at which to trade 100%/0% instead"
    }
    display_names=  {
      "fulltrade_threshold": "Confidence Threshold"
    }

    model_file = _model_file
    candles = _candles
    markets = _markets
    data_version = _data_version
    perception_window = None

    def __init__(self, *args, **kwargs):
        NeuralStrategy.__init__(self, *args, **kwargs)
        if self.data_version == 5:
            self.labels = ['close', 'open', 'low', 'high', 'volume']
            b = self.perception_window or 128
            self.data_shape = (b, len(self.labels))
        elif self.data_version in [3, 4]:
            b = self.perception_window or 64
            self.data_shape = ((b * 2) + 2,)

        self.data = torch.zeros(self.data_shape, requires_grad=False, device='cpu')

    def loads(self, data: bytes):
        try:
            if 'GRU' in self.model_file:
                self.model.hn = dill.loads(data)
        except:
            print(self.model_file, 'skipping loads because of errors')

    def dumps(self):
        if 'GRU' in self.model_file:
            return dill.dumps(self.model.hn)

        return b''

    def meandev_norm(self, y):
        return (y - np.mean(y)) / np.std(y)

    def observe(self):
        # D3
        w = 64 # self.perception_window
        close_data = np.ediff1d(self.env.get_window(window=w + 1, dkey='close'))
        close_data = self.meandev_norm(close_data)
        volume_data = np.ediff1d(self.env.get_window(window=w + 1, dkey='volume'))
        volume_data = self.meandev_norm(volume_data)
        self.data[:w] = torch.from_numpy(close_data)
        self.data[w:w*2] = torch.from_numpy(volume_data)

        price = self.env.current_v()
        sum_balance = self.env.user.tokBalance * price + self.env.user.curBalance
        self.data[w*2] = self.env.user.curBalance / sum_balance
        self.data[(w*2)+1] = (self.env.user.tokBalance * self.env.current_v()) / sum_balance
        return self.data.view(1, -1, 1)
    
    def required_samples(self):
        return 65

    def step(self):
        # TODO: if init, preload data
        obs = self.observe()

        with torch.no_grad():
            action = self.model.forward(obs)
            action = action.detach().numpy()

        self.try_action(action)

if __name__ == '__main__':
    import zstd

    filename = 'strategy-out.dill'
    print(AITemplate.__dict__)
    d = dill.dumps(AITemplate, byref=False, recurse=True)
    print(f'Strategy size: {len(d)} bytes')
    print(f'Compressed size: {len(zstd.compress(d, 3))} bytes')
    print(f'Writing raw strategy to {filename}')

    with open(filename, 'bw+') as f:
        f.write(d)
