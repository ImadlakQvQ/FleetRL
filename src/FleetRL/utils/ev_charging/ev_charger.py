import numpy as np
import pandas as pd

from FleetRL.fleet_env.config.ev_config import EvConfig
from FleetRL.fleet_env.config.score_config import ScoreConfig
from FleetRL.fleet_env.config.time_config import TimeConfig
from FleetRL.fleet_env.episode import Episode
from FleetRL.utils.load_calculation.load_calculation import LoadCalculation


class EvCharger:

    def __init__(self):
        # Price analysis: https://www.bdew.de/media/documents/230215_BDEW-Strompreisanalyse_Februar_2023_15.02.2023.pdf
        # Average spot price for 2020 was ~3.2 ct/kWh and ~4 ct/kWh if looking at peak times only
        # --> 50% are fees --> spot price is multiplied by factor of 1.5 and offset by +1
        # this accounts for fees even when prices are negative or zero, but also scales with price levels
        self.spot_multiplier = 1.5  # no unit
        self.spot_offset = 0.01  # €/kWh

        # If energy is injected to the grid, it can be treated like solar feed-in from households
        # https://echtsolar.de/einspeiseverguetung/#t-1677761733663
        # Fees for handling of 25% are assumed
        self.grid_injection_tariff = 0.07  # €/kWh for Jan 2023, average from 10kW, 40kW and 100 kW
        self.handling_fees = 0.25  # 25%

    def charge(self, db: pd.DataFrame, num_cars: int, actions, episode: Episode,
               load_calculation: LoadCalculation,
               ev_conf: EvConfig, time_conf: TimeConfig, score_conf: ScoreConfig, print_updates: bool, target_soc: float):

        """
        :param db: The schedule database of the EVs
        :param spot_price: Spot price information
        :param num_cars: Number of cars in the model
        :param actions: Actions taken by the agent
        :param episode: Episode object with its parameters and functions
        :param load_calculation: Load calc object with its parameters and functions
        :param soh: list that specifies the battery degradation of each vehicle
        :param ev_conf: Config of the EVs
        :param time_conf: Time configuration
        :param score_conf: Score and penalty configuration
        :param print_updates:
        :param target_soc:
        :return: soc, next soc, the reward and the monetary value (cashflow)
        """

        # reset next_soc, cost and revenue
        episode.next_soc = []
        episode.charging_cost = 0
        episode.discharging_revenue = 0
        episode.total_charging_energy = 0

        invalid_action_penalty = 0
        overcharging_penalty = 0

        charge_log = np.ndarray(0)
        charging_energy = 0.0
        discharging_energy = 0.0

        # go through the cars and calculate the actual deliverable power based on action and constraints
        for car in range(num_cars):

            # max possible power in kW depends on the onboard charger equipment and the charging station
            possible_power = min([ev_conf.obc_max_power, load_calculation.evse_max_power])
            # car is charging
            if actions[car] >= 0:
                # the charging energy depends on the maximum chargeable energy and the desired charging amount
                # SoH is accounted for in this equation as well
                ev_total_energy_demand = (target_soc - episode.soc[car] * episode.soh[car]) * ev_conf.battery_cap  # total energy demand in kWh
                demanded_charge = possible_power * actions[car] * time_conf.dt  # demanded energy in kWh by the agent

                # if the agent wants to charge more than the battery can hold
                if demanded_charge * ev_conf.charging_eff > ev_total_energy_demand:
                    current_oc_pen = score_conf.penalty_overcharging * (demanded_charge - ev_total_energy_demand) ** 2
                    overcharging_penalty += current_oc_pen
                    if print_updates:
                        print(f"Overcharged, penalty of: {current_oc_pen}")

                # if the car is there, allocate charging energy to the battery in kWh
                if db.loc[(db["ID"] == car) & (db["date"] == episode.time), "There"].values == 1:
                    charging_energy = min(ev_total_energy_demand / ev_conf.charging_eff, demanded_charge)

                # the car is not there, no charging
                else:
                    charging_energy = 0
                    # if agent gives an action even if no car is there, give a small penalty
                    if np.abs(actions[car]) > 0.05:
                        current_inv_pen = score_conf.penalty_invalid_action * (actions[car] ** 2)
                        invalid_action_penalty += current_inv_pen
                        if print_updates:
                            print(f"Invalid action, penalty given: {round(current_inv_pen, 3)}.")

                # next soc is calculated based on charging energy
                # TODO: not all cars must have the same battery cap
                episode.next_soc.append(episode.soc[car] * episode.soh[car]
                                        + charging_energy * ev_conf.charging_eff / ev_conf.battery_cap)

                # get pv energy and subtract from charging energy needed from the grid
                # assuming pv is equally distributed to the connected cars
                # try except because pv is sometimes deactivated
                try:
                    current_pv_energy = (db.loc[db["date"] == episode.time, "pv"].values[0]) * time_conf.dt  # in kWh
                except KeyError:
                    current_pv_energy = 0.0  # kWh
                connected_cars = db.loc[(db["date"] == episode.time), "There"].sum()
                # for the case that no car is connected, to avoid division by 0
                connected_cars = max(connected_cars, 1)
                # energy drawn from grid after deducting pv self-consumption
                grid_energy_demand = max(0, charging_energy - (current_pv_energy / connected_cars))  # kWh

                # get current spot price, div by 1000 to go from €/MWh to €/kWh
                current_spot = (db.loc[db["date"] == episode.time, "DELU"].values[0]) / 1000.0

                # calculate charging cost for this ev and add it to the total charging cost of the step
                episode.charging_cost += (grid_energy_demand * (current_spot + self.spot_offset) * self.spot_multiplier)

                # save the total charging energy in a variable
                episode.total_charging_energy += charging_energy

            # car is discharging - v2g is currently modelled as energy arbitrage on the day ahead spot market
            elif actions[car] < 0:
                # check how much energy is left in the battery and how much discharge is desired
                ev_total_energy_left = -1 * episode.soc[car] * episode.soh[car] * ev_conf.battery_cap  # amount of energy left in the battery in kWh
                demanded_discharge = possible_power * actions[car] * time_conf.dt  # demanded discharge in kWh by agent

                # variable to check if car is plugged in
                there = db.loc[(db["ID"]==car) & (db["date"]==episode.time), "There"].values[0]

                # energy drawdown from battery bigger than what is left in the battery
                if (demanded_discharge * ev_conf.discharging_eff < ev_total_energy_left) and (there != 0):
                    current_oc_pen = score_conf.penalty_overcharging * (ev_total_energy_left - demanded_discharge) ** 2
                    overcharging_penalty += current_oc_pen
                    if print_updates:
                        print(f"Overcharged, penalty of: {round(current_oc_pen,3)}")

                # if the car is there get the actual discharging energy
                if there == 1:
                    discharging_energy = max(ev_total_energy_left / ev_conf.discharging_eff, demanded_discharge)  # max because values are negative, kWh

                # car is not there, discharging energy is 0
                else:
                    discharging_energy = 0
                    # if discharge command is sent even if no car is there
                    if np.abs(actions[car]) > 0.05:
                        current_inv_pen = score_conf.penalty_invalid_action * (actions[car] ** 2)
                        invalid_action_penalty += current_inv_pen
                        if print_updates:
                            print(f"Invalid action, penalty given: {round(current_inv_pen, 3)}.")

                # calculate next soc, which will decrease, efficiency is taken into account below
                episode.next_soc.append(episode.soc[car] * episode.soh[car]
                                        + discharging_energy / ev_conf.battery_cap)

                # Discharged energy renumerated at PV feed-in minus 30%
                episode.discharging_revenue += (-1 * discharging_energy
                                                * ev_conf.discharging_eff
                                                * self.grid_injection_tariff
                                                * (1-self.handling_fees))  # €

                # print(f"discharging revenue: {discharging_revenue.values[0]}")

                # save the total charging energy in a self variable
                episode.total_charging_energy += discharging_energy

            else:
                raise TypeError("The parsed action value was not recognised")

            # append total charging energy of the car to the charge log, used in post processing
            charge_log = np.append(charge_log, charging_energy + discharging_energy)

        # calculate net cashflow based on cost and revenue
        cashflow = -1 * episode.charging_cost + episode.discharging_revenue

        # reward is a function of cashflow and penalties
        reward = (score_conf.price_multiplier * cashflow) + invalid_action_penalty + overcharging_penalty

        # return soc, next soc and the value of reward (remove the index)
        return episode.soc, episode.next_soc, float(reward), float(cashflow), charge_log
