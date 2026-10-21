"""Tests for tariff loading and the cost engine."""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from custom_components.energy_cost_compare.calc import Interval
from custom_components.energy_cost_compare.cost import Scenario, price_situation, sum_days
from custom_components.energy_cost_compare.tariffs import (
    Contract,
    GridTariff,
    Periods,
    TariffSet,
    Taxes,
    load_tariffs,
)

UTC = timezone.utc
DATA = Path(__file__).parent.parent / "custom_components" / "energy_cost_compare" / "data"


def _tariffs(netting: bool = True) -> TariffSet:
    taxes = Taxes(Periods.parse([{
        "valid_from": "2026-01-01", "vat": 0.0, "electricity_tax": 0.10,
        "tax_reduction_per_year": 0.0, "netting": netting,
    }], "t"))
    grid = GridTariff("x", Periods.parse([{"valid_from": "2026-01-01"}], "g"))
    return TariffSet({}, taxes, grid, [])


def _contract(kind: str, **electricity) -> Contract:
    return Contract.parse({
        "id": "c", "name": "c", "supplier": "s", "kind": kind, "checked_on": "2026-10-01",
        "periods": [{"valid_from": "2026-01-01", "electricity": electricity}],
    }, "test")


def _day(day: int, imp: float, exp: float, epex: float | None = None) -> list[Interval]:
    """One interval per day carrying all the day's energy (situation C)."""
    start = int(datetime(2026, 10, day, 12, tzinfo=UTC).timestamp())
    values = {"grid_import": imp, "grid_export": exp, "pv": 0.0}
    if epex is not None:
        values["epex"] = epex
    return [Interval(start=start, values=values)]


def test_shipped_tariff_files_load():
    tariffs = load_tariffs(DATA)
    assert tariffs.errors == []
    assert len(tariffs.contracts) == 8
    assert tariffs.taxes.at(date(2027, 1, 1))["netting"] is False


def test_fixed_contract_with_netting():
    contract = _contract("fixed", supply=0.20, feed_in=0.05)
    intervals = _day(1, 10, 0) + _day(2, 0, 4)
    days = price_situation(intervals, contract, _tariffs(), "C", UTC)
    # Day 1: 10 kWh x (0.20 + 0.10). Day 2: 4 kWh netted back at 0.30.
    assert days[0].total == pytest.approx(3.0)
    assert days[1].total == pytest.approx(-1.2)
    assert sum_days(days) == pytest.approx(6 * 0.30)


def test_fixed_contract_excess_export_earns_feed_in():
    contract = _contract("fixed", supply=0.20, feed_in=0.05)
    days = price_situation(_day(1, 2, 5), contract, _tariffs(), "C", UTC)
    # 2 kWh netted, 3 kWh excess at 0.05.
    assert sum_days(days) == pytest.approx(-0.15)


def test_without_netting():
    contract = _contract("fixed", supply=0.20, feed_in=0.05, feed_in_cost=0.01)
    days = price_situation(_day(1, 2, 5), contract, _tariffs(), "C", UTC, Scenario(netting=False))
    assert sum_days(days) == pytest.approx(2 * 0.30 - 5 * 0.05 + 5 * 0.01)


def test_dynamic_contract_settles_at_market_price():
    contract = _contract("dynamic", markup=0.02, feed_in_markup=0.0)
    intervals = _day(1, 4, 0, epex=0.30) + _day(2, 0, 4, epex=0.05)
    days = price_situation(intervals, contract, _tariffs(), "C", UTC)
    # Import 4 x (0.32 + 0.10 tax); export 4 x 0.05, and the 4 kWh tax comes back.
    assert sum_days(days) == pytest.approx(4 * 0.32 - 4 * 0.05)


def test_missing_price_reuses_last_one():
    contract = _contract("dynamic", markup=0.0)
    intervals = _day(1, 1, 0, epex=0.25) + _day(2, 1, 0)
    days = price_situation(intervals, contract, _tariffs(netting=False), "C", UTC)
    assert days[1].total == pytest.approx(0.35)
    assert days[1].missing_prices == 1


def test_fixed_costs_and_vat():
    taxes = Taxes(Periods.parse([{
        "valid_from": "2026-01-01", "vat": 0.21, "electricity_tax": 0.0,
        "tax_reduction_per_year": 365.0, "netting": True,
    }], "t"))
    grid = GridTariff("x", Periods.parse([{"valid_from": "2026-01-01", "electricity_per_month": 36.5}], "g"))
    tariffs = TariffSet({}, taxes, grid, [])
    contract = _contract("fixed", supply=0.0, fixed_per_month=6.0833333)
    days = price_situation(_day(1, 0, 0), contract, tariffs, "C", UTC)
    per_day = (6.0833333 * 12 / 365 + 36.5 * 12 / 365 - 1.0) * 1.21
    assert days[0].total == pytest.approx(per_day, rel=1e-4)


