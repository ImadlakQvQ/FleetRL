import math
from copy import copy

from fleetrl.fleet_env.fleet_environment import FleetEnv
from fleetrl.benchmarking.benchmark import Benchmark

from stable_baselines3.common.vec_env import SubprocVecEnv, VecNormalize
from stable_baselines3.common.env_util import make_vec_env

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt


class DistributedCharging(Benchmark):

    def __init__(self,
                 n_steps: int,
                 n_evs: int,
                 n_episodes: int = 1,
                 n_envs: int = 1,
                 time_steps_per_hour: int = 4):

        self.n_steps = n_steps
        self.n_evs = n_evs
        self.n_episodes = n_episodes
        self.n_envs = n_envs
        self.time_steps_per_hour = time_steps_per_hour
        self.env_config = None

    def run_benchmark(self,
                      use_case: str,
                      env_kwargs: dict,
                      seed: int = None) -> pd.DataFrame:

        dist_vec_env = make_vec_env(FleetEnv,
                                    n_envs=self.n_envs,
                                    vec_env_cls=SubprocVecEnv,
                                    env_kwargs=env_kwargs,
                                    seed=seed)

        dist_norm_vec_env = VecNormalize(venv=dist_vec_env,
                                         norm_obs=True,
                                         norm_reward=True,
                                         training=True,
                                         clip_reward=10.0)

        dist_norm_vec_env.reset()

        for i in range(self.n_steps * self.time_steps_per_hour * self.n_episodes):
            if dist_norm_vec_env.env_method("is_done")[0]:
                dist_norm_vec_env.reset()
            dist_norm_vec_env.step(
                ([np.clip(np.multiply(np.ones(self.n_evs), dist_norm_vec_env.env_method("get_dist_factor")[0]), 0, 1)]))

        dist_log: pd.DataFrame = dist_norm_vec_env.env_method("get_log")[0]

        dist_log.reset_index(drop=True, inplace=True)
        dist_log = dist_log.iloc[0:-2]

        self.env_config = env_kwargs["env_config"]

        return dist_log

    def plot_benchmark(self,
                       dist_log: pd.DataFrame,
                       ) -> None:

        dist_log["hour_id"] = (dist_log["Time"].dt.hour + dist_log["Time"].dt.minute / 60)

        mean_per_hid_dist = dist_log.groupby("hour_id").mean()["Charging energy"].reset_index(drop=True)
        mean_all_dist = []
        for i in range(mean_per_hid_dist.__len__()):
            mean_all_dist.append(np.mean(mean_per_hid_dist[i]))

        mean_dist = pd.DataFrame()
        mean_dist["Distributed charging"] = np.multiply(mean_all_dist, 4)

        mean_dist.plot()

        plt.xticks([0, 8, 16, 24, 32, 40, 48, 56, 64, 72, 80, 88]
                   , ["00:00", "02:00", "04:00", "06:00", "08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00",
                      "22:00"],
                   rotation=45)

        plt.legend()
        plt.grid(alpha=0.2)

        plt.ylabel("Charging power in kW")
        price_lookahead = self.env_config["price_lookahead"] * int(self.env_config["include_price"])
        bl_pv_lookahead = self.env_config["bl_pv_lookahead"]
        number_of_lookaheads = sum([int(self.env_config["include_pv"]), int(self.env_config["include_building"])])
        # check observer module for building of observation list
        power_index = self.n_evs * 6 + 2 * (price_lookahead+1) + number_of_lookaheads * (bl_pv_lookahead+1) + 1
        max_val = dist_log.loc[0, "Observation"][power_index]
        plt.ylim([-max_val * 1.2, max_val * 1.2])
        plt.show()
