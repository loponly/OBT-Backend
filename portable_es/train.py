# Very fast 220 steps, 3s (190 points)

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
from evosim.models.es_large import EvolutionStrategies

np.seterr(all='raise')

market = 'LTC:USDT'
marketid = market.replace(':', '')
api = ApiAdapter(binance_map, '%s_4h' % marketid) 
env = SimulMetrics(api)

steps = 4000

nenv = SimuGym(env, max_steps=steps)
torch.manual_seed(2); np.random.seed(2)

target = 300 # Realistic reward converage value
model = EvolutionStrategies(inputs=45, outputs=2, target=target)
state = nenv.reset()
episodes = 30000

ave_reward = deque(maxlen=model.population_size)
ave_gain = deque(maxlen=model.population_size)

# base: exp-5-8 (base + patches + no-adaptive + no-exploration)
# base: exp-9 base
# base: exp-10 base + exp-decay
# base: exp-11 medium + exp-decay
# base: exp-12 medium + exp-decay + ???
# base: exp-13 medium + exp-decay + sortino
# base: exp-14 medium + exp-decay + sortino + long-period + no-penalties
writer = SummaryWriter('runs/exp-14')

def get_flat_parameters(model):
	with torch.no_grad():
		params = torch.FloatTensor([])
		for x in model.master_weights:
			params = torch.cat((torch.flatten(x), params))
		params = params.detach().numpy()
	return params

embedding_history = []
evals_per_episode = 1
episode_seed = np.random.RandomState(np.random.randint(0, 2 ** 31)).get_state()


def decay(eps=0.999):
	model.sigma *= eps
	model.learning_rate *= eps

def current_RandomState():
	rstate = np.random.RandomState(0)
	rstate.set_state(episode_seed)
	return rstate

def update_RandomState():
	global episode_seed
	episode_seed = np.random.RandomState(seed=current_RandomState().randint(0, 2 ** 31)).get_state()

for episode in range(episodes):
	episode_reward = 0
	nenv.randomize(current_RandomState())
	state = nenv.reset()

	for evals, has_next in lookahead(range(evals_per_episode)):
		while True:
			action = model.forward(torch.FloatTensor(state))
			state, reward, done = nenv.step(action.detach().numpy())
			episode_reward += reward

			if done:
				ave_gain.append(nenv.fraction_gain())
				
				nenv.randomize()
				nenv.soft_reset()
				# print(nenv.start, done, has_next)

				if not has_next:
					ave_reward.append(episode_reward)

					# nenv.render()
					evolved = model.log_reward(episode_reward / evals_per_episode, nenv.should_explore())
					if evolved:
						print(episode, 'Gen %d, Average reward: %f %.3f (lr=%f, o=%f); b: %d (%.3f), s: %d (%.3f) f: %d (%.3f)' % (episode // model.population_size, np.mean(ave_reward), 1+ np.mean(ave_gain), model.learning_rate, model.sigma, len(nenv.emetrics['buys']), np.mean(nenv.emetrics['buys']), len(nenv.emetrics['sells']), np.mean(nenv.emetrics['sells']), len(nenv.emetrics['fails']), np.sum(nenv.emetrics['fails'])))

						gen = episode // model.population_size
						writer.add_scalar('avg_gain', np.mean(ave_gain) + 1, gen)
						writer.add_scalar('avg_reward', np.mean(ave_reward), gen)
						writer.add_scalar('lr', model.learning_rate, gen)
						writer.add_scalar('sigma', model.sigma, gen)
						if gen % 3 == 0:
							embedding_history.append(get_flat_parameters(model))

						decay()
						update_RandomState()
						ave_reward = deque(maxlen=model.population_size)
						ave_gain = deque(maxlen=model.population_size)
				break
		
	# if np.mean(ave_reward) >= target:
	# 	print('Completed at episode: ', episode)
	# 	break

writer.add_embedding(
		np.array(embedding_history),
		global_step=gen)
torch.save(model, 'es-b.pt')