import torch
import random
import numpy as np
import os
import sys
import pickle
from tensorboardX import SummaryWriter
from linear_attention_transformer import LinearAttentionTransformer

abs_path = os.path.realpath('./')
if not abs_path in sys.path:
    sys.path.append(abs_path)

from tradeEnv.maths import meandev_norm, symmetric_log

learning_rate = 3e-4 # If you set this too high, it might explode. If too low, it might not learn
iters = 1024
channels = 5
bs = 64
epochs = 100000

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

class LinTrans(torch.nn.Module):
  def __init__(self):
    super().__init__()
    self.pos_emb = torch.nn.Parameter(torch.zeros(1, 1024, 64))
    self.expand = torch.nn.Linear(5, 64)
    self.emb = torch.nn.Linear(64, 64)
    self.conv_emb = torch.nn.Conv1d(1024, 1024, 3, padding=1)
    self.base = LinearAttentionTransformer(
          dim = 64,
          heads = 8,
          depth = 2,
          max_seq_len = 1024,
          n_local_attn_heads = 4
        )
    self.drop = torch.nn.Dropout(0.1)
    self.collapse = torch.nn.Linear(64, 5)

  def forward(self, x):
    x = self.expand(x)
    x = self.emb(x)
    x = self.conv_emb(x)
    x = self.drop(x + self.pos_emb)
    x = self.base(x)
    return self.collapse(x)


model = LinTrans()

def create_sinusoidal_embeddings(n_pos, bsize):
    pos_tensor = torch.arange(0,n_pos, dtype=torch.float32).repeat((bsize, 1)).T.detach()
    for j in range(bsize):
      pos_tensor[:, j] /= np.power(10000, 2*j/bsize)
    pos_tensor[:, 0::2] = torch.sin(pos_tensor[:, 0::2])
    pos_tensor[:, 1::2] = torch.cos(pos_tensor[:, 1::2])
    return pos_tensor


criterion = torch.nn.SmoothL1Loss(reduction='mean')
optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate, betas=(0.9, 0.95))
writer = SummaryWriter('runs/transformer-2')

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
    # TODO: better normalization than ediff1d :thinking:
    if x == 'volume':
      data[:, i] = torch.from_numpy(symmetric_log(env.get_view(dkey=x)[1:]))
      data[:, i] = data[:, i].log1p()
    else:
      data[:, i] = torch.from_numpy(symmetric_log(np.ediff1d(env.get_view(dkey=x))))
  envs_data.append(data)

with torch.cuda.device(device):
  vrae = VRAE(sequence_length=sequence_length,
              number_of_features = number_of_features,
              hidden_size = hidden_size, 
              hidden_layer_depth = hidden_layer_depth,
              latent_length = latent_length,
              batch_size = batch_size,
              learning_rate = learning_rate,
              n_epochs = n_epochs,
              dropout_rate = dropout_rate,
              optimizer = optimizer, 
              cuda = cuda,
              print_every=print_every, 
              clip=clip, 
              max_grad_norm=max_grad_norm,
              loss = loss,
              block = block,
              dload = dload)

  vrae.fit(train_dataset)
  vrae.save('vrae.pth')

