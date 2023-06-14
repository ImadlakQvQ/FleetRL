import numpy as np
import pandas as pd

from FleetRL.utils.observation.observer import Observer


class ObserverWithBuildingLoad(Observer):
    def get_obs(self, db: pd.DataFrame, price_lookahead: int, bl_pv_lookahead:int, time: pd.Timestamp) -> list:

        soc = db.loc[(db['date'] == time), 'SOC_on_return'].values
        hours_left = db.loc[(db['date'] == time), 'time_left'].values

        price_start = np.where(db["date"] == time)[0][0]
        price_end = np.where(db["date"] == (time + np.timedelta64(price_lookahead, 'h')))[0][0]
        price = db["DELU"][price_start: price_end].values

        bl_start = np.where(db["date"] == time)[0][0]
        bl_end = np.where(db["date"] == (time + np.timedelta64(bl_pv_lookahead, 'h')))[0][0]
        building_load = db["load"][bl_start: bl_end].values

        return [soc, hours_left, price, building_load]

    @staticmethod
    def get_trip_len(db: pd.DataFrame, car: int, time: pd.Timestamp) -> float:
        """
        :param db: from the env
        :param car: car ID
        :param time: current timestamp
        :return: length of trip in hours as a float
        """

        trip_len = db.loc[(db["ID"] == car) & (db["date"] == time), "last_trip_total_length_hours"].values

        return trip_len