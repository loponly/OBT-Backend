import numpy as np
from decimal import *

np.seterr(divide='raise')

# RSI is basically an exponentially smoothed gain/loss factor
# Where alpha is the time period chosen
# One of c_g or c_l will be 0 and the other positive
# 1 + ((p_g * alpha + c_g) / (p_l * alpha + c_l))
# Exactly accurate with the algorithm as described on 
# https://school.stockcharts.com/doku.php?id=technical_indicators:relative_strength_index_rsi
def rsi(prices, n=14):
    seed = np.ediff1d(prices) # FIXME: Needs 2n to get full n rsi because of ema

    # Extract gain/loss (default 0)
    up = np.where(seed >= 0, seed, [1e-9])
    down = -np.where(seed < 0, seed, [1e-9])
    
    # Seed
    up = np.concatenate(([np.mean(up[:n])], up[n:]))
    down = np.concatenate(([np.mean(down[:n])], down[n:]))

    # (SES) (1 / n) = n-1:1 
    up_data = ewma_vectorized_safe(up, 1/n)
    down_data = ewma_vectorized_safe(down, 1/n)

    rsi = 1 - (1 / (1 + (up_data / down_data)))
    return rsi[-n:]
    
# [Deprecated use `rsi` instead]
# Accurate within 5%
def rsiFunc(prices, n=14):
    deltas = np.diff(prices)
    seed = deltas[:n+1]
    up = seed[seed>=0].sum()/n + 1e-9
    down = -seed[seed<0].sum()/n + 1e-9
    rs = up/down
    rsi = np.zeros_like(prices)
    rsi[:n] = 100. - 100./(1.+rs)

    for i in range(n, len(prices)):
        delta = deltas[i-1] # cause the diff is 1 shorter

        if delta>0:
            upval = delta
            downval = 0.
        else:
            upval = 0.
            downval = -delta

        up = (up*(n-1) + upval)/n
        down = (down*(n-1) + downval)/n

        rs = up/down
        rsi[i] = 100. - 100./(1.+rs)

    return (rsi / 100.)

# .5 = 1:1, .33333 = 2:1, .25 = 3:1 alpha = old:new
def ewma_vectorized(data, alpha, offset=None, dtype=None, order='C', out=None):
    """
    Calculates the exponential moving average over a vector.
    Will fail for large inputs.
    :param data: Input data
    :param alpha: scalar float in range (0,1)
        The alpha parameter for the moving average.
    :param offset: optional
        The offset for the moving average, scalar. Defaults to data[0].
    :param dtype: optional
        Data type used for calculations. Defaults to float64 unless
        data.dtype is float32, then it will use float32.
    :param order: {'C', 'F', 'A'}, optional
        Order to use when flattening the data. Defaults to 'C'.
    :param out: ndarray, or None, optional
        A location into which the result is stored. If provided, it must have
        the same shape as the input. If not provided or `None`,
        a freshly-allocated array is returned.
    """
    data = np.array(data, copy=False)

    if dtype is None:
        dtype = np.float32 if data.dtype == np.float32 else np.float64
    else:
        dtype = np.dtype(dtype)

    if data.ndim > 1:
        # flatten input
        data = data.reshape(-1, order)

    if out is None:
        out = np.empty_like(data, dtype=dtype)
    else:
        assert out.shape == data.shape
        assert out.dtype == dtype

    if data.size < 1:
        # empty input, return empty array
        return out

    if offset is None:
        offset = data[0]

    alpha = np.array(alpha, copy=False).astype(dtype, copy=False)

    # scaling_factors -> 0 as len(data) gets large
    # this leads to divide-by-zeros below
    scaling_factors = np.power(1. - alpha, np.arange(data.size + 1, dtype=dtype),
                               dtype=dtype)
    # create cumulative sum array
    np.multiply(data, (alpha * scaling_factors[-2]) / scaling_factors[:-1],
                dtype=dtype, out=out)
    np.cumsum(out, dtype=dtype, out=out)

    # cumsums / scaling
    out /= scaling_factors[-2::-1]

    if offset != 0:
        offset = np.array(offset, copy=False).astype(dtype, copy=False)
        # add offsets
        out += offset * scaling_factors[1:]

    return out

