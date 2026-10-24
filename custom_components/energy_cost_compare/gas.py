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


# Heating degree days use this base temperature (°C).
DEGREE_DAY_BASE = 18.0
# Normal monthly mean temperature at De Bilt, 1991-2020 (KNMI).
NORMAL_MONTH_TEMP = (3.6, 4.0, 6.6, 9.8, 13.4, 16.2, 18.3, 17.9, 14.7, 10.9, 7.0, 4.2)
MIN_ESTIMATE_DAYS = 7


def degree_days(temp: float) -> float:
    return max(0.0, DEGREE_DAY_BASE - temp)


def normal_year_degree_days() -> float:
    """Degree days in a normal year, from the monthly normals (2001: not a leap year)."""
    return sum(
        calendar.monthrange(2001, month)[1] * degree_days(temp)
        for month, temp in enumerate(NORMAL_MONTH_TEMP, start=1)
    )


def _fit(xs: list[float], ys: list[float]) -> tuple[float, float, float] | None:
    """Least squares y = a + b·x: (a, b, r²), or None without spread in x."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx < 1e-6:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    syy = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - a - b * x) ** 2 for x, y in zip(xs, ys))
    return a, b, (1 - ss_res / syy) if syy else 1.0


def _year_total(fit: tuple[float, float, float], floor: float) -> float:
    """A fitted daily value over the months of a normal year, never below `floor`."""
    a, b, _ = fit
    return sum(
        calendar.monthrange(2001, month)[1] * max(floor, a + b * degree_days(temp))
        for month, temp in enumerate(NORMAL_MONTH_TEMP, start=1)
    )


def estimate_year(
    gas_days: list[DayCost], hp_days: list[DayCost], temps: dict[date, float], today: date
) -> dict[str, float] | None:
    """Heat, heat pump electricity and both costs in a year of normal weather.

    Fits daily heat and heat pump kWh against degree days and applies the fit to each
    month's normal temperature. A month never gets less than the lowest measured day
    (hot water goes on in summer), so a fit on autumn days, when the heating starts,
    can't make summer negative. Prices are the averages of the measured days. Today is
    left out, since it is not over.
    """
    hp_by_day = {d.day: d for d in hp_days}
    rows = [
        (degree_days(temps[g.day]), g, hp_by_day[g.day])
        for g in gas_days
        if g.day < today and g.day in temps and g.day in hp_by_day
    ]
    if len(rows) < MIN_ESTIMATE_DAYS:
        return None
    xs = [x for x, _, _ in rows]
    heat = [g.parts["heat_kwh"] for _, g, _ in rows]
    elec = [h.parts["kwh"] for _, _, h in rows]
    heat_fit = _fit(xs, heat)
    elec_fit = _fit(xs, elec)
    if heat_fit is None or elec_fit is None:
        return None
    year_dd = normal_year_degree_days()
    heat_kwh = _year_total(heat_fit, max(0.0, min(heat)))
    hp_kwh = _year_total(elec_fit, max(0.0, min(elec)))

    m3 = sum(g.parts["m3"] for _, g, _ in rows)
    measured_heat = sum(g.parts["heat_kwh"] for _, g, _ in rows)
    measured_kwh = sum(h.parts["kwh"] for _, _, h in rows)
    if not m3 or not measured_heat or not measured_kwh:
        return None
    gas_m3 = heat_kwh * m3 / measured_heat
    gas_variable = gas_m3 * sum(g.parts["variable"] for _, g, _ in rows) / m3
    gas_fixed = 365 * sum(g.parts["fixed"] for _, g, _ in rows) / len(rows)
    hp_cost = hp_kwh * sum(h.total for _, _, h in rows) / measured_kwh
    return {
        "heat_kwh": heat_kwh,
        "heat_pump_kwh": hp_kwh,
        "gas_m3": gas_m3,
        "gas_variable_eur": gas_variable,
        "gas_fixed_eur": gas_fixed,
        "gas_cost": gas_variable + gas_fixed,
        "heat_pump_cost": hp_cost,
        "saving": gas_variable + gas_fixed - hp_cost,
        "base_kwh_per_day": heat_fit[0],
        "kwh_per_degree_day": heat_fit[1],
        "r2": heat_fit[2],
        "days_used": len(rows),
        "degree_days_year": year_dd,
    }
