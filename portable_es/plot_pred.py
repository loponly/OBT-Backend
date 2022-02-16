import torch
import copy
import numpy as np
import trendvis
import matplotlib.pyplot as plt
from tradeEnv.metrics import SimulMetrics
from tradeEnv.api_adapter import ApiAdapter, binance_map, interpolate
from tradeEnv.gym import PredGym
from tradeEnv.maths import symmetric_exp

model = torch.load("rnn-m.pt")
device = torch.device('cpu')
model.to(device)
model.hn = None
model.device = device
model.reset()

gym = PredGym(['EOS:USDT'], ['15m'])
np.random.seed(0)
gym.randomize()
gym.reset()


def reverse_observe(data, ncandle):
    dohlcv = symmetric_exp(ncandle)
    dohlcv[:-1] = data[:-1] + dohlcv[:-1]
    dohlcv[-1] *= 2.
    return dohlcv


iterations = 20
data_range = 300
warm_up = 64
data = {}

def run_sim():
    queue = [None] * (iterations -2) + [copy.deepcopy(model)] # Use as shift register
    for i in range(data_range):
        for it in range(iterations):
            # Init
            if not data.get(it, False):
                data[it] = [[0,0,0,0,0]]

            if it == 0:
                candle = gym.observe().detach().numpy()
                data[it].append(reverse_observe(data[it][-1], candle))
                gym.sstep()
                continue

            # Eval
            # TODO: skip 64?
            cmodel = queue[len(queue)-it]
            if cmodel:
                with torch.no_grad():
                    ncandle = cmodel(torch.tensor(data[it-1][-2]).float())
                    if i > warm_up:
                        # TODO: fix jump after warmup
                        # TODO: check data issues?
                        data[it].append(reverse_observe(data[it-1][-2], ncandle.detach().numpy()))
                        if i == warm_up + 1:
                            print(data[it][-1], ncandle)
                    else:
                        data[it].append(data[0][-1])
            else:
                data[it].append(data[0][-1])
        queue.append(copy.deepcopy(queue[-1]))
        queue.pop(0) # remove models after iterations are done
        # print(queue)
        
    # candle = gym.observe().detach().numpy()
    # data[0].append(reverse_observe(data[0], candle))

    lw = 1.0
    ex0 = trendvis.XGrid([1,1,1,1,1], figsize=(20,20))

    for d in range(1, iterations, 2):
        del data[d]

    # for x in data:
    #     print(x, len(data[x][0]))

    # Convenience function for plotting line data
    # Automatically colors y axis spines to
    # match line colors (auto_spinecolor=True)

    # TODO: time marked?
    legend = ['black', 'lime', 'green', 'orchid', 'blue',  'purple', 'navy', 'orange', 'red', 'darkred']
    time_range = np.linspace(0, data_range+1, num=data_range+1)
    # print(np.array(data[4])[:,4])
    # print([(time_range, np.array(data[x])[:,0], legend[i],) for i, x in enumerate(data)])
    trendvis.plot_data(ex0,
        [
            [(time_range, np.array(data[x])[:,0], legend[i],) for i, x in enumerate(data)],
            [(time_range, np.array(data[x])[:,1], legend[i],) for i, x in enumerate(data)],
            [(time_range, np.array(data[x])[:,2], legend[i],) for i, x in enumerate(data)],
            [(time_range, np.array(data[x])[:,3], legend[i],) for i, x in enumerate(data)],
            [(time_range, np.array(data[x])[:,4], legend[i],) for i, x in enumerate(data)]
        ],
        lw=lw, markeredgecolor='none', marker='')

    # Get rid of extra spines
    ex0.cleanup_grid()
    ex0.set_spinewidth(lw)

    # ex0.set_all_ticknums([(2, 1)], [(0.2, 0.1), (1, 0.5), (2, 1)])
    # ex0.set_ticks(major_dim=(7, 3), minor_dim=(4, 2))

    ex0.set_ylabels(['close', 'low', 'high', 'open', 'volume'])

    # In XGrid.fig.axes, axes live in a 1 level list
    # In XGrid.axes, axes live in a nested list of [row][column]
    ex0.axes[2][0].set_xlabel('Time', fontsize=14)

    ex0.fig.savefig('plot.png')

run_sim()