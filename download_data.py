import argparse
import os
from datetime import date, datetime, timedelta
import pandas as pd
from octopus_energy import Aggregate, OctopusEnergyRestClient
import asyncio


def get_solar_edge_energy(site_id, api_key, start_date, end_date, time_unit="DAY"):
    """Call the SolarEdge API and return the daily energy usage as a Pandas dataframe"""
    url = f"https://monitoringapi.solaredge.com/site/{site_id}/energy.csv?timeUnit={time_unit}&startDate={start_date}&endDate={end_date}&api_key={api_key}"
    df = pd.read_csv(url)
    return df


async def _get_octopus_energy_consumption_async(octopus_api_token, octopus_mprn, octopus_serial_number):
    """Call the Octopus Energy API and get the daily energy consumption as a Pandas dataframe"""
    async with OctopusEnergyRestClient(octopus_api_token) as client:
        # TODO: fix so page size doesn't need to keep growing over time!
        consumption = await client.get_electricity_consumption_v1(octopus_mprn, octopus_serial_number, group_by=Aggregate.DAY, page_size=1000)
        df = pd.DataFrame.from_records(consumption["results"])
        return df

def get_octopus_energy_consumption(octopus_api_token, octopus_mprn, octopus_serial_number):
    loop = asyncio.new_event_loop()
    df = loop.run_until_complete(_get_octopus_energy_consumption_async(octopus_api_token, octopus_mprn, octopus_serial_number))
    loop.close()
    return df

def get_historical_import_readings():
    """Get the daily energy import from a historical spreadsheet as a Pandas dataframe"""
    df = pd.read_csv("data/Berllan electricity - Sheet1.csv")
    df = df[["Date", "Reading"]]
    df["Date"] = pd.to_datetime(df["Date"], format="%d/%m/%Y").dt.date
    df = df[df["Date"].notnull()]

    # interpolate readings for missing days
    df = df.set_index("Date")["Reading"].interpolate(method="linear")
    df = df.reset_index()

    # calculate (approximate) daily usage
    df["import"] = df["Reading"] - df.shift(1)["Reading"]

    df = df.rename(columns={"Date": "date"})
    df = df[["date", "import"]]
    return df

def get_ashp_readings():
    """Get the daily ASHP readings from a spreadsheet as a Pandas dataframe"""
    df = pd.read_csv("data/Berllan electricity - ASHP.csv")
    df = df[["Date", "ASHP reading"]]
    df["Date"] = pd.to_datetime(df["Date"], format="%d/%m/%Y").dt.date
    df = df[df["Date"].notnull()]

    # interpolate readings for missing days
    df = df.set_index("Date")["ASHP reading"].interpolate(method="linear")
    df = df.reset_index()

    # calculate (approximate) daily usage
    df["ASHP"] = df["ASHP reading"] - df.shift(1)["ASHP reading"]

    df = df.rename(columns={"Date": "date"})
    df = df[["date", "ASHP"]]
    return df

if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="Run using local data (don't download)")
    args = parser.parse_args()

    # SolarEdge

    if not args.local:
        # Fail fast if any of these env variables are not defined
        solar_edge_site_id = os.environ["SOLAR_EDGE_SITE_ID"]
        solar_edge_api_key = os.environ["SOLAR_EDGE_API_KEY"]

        # SolarEdge can only download one year at a time at daily resolution, so do in batches
        solar_dfs = []
        for year in range(2020, 2022):
            solar_df = get_solar_edge_energy(
                solar_edge_site_id, solar_edge_api_key, f"{year}-01-01", f"{year}-12-31"
            )
            solar_dfs.append(solar_df)

        yesterday = datetime.now() - timedelta(1)
        start_date = datetime.strftime(yesterday, "%Y-01-01")
        end_date = datetime.strftime(yesterday, "%Y-%m-%d")
        solar_df = get_solar_edge_energy(
            solar_edge_site_id, solar_edge_api_key, start_date, end_date
        )
        solar_dfs.append(solar_df)

        solar_df = pd.concat(solar_dfs)

        solar_df.to_pickle("./solar.pkl")

    solar_df = pd.read_pickle("./solar.pkl")

    solar_df = solar_df.rename(columns={"value": "solar"})
    solar_df["solar"] = solar_df["solar"] / 1000 # convert Wh to kWh (units)
    solar_df["date"] = pd.to_datetime(solar_df["date"]).dt.date

    # Octopus

    if not args.local:
        # Fail fast if any of these env variables are not defined
        octopus_api_token = os.environ["OCTOPUS_API_TOKEN"]
        octopus_mprn = os.environ["OCTOPUS_MPRN"]
        octopus_export_mprn = os.environ["OCTOPUS_EXPORT_MPRN"]
        octopus_serial_number = os.environ["OCTOPUS_SERIAL_NUMBER"]

        electricity_df = get_octopus_energy_consumption(octopus_api_token, octopus_mprn, octopus_serial_number)
        electricity_df.to_pickle("./electricity.pkl")

        electricity_export_df = get_octopus_energy_consumption(octopus_api_token, octopus_export_mprn, octopus_serial_number)
        electricity_export_df.to_pickle("./electricity_export.pkl")

    electricity_df = pd.read_pickle("./electricity.pkl")

    electricity_export_df = pd.read_pickle("./electricity_export.pkl")

    electricity_df["date"] = pd.to_datetime(electricity_df["interval_start"], utc=True).dt.date
    electricity_df = electricity_df.rename(columns={"consumption": "import"})
    electricity_df = electricity_df[["date", "import"]]

    electricity_export_df["date"] = pd.to_datetime(electricity_export_df["interval_start"], utc=True).dt.date
    electricity_export_df = electricity_export_df.rename(columns={"consumption": "export"})
    electricity_export_df = electricity_export_df[["date", "export"]]

    # Historical data (manually entered)

    historical_df = get_historical_import_readings()

    ashp_df = get_ashp_readings()

    df = pd.merge(solar_df, electricity_df, how="outer", on=["date"], right_index=False, left_index=False)
    df = pd.merge(df, electricity_export_df, how="outer", on=["date"], right_index=False, left_index=False)

    df = pd.merge(df, historical_df, how="outer", on=["date"], right_index=False, left_index=False, suffixes=(None, "_y"))
    df["import"] = df["import"].fillna(df["import_y"])
    df = df[["date", "solar", "import", "export"]]

    df = pd.merge(df, ashp_df, how="outer", on=["date"], right_index=False, left_index=False)

    # Start in 2021
    start_date = date(2021, 1, 1)
    df = df[(df["date"] >= start_date)]

    df.to_csv(f"docs/electricity.csv", index=False)
