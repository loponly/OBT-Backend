# API Compositor

## Project Structure
* `/` has config/data files
* `/tradeEnv` has simulation/testing enviroment 
* `/routes` has the API logic
* `/server.py` has server program/config

## Data
* Try different type of markets

## Viewing swagger UI
Note: on **windows** replace `$(pwd)` with the absolute path of root directory of this repository.

```
docker pull swaggerapi/swagger-ui
docker run -p 8080:8080 -e SWAGGER_JSON=/vol/openapi.json -v "$(pwd):/vol" swaggerapi/swagger-ui
```
Then open `localhost:8080`.

## Set up the API locally
### Requirements
* Python 3.6+ (w/ pip)

### Enviroment
```
git clone https://git.devdroplets.com/oi-2020/api-compositor.git
cd api-compositor
pip3 install -r requirements.txt 
```

### Downloading Market Data
Edit the `candlesType` & `MarkID` in `test_binance.py`
Then run:
```
python3 test_binance.py
```
There should now be a file in `/store/dataset` with the correct format.

### Starting the api
#### For Windows users
```
pip3 install waitress
waitress-serve server:api
```

#### For POSIX users
```
gunicorn server:api
```


## Creating new strategies
Creating a strategy is pretty easy you can create a `Strategy` subclass and implement `step(self)` as seen below.
More examples can be found in `/tradeEnv/strategy.py`

```py
# Uses RSI as a threshold function to buy/sell
class RSIThreshold(Strategy):
    def step(self):
        # We don't want to waste our buy/sell fees, so there is a gap
        upthreshold = 0.7
        downthreshold = 0.3

        dmav = self.env.ti.rsi(50)[-1:] # 24H
        
        if dmav > upthreshold:
            self.buy(30.)

        if dmav < downthreshold:
            self.sell(30.)
```

## Compositing strategies
We allow compsiting of strategies using the `CompositeStrat` class, this will take the average buy/sell strength and buy/sell that amounf if a certain threshold is hit (to avoid hyper-active trading).

```py
strat = CompositeStrat(env, [LWMADifferential(env), RSIThreshold(env), MovingDifferential(env)], threshold=10)
```

## Backtesting locally
You can backtest your strategy by modifying simulate.py, this will allow you to fully customize your backtest.
The resulting output is the average over all of the runs.

## Adding custom env
You can add env in `SimulMetrics` class or by writing some user-side code, examples of this can be found in the `SimulMetrics` class itself.

The most important thing is to use `get_view()` instead of using the data directly, this is so you don't look into 'future' while backtesting. 

## VSCode enviroment
Vscode should work with mostly default settings. 

The following plugins are recommended:
* `Python`  (2021.2+)
* `Pylance` (2021.1+, configure Python to use Pylance)
* `Better Comments` (2.1.0+)
* `Gitlens` (11.2+, Optional)
* `Git History` (0.6+, Optional)
