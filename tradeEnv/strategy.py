from .maths import withinTolerance
import numpy as np
from typing import Tuple
import math

# Percentage to fraction
def p2f(x):
    return x / 100.

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
        self.sells = []
        self.buys = []
        self.env = env
        self.hp = {**self.defaults, **kwargs}
        self.cast_hp()

    def cast_hp(self):
        for param in self.proto_params:
            param_el = self.proto_params[param]
            param_type = type(param_el[-1])
            param_len  = len(param_el)
            self.hp[param] = param_type(self.hp[param])


            if param_type in [float, int]:
                if not math.isfinite(self.hp[param]):
                    raise TypeError('Failed to cast parameter %s to expected type' % param)

                if param_len == 2:
                    if self.hp[param] > max(param_el) or self.hp[param] < min(param_el):
                        raise TypeError('Parameter %s out of range [%.3f %.3f]' % (param, min(param_el), max(param_el)))
                else:
                    if self.hp[param] not in param_el:
                        raise TypeError('Parameter %s (%s) not in %s' % (param, self.hp[param], str(param_el)))
            else:
                if self.hp[param] not in param_el:
                    raise TypeError('Parameter %s (%s) not in %s' % (param, self.hp[param], str(param_el)))
            
            # # Convert percentage
            # if self.param_types[param] == '%':
            #     self.hp[param] = self.hp[param] / 100.

    def buy(self, value, dtype='percent'):
        amount = self.env.buy(value, dtype)
        if amount:
            self.buys.append({
                'amount': amount,
                'price': self.env.current_v(),
                'time': self.env.timestep
            })
    
    def sell(self, value, dtype='percent'):
        amount = self.env.sell(value, dtype)
        if amount:
            self.sells.append({
                'amount': amount,
                'price': self.env.current_v(),
                'time': self.env.timestep
            })

    def required_samples(self):
        return 201 # default

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


class BuyAndHold(Strategy):
    strategy_description = 'Simply buy an asset and don’t sell it despite price fluctuations. In other words, long-term investment.'
    strategy_image = 'static_images/Buy-and-Hold.jpg'
    status = 'normal'
    defaults = {
    }
    
    proto_params = {
    }


    def required_samples(self):
        return 1

    def step(self):
        self.buy(1.)

# Uses oppertunistic algorithm, based on the moving derivative average
# Buy when going up
# Sell when going down
class MovingDifferential(Strategy):
    def required_samples(self):
        return 25

    def step(self):
        # We don't want to waste our buy/sell fees, so there is a gap
        upthreshold = 20. 
        downthreshold = -20.
        dmav = self.env.ti.d_moving_avg(24) # 24H
        
        if dmav > upthreshold:
            self.buy(.1)

        if dmav < downthreshold:
            self.sell(.1)

# Similar to Moving differential but different formula
class LWMADifferential(Strategy):
    def required_samples(self):
        return 25

    def step(self):
        # We don't want to waste our buy/sell fees, so there is a gap
        upthreshold = 20. 
        downthreshold = -20.
        
        dmav = self.env.ti.d_lwma(24) # 24H
        
        if dmav > upthreshold:
            self.buy(.3)

        if dmav < downthreshold:
            self.sell(.3)

# Look at RSI gradient + Raw RSI value as a threshold function to buy/sell
class RSIDifferential(Strategy):
    strategy_description = 'RSI strategy utilizes the RSI indicator to determine good buy and sell opportunities. RSI depicts the strength of the market and indicates either it is overbought or oversold.'
    strategy_image = 'static_images/RSI-Threshold.png'
    defaults = {
        'trade_amount': 100,
        'up_threshold': 80.,
        'down_threshold': 20.,
        'z': 1.,
        'rsi_window': 26,
    }
    
    proto_params = {
        'trade_amount': [0.1, 100.],
        'up_threshold': [50., 100.],
        'down_threshold': [0., 50],
        'z': [0.1, 5.],
        'rsi_window': [6, 40],
    }

    descriptions = {
        'trade_amount': '%% of the starting balance to invest in the trade',
        'up_threshold': 'RSI level when to SELL',
        'down_threshold': 'RSI level when to BUY',
        'z': 'TThe required level to change in RSI to confirm SELL/BUY',
        'rsi_window': 'Number of price action samples used for RSI calculation'
    }

    display_names = {
        'trade_amount': 'Trade Amount',
        'up_threshold': 'Upper RSI',
        'down_threshold': 'Lower RSI',
        'z': 'Change Index',
        'rsi_window': 'RSI Samples'
    }

    param_types = {
        'trade_amount': '%',
        'up_threshold': '%',
        'down_threshold': '%',
        'z': '%',
        'rsi_window': 'samples'
    }

    def required_samples(self):
        return self.hp['rsi_window'] * 2 + 2

    def step(self):
        rsi = self.env.ti.rsi(n=self.hp['rsi_window'], return_all=True) # 24H
        drsi = np.ediff1d(rsi)

        if drsi[-1] < -p2f(self.hp['z']) and rsi[-1] > p2f(self.hp['up_threshold']):
            self.sell(p2f(self.hp['trade_amount']))

        if drsi[-1] > p2f(self.hp['z']) and rsi[-1] < p2f(self.hp['down_threshold']):
             self.buy(p2f(self.hp['trade_amount']))

