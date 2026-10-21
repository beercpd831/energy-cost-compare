"""Gas comparison: what the heat from the heat pump would have cost with a gas boiler."""

from __future__ import annotations

import calendar
from datetime import date, datetime, tzinfo

from .calc import Interval
from .cost import DayCost
from .tariffs import FIXED, Contract, TariffSet

# Upper heating value of Dutch natural gas (35.17 MJ/m3).
KWH_PER_M3 = 9.769


def price_heat(
    intervals: list[Interval],
    contract: Contract,
    tariffs: TariffSet,
    tz: tzinfo,
    efficiency: float,
) -> tuple[list[DayCost], list[DayCost]]:
    """Per day: (gas boiler cost, heat pump electricity cost), both incl. VAT.

    Only fixed contracts have a gas price here; dynamic gas follows a market price that
    isn't recorded. The heat pump's electricity is priced at the contract's supply price
    plus energy tax, which is what one more kWh costs. The gas side includes the fixed
    costs of a gas connection (supplier and grid operator), since going all-electric
    removes them.
    """
    if contract.kind != FIXED:
        return [], []
    heat: dict[date, float] = {}
    elec: dict[date, float] = {}
    for interval in intervals:
        day = datetime.fromtimestamp(interval.start, tz).date()
        heat[day] = heat.get(day, 0.0) + interval.get("heat")
        elec[day] = elec.get(day, 0.0) + interval.get("hp_elec")

    gas_days: list[DayCost] = []
    hp_days: list[DayCost] = []
    for day in sorted(heat):
        rates = contract.electricity(day)
        gas = contract.gas(day)
        if rates is None or not gas:
            continue
        tax = tariffs.taxes.at(day)
        grid = tariffs.grid.at(day)
        vat = 1 + float(tax.get("vat", 0.21))
        per_day = 12 / (366 if calendar.isleap(day.year) else 365)
        m3 = heat[day] / (efficiency * KWH_PER_M3)
        gas_cost = (
            m3 * (float(gas.get("supply", 0.0)) + float(tax.get("gas_tax", 0.0)))
            + float(gas.get("fixed_per_month", 0.0)) * per_day
            + float(grid.get("gas_per_month", 0.0)) * per_day
        ) * vat
        hp_cost = elec[day] * (rates.supply + float(tax.get("electricity_tax", 0.0))) * vat
        gas_days.append(DayCost(day, gas_cost, {"m3": m3, "heat_kwh": heat[day]}))
        hp_days.append(DayCost(day, hp_cost, {"kwh": elec[day]}))
    return gas_days, hp_days
