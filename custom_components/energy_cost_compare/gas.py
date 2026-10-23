"""Gas comparison: what the heat from the heat pump would have cost with a gas boiler."""

from __future__ import annotations

import calendar
from datetime import date, datetime, tzinfo

from .calc import Interval
from .cost import DayCost, estimate_missing_prices
from .tariffs import FIXED, Contract, TariffSet

# Upper heating value of Dutch natural gas (35.17 MJ/m3).
KWH_PER_M3 = 9.769


def price_heat(
    intervals: list[Interval],
    contract: Contract,
    tariffs: TariffSet,
    tz: tzinfo,
    efficiency: float,
    gas_market: dict[date, float] | None = None,
) -> tuple[list[DayCost], list[DayCost]]:
    """Per day: (gas boiler cost, heat pump electricity cost), both incl. VAT.

    Fixed contracts use their gas supply price. Dynamic contracts pay the day's gas
    market price (excl. VAT, per local date) plus their markup, so they need
    `gas_market`; days before the first known market price are left out. The heat
    pump's electricity is priced at what one more kWh costs: the contract's supply
    price (for dynamic contracts the quarter's market price plus markup) plus energy
    tax. The gas side includes the fixed costs of a gas connection (supplier and grid
    operator), since going all-electric removes them.
    """
    fixed = contract.kind == FIXED
    if not fixed and not gas_market:
        return [], []
    estimates = {} if fixed else estimate_missing_prices(intervals)
    heat: dict[date, float] = {}
    elec: dict[date, float] = {}
    # Dynamic: the heat pump's kWh times the quarter's market price (excl. VAT).
    elec_market: dict[date, float] = {}
    for interval in intervals:
        day = datetime.fromtimestamp(interval.start, tz).date()
        heat[day] = heat.get(day, 0.0) + interval.get("heat")
        kwh = interval.get("hp_elec")
        elec[day] = elec.get(day, 0.0) + kwh
        if not fixed:
            epex = interval.values.get("epex")
            if epex is None:
                epex = estimates.get(interval.start, 0.0)
            elec_market[day] = elec_market.get(day, 0.0) + kwh * epex

    market_days = sorted(gas_market or {})
    gas_days: list[DayCost] = []
    hp_days: list[DayCost] = []
    for day in sorted(heat):
        rates = contract.electricity(day)
        gas = contract.gas(day)
        if rates is None or not gas:
            continue
        if fixed:
            gas_supply = float(gas.get("supply", 0.0))
            elec_supply = elec[day] * rates.supply
        else:
            # The gas day runs from 06:00; a missing day takes the last known price.
            known = [d for d in market_days if d <= day]
            if not known:
                continue
            gas_supply = gas_market[known[-1]] + float(gas.get("markup", 0.0))
            elec_supply = elec_market[day] + elec[day] * rates.markup
        tax = tariffs.taxes.at(day)
        grid = tariffs.grid.at(day)
        vat = 1 + float(tax.get("vat", 0.21))
        per_day = 12 / (366 if calendar.isleap(day.year) else 365)
        m3 = heat[day] / (efficiency * KWH_PER_M3)
        variable = m3 * (gas_supply + float(tax.get("gas_tax", 0.0))) * vat
        fixed_costs = (
            float(gas.get("fixed_per_month", 0.0)) * per_day
            + float(grid.get("gas_per_month", 0.0)) * per_day
        ) * vat
        hp_cost = (elec_supply + elec[day] * float(tax.get("electricity_tax", 0.0))) * vat
        gas_days.append(DayCost(day, variable + fixed_costs, {
            "m3": m3, "heat_kwh": heat[day], "variable": variable, "fixed": fixed_costs,
        }))
        hp_days.append(DayCost(day, hp_cost, {"kwh": elec[day]}))
    return gas_days, hp_days