def ewma_vectorized_2d(data, alpha, axis=None, offset=None, dtype=None, order='C', out=None):
    """
    Calculates the exponential moving average over a given axis.
    :param data: Input data, must be 1D or 2D array.
    :param alpha: scalar float in range (0,1)
        The alpha parameter for the moving average.
    :param axis: The axis to apply the moving average on.
        If axis==None, the data is flattened.
    :param offset: optional
        The offset for the moving average. Must be scalar or a
        vector with one element for each row of data. If set to None,
        defaults to the first value of each row.
    :param dtype: optional
        Data type used for calculations. Defaults to float64 unless
        data.dtype is float32, then it will use float32.
    :param order: {'C', 'F', 'A'}, optional
        Order to use when flattening the data. Ignored if axis is not None.
    :param out: ndarray, or None, optional
        A location into which the result is stored. If provided, it must have
        the same shape as the desired output. If not provided or `None`,
        a freshly-allocated array is returned.
    """
    data = np.array(data, copy=False)

    assert data.ndim <= 2

    if dtype is None:
        dtype = np.float32 if data.dtype == np.float32 else np.float64
    else:
        dtype = np.dtype(dtype)

    if out is None:
        out = np.empty_like(data, dtype=dtype)
    else:
        assert out.shape == data.shape
        assert out.dtype == dtype

    if data.size < 1:
        # empty input, return empty array
        return out

    if axis is None or data.ndim < 2:
        # use 1D version
        if isinstance(offset, np.ndarray):
            offset = offset[0]
        return ewma_vectorized(data, alpha, offset, dtype=dtype, order=order,
                               out=out)

    assert -data.ndim <= axis < data.ndim

    # create reshaped data views
    out_view = out
    if axis < 0:
        axis = data.ndim - int(axis)

    if axis == 0:
        # transpose data views so columns are treated as rows
        data = data.T
        out_view = out_view.T

    if offset is None:
        # use the first element of each row as the offset
        offset = np.copy(data[:, 0])
    elif np.size(offset) == 1:
        offset = np.reshape(offset, (1,))

    alpha = np.array(alpha, copy=False).astype(dtype, copy=False)

    # calculate the moving average
    row_size = data.shape[1]
    row_n = data.shape[0]
    scaling_factors = np.power(1. - alpha, np.arange(row_size + 1, dtype=dtype),
                               dtype=dtype)
    # create a scaled cumulative sum array
    np.multiply(
        data,
        np.multiply(alpha * scaling_factors[-2], np.ones((row_n, 1), dtype=dtype),
                    dtype=dtype)
        / scaling_factors[np.newaxis, :-1],
        dtype=dtype, out=out_view
    )
    np.cumsum(out_view, axis=1, dtype=dtype, out=out_view)
    out_view /= scaling_factors[np.newaxis, -2::-1]

    if np.size(offset) != 1 or offset != 0:
        offset = offset.astype(dtype, copy=False)
        # add the offsets to the scaled cumulative sums
        out_view += offset[:, np.newaxis] * scaling_factors[np.newaxis, 1:]

    return out

