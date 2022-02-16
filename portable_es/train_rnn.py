import torch
import numpy as np
from tensorboardX import SummaryWriter
from evosim.models.seqclass import Network, FilterNetwork, RNNClassifier
from tradeEnv.gym import SimuGym, PredGym

learning_rate = 0.005 # If you set this too high, it might explode. If too low, it might not learn
iters = 1024
channels = 5

torch.manual_seed(2); np.random.seed(2)
device = torch.device('cuda:0')
gym = PredGym(markets=['BTC:USDT', 'LTC:USDT', 'BNB:USDT', 'ETH:USDT'], candleSizes=['15m'], max_steps=iters, device=device)
model = RNNClassifier(1, 5, channels=channels, hidden=32, layers=5, device=device)
criterion = torch.nn.MSELoss(reduction='mean')
optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
writer = SummaryWriter('runs/rnn-1')

import atexit
def exit_handler():
	torch.save(model, 'rnn-m.pt')

atexit.register(exit_handler)

model.to(device)
with torch.cuda.device(device):
  for e in range(1000):
    gym.randomize()
    gym.reset()
    model.reset()

    ground_log = torch.zeros((iters, channels), device=device)
    pred_log = torch.zeros((iters, channels), device=device)
    obs = gym.observe()
    obs = obs.to(device)
    obs.retain_grad()
    # TODO: vectorize by preloading iters
    actual = 0 
    for i in range(iters):
      pred_log[i] = model(obs)
      obs, _, done = gym.sstep()
      if done: 
        break
      ground_log[i] = obs
      actual += 1
      # print(pred_log[i], ground_log[i])

    loss = criterion(pred_log[:actual,:-1], ground_log[:actual,:-1]) + criterion(pred_log[:actual,-1], ground_log[:actual,-1]) * 0.2  # Volume not predicted only ohlc
    optimizer.zero_grad()
    loss.backward(retain_graph=True)
    optimizer.step()

    ploss = loss.item()
    if ploss > 1:
      print(ground_log[:actual], pred_log[:actual])
    print(ploss)
    writer.add_scalar('loss', ploss, e)