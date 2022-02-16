# SimuMetrics
## Introduction
SimuMetrics is the simulation model used in backtesting and ML training.
We need this to handle simulation/interaction with market data, which is one of the main requirements for our current application; and we can build on it with various different modules.

Simumetrics model mainly focuses on providing an unified interface for strategies and developers to work with.
It allows you to step through the history and write code asif you were interacting with a real trading enviroment.

## Usage
SimuMetrics is currently used in backtesting (`backrunner.py`), AI training (`gym.py`) and is extended in real-time trading (`realtime.py`).

It allows for data retrieval (`get_window`, `current_v`, `portfolioValue`, `get_timestamp`), virtual trading (`buy`, `sell`), and stepping (`step`).

`indexstep` can be used to change the virtual time in the simulator. All data retrieval functions are relative to this virtual time.

## Requirements

It requires an `ApiAdapter` as input to load in historical data.

It internally uses `MarketInfo` and `UserMetrics` as CRUD layers, and `TechnicalIndicators` as an extension layer for more complex math functions.


## Source 
Code available at: [https://git.fhict.nl/I404788/trading-bot/-/blob/master/tradeEnv/metrics.py](https://git.fhict.nl/I404788/trading-bot/-/blob/master/tradeEnv/metrics.py)


## Related Modules

`UserMetrics` is used by `SimuMetrics` as container for user specific data.

`MarketInfo` is used by `SimuMetrics` as a container for historical data.

`TechnicalIndicators` is used by `SimuMetrics` as a logic layer for technical indicators.

`Strategy` uses `SimuMetrics` as an interface to the data and actions.

`Backrunner` uses `SimuMetrics` and `Strategy` to create an automated testing module

`Gym` uses `SimuMetrics` to create a virtual enviroment for ML models to work in.