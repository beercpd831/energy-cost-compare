"""End-to-end test: statistics in the recorder end up as ledger sensors."""

from datetime import timedelta

import pytest
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.energy_cost_compare.const import DOMAIN

COUNTERS = {
    "sensor.grid_in": 0.5,  # kWh per hour
    "sensor.grid_out": 0.2,
    "sensor.pv": 1.0,
    "sensor.inv_in": 0.4,
    "sensor.inv_out": 0.1,
    "sensor.heat": 2.0,
    "sensor.hp": 0.5,
}


async def _import_hourly(hass: HomeAssistant, start, hours: int) -> None:
    for entity_id, per_hour in COUNTERS.items():
        meta = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=None,
            source="recorder",
            statistic_id=entity_id,
            unit_of_measurement="kWh",
            unit_class="energy",
            mean_type=0,
        )
        rows = [
            StatisticData(start=start + timedelta(hours=h), state=per_hour * (h + 1), sum=per_hour * (h + 1))
            for h in range(hours)
        ]
        async_import_statistics(hass, meta, rows)
    price_meta = StatisticMetaData(
        has_mean=True,
        has_sum=False,
        name=None,
        source="recorder",
        statistic_id="sensor.price",
        unit_of_measurement="EUR/kWh",
        unit_class=None,
        mean_type=1,
    )
    async_import_statistics(
        hass,
        price_meta,
        [StatisticData(start=start + timedelta(hours=h), mean=0.121, min=0.121, max=0.121) for h in range(hours)],
    )
    temp_meta = StatisticMetaData(
        has_mean=True,
        has_sum=False,
        name=None,
        source="recorder",
        statistic_id="sensor.outdoor",
        unit_of_measurement="°C",
        unit_class="temperature",
        mean_type=1,
    )
    async_import_statistics(
        hass,
        temp_meta,
        [StatisticData(start=start + timedelta(hours=h), mean=8.0 + h % 5, min=0.0, max=20.0) for h in range(hours)],
    )


async def test_ledger_from_hourly_statistics(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant, tmp_path
) -> None:
    hass.config.config_dir = str(tmp_path)  # keep tariff files out of the shared test config
    start_day = (dt_util.now() - timedelta(days=12)).date()
    start = dt_util.as_utc(dt_util.start_of_local_day(start_day))
    await _import_hourly(hass, start, 24)
    await async_wait_recording_done(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "grid_import": ["sensor.grid_in"],
            "grid_export": ["sensor.grid_out"],
            "pv": ["sensor.pv"],
            "inv_in": ["sensor.inv_in"],
            "inv_out": ["sensor.inv_out"],
            "heat": ["sensor.heat"],
            "hp_elec": ["sensor.hp"],
            "price_sensor": "sensor.price",
            "price_includes_vat": True,
            "outdoor_temperature": "sensor.outdoor",
            "start_date": start_day.isoformat(),
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    coordinator = entry.runtime_data
    totals = coordinator.summary.since_start
    # Sums start at zero, so the first hour's change counts too.
    hours = 24
    assert totals.house_load == pytest.approx((0.5 - 0.2 + 1.0 - 0.4 + 0.1) * hours)
    assert totals.situations["B"][0] == pytest.approx(0.0)
    assert totals.situations["B"][1] == pytest.approx(0.0)

    state = hass.states.get("sensor.energy_cost_compare_house_load_since_start")
    assert state is not None
    assert float(state.state) == pytest.approx(1.0 * hours, abs=0.01)
    # Without 5-minute statistics the recent days are stored as incomplete intervals.
    assert totals.incomplete > 0
    assert totals.intervals - totals.incomplete == hours * 4

    # Costs: 9 shipped contracts priced, and the sensors filled in.
    assert len(coordinator.summary.costs) == 9
    # The price sensor includes VAT: 0.121 / 1.21 = 0.10 excl. VAT.
    vattenfall = hass.states.get("sensor.energy_cost_compare_vattenfall_1_jaar_vast_cost_of_actual_usage")
    assert vattenfall is not None
    assert vattenfall.attributes["price_periods"][0]["electricity"]["supply"] == pytest.approx(0.1846)
    zonneplan_c = coordinator.costs_for("zonneplan_dynamisch", "C")
    assert zonneplan_c and zonneplan_c[0].missing_prices == 0
    baseline = hass.states.get("sensor.energy_cost_compare_cost_without_pv_and_battery_baseline_since_start")
    # Only the first day has energy; the other days carry fixed costs minus the
    # energy tax reduction, so the total can be anything. It has to be a number.
    assert baseline is not None
    float(baseline.state)
    # Import and export as a supplier app shows them.
    assert "import_eur" in baseline.attributes and "export_eur" in baseline.attributes
    bill = baseline.attributes["bill"]
    assert {"supply", "energy_tax", "fixed", "grid", "tax_reduction"} <= set(bill)
    saving = hass.states.get("sensor.energy_cost_compare_saving_pv_and_battery_since_start")
    assert saving is not None and saving.state not in ("unknown", "unavailable")
    assert hass.states.get("select.energy_cost_compare_baseline_contract").state == "average_fixed"
    assert hass.states.get("select.energy_cost_compare_future_scenario").state == "2027_no_netting"
    future = hass.states.get("sensor.energy_cost_compare_scenario_saving_pv_and_battery")
    assert future is not None and future.attributes["scenario"] == "2027 zonder salderen"
    assert "annual_estimate" in future.attributes
    assert coordinator.summary.scenario_costs

    # Gas comparison on the average of the fixed contracts.
    heat = hass.states.get("sensor.energy_cost_compare_saving_heat_pump_since_start")
    assert heat is not None and heat.state not in ("unknown", "unavailable")
    assert heat.attributes["heat_kwh"] == pytest.approx(2.0 * hours, abs=0.1)
    assert heat.attributes["heat_pump_kwh"] == pytest.approx(0.5 * hours, abs=0.1)
    # Daily mean outdoor temperature from the hourly statistics.
    mean = sum(8.0 + h % 5 for h in range(hours)) / hours
    assert coordinator.summary.outdoor_temp[start_day] == pytest.approx(mean)
    first = heat.attributes["days"][0]
    assert first["date"] == start_day.isoformat()
    assert first["outdoor_temp"] == pytest.approx(mean, abs=0.05)
    assert first["cop"] == pytest.approx(4.0, abs=0.01)
    # One day with temperatures is too little for a yearly estimate.
    estimate = hass.states.get("sensor.energy_cost_compare_saving_heat_pump_per_year_estimate")
    assert estimate is not None and estimate.state == "unavailable"
