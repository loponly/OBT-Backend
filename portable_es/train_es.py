import gym
import numpy as np
import torch
import torch.nn.functional
from collections import deque
from tensorboardX import SummaryWriter
from tradeEnv.metrics import SimulMetrics
from tradeEnv.gym import SimuGym
from tradeEnv.maths import ewma_vectorized_safe
from tradeEnv.api_adapter import ApiAdapter, binance_map
from tradeEnv.utils	import lookahead
from evosim.models.estool_wrap import ESWrap
from evosim.models.es import OpenES, SimpleGA, CMAES, PEPG
from evosim.models.seqclass import Network, FilterNetwork

np.seterr(all='raise')

# , 'BTC:USDT'
nenv = SimuGym(markets=['LTC:USDT'], candleSizes=['4h'], max_steps=100)
torch.manual_seed(2); np.random.seed(2)

auxp = False # Enable reccurent vector
model = ESWrap(FilterNetwork(130, 3, 50, aux=auxp)).cuda()
estool = OpenES(model.NPARAMS,sigma_init=0.3)
model.set_estool(estool)

ave_reward = deque(maxlen=model.population_size)
ave_gain = deque(maxlen=model.population_size)

# cma-es-1: pepg (i know) + 2-class model + sortino
# pepg-2: 2-class model + profit v + penalty
# pepg-3: 3-class model + profit v + penalty
# pepg-4: 3-class model + profit v + penalty + 72 features
# pepg-5: 2-class model + profit v + penalty + 72 features + 2x episodes
# pepg-6: 2-class model + profit v + penalty + 72 features + 2x episodes + Multi-Market
writer = SummaryWriter('runs/pepg-6')

episodes = 60000
evals_per_episode = 2
state = nenv.reset()
episode_seed = np.random.RandomState(np.random.randint(0, 2 ** 31)).get_state()

def current_RandomState():
	rstate = np.random.RandomState(0)
	rstate.set_state(episode_seed)
	return rstate

def update_RandomState():
	global episode_seed
	episode_seed = np.random.RandomState(seed=current_RandomState().randint(0, 2 ** 31)).get_state()


import atexit

def exit_handler():
	writer.add_embedding(
		np.array(embedding_history),
		global_step=gen)
	torch.save(model, 'es-c.pt')

atexit.register(exit_handler)


embedding_history = []
for episode in range(episodes):
	episode_reward = 0
	nenv.randomize(current_RandomState())
	state = nenv.reset()

	for evals, has_next in lookahead(range(evals_per_episode)):
		if auxp:
			aux = torch.zeros((auxp,))
		while True:
			if auxp:
				action, aux = model.forward(state, aux)
			else:
				action = model.forward(state)

			action = action.cpu().detach().numpy()
			# print(action)
			state, reward, done = nenv.step(action)
			episode_reward += reward

			if done:
				ave_gain.append(nenv.fraction_gain())
				
				nenv.randomize()
				nenv.soft_reset()
				# print(nenv.start, done, has_next, evals)

				if not has_next:
					ave_reward.append(episode_reward)

					# nenv.render()
					evolved = model.log_reward(episode_reward / evals_per_episode, nenv.should_explore())
					if evolved:
						np.seterr(all='ignore')
						print(episode, 'Gen %d, Average reward: %f %.3f (lr=%f, o=%f); b: %d (%.3f), s: %d (%.3f) f: %d (%.3f)' % (episode // model.population_size, np.mean(ave_reward), 1+ np.mean(ave_gain), np.mean(model.learning_rate), np.mean(model.sigma), len(nenv.emetrics['buys']), np.mean(nenv.emetrics['buys']), len(nenv.emetrics['sells']), np.mean(nenv.emetrics['sells']), len(nenv.emetrics['fails']), np.sum(nenv.emetrics['fails'])))
						np.seterr(all='raise')

						gen = episode // model.population_size
						writer.add_scalar('avg_gain', np.mean(ave_gain) + 1, gen)
						writer.add_scalar('avg_reward', np.mean(ave_reward), gen)
						writer.add_scalar('lr', np.mean(model.learning_rate), gen)
						writer.add_scalar('sigma', np.mean(model.sigma), gen)
						if gen % 3 == 0:
							embedding_history.append(np.mean(model.solutions, axis=0))

						update_RandomState()
						ave_reward = deque(maxlen=model.population_size)
						ave_gain = deque(maxlen=model.population_size)
				break
		
	# if np.mean(ave_reward) >= target:
	# 	print('Completed at episode: ', episode)
	# 	break