def test_new_year_restarts_netting():
    contract = _contract("fixed", supply=0.20)
    start = int(datetime(2026, 12, 31, 12, tzinfo=UTC).timestamp())
    intervals = [
        Interval(start=start, values={"grid_export": 5.0}),
        Interval(start=start + 86400, values={"grid_import": 5.0}),
    ]
    taxes = Taxes(Periods.parse([{"valid_from": "2026-01-01", "vat": 0.0, "electricity_tax": 0.1, "netting": True}], "t"))
    tariffs = TariffSet({}, taxes, GridTariff("x", Periods.parse([{"valid_from": "2026-01-01"}], "g")), [])
    days = price_situation(intervals, contract, tariffs, "C", UTC)
    # 2026's export has nothing to net against; 2027's import is paid in full.
    assert days[0].total == pytest.approx(0.0)
    assert days[1].total == pytest.approx(1.5)


def test_shipped_scenarios_load():
    tariffs = load_tariffs(DATA)
    assert set(tariffs.scenarios) == {"now", "2027_no_netting", "2027_prices_up", "2027_time_of_use_grid"}
    assert tariffs.scenarios["2027_no_netting"].tariff_date == date(2027, 1, 1)


def test_scenario_uses_tariffs_of_its_date():
    contract = Contract.parse({
        "id": "c", "name": "c", "supplier": "s", "kind": "fixed", "checked_on": "2026-10-01",
        "periods": [
            {"valid_from": "2026-01-01", "electricity": {"supply": 0.20, "feed_in": 0.05}},
            {"valid_from": "2027-01-01", "electricity": {"supply": 0.30, "feed_in": 0.05, "feed_in_cost": 0.02}},
        ],
    }, "test")
    intervals = _day(1, 2, 5)
    now = sum_days(price_situation(intervals, contract, _tariffs(), "C", UTC))
    future = sum_days(price_situation(
        intervals, contract, _tariffs(), "C", UTC,
        Scenario(netting=False, tariff_date=date(2027, 1, 1), supply_factor=2.0),
    ))
    assert now == pytest.approx(-0.15)
    # 2 kWh at (0.60 + 0.10 tax), 5 kWh export at 0.05 minus 0.02 costs.
    assert future == pytest.approx(2 * 0.70 - 5 * 0.05 + 5 * 0.02)


def test_grid_slots():
    contract = _contract("fixed", supply=0.0)
    start = int(datetime(2026, 10, 1, 17, 30, tzinfo=UTC).timestamp())
    intervals = [Interval(start=start, values={"grid_import": 1.0})]
    scenario = Scenario(netting=False, grid_slots=[{"from": "17:00", "to": "21:00", "per_kwh": 0.06}])
    days = price_situation(intervals, contract, _tariffs(netting=False), "C", UTC, scenario)
    assert sum_days(days) == pytest.approx(0.10 + 0.06)


def test_gas_comparison():
    from custom_components.energy_cost_compare.gas import KWH_PER_M3, price_heat

    contract = Contract.parse({
        "id": "c", "name": "c", "supplier": "s", "kind": "fixed", "checked_on": "2026-10-01",
        "periods": [{"valid_from": "2026-01-01", "electricity": {"supply": 0.20},
                     "gas": {"supply": 1.00, "fixed_per_month": 0.0}}],
    }, "test")
    taxes = Taxes(Periods.parse([{"valid_from": "2026-01-01", "vat": 0.0, "electricity_tax": 0.10,
                                  "gas_tax": 0.60}], "t"))
    tariffs = TariffSet({}, taxes, GridTariff("x", Periods.parse([{"valid_from": "2026-01-01"}], "g")), [])
    start = int(datetime(2026, 10, 1, 12, tzinfo=UTC).timestamp())
    intervals = [Interval(start=start, values={"heat": 40.0, "hp_elec": 10.0})]
    gas, hp = price_heat(intervals, contract, tariffs, UTC, 0.9)
    m3 = 40 / (0.9 * KWH_PER_M3)
    assert gas[0].total == pytest.approx(m3 * 1.60)
    assert hp[0].total == pytest.approx(10 * 0.30)


def test_daytime_feed_in_bonus():
    contract = _contract("dynamic", markup=0.0, feed_in_markup=0.01, feed_in_bonus=0.1,
                         feed_in_bonus_min_price=-0.02)
    day = _day(1, 0, 4, epex=0.20)
    night = _day(2, 0, 4, epex=0.20)
    slightly_negative = _day(3, 0, 4, epex=-0.01)
    negative = _day(4, 0, 4, epex=-0.05)
    sun_up = {day[0].start, slightly_negative[0].start, negative[0].start}
    days = price_situation(day + night + slightly_negative + negative, contract, _tariffs(netting=False),
                           "C", UTC, daylight=lambda ts: ts in sun_up)
    assert days[0].total == pytest.approx(-4 * (0.20 * 1.1 + 0.01))
    assert days[1].total == pytest.approx(-4 * (0.20 + 0.01))
    assert days[2].total == pytest.approx(-4 * (-0.01 * 1.1 + 0.01))
    # No bonus below the minimum price.
    assert days[3].total == pytest.approx(-4 * (-0.05 + 0.01))


def test_missing_price_estimated_from_neighbouring_days():
    contract = _contract("dynamic", markup=0.0)
    intervals = _day(1, 1, 0, epex=0.20) + _day(2, 1, 0) + _day(3, 1, 0, epex=0.30)
    days = price_situation(intervals, contract, _tariffs(netting=False), "C", UTC)
    # Same time of day on the days before and after: (0.20 + 0.30) / 2, plus 0.10 tax.
    assert days[1].total == pytest.approx(0.35)
    assert days[1].missing_prices == 1