# Combines N strategies into 1
# Warning: Singleton & Meta-programming ahead
class CompositeStrat(Strategy):
    bought = []
    sold = []
    weight = 1

    def required_samples(self):
        requirements = [x.required_samples() for x in self.strats]
        return max(requirements) + 1

    def __init__(self, env, strategies, weights=None, threshold=10, **kwargs):
        super().__init__(env, **kwargs)
        self.strats = strategies
        self.threshold = threshold

        self.weights = weights or np.ones(len(weights))
        assert len(self.weights) == len(self.strats), "Can't broadcast weights to strategies"

        for strat in self.strats:
            strat.buy = CompositeStrat.buy.__get__(strat)
            strat.sell = CompositeStrat.sell.__get__(strat)

    def step(self):
        for i, strat in enumerate(self.strats):
            if self.weights:
                CompositeStrat.weight = self.weights[i]
            strat.step()


        sell_strength = np.sum(CompositeStrat.sold) / np.sum(self.weights)
        if sell_strength > self.threshold:
            self.sell(sell_strength)

        buy_strength = np.sum(CompositeStrat.bought) / np.sum(self.weights)
        if buy_strength > self.threshold:
            self.buy(buy_strength)

        CompositeStrat.sold = []
        CompositeStrat.bought = []

    def buy(self, percent, **kwargs):
        dtype = kwargs.get('dtype', None)
        if dtype != 'percent':
            raise NotImplementedError('Compsite strategy only supports percentage based strategies')
        # Strategy.buy.__get__(self)(percent)
        CompositeStrat.bought.append(percent * CompositeStrat.weight)
    
    def sell(self, percent, **kwargs):
        dtype = kwargs.get('dtype', None)
        if dtype != 'percent':
            raise NotImplementedError('Compsite strategy only supports percentage based strategies')
        # Strategy.sell.__get__(self)(percent)
        CompositeStrat.sold.append(percent * CompositeStrat.weight)


class MACDCrossover(Strategy):
    strategy_description = 'This strategy is a simple utilization of the MACD indicator. When the signal line (9-period EMA) is above the MACD line (difference between 26- and 12-period EMA’s), it is a sign to buy. If the opposite - sell. The periods are configurable in settings. '
    strategy_image = 'static_images/MACD-Crossover.png'
    defaults = {
        'trade_amount': 100.,
        'long_window': 26,
        'short_window': 12,
        'signal_window': 9,
        'sideway_margin': 20.,
        'sideway_window': 5,
    }
    
    proto_params = {
        'trade_amount': [0.1, 100.],
        'long_window': [12, 40],
        'short_window': [6, 40],
        'signal_window': [2, 18],
        'sideway_margin': [0., 100.],
        'sideway_window': [3, 20],
    }

    descriptions = {
        'trade_amount': '%% of the starting balance to invest in the trade',
        'long_window': 'Period of MACD long Exponential Moving Average',
        'short_window': 'Period of MACD short Exponential Moving Average',
        'signal_window': 'Period of signal Exponential Moving Average',
        'sideway_margin': 'Maximum difference between the signal EMA and MACD. Used to activate Sideways Pattern',
        'sideway_window': 'Number of concurrent bars within the sideways distance ratio to activate Sideways Pattern',
    }

    display_names = {
        'trade_amount': 'Trade Amount',
        'long_window': 'MACD Long EMA',
        'short_window': 'MACD Short EMA',
        'signal_window': 'Signal EMA',
        'sideway_margin': 'Sideways Distance Ratio',
        'sideway_window': 'Samples for Sideways',
    }

    param_types = {
        'trade_amount': '%',
        'long_window': 'samples',
        'short_window': 'samples',
        'signal_window': 'samples',
        'sideway_margin': '%',
        'sideway_window': 'samples',
    }

    def __init__(self, env, **kwargs):
        super().__init__(env, **kwargs)

    def required_samples(self):
        return max([self.hp['long_window'], self.hp['short_window'], self.hp['sideway_window']]) + 1

    def step(self):
        signal = self.env.ti.macd_signal(self.hp['signal_window'], self.hp['long_window'], self.hp['short_window'], return_all=True)[9:]
        macd = self.env.ti.macd(self.hp['long_window'], self.hp['short_window'], return_all=True)[9:]
        diff = signal - macd

        # sideway_d = np.abs(np.ediff1d(signal[-self.sideway_window:]))
        # sideway_d /= np.max(sideway_d)


        # sideway_d = diff[-self.sideway_window:-1]
        # sideway_m = np.mean(sideway_d)
        # sideway_d -= sideway_m
        # sideway_d /= sideway_m
        # sideway_d = np.abs(sideway_d)
        # # print(sideway_d)

        # if (sideway_d < p2f(self.sideway_margin)).all():
        #     print(sideway_d, abs(sideway_d.min() / sideway_d.max()))
        #     # Percentage difference between max/min is less than margin (e.g.20%)
        #     # Assume a sideways market, no buy/sell
        #     return 

        # Debug plot
        # fc = max(np.mean(signal), np.mean(macd))
        # plt.plot(np.arange(len(macd)), 5 * macd / fc, 'r') 
        # plt.plot(np.arange(len(signal)), 5 * signal / fc, 'b')
        # plt.plot(np.arange(len(diff)), diff/2, 'g')
        

        # Only on crossover (current is [over] line, previous was [under])
        if diff[-1] < 0 and diff[-2] >= 0: 
            self.sell(p2f(self.hp['trade_amount']))
            # plt.text(0, 0, 'buy')
            # print('sell')
        
        if diff[-1] > 0 and diff[-2] <= 0: 
            # plt.text(0, 0, 'sell')
            self.buy(p2f(self.hp['trade_amount']))

        # plt.show()

class ChaosMonkey(Strategy):
    defaults = {
    }
    
    proto_params = {
    }


    def __init__(self, env, **kwargs):
        super().__init__(env, **kwargs)
        self.random_state = np.random.RandomState(seed=kwargs.get('seed', 124098))

    def step(self):
        choice = self.random_state.randint(0, 3)

        if choice == 1: # BUY
            self.buy(self.random_state.uniform(0,.5))
        elif choice == 2:
            self.sell(self.random_state.uniform(0,.5))


class MACrossover(Strategy):
    strategy_description = 'The strategy utilizes 2 moving averages and simple trigger - crossover. Two of the most common moving averages used by the traders are 50- and 200-period moving averages. MA periods are configurable in settings'
    strategy_image = 'static_images/MA-Crossover.png'
    defaults = {
        'long_window': 200,
        'short_window': 50
    }

    proto_params = {
        'long_window': [20, 400],
        'short_window': [20, 200]
    }

    descriptions = {
        'long_window': 'Period of long Moving Average',
        'short_window': 'Period of short Moving Average',
    }

    display_names = {
        'long_window': 'MA Long',
        'short_window': 'SMA Short',
    }

    param_types = {
        'long_window': 'samples',
        'short_window': 'samples'
    }

    def __init__(self, env, **kwargs):
        super().__init__(env, **kwargs)
        self.wasBull = False

    def required_samples(self):
        return max([self.hp['long_window'], self.hp['short_window']]) + 1

    def step(self):
        longv = self.env.ti.moving_avg(self.hp['long_window'])
        shortv = self.env.ti.moving_avg(self.hp['short_window'])

        if not self.wasBull and shortv > longv:
            self.buy(75.)

        if self.wasBull and longv > shortv:
            self.sell(75.)

        self.wasBull = shortv > longv



