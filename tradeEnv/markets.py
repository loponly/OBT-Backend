from tradeEnv.maths import quantize_float
import numpy as np
from typing import Dict
import matplotlib.pyplot as plt
from .utils import span_from_candletype

"""
This file contains the basic structure needed to create/evaluate markets without any fancy simulation/processing
"""

class Market:
    historical: Dict[str, np.ndarray]
    candle_type: str

    # NOTE: this class should not have an explicit init

    def ohlc_json(self):
        """Return a json-able dict with market data for exporting"""
        d = {}
        for x in range(len(self.historical['time'])):
            tk = int(self.historical['time'][x])
            d[tk] = {}
            for k in self.historical:
                if k == 'time':
                    continue
                d[tk][k] = float(self.historical[k][x])
        
        return d

    def __len__(self):
        return len(self.historical['time'])

    def update(self, get_state=False):
        pass

    def _calc_window(self, window, offset=None):
        """Calculate window as python indices"""
        _window = window
        _offset = offset
        if offset is not None:
            if offset >= 0:
                _window = -_window - offset
                _offset = -offset
            else:
                # Basically regular index slicing
                _window = -offset + -window
                _offset = -offset
        else:
            _window = -window

        return _window, _offset

    # Checks if requested window operation is possible
    def is_valid_window(self, window, offset=None):
        """Checks if window request would succeed"""
        # Convert offset to index
        if offset:
            offset = len(self) - offset if offset >= 0 else -offset
        return len(self) > (window + (offset or 0))

    def get_window(self, window, offset=None, dkey='close'):
        """Get `window` samples from end-offset"""
        assert window > 0, "Window needs to be larger than 0"

        _window, _offset = self._calc_window(window, offset)
        assert self.is_valid_window(_window, _offset), "Offset/window too high for current view: %d + %d > %s" % (_window, _offset or 0, len(self))

        return self.historical[dkey][_window:_offset]

    def plot_market(self, candles=300, offset=None, dpi=200.0, figsize=[10,5]):
        """Create a matplotlib figure with a simple plot of the market (currently: x:time, y:close, e:[high, low])"""
        fig = plt.figure(figsize=figsize, dpi=dpi)
        ax = fig.gca()

        # self.limit_orders
        space = self.get_window(candles, offset=offset, dkey='time')
        close = self.get_window(candles, offset=offset, dkey='close')
        ehigh = self.get_window(candles, offset=offset, dkey='high') - close
        elow = close - self.get_window(candles, offset=offset, dkey='low') 
        # TODO: volume bars

        assert len(space) == candles, f"{len(space)} != {candles}"

        ax.errorbar(space, close, [elow, ehigh], linestyle='-', zorder=0, c='slategray')

        return fig

    def get_candle_period(self):
        return span_from_candletype(self.candle_type)


class SineMarket(Market):
    """
    Generates a market using sine waveform (close/open from 1. to 2.), alpha is the radian-per-step
    """
    def __init__(self, samples=500, alpha=np.pi/16, error=0.05, price_denom=None):
        super().__init__()
        self.candle_type = '1s'
        self.historical = {}
        self.historical['time'] = np.arange(samples)
        self.historical['close'] = ((np.sin(alpha*self.historical['time'])+1)/2) + 1
        self.historical['open'] = ((np.sin(alpha*self.historical['time'] - alpha)+1)/2)+1 
        self.historical['high'] = self.historical['close'] + error
        self.historical['low'] = self.historical['close'] - error
        self.historical['volume'] = np.ones(samples)

        if price_denom != None:
            for k in ['close', 'open', 'high', 'low']:
                self.historical[k] =  quantize_float(self.historical[k], price_denom)


class StepWaveMarket(Market):
    """
    Similar to SineMarket but a hard-switch between 1. & 2. every alpha steps
    """
    def __init__(self, samples=500, alpha=1, error=0.05, price_denom=None):
        super().__init__()
        self.candle_type = '1s'
        self.historical = {}
        self.historical['time'] = np.arange(samples)
        # Basically just converting sine to hard-sine but there are some numerical fixes (don't refactor)
        self.historical['close'] = np.round((np.sin((np.pi/alpha)*self.historical['time']+1e-9)+1)/2)+1
        self.historical['open'] = np.round((np.sin((np.pi/alpha)*(self.historical['time']+1)+1e-9)+1)/2)+1
        self.historical['high'] = self.historical['close'] + error
        self.historical['low'] = self.historical['close'] - error
        self.historical['volume'] = np.ones(samples)

        if price_denom != None:
            for k in ['close', 'open', 'high', 'low']:
                self.historical[k] =  quantize_float(self.historical[k])
        

