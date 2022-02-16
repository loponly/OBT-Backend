

# Strategy
## Introduction

Strategy is the base class all trading strategies should implement.

It includes some meta-programming to automatically verify parameters and put them in the right type.

We use it to create different trading strategies for market data which are easily adaptable into our simulation/realtime enviroment.
It uses `SimuMetrics` and `TechnicalIndicators` to trade automatically.

There are many different [strategies already implemented](#Implementations). 

## Usage

All strategies have a few meta-data items:

* param_types:    Provides the suffix/prefix for the UI
* proto_params:   Provides the bounds of the parameters
* descriptions:   Provides a description of each parameter for the UI
* display_names:  Provides a neam which can be used in the UI
* defaults:       Provides the default values for each parameter

They also all implement `step` and `required_samples`.

`step` is called when a new candle (datapoint) is available.

`required_samples` is used to check if we have enough data available to even run the strategy (this might change depending on the parameters).


## Source

Code available at: [https://git.fhict.nl/I404788/trading-bot/-/blob/master/tradeEnv/strategy.py](https://git.fhict.nl/I404788/trading-bot/-/blob/master/tradeEnv/strategy.py)

## Implementations

### BuyAndHold
BuyAndHold is the baseline strategy which we use to compare the other strategies.
As the name implies it only buys and doesn't sell.

### RSIDifferential
Also called RSI Threshold this strategy uses the RSI indicator to buy/sell.
It is fully described in our strategy research document.

### CompositeStrat
A module using a bit of meta-programming, which wraps multiple strategies and combines them into one.

Strategies can be weighted if one is more important than others.

### MACDCrossover
This is one of the standard strategies described in the strategy research document.

It's currently still in WIP because one of the features described in the research document doesn't work in testing.

### ChaosMonkey
This is a strategy suggested by our stakeholder, it trades at random (asif a monkey is on the keyboard).

### MACrossover
Similar to MACDCrossover, it is described in the strategy research document.

It is a very simple strategy and doesn't need to be adjusted.

### StuckInABox
Like some of the other strategies this is one from our research document. As far as I'm aware this is created by Max as an alternative to Swing Trading.

It is a relatively complex algorithm and still requires more testing, however it is already functional.
