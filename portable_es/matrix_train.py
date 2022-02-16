from portable_es import ESManager, ESWorker

if __name__ == "__main__":
    import os
    import sys

    abs_path = os.path.realpath('..')
    if not abs_path in sys.path:
        sys.path.append(abs_path)
    os.chdir('../')

    from evosim.models.transformers import LinTransRNN
    from evosim.models.seqclass import Network, FilterNetwork, RNNClassifier, LSTMClassifier, TCNClassifier
    from tradeEnv.gym import SimuGym, PredGym
    from evosim.tests.envs.cartpole import CartPoleSwingUpEnv
    from optimizers import *
    import copy

    # {x: y[], a: b[]} -> [{x: y[0], a: b[0]}, {x: y[0], a: b[1]}, ....]
    # Generates matrix of all permutations in a dictionary of arrays
    # If non array is provided it is assumed to be a static value
    def generate_matrix(matrix: dict):
        x = list(matrix)[0]
        m = copy.deepcopy(matrix) # TODO: fix non-picklable iterables (i.e. generators)
        del m[x]
        
        # Statoc values
        if type(matrix[x]) != list:
            matrix[x] = [matrix[x]]

        for y in matrix[x]:
            if m:
                for om in generate_matrix(m):
                    yield {x: y, **om}
            else:
                yield {x: y}

    env_matrix = {
        'candleSizes': [['4h'], ['1h'], ['15m'], ['5m']], 
        'perception_window': [64, 128, 256], 
        'markets': [['BTC:USDT', 'LTC:USDT', 'BNB:USDT', 'ETH:USDT']], 
        'data_version': 3,
        'max_steps': 300
    }

    # env_m = list(generate_matrix(env_matrix))
    # print(len(env_m))
    # print(env_m)
    # sys.exit(0)

    config_matrix = {
        'sigma': 0.15, # delta~N(0,sigma)
        'sigma_decay': 0.9995,
        'lr': 0.01,
        'lr_decay': 0.99999,
        'optimizer': Adam(),
        'popsize': 52,
        'antithetic': True,
        'reward_norm': 'ranked',
        'epochs': 1000,
        'device': 'cpu',
        'pre_training_epochs': 0,

        'env_config': list(generate_matrix(env_matrix)),
        'env_class': SimuGym,
        'env_eval_every': 5,
        'env_episodes': 4,

        'model_class': RNNClassifier,
        'model_args': (1, 3),
        'model_kwargs': {'hidden': 24, 'channels': 130, 'layers': 3},
    }

    for config in generate_matrix(config_matrix):
        file = f'GRU{config["model_kwargs"]["hidden"]}-{config["env_config"]["candleSizes"][0]}-{config["env_config"]["perception_window"]}.pt'
        if os.path.isfile(file):
            print(f'Skipping {file}, already trained')
            continue
        trace_file = f'GRU{config["model_kwargs"]["hidden"]}-{config["env_config"]["candleSizes"][0]}-{config["env_config"]["perception_window"]}-trace.pt'

        config['logdir'] = f'rnn{config["model_kwargs"]["hidden"]}-D3-matrix-{config["env_config"]["candleSizes"][0]}-{config["env_config"]["perception_window"]}'
        config['model_kwargs']['channels'] = config['env_config']['perception_window'] * 2 + 2
        print(f'Running... ({file}, {config["logdir"]})')

        manager = ESManager(config)

        for n in range(5):
            manager.create_local_worker(ESWorker)

        setattr(manager.model.model, 'config', config['env_config']) # For production usage
        setattr(manager.model.model, 'candles', config['env_config']['candleSizes'][0])

        # For adding remote workers
        print('client creds:', manager.get_client_args())

        while not manager.done:
            manager.run_once()

        torch.save(manager.model.model, file)
        torch.save({'trace': manager.update_history,
                    'raw_trace': manager.raw_history, **config}, trace_file)

        # Stop all workers
        manager.stop()
