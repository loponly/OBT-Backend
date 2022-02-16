import numpy as np

def naive_omega(returns: np.ndarray, benchmarkf: float):
    top = np.sum(returns[returns>0]-benchmarkf)
    bottom = np.sum(benchmarkf-returns[returns<0])
    return top/bottom

def capital_market_line(portfolio_returns:np.ndarray, market_returns:np.ndarray, risk_free:float):
    """
    Note: this is not really a useful metric outside of macro analysis
    """
    return risk_free * len(market_returns) + (np.sum(market_returns - risk_free)/np.nanstd(market_returns)) * np.nanstd(portfolio_returns)

def drawdown(returns: np.ndarray):
    """
    Current relative dropdown
    """
    cummulative = np.cumprod(returns + 1)
    top = cummulative.max()
    return (cummulative[-1] - top) / top

def max_drawdown(returns: np.ndarray):
    """
    Max relative dropdown
    """
    cummulative = np.cumprod(returns + 1)
    end = np.argmax(np.maximum.accumulate(cummulative) - cummulative)
    if end == 0: # No drop found
        return 0
    top = np.max(cummulative[:end])
    return (cummulative[end] - top) / top

def information_ratio(returns: np.ndarray, benchmark: np.ndarray):
    diff = np.cumprod(returns + 1) - np.cumprod(benchmark + 1)
    return diff[-1] / np.nanstd(diff)

def covariance(x: np.ndarray, y: np.ndarray):
    "+Validated correct (by proxy)"
    return np.sum((x - x.mean()) * (y - y.mean())) / len(x)

def beta(returns: np.ndarray, market_average: np.ndarray):
    """
    With respect to the actual market or a market index
    +Validated correct
    """
    covar = covariance(returns, market_average)
    var = np.var(market_average)
    return covar/var

def final_return(returns: np.ndarray):
    return np.prod(returns + 1) - 1

def market_risk_premium(returns: float, risk_free: float):
    "+Validated correct (by proxy)"
    return returns - risk_free

def teynor_ratio(returns: np.ndarray, market_average: np.ndarray, risk_free: float):
    "+Validated correct"
    return (market_risk_premium(final_return(returns), risk_free)) / beta(returns, market_average)

def expected_returns(benchmark: np.ndarray, market_average: np.ndarray, risk_free: float):
    return risk_free + beta(benchmark, market_average) * market_risk_premium(final_return(benchmark), risk_free)

def excess_returns(returns: np.ndarray, benchmark: np.ndarray, market_average: np.ndarray, risk_free: float):
    return final_return(returns) - expected_returns(benchmark, market_average, risk_free)

def v2_ratio(returns: np.ndarray, benchmark: np.ndarray):
    """
    Benchmark recommended to be market average
    """
    creturns = np.cumprod(returns + 1)
    cbenchmark = np.cumprod(benchmark + 1)
    top = creturns[-1] - cbenchmark[-1]
    average_adjusted_returns = np.sqrt(np.sum([((creturns[i]/creturns[:i+1].max())-(cbenchmark[i]/cbenchmark[:i+1].max())) ** 2  for i in range(len(creturns))]) / len(creturns))
    return top / (1 + average_adjusted_returns)

def sterling_ratio(returns: np.ndarray, drawdown_periods: int = 3, drawdown_f=max_drawdown):
    """
    The original formula uses the arbitrary Ny:1y timeframes (some sources say 1y:1y), we should allow for any ratio
    We omit the arbitrary -10% as it's practically useless anyway
    +Validated correct
    """
    compound_ror = final_return(returns)
    dd_periods = np.array_split(returns, drawdown_periods)
    avg_drawdown = np.mean([drawdown_f(x) for x in dd_periods])
    return compound_ror / np.abs(avg_drawdown)
