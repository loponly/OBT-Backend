import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.utils.prune as prune
import numpy as np
import os
import json
import time
import matplotlib.pyplot as plt
from tensorboardX import SummaryWriter
from tradeEnv.metrics import SimulMetrics
from tradeEnv.api_adapter import ApiAdapter, binance_map
from tradeEnv.maths import bound_norm, sign, symmetric_log, symmetric_exp, ewma_vectorized_safe, meandev_norm
from evosim.models.denoising import WavyCAE, ShallowAE, DeepConvAE, ConFAE, CVAE
from evosim.models.optimizers import QHAdamW

np.seterr(all='raise')

datasets = ['BTC:USDT', 'LTC:USDT', 'BNB:USDT', 'ETH:USDT']
close_dataset = []
volume_dataset = []

for d in datasets:
  marketid = d.replace(':', '')
  api = ApiAdapter(binance_map, '%s_%s' % (marketid, '5m')) 
  env = SimulMetrics(api, marketid)
  # print(env.data['close'])
  close_dataset.append(env.data['close'])
  volume_dataset.append(env.data['volume'])

# print(close_dataset)
torch.manual_seed(2); np.random.seed(2)

ctx = 1022
mtx = 2
inputs = ctx + mtx
ich = 2 # Depends on get_sample
# net = ShallowAE(inputs, 128)
net = CVAE(ctx + mtx, ich, cch=4, latent=128)
net = net.cuda()
writer = SummaryWriter('runs/cae-6')

def print_params(model):
    pcount = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print('Parameters: ', pcount)
    return pcount

NPARAMS = print_params(net)


epochs = 39999
batch_size = 64

criterion = nn.SmoothL1Loss(reduction='mean')
optimizer = QHAdamW(net.parameters(), lr=5e-4)
# optimizer = optim.Adam(net.parameters(), lr=5e-4, weight_decay=1e-5)
scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=500, factor=0.8)


def transform_data(x, maxv=64., reverse=False):
  if reverse:
    # transferf = lambda y: (maxv ** y ** 2) - 1
    # transferf = lambda y: ((y ** 3) * maxv)
    
    return ((x[:, :-2].T * symmetric_exp(x[:, -2])) + symmetric_exp(x[:, -1])).T
  else:
    # print(x.mean(), x.std())
    return np.hstack((meandev_norm(x), symmetric_log([x.std(), x.mean()])))

def get_sample():
  setidx = np.random.randint(0, len(close_dataset))

  offset = np.random.randint(100, len(close_dataset[setidx])-(ctx+100))
  # offset = 0
  # ediff bounds are 52.84, -43.13; total variance ~100
  close_data = close_dataset[setidx][offset:offset+ctx+1]
  close_data = transform_data(np.ediff1d(close_data), maxv=64)
  volume_data = volume_dataset[setidx][offset:offset+ctx+1]
  volume_data = transform_data(np.ediff1d(volume_data), maxv=64)
  # print(close_data)
  return torch.from_numpy(np.vstack((close_data, volume_data)))
  # return torch.from_numpy(close_data)

def get_batch():
  batch = torch.zeros((batch_size, ich, ctx + mtx))
  for i in range(batch_size):
    batch[i] = get_sample()
  return batch, batch

last_time = time.time()
for epoch in range(1, epochs):  # loop over the dataset multiple times
    running_loss = 0.0

    # get the inputs; data is a list of [inputs, labels]
    inputs, _ = get_batch()
    # print(inputs.max(), inputs.min())

    minputs = inputs.cuda()

    # zero the parameter gradients
    optimizer.zero_grad()

    outputs = net.forward(minputs)
    print(outputs)

    # print(outputs.min(), outputs.max(), inputs.min(), inputs.max())

    # First [:ctx] is the timeseries normalized, [ctx:] is the magnitude and things like that
    loss = criterion(outputs[:, :, :ctx], minputs[:, :, :ctx]) + criterion(outputs[:, :, ctx:], minputs[:, :, ctx:]) * 5  #+ criterion(torch.cumsum(outputs, dim=2), torch.cumsum(minputs, dim=2)) * 0.5
    loss.backward()
    optimizer.step()

    # print statistics
    running_loss += loss.item()
    if epoch % 10 == 0:
        print('[epoch %d, %5d, lr %.5f, %.5fe/s] loss: %.3f' %
              (epoch + 1, (epoch +1) * batch_size,  optimizer.param_groups[0]["lr"], 10 / (time.time() - last_time), running_loss))
        last_time = time.time()

        writer.add_scalar('loss', running_loss, epoch)

        output_ex = outputs.cpu().detach().numpy()
        inputs_ex = inputs.cpu().detach().numpy()
        # print(inputs_ex.shape)
        ichart = np.cumsum(transform_data(inputs_ex[0], reverse=True, maxv=64), axis=-1)[0]
        ochart = np.cumsum(transform_data(output_ex[0], reverse=True, maxv=64), axis=-1)[0]
        writer.add_scalar('mse_error', np.mean((ochart - ichart) ** 2), epoch)

        scheduler.step(running_loss)
        running_loss = 0.0

        if False:
            print('Pruning...')
            pruned_count = 0
            for name, module in net.named_modules():
                pruned = 0
                if isinstance(module, torch.nn.Conv1d):
                    prune.l1_unstructured(module, name='weight', amount=0.3)
                    pruned = float(torch.sum(module.weight == 0))
                    print('Module', name, "Sparsity:", pruned / float(module.weight.nelement()))
                    # prune.ln_structured(module, name="weight", amount=0.5, n=2, dim=0)
                elif isinstance(module, torch.nn.Linear):
                    prune.l1_unstructured(module, name='weight', amount=0.4)
                    pruned = float(torch.sum(module.weight == 0))
                    print('Module', name, "Sparsity:", pruned  / float(module.weight.nelement()))
                pruned_count += pruned
            print('Pruned: ', pruned_count)
            print('Pruned%: ', pruned_count / NPARAMS)

        if epoch % 1000 == 0:
            xplt = plt.figure()
            print(output_ex[0][0])
            plt.plot(np.cumsum(transform_data(inputs_ex[0], reverse=True, maxv=64), axis=-1)[0], figure=xplt)
            plt.plot(np.cumsum(transform_data(output_ex[0], reverse=True, maxv=64), axis=-1)[0], figure=xplt, linestyle='--')

            writer.add_figure('orig_vs_reprod', xplt, epoch)
            writer.add_histogram('hist_original', inputs[0], epoch)
            writer.add_histogram('hist_reprod', output_ex[0], epoch)

pruned_count = 0
for name, module in net.named_modules():
    pruned = 0
    if isinstance(module, torch.nn.Conv1d):
        pruned = float(torch.sum(module.weight == 0))
        print('Module', name, "Sparsity:", pruned / float(module.weight.nelement()))
        prune.remove(module, 'weight')
        # prune.ln_structured(module, name="weight", amount=0.5, n=2, dim=0)
    elif isinstance(module, torch.nn.Linear):
        pruned = float(torch.sum(module.weight == 0))
        print('Module', name, "Sparsity:", pruned  / float(module.weight.nelement()))
        prune.remove(module, 'weight')
    pruned_count += pruned
    
print('Pruned: ', pruned_count)
print('Pruned%: ', pruned_count / NPARAMS)

print('Finished Training')
torch.save(net, 'emb-w2.pt')