from FleetRL.env.fleet_environment import FleetEnv
from FleetRL.utils.prices import load_prices

env = FleetEnv()
env.reset()
for i in range(14):
    print(env.step([1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]))
    print("\n")


load_prices(env)


