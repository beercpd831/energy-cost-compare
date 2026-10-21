"""Tests for the pure energy calculations."""

from datetime import datetime, timezone

import pytest

from custom_components.energy_cost_compare.calc import (
    Interval,
    aggregate_changes,
    floor_interval,
    summarise,
)

T0 = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
TS = int(T0.timestamp())


def test_floor_interval():
    assert floor_interval(datetime(2026, 10, 7, 10, 29, 59, tzinfo=timezone.utc)) == datetime(
        2026, 10, 7, 10, 15, tzinfo=timezone.utc
    )


def test_house_load_and_situations():
    # Sunny quarter: 1.0 kWh PV, 0.3 exported, 0.4 went into the battery, house used 0.3.
    i = Interval(
        start=TS,
        values={"grid_import": 0.0, "grid_export": 0.3, "pv": 1.0, "inv_in": 0.42, "inv_out": 0.02},
    )
    assert i.house_load == pytest.approx(0.3)
    assert i.situation("A") == pytest.approx((0.3, 0.0))
    assert i.situation("B") == pytest.approx((0.0, 0.7))
    assert i.situation("C") == pytest.approx((0.0, 0.3))
    assert i.battery_ac_net == pytest.approx(0.4)
    assert i.battery_losses is None


def test_battery_losses_with_dc_counters():
    i = Interval(
        start=TS,
        values={"grid_import": 0.0, "grid_export": 0.0, "pv": 0.0, "inv_in": 0.0, "inv_out": 0.45,
                "dc_charge": 0.0, "dc_discharge": 0.5},
    )
    # 0.5 kWh left the battery, 0.45 kWh arrived as AC: 0.05 kWh lost.
    assert i.battery_losses == pytest.approx(0.05)
    assert i.house_load == pytest.approx(0.45)


def test_aggregate_five_minute_rows():
    rows = {"a": [(TS, 0.1), (TS + 300, 0.2), (TS + 600, 0.3), (TS + 900, 0.4)]}
    out = aggregate_changes(rows, T0, datetime.fromtimestamp(TS + 1800, tz=timezone.utc), 5)
    assert out[TS].get("a") == pytest.approx(0.6)
    assert out[TS].complete
    assert out[TS + 900].get("a") == pytest.approx(0.4)
    assert not out[TS + 900].complete


def test_aggregate_hourly_rows_are_split():
    rows = {"a": [(TS, 1.0)]}
    out = aggregate_changes(rows, T0, datetime.fromtimestamp(TS + 3600, tz=timezone.utc), 60)
    assert [round(out[TS + k * 900].get("a"), 3) for k in range(4)] == [0.25] * 4
    assert all(i.complete for i in out.values())


def test_missing_quantity_marks_incomplete():
    rows = {"a": [(TS, 0.1), (TS + 300, 0.1), (TS + 600, 0.1)], "b": []}
    out = aggregate_changes(rows, T0, datetime.fromtimestamp(TS + 900, tz=timezone.utc), 5)
    assert not out[TS].complete
    assert out[TS].get("b") == 0.0


def test_summarise():
    a = Interval(start=TS, values={"grid_import": 0.5, "pv": 0.0})
    b = Interval(start=TS + 900, complete=False, values={"grid_export": 0.2, "pv": 0.6})
    t = summarise([a, b])
    assert t.intervals == 2 and t.incomplete == 1
    assert t.house_load == pytest.approx(0.9)
    assert t.situations["B"] == pytest.approx([0.5, 0.2])
