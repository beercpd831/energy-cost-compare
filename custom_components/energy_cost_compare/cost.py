"""Cost engine: prices a situation (A, B or C) under a contract, day by day.

Netting (salderen) works on yearly totals, so the engine keeps year-to-date totals and
derives each day's cost as the change in the year-to-date bill. A single day can then
look odd (a sunny day may even cost less than nothing), but every month and year adds up
to what the yearly invoice would say. Years are calendar years.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo

from .calc import Interval
from .tariffs import FIXED, Contract, TariffSet

COMPONENTS = ("supply", "feed_in", "feed_in_cost", "energy_tax", "fixed", "grid", "tax_reduction", "vat")


@dataclass
class Scenario:
    """Rule and price changes replayed on the recorded energy data."""

    id: str = "now"
    name: str = "Now"
    netting: bool | None = None  # None: as the tax file says for each day
    tariff_date: date | None = None  # price every day with the tariffs valid on this date
    supply_factor: float = 1.0  # supplier prices: supply, markup, fixed costs
    market_factor: float = 1.0  # market (EPEX) price
    tax_factor: float = 1.0  # energy tax
    grid_factor: float = 1.0  # grid operator costs
    feed_in_factor: float = 1.0  # feed-in compensation and feed-in costs
    grid_slots: list[dict] | None = None  # replaces the grid file's time slots
    note: str = ""


@dataclass
class _Year:
    imp: float = 0.0
    exp: float = 0.0
    supply: float = 0.0  # sum of supply price x import
    feed: float = 0.0  # sum of feed-in rate x export
    feed_cost: float = 0.0
    tax: float = 0.0  # sum of energy tax x import
    fixed: float = 0.0
    grid: float = 0.0
    reduction: float = 0.0
    netting: bool = True
    vat: float = 0.21

    def bill(self, kind: str) -> dict[str, float]:
        """Year-to-date bill per component, euro incl. VAT (VAT also shown on its own)."""
        netted = min(self.imp, self.exp) if self.netting else 0.0
        avg_supply = self.supply / self.imp if self.imp else 0.0
        avg_tax = self.tax / self.imp if self.imp else 0.0
        if kind == FIXED:
            # Netted kWh cancel at the supply price; only the excess earns the feed-in rate.
            supply = self.supply - netted * avg_supply
            feed = (self.feed / self.exp) * (self.exp - netted) if self.exp else 0.0
        else:
            # Dynamic contracts settle every quarter at the market price; netting only
            # returns the energy tax.
            supply = self.supply
            feed = self.feed
        parts = {
            "supply": supply,
            "feed_in": -feed,
            "feed_in_cost": self.feed_cost,
            "energy_tax": self.tax - netted * avg_tax,
            "fixed": self.fixed,
            "grid": self.grid,
            "tax_reduction": -self.reduction,
        }
        excl = sum(parts.values())
        result = {k: v * (1 + self.vat) for k, v in parts.items()}
        result["vat"] = excl * self.vat
        result["total"] = excl * (1 + self.vat)
        return result


@dataclass
class DayCost:
    day: date
    total: float
    parts: dict[str, float] = field(default_factory=dict)
    missing_prices: int = 0


def _days_in_year(day: date) -> int:
    return 366 if calendar.isleap(day.year) else 365


def price_situation(
    intervals: list[Interval],
    contract: Contract,
    tariffs: TariffSet,
    situation: str,
    tz: tzinfo,
    scenario: Scenario | None = None,
    daylight: Callable[[int], bool] | None = None,
) -> list[DayCost]:
    """Cost per local day, oldest first. Days before the contract's first period are skipped.

    daylight(timestamp) tells whether the sun is up, for a daytime feed-in bonus.
    """
    scenario = scenario or Scenario()
    days: list[DayCost] = []
    year: _Year | None = None
    previous: dict[str, float] = {}
    current_day: date | None = None
    current_year: int | None = None
    rates = None
    tax = grid = {}
    last_epex: float | None = None
    missing = 0

    def close_day() -> None:
        nonlocal previous, missing
        bill = year.bill(contract.kind)
        delta = {k: bill[k] - previous.get(k, 0.0) for k in bill}
        days.append(DayCost(current_day, delta.pop("total"), delta, missing))
        previous = bill
        missing = 0

    for interval in intervals:
        day = datetime.fromtimestamp(interval.start, tz).date()
        if day != current_day:
            if current_day is not None and year is not None and rates is not None:
                close_day()
            current_day = day
            tariff_day = scenario.tariff_date or day
            rates = contract.electricity(tariff_day)
            if rates is None:
                continue
            tax = tariffs.taxes.at(tariff_day)
            grid = dict(tariffs.grid.at(tariff_day))
            if scenario.grid_slots is not None:
                grid["slots"] = scenario.grid_slots
            if year is None or day.year != current_year:
                year = _Year()
                previous = {}
            current_year = day.year
            year.netting = scenario.netting if scenario.netting is not None else bool(tax.get("netting", True))
            year.vat = float(tax.get("vat", 0.21))
            per_day = 12 / _days_in_year(day)
            year.fixed += rates.fixed_per_month * per_day * scenario.supply_factor
            year.grid += float(grid.get("electricity_per_month", 0.0)) * per_day * scenario.grid_factor
            year.reduction += float(tax.get("tax_reduction_per_year", 0.0)) / _days_in_year(day)
        if rates is None:
            continue

        imp, exp = interval.situation(situation)
        epex = interval.values.get("epex")
        if epex is None:
            epex = last_epex
            if contract.kind != FIXED:
                missing += 1
        else:
            last_epex = epex

        market = (epex or 0.0) * scenario.market_factor
        if contract.kind == FIXED:
            supply_price = rates.supply * scenario.supply_factor
            feed_rate = (rates.feed_in if rates.feed_in is not None else 0.0) * scenario.feed_in_factor
        else:
            supply_price = market + rates.markup * scenario.supply_factor
            feed_rate = market + rates.feed_in_markup * scenario.feed_in_factor
            if rates.feed_in_bonus and market > 0 and daylight is not None and daylight(interval.start):
                feed_rate += market * rates.feed_in_bonus
        grid_price = _slot_price(grid, datetime.fromtimestamp(interval.start, tz)) * scenario.grid_factor

        year.imp += imp
        year.exp += exp
        year.supply += supply_price * imp
        year.feed += feed_rate * exp
        year.feed_cost += rates.feed_in_cost * scenario.feed_in_factor * exp
        year.tax += float(tax.get("electricity_tax", 0.0)) * scenario.tax_factor * imp
        year.grid += grid_price * imp

    if current_day is not None and year is not None and rates is not None:
        close_day()
    return days


def _slot_price(grid: dict, moment: datetime) -> float:
    """Time-dependent grid tariff per kWh (none yet in the Netherlands, ready for it)."""
    slots = grid.get("slots") or []
    hhmm = moment.strftime("%H:%M")
    for slot in slots:
        start, end = str(slot["from"]), str(slot["to"])
        inside = start <= hhmm < end if start < end else (hhmm >= start or hhmm < end)
        if inside:
            return float(slot.get("per_kwh", 0.0))
    return 0.0


def sum_days(days: list[DayCost], start: date | None = None, end: date | None = None) -> float:
    """Total over days with start <= day < end."""
    return sum(
        d.total for d in days if (start is None or d.day >= start) and (end is None or d.day < end)
    )