def ewma_vectorized_safe(data, alpha, row_size=None, dtype=None, order='C', out=None):
    """
    Reshapes data before calculating EWMA, then iterates once over the rows
    to calculate the offset without precision issues
    :param data: Input data, will be flattened.
    :param alpha: scalar float in range (0,1)
        The alpha parameter for the moving average.
    :param row_size: int, optional
        The row size to use in the computation. High row sizes need higher precision,
        low values will impact performance. The optimal value depends on the
        platform and the alpha being used. Higher alpha values require lower
        row size. Default depends on dtype.
    :param dtype: optional
        Data type used for calculations. Defaults to float64 unless
        data.dtype is float32, then it will use float32.
    :param order: {'C', 'F', 'A'}, optional
        Order to use when flattening the data. Defaults to 'C'.
    :param out: ndarray, or None, optional
        A location into which the result is stored. If provided, it must have
        the same shape as the desired output. If not provided or `None`,
        a freshly-allocated array is returned.
    :return: The flattened result.
    """
    data = np.array(data, copy=False)

    if dtype is None:
        dtype = np.float32 if data.dtype == np.float32 else np.float
    else:
        dtype = np.dtype(dtype)

    row_size = int(row_size) if row_size is not None else get_max_row_size(alpha, dtype)

    if data.size <= row_size:
        # The normal function can handle this input, use that
        return ewma_vectorized(data, alpha, dtype=dtype, order=order, out=out)

    if data.ndim > 1:
        # flatten input
        data = np.reshape(data, -1, order=order)

    if out is None:
        out = np.empty_like(data, dtype=dtype)
    else:
        assert out.shape == data.shape
        assert out.dtype == dtype

    trailing_n = int(data.size % row_size)  # the amount of data leftover
    first_offset = data[0]

    if trailing_n > 0:
        row_n = int(data.size // row_size)  # the number of rows to use
        # set temporary results to slice view of out parameter
        out_main_view = np.reshape(out[:-trailing_n], (row_n, row_size))
        data_main_view = np.reshape(data[:-trailing_n], (row_n, row_size))
    else:
        out_main_view = out
        data_main_view = data

    # get all the scaled cumulative sums with 0 offset
    ewma_vectorized_2d(data_main_view, alpha, axis=1, offset=0, dtype=dtype,
                       order='C', out=out_main_view)

    scaling_factors = (1 - alpha) ** np.arange(1, row_size + 1)
    last_scaling_factor = scaling_factors[-1]

    # create offset array
    offsets = np.empty(out_main_view.shape[0], dtype=dtype)
    offsets[0] = first_offset
    # iteratively calculate offset for each row
    for i in range(1, out_main_view.shape[0]):
        offsets[i] = offsets[i - 1] * last_scaling_factor + out_main_view[i - 1, -1]

    # add the offsets to the result
    out_main_view += offsets[:, np.newaxis] * scaling_factors[np.newaxis, :]

    if trailing_n > 0:
        # process trailing data in the 2nd slice of the out parameter
        ewma_vectorized(data[-trailing_n:], alpha, offset=out_main_view[-1, -1],
                        dtype=dtype, order='C', out=out[-trailing_n:])
    return out

def get_max_row_size(alpha, dtype=float):
    assert 0. <= alpha < 1.
    # This will return the maximum row size possible on 
    # your platform for the given dtype. I can find no impact on accuracy
    # at this value on my machine.
    # Might not be the optimal value for speed, which is hard to predict
    # due to numpy's optimizations
    # Use np.finfo(dtype).eps if you  are worried about accuracy
    # and want to be extra safe.
    epsilon = np.finfo(dtype).tiny
    # If this produces an OverflowError, make epsilon larger
    return int(np.log(epsilon)/np.log(1-alpha)) + 1


def ewma_window_size(alpha, sum_proportion):
    # Increases with increased sum_proportion and decreased alpha
    # solve (1-alpha)**window_size = (1-sum_proportion) for window_size        
    return int(np.log(1-sum_proportion) / np.log(1-alpha))

# Standard E(W)MA doesn't know 'days' only alpha or smoothing factor
# This utility converts days to alpha using a 'common method'
# See https://www.investopedia.com/terms/e/ema.asp
def ema_days2alpha(days):
    return 2/(days+1)

# Return topk and their indices
def topk(arr, k=1):
    indices = arr.argsort()[-k:]
    return arr[indices], indices

def mink(arr, k=1):
    indices = arr.argsort()[:k]
    return arr[indices], indices

def continous_moving_average(a, n=3) :
    ret = np.cumsum(a)
    ret[n:] = ret[n:] - ret[:-n]
    return ret[n - 1:] / n

def scale(X, x_min, x_max):
    nom = (X-X.min(axis=0))*(x_max-x_min)
    denom = X.max(axis=0) - X.min(axis=0)
    denom[denom==0] = 1
    return x_min + nom/denom 

# Simple exponential Smoothing (same as EMA)
def ses(x, alpha=.5):
    y = []
    c = x[0]
    for z in x:
            a = (alpha * z) + ((1-alpha) * c)
            y.append(a)
            c = a
    return y

def softmax(x):
    return np.exp(x)/np.sum(np.exp(x))

def softmax(x):
    """Compute softmax values for each sets of scores in x."""
    e_x = np.exp(x - np.max(x))
    return e_x / e_x.sum(axis=0) # only difference

def abs_norm(x):
    return x / np.sum(x)

# Max variance
def max_var(x):
    minv = x.min()
    maxv = x.max()
    if abs(minv) > maxv:
        return minv
    else:
        return maxv

# Difference between max and min
def max_diff(x):
    minv = x.min()
    maxv = x.max()
    return maxv - minv

# Transfer sign between arithmatic operations
# First argument of f should be x
def neg_free(f, x, *args, **kwargs):
    # Transfer sign
    negs = x < 0
    x = np.abs(x)
    x = f(x, *args, **kwargs)
    x = x * np.where(negs, [-1], [1])
    return x

# Symmetric Log (essentially log1p + neg_free)
def symmetric_log(x):
    # Transfer sign
    x = np.array(x)
    negs = x < 0
    x = np.abs(x)
    x = np.log10(x + 1) # log1p
    x = x * np.where(negs, [-1], [1])
    return x

# Inverse of symmetric_log
def symmetric_exp(x):
    x = np.array(x)
    negs = x < 0
    x = np.abs(x)
    x = (10. ** x) - 1
    x = x * np.where(negs, [-1], [1])
    return x

def legacy_bound_norm(x, a=0, b=1, btype='quadratic'):
    assert a < b, "Failed %.2f > %.2f" % (a, b)
    max_x = x.max() + 1e-9
    #  a + (b-a) × x/10 or a + (b-a) × (x/10)2 ≥ a or a × (b/a)x/10 ≥ 0.
    if btype == 'linear':
        return a + (b-a) * (x/max_x)
    elif btype == 'quadratic':
        # assert x >= 0, "quadratic requires x >= 0"
        return a + (b-a) * (x/max_x) ** 2
    elif btype == 'exponential':
        assert a > 0, "exponential requires a > 0"
        return a * (b/a) ** (x/max_x)
    else:
        raise TypeError('btype "%s" does not exist' % btype)


# Retains the negative sign, using a constant transfer function
def bound_norm(x, a=0, b=1, maxsv=None, btype='quadratic'):
    assert a < b, "Failed %.2f > %.2f" % (a, b)

    if not maxsv:
        maxsv = max(abs(x.min()), abs(x.max()))

    negatives = x.min() < 0
    # Transfer sign
    if negatives:
        negs = x < 0
        x = np.abs(x)
        oa = a
        if oa < 0:
            assert b == -a, "Using negative a requires the center to be at 0"
            a = 0
        

    #  a + (b-a) × x/10 or a + (b-a) × (x/10)2 ≥ a or a × (b/a)x/10 ≥ 0.
    if btype == 'linear':
        x = a + (b-a) * (x/maxsv)
    elif btype == 'quadratic':
        # assert x >= 0, "quadratic requires x >= 0"
        x = a + (b-a) * (x/maxsv) ** 2
    elif btype == 'cuberoot':
        # Root to include more definition around 0
        x = a + (b-a) * (x ** (1/3))/(maxsv ** (1/3))
    elif btype == 'exponential':
        assert a > 0, "exponential requires a > 0"
        x = a * (b/a) ** (x/maxsv)
    else:
        raise TypeError('btype "%s" does not exist' % btype)
    
    if negatives:
        x = x * np.where(negs, [oa], [1])
        
    return x

def meandev_norm(x):
    return (x - x.mean()) / x.std()


def euclidean_distance(x, y):
    n, m = len(x), len(y)
    if n > m:
        a = np.linalg.norm(y - x[:m])
        b = np.linalg.norm(y[-1] - x[m:])
    else:
        a = np.linalg.norm(x - y[:n])
        b = np.linalg.norm(x[-1] - y[n:])
    return np.sqrt(a**2 + b**2)

def compute_novelty_vs_archive(archive, novelty_vector, k):
    nov = novelty_vector.astype(np.float)
    distances = [
        euclidean_distance(point.astype(np.float), nov) for point in archive
    ]

    # Pick k nearest neighbors
    distances = np.array(distances)
    top_k_indicies = (distances).argsort()[:k]
    top_k = distances[top_k_indicies]
    return top_k.mean()

def sign(a):
    return (a>0) - (a<0)

def b2sign(b):
    return -1 if not bool(b) else 1

# Default 10%
def withinTolerance(mean, v, tolerance=0.1):
    # FIXME: faulty percentage difference calculations
    return abs(mean) * (1 + tolerance) > abs(v) and abs(mean) * (1 - tolerance) < abs(v)

def sigmoid(x):                                        
    return 1 / (1 + np.exp(-x))

def safeVal(obj):
    if type(obj) in [np.ndarray, list, float]:
        return np.nan_to_num(obj)
    else:
        return obj

#def quantize_float(x, fixed_point=5):
#    x = Decimal(x).quantize(Decimal(10**-fixed_point), rounding=ROUND_DOWN)
#    return float(x)

#def quantize_float(x, fixed_point=5):
#    x -= 4.9999999999999 * (10 ** -(fixed_point + 1)) # Round down
#    return np.around(x, fixed_point)

def quantize_float(x, fixed_point=5):
    x = np.floor(x * (10 ** fixed_point))

    return x / (10 ** fixed_point)

# epsilon, a small number so we don't get divide_by_zero errors
eps = 1e-9
