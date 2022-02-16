import torch
import random
import numpy as np
import os
import sys
import pickle
from tensorboardX import SummaryWriter

abs_path = os.path.realpath('./')
if not abs_path in sys.path:
    sys.path.append(abs_path)

from tradeEnv.maths import meandev_norm, symmetric_log
from evosim.models.transformers import LinTransAutoregressiveEncoder

learning_rate = 3e-4 # If you set this too high, it might explode. If too low, it might not learn
iters = 1024
channels = 5
bs = 64
epochs = 500000

torch.manual_seed(2); np.random.seed(2)
device = torch.device('cuda:0')

envs = []
markets = ['BTC:USDT', 'LTC:USDT', 'ETH:USDT']
candle = '4h'
for market in markets:
  fpath = 'store/envs/%s_%s.pkl' % (market, candle)
  if os.path.isfile(fpath):
      with open(fpath, 'rb') as f:
          envs.append(pickle.load(f))


model = LinTransAutoregressiveEncoder()

def create_sinusoidal_embeddings(n_pos, bsize):
    pos_tensor = torch.arange(0,n_pos, dtype=torch.float32).repeat((bsize, 1)).T.detach()
    for j in range(bsize):
      pos_tensor[:, j] /= np.power(10000, 2*j/bsize)
    pos_tensor[:, 0::2] = torch.sin(pos_tensor[:, 0::2])
    pos_tensor[:, 1::2] = torch.cos(pos_tensor[:, 1::2])
    return pos_tensor


criterion = torch.nn.SmoothL1Loss(reduction='mean')
optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate, betas=(0.9, 0.95))
writer = SummaryWriter('runs/transformer-5')

import atexit
def exit_handler():
	torch.save(model.state_dict(), 'transformer-m.pt')

atexit.register(exit_handler)

model.to(device)
data_labels = ['close', 'open', 'high', 'low', 'volume']
emb = create_sinusoidal_embeddings(iters, len(data_labels)).to(device)

envs_data = []
for env in envs:
  data = torch.zeros((len(env)-1, len(data_labels)), device=device)
  for i, x in enumerate(data_labels):
    # if x == 'volume':
    #   data[:, i] = torch.from_numpy(env.get_view(dkey=x)[1:])
    #   data[:, i] /= torch.norm(data[:, i])
    # else:
    data[:, i] = meandev_norm(torch.from_numpy(np.ediff1d(env.get_view(dkey=x))))
  envs_data.append(data)

with torch.cuda.device(device):
  for z in range(epochs):
      xi = torch.zeros((bs, iters, len(data_labels)), device=device)
      xo = torch.zeros((bs, iters, len(data_labels)), device=device)
      for x in range(bs):
        denv = envs_data[random.randint(0, len(envs_data)-1)]
        idx = random.randint(1, len(denv)-iters-2)
        xi[x] = denv[idx:idx+iters, :]
        # print(xi[x])
        xo[x] = denv[idx+1:idx+iters+1, :]

      optimizer.zero_grad()
      po = model(xi)
      # print(po, xi)

      loss = criterion(po, xo)  # Volume not predicted only ohlc
      loss.backward()
      torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
      optimizer.step()

      ploss = loss.item()
      print(z, ploss)
      writer.add_scalar('loss', ploss, z)