# This class implements a stateful version
class StuckInABox(Strategy):
    strategy_description = 'Sometimes when the market is indecisive whether the price should go up or down, the price is stuck between 2 levels and bouncing from each other multiple times demonstrating sideways movement. The strategy trades currency between upper and lower levels of the sideways channel.'
    strategy_image = 'static_images/Stuck-in-a-Box.png'
    defaults = {
        'trade_amount': 100.,
        'window': 100,
        'tolerance': 2.,
        'exit_trade_amount': 100.,
        'min_bounce': 2,
        'min_gap': 2,
    }
    
    proto_params = {
        'trade_amount': [0., 100.],
        'window': [50, 200],
        'tolerance': [0.1, 5],
        'exit_trade_amount': [0., 100.],
        'min_bounce': [2, 10],
        'min_gap': [2, 100],
    }

    display_names = {
        'trade_amount': 'Trade Amount',
        'window': 'Window',
        'tolerance': 'Tolerance',
        'exit_trade_amount': 'Exit Amount',
        'min_bounce': 'Minimum Number of Bounces',
        'min_gap': 'Minimum In-between Gap',
    }

    descriptions = {
        'trade_amount': '%% of the starting balance to invest in the trade',
        'window': 'Number of candlesticks before the start date taken for pattern recognition',
        'tolerance': 'The factor of the size of the bounds. %% above and below the bound to count bounces',
        'exit_trade_amount': '%% of the current trade value to use when exiting the trade ',
        'min_bounce': 'Minimum number of bounces from each upper and lower bound to activate the pattern',
        'min_gap': 'Minimum number of bars in-between bounces to consider the next bounce. Used to prevent false signals',
    }

    param_types = {
        'trade_amount': '%',
        'window': 'samples',
        'tolerance': '%',
        'exit_trade_amount': '%',
        'min_bounce': 'samples',
        'min_gap': 'samples',
    }

    def __init__(self, env, **kwargs):
        super().__init__(env, **kwargs)
        self.reset() 
        self.prevPattern = False # To detect entry/exit

    def required_samples(self):
        return self.hp['window'] + 13

    def isInPattern(self) -> Tuple[bool, float, float]:
        cursor = self.env.indexstep - self.hp['window']
        limit = self.env.indexstep - 5 # We don't expect the pattern to be <50 candle sticks long

        # Go through all possible timeframes for the pattern
        while not isinstance(cursor, tuple) and cursor < limit:
            cursor = self._isInPattern(cursor)

        # No valid pattern found
        if not isinstance(cursor, tuple):
            return False, -1., -1.

        return cursor

    def findNextPeek(self, start, gradreq=1):
        start += 2 # Get away from possible previous peek
        lwma_window = 12
        # print(self.env._calc_window(lwma_window, offset=-start), start)
        while self.env.is_valid_window(lwma_window, offset=-start) and abs(self.env.ti.d_lwma(lwma_window, offset=-start)) < gradreq:
            start += 1

        return start -1

    def _isInPattern(self, window):
        # print(window)
        data = self.env.get_view()[window:]

        # Sort indexes by amount
        sIdx = data.argsort()
        upper = data[sIdx[-1]]
        lower = data[sIdx[0]]

        # print(upper, lower)
        
        def getBounces(_pRange):
            inRange = np.logical_and(data > _pRange[1], data < _pRange[0])
            inRange[0] = False # First element should be outside of bound

            _btransitions = inRange[:-1] != inRange[1:] #getTransitions(inRange)[1:-1] # Remove first and last element
            _transitions = np.nonzero(_btransitions)[0]

            if len(_transitions) > 1:
                # Filter by gap and return

                # future: use abs if it's unsorted
                _bad_tran = _transitions[1:] - _transitions[:-1] < self.hp['min_gap']
                _bad_tran = np.append(_bad_tran, True) # Last item is always accepted

                _transitions = _transitions[_bad_tran]

                return list(_transitions)
            return []
        
        # Find other data within our tolerance
        pRange = [upper, upper * pow((1 - p2f(self.hp['tolerance'])), 2)]
        upper_transitions = getBounces(pRange) or [0]
        if len(upper_transitions) // 2 < self.hp['min_bounce']:
            return self.findNextPeek(max(upper_transitions[-1], sIdx[0]) + 1 + window)

        pRange = [lower * ((1 + p2f(self.hp['tolerance'])) ** 2), lower]
        lower_transitions = getBounces(pRange) or [0]
        if len(lower_transitions) // 2 < self.hp['min_bounce']:
            return self.findNextPeek(max(lower_transitions[-1], sIdx[-1]) + 1 + window)

        if upper_transitions[-1] < lower_transitions[0]:
            return lower_transitions[0] - 1 + window

        if lower_transitions[-1] < upper_transitions[0]:
            return upper_transitions[0] - 1 + window

        # TODO: Validate transition indices (are they actually within max/min?)
        # print(np.array(upper_transitions) + window, np.array(lower_transitions) + window, sIdx[0] + window, sIdx[-1] + window, self.env.indexstep)
        return True, upper, lower

    def step(self):
        v = self.env.current_v()
        inPattern, bmax, bmin = self.isInPattern()

        if inPattern:
            # print("Found StuckInABox Pattern! %.3f %.3f" % (bmax, bmin))
            # Buy/Sell/Hold
            if withinTolerance(bmax, v, tolerance=self.hp['tolerance']):
                self.sell(p2f(self.hp['trade_amount']))

            if withinTolerance(bmin, v, tolerance=self.hp['tolerance']):
                self.buy(p2f(self.hp['trade_amount']))
        elif self.prevPattern:
            self.sell(p2f(self.hp['exit_trade_amount']))
            # TODO: Export supstanceData

        self.prevPattern = inPattern

    def reset(self):
        pass
