import os
import sys

abs_path = os.path.realpath('..')
if not abs_path in sys.path:
    sys.path.append(abs_path)
os.chdir('../')

import torch
from evosim.models.transformers import LinTransRNN
from evosim.models.seqclass import Network, FilterNetwork, RNNClassifier, LSTMClassifier, TCNClassifier
from evosim.models.regressors import RNNLimit
from tradeEnv.gym import SimuGym, PredGym
from evosim.tests.envs.cartpole import CartPoleSwingUpEnv
from portable_es import ESManager, ESWorker
from portable_es.optimizers import Adam, AdaBelief, AdaMM


config = {
    # Hyperparameters
    'sigma': 0.1, # delta~N(0,sigma)
    'sigma_decay': 0.9995,
    # 'sigma': 0.05,
    # 'sigma_decay': 0.99999,
    'lr': 0.0015,
    'lr_decay': 0.9999,
    'optimizer': AdaBelief(),
    # 'popsize': 52,
    # 'antithetic': True,
    'popsize': 52,
    'antithetic': True,
    'reward_norm': 'ranked',  # ranked, stdmean # TODO: topk
    'epochs': 10000,
    'device': 'cpu',
    'pre_training_epochs': 0,
    # 'logdir': 'rnn24_3-d3-4h-ls-1',
    'logdir': 'rnn64_3-4h-22-D6.1-64-ta-xmr',
    # 'logdir': 'tcn-14-D4-4h',

    # Model/Env config
    # 'model_class': TCNClassifier,
    # 'model_args': (128, 3, [64, 32, 16, 8, 16]),
    # 'model_kwargs': {'dropout': 0., 'kernel_size': 2},

    # 'model_class': RNNLimit,
    # 'model_args': (1,),
    # 'model_kwargs': {'channels': 64 * 5, 'hidden': 64, 'layers': 3},

    # 'model_class': LinTransRNN,
    # 'model_args': (1, 3),
    # 'model_kwargs': {'state_size': 24, 'channels': 5, 'hchannels': 24, 'seqlen': 128, 'state_layers': 2},

    'model_class': RNNClassifier,
    'model_args': (1, 3),
    'model_kwargs': {'hidden': 64, 'channels': 64 * 5, 'layers': 3},

    # 'model_class': FilterNetwork,
    # 'model_args': (130, 3, 75),
    # 'model_kwargs': {},
    'env_eval_every': 5,
    'env_class': SimuGym, #, 'LTC:USDT', 'BNB:USDT', 'ETH:USDT'
    'env_config': {'markets': ['BTC:USDT'], 'candleSizes': ['1h'], 'data_version': 3,'max_steps': 300, 'skip_frames': 0},
    'env_episodes': 4,

    # 'model_class': FilterNetwork,
    # 'model_args': (5, 1, 30),
    # 'model_kwargs': {},
    # 'env_eval_every': 5,
    # 'env_class': CartPoleSwingUpEnv,
    # 'env_config': {},
    # # 'env_config': {'markets': ['BTC:USDT', 'LTC:USDT', 'BNB:USDT', 'ETH:USDT'], 'candleSizes': ['4h'], 'max_steps': 300, 'skip_frames': 0},
    # 'env_episodes': 1,

    # 'model_class': RNNClassifier,
    # 'model_args': (1, 5),
    # 'model_kwargs': {'hidden': 32, 'channels': 5, 'layers': 7},
    # 'env_class': PredGym,
    # 'env_config': {'markets': ['BTC:USDT', 'LTC:USDT', 'BNB:USDT', 'ETH:USDT'], 'candleSizes': ['15m'], 'max_steps': 300, 'skip_frames': 0},
    # 'env_episodes': 4
}

manager = ESManager(config)
setattr(manager.model.model, 'config', config['env_config']) # For production usage
setattr(manager.model.model, 'candles', config['env_config']['candleSizes'][0])

for n in range(6):
    manager.create_local_worker(ESWorker)

# For adding remote workers
print('client creds:', manager.get_client_args())

# Setup exit handler
import atexit

def exit_handler():
    torch.save(manager.model.model, 'es-dc.pt')
    torch.save({'trace': manager.update_history,
                'raw_trace': manager.raw_history, **config}, 'checkpt-trace.pt')

atexit.register(exit_handler)

while not manager.done:
    manager.run_once()
    # Can do other tasks here as well

torch.save(manager.model.model, 'es-dc.pt')

# Stop all workers
manager.stop()