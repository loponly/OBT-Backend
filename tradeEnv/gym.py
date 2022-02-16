import numpy as np
import torch
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from .utils import add_pkg
add_pkg()

from typing import List
from .metrics import SimulMetrics
from .api_adapter import ApiAdapter, binance_map
from .maths import meandev_norm, symmetric_log, symmetric_exp, eps, safeVal
from empyrical import sortino_ratio, sharpe_ratio, max_drawdown

class SimuGym:
    def __init__(self, markets=['LTC:USDT'], candleSizes=['4h'], data_version=1, max_steps=1000, skip_frames=0, discount_factor=0.999, fee=0.9975, perception_window=None, is_tanh=True, device=None):
        self.envs = []
        for market in markets:
            for candleSize in candleSizes:
                marketid = market.replace(':', '')
                api = ApiAdapter(binance_map, '%s_%s' % (marketid, candleSize)) 
                env = SimulMetrics(api, 0, fee=fee)
                self.envs.append(env)

        self.device = device
        self.env = self.envs[0]
        self.skip_frames = skip_frames
        self.imax_steps = self.max_steps = max_steps
        self.steps = 0
        self.start = 0
        self.discount_factor = discount_factor
        self.last_seed = np.random.RandomState(0)
        self.perception_window = perception_window
        self.max_limit = 0.10 * (1.0/6.0) # 20%

        self.data_version = data_version

        self.no_trade_penalty = 1500 # 4h=50-1500
        if '15m' in candleSizes:
            self.no_trade_penalty = 250

        self.limit_orders = []

        if self.data_version == 5:
            self.labels = ['close', 'open', 'low', 'high', 'volume']
            b = self.perception_window or 128
            self.data_shape = (len(self.labels), b)
        elif self.data_version in [3, 4]:
            b = self.perception_window or 64
            self.data_shape = ((b * 2) + 2,)

        self.data = torch.zeros(self.data_shape, requires_grad=False, device=self.device or 'cpu')

        self.randomize()
        self.reset()

    def observe(self):
        if self.data_version == 3:
            w = (self.perception_window or 64)
            close_data = np.ediff1d(self.env.get_window(window=w + 1, dkey='close'))
            close_data = meandev_norm(close_data)
            volume_data = np.ediff1d(self.env.get_window(window=w + 1, dkey='volume'))
            volume_data = meandev_norm(volume_data)
            self.data[:w] = torch.from_numpy(close_data)
            self.data[w:w*2] = torch.from_numpy(volume_data)

            price = self.env.current_v()
            sum_balance = self.env.user.tokBalance * price + self.env.user.curBalance
            self.data[w*2] = self.env.user.curBalance / (sum_balance + eps)
            self.data[(w*2)+1] = (self.env.user.tokBalance * self.env.current_v()) / (sum_balance + eps)
            return self.data.view(1, 1, -1)

        if self.data_version == 4: # v3.1
            w = (self.perception_window or 64)
            close_data = np.ediff1d(self.env.get_window(window=w + 1, dkey='close'))
            volume_data = np.ediff1d(self.env.get_window(window=w + 1, dkey='volume'))
            self.data[:w] = torch.from_numpy(close_data)
            self.data[:w] /= torch.norm(self.data[:w])
            self.data[w:w*2] = torch.from_numpy(volume_data)
            self.data[w:w*2] /= torch.norm(self.data[:w])

            price = self.env.current_v()
            sum_balance = self.env.user.tokBalance * price + self.env.user.curBalance
            self.data[w*2] = self.env.user.curBalance / (sum_balance + eps)
            self.data[(w*2)+1] = (self.env.user.tokBalance * self.env.current_v()) / (sum_balance + eps)
            return self.data.view(1, 1, -1)

        if self.data_version == 5: 
            w = (self.perception_window or 128)
            for i, x in enumerate(self.labels): 
                self.data[i, :] = meandev_norm(torch.from_numpy(np.ediff1d(self.env.get_window(window=w+1, dkey=x))))

            return self.data
    
    def set_env(self, env: SimulMetrics):
        self.env = env
        self.randomize()
        self.reset()

    def try_trade(self, p, trade='sell', dtype='percent'):
        x = None
        if trade == 'sell':
            x = self.env.sell(p, dtype=dtype)
            return x
        elif trade == 'buy':
            x = self.env.buy(p, dtype=dtype)
        else:
            print('warning: unknown trade "%s"' % trade)

        return x

    def try_action(self, action):
        # ((sell_limit, sell_amount), (buy_limit, buy_amount), ?i[expire])
        if getattr(action[0], 'shape', None) and getattr(action[1], 'shape', None):
            selllimit = safeVal(action[0].squeeze(0).cpu().detach().numpy())
            buylimit = safeVal(action[1].squeeze(0).cpu().detach().numpy())

            if len(action) == 3:
                # Usually unused
                expire = action[-1].argmax() + 1
            else:
                expire = 1
            
            # print(action)

            expire_time = self.env.time_at_relative_candle(expire)
            stx = self.env.sell_limitp(selllimit[1], self.env.current_v() * (1 + (selllimit[0] * self.max_limit)))#, expire_time)
            btx = self.env.buy_limitp(buylimit[1], self.env.current_v() * (1 - (buylimit[0] * self.max_limit)))#, expire_time)
            # print([btx, stx], [buylimit[1], selllimit[1]], [self.env.current_v() * (1 - (selllimit[0] * self.max_limit)), self.env.current_v()], [self.env.current_v() * (1 + (buylimit[0] * self.max_limit)), self.env.current_v()])
            # print(self.env.user.open_orders)
            return 2, stx or btx

        # e[Buy, Sell, ?Hold]
        elif len(action) == 3 or len(action) == 2:
            if torch.is_tensor(action[0]):
                action = action.cpu().detach().numpy()
            idx = action.argmax()
            prob = action[idx]
            
            # print(prob)

            if idx == 0: # BUY
                fill = self.try_trade(prob, trade='buy') # 50%
                return 0, fill
            elif idx == 1: # SELL
                fill = self.try_trade(prob, trade='sell') # 50%
                return 1, fill
            else: # HOLD
                return 2, 0
        else:
            raise NotImplementedError("Failed to parse action")
    
    def get_trades(self):
        sells = []
        buys = []
        for x in self.env.user.trade_log:
            if x['type'] == 'sell':
                sells.append(x)
            elif x['type'] == 'buy':
                buys.append(x)
            else:
                raise NotImplementedError('Unkown trade type')
        
        return sells, buys

    def done(self, obs):
        reward = 0

        value_reward = self.fraction_gain() * 1000

        try:
            frac_diff = np.diff(self.net_worth_log) / np.abs(self.net_worth_log[:-1])

            sortino_reward = sortino_ratio(frac_diff) * 100
            sharpe_reward = sharpe_ratio(frac_diff) * 100
            dropdown_reward = 0#max_drawdown(frac_diff) * 100
            if not np.isnan(sortino_reward) and not np.isinf(sortino_reward):
                reward += sortino_reward + sharpe_reward + dropdown_reward
        except:
            pass
        # print(sortino_ratio(np.ediff1d(self.net_worth_log)), np.std(self.net_worth_log), self.steps)

        # Attempted limit orders (balanced and high amounts)
        orders = np.array(list(map(lambda a: 5 if a.side == 'buy' else -5, self.limit_orders)))
        if len(orders) > 0:
            reward -= np.abs(np.mean(orders)) # should be around 0 if even amount of buy & sell
            reward += len(orders)/4
        else:
            reward -= 50

        sells, buys = self.get_trades()
        no_trade_penalty = self.no_trade_penalty/(len(sells) + 1) + self.no_trade_penalty/(len(buys) + 1)
        reward -= no_trade_penalty
        reward += value_reward
        return [obs, reward, True]

    def step(self, action):
        # Confidence prediction
        trade_type, fill = self.try_action(action)

        obs = self.observe()

        reward = 0
        if trade_type != 2 or fill != None:
            reward += 0.1 # Small reward for each time it trades
        
        for o in self.env.user.open_orders:
            self.limit_orders.append(self.env.user.open_orders[o])

        done = not self.env.step() or self.steps >= self.max_steps-1
        self.net_worth_log.append(self.env.portfolioValue())
        #reward += (self.discount_factor ** self.steps) * ((self.net_worth_log[-1] - self.net_worth_log[-2]) / self.net_worth_log[-2]) * 100

        if done:
            return self.done(obs)

        self.steps += 1

        # Skip frames if configured
        for nop in range(self.skip_frames):
            done = not self.env.step() or self.steps >= self.max_steps-1
            self.steps += 1
            if done:
                return self.done(obs)

        return [obs, reward, False]

    def fraction_gain(self):
        return ((self.env.portfolioValue() - self.base_line) / self.base_line)

    # Tell the model to randomize weights
    def should_explore(self):
        sells, buys = self.get_trades()
        return not len(sells) or not len(buys)

    def plot_limit(self):
        fig = Figure(figsize=[10, 5], dpi=200.0)
        ax = fig.gca()

        # self.limit_orders
        space = self.env.get_window(self.steps, dkey='time')
        close = self.env.get_window(self.steps, dkey='close')
        ehigh = self.env.get_window(self.steps, dkey='high') - close
        elow = close - self.env.get_window(self.steps, dkey='low') 

        ax.errorbar(space, close, [elow, ehigh], linestyle='-', zorder=0, c='slategray')

        # TODO: use plot_market from <Market>
        # fig = self.mi.plot_market(self.steps, offset=-self.env.indexstep)

        if len(self.env.user.trade_log) > 0:
            # TODO: FIXME: if no sell limit/trade is made color is always red (even if -10)
            trades = np.array(list(map(lambda a: [a['date'], a['price'], 20, 10 if a['type'] == 'buy' else -10], self.env.user.trade_log)))
            ax.scatter(*trades.T, cmap=plt.get_cmap('RdYlBu'), marker='x', zorder=10)
            # Update limit orders (no overlap ideally)
            self.trade_times = list(map(lambda a: a['date'], self.env.user.trade_log))
            self.limit_orders = list(filter(lambda x: x.expire_time not in self.trade_times, self.limit_orders))

        if len(self.limit_orders) > 0:
            orders = np.array(list(map(lambda a: [a.expire_time, a.price, 8, 10 if a.side == 'buy' else -10], self.limit_orders)))
            ax.scatter(*orders.T, cmap=plt.get_cmap('RdYlBu'), marker='o', zorder=15)

        #Image from plot
        ax.axis('off')
        fig.tight_layout(pad=0)

        # To remove the huge white borders
        ax.margins(0)

        fig.canvas.draw()
        image_from_plot = np.asarray(fig.canvas.buffer_rgba())
        image_from_plot = image_from_plot.transpose(2, 0, 1)
        # image_from_plot = np.frombuffer(fig.canvas.tostring_rgb(), dtype=np.uint8)
        # image_from_plot = image_from_plot.reshape((3,) + fig.canvas.get_width_height()[::-1])
        print(image_from_plot.shape)
        return image_from_plot

    def eval(self):
      sells, buys = self.get_trades()
      data = {**self.env.user.__dict__}

      sortino_reward = 0
      sharpe_reward = 0
      try:
          frac_diff = np.diff(self.net_worth_log) / np.abs(self.net_worth_log[:-1])

          sortino_reward = sortino_ratio(frac_diff)
          sharpe_reward = sharpe_ratio(frac_diff)
      except:
          pass

      return {
          'scalars': {
              'buys': len(buys),
              'sells': len(sells),
              'max_balance': data['max_balance'],
              'min_balance': data['min_balance'],
              'trade_count': len(buys) + len(sells),
              'sortino': sortino_reward,
              'sharpe': sharpe_reward,
              'roi': self.fraction_gain(),
          },
          'images': {
            'limit': self.plot_limit()
        }
      }

    def randomize(self, nprandom_state=None):
        if nprandom_state:
            self.last_seed = nprandom_state

        # Random market
        env_idx = self.last_seed.randint(0, len(self.envs))
        self.env = self.envs[env_idx]

        # Random Parameters
        self.max_steps = self.last_seed.randint(self.imax_steps//2, self.imax_steps)
        self.start = self.last_seed.randint(1024, len(self.env.mi.historical['time']) - self.max_steps)
        balance_seed = self.last_seed.randint(25, 3000) # Value of portfolio
        # fraction_start = self.last_seed.uniform(0,1)    # Fraction of which is in tokens 
        # self.starting_balance = [(balance_seed * fraction_start) / self.env.mi.historical['close'][self.start], balance_seed * (1-fraction_start)]
        self.starting_balance = [0, balance_seed]
    
    def soft_reset(self):
        self.env.reset(new_indexstep=self.start, startingBalance=self.starting_balance)
        self.steps = 0
        self.base_line = self.env.portfolioValue()        
        self.net_worth_log = [self.base_line]
        self.limit_orders = []

    def reset(self):
        self.soft_reset()
        return self.observe()

    def render(self):
        pass # TODO

def mse(A, B):
    return np.square(np.subtract(A, B)).mean()

class PredGym(SimuGym):
    def observe(self):
        closed = symmetric_log(np.ediff1d(self.env.get_window(window=2, dkey='close')))
        lowd =   symmetric_log(np.ediff1d(self.env.get_window(window=2, dkey='low')))
        highd =  symmetric_log(np.ediff1d(self.env.get_window(window=2, dkey='high')))
        opend =  symmetric_log(np.ediff1d(self.env.get_window(window=2, dkey='open')))
        volumed = np.log1p(self.env.current_v(vtype='volume')) / 4.0

        data = np.hstack((closed, lowd, highd, opend, volumed)) 
        data = torch.tensor(data, requires_grad=False, dtype=torch.float32, device=self.device or 'cpu')

        return data

    def eval(self):
        return {}

    def sstep(self):
        self.steps += 1
        done = not self.env.step() or self.steps >= self.max_steps-1
        obs = self.observe()
        return [obs, 0, done]

    def step(self, action):
        self.steps += 1
        done = not self.env.step() or self.steps >= self.max_steps-1

        obs = self.observe()
        reward = 0

        # if self.steps > 64: # TODO: dynamic warm up
        reward = (1/mse(action, obs.detach().numpy())) / self.max_steps

        
        return [obs, reward, done]
