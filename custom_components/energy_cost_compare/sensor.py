"""Sensors: energy per situation, costs, savings, and one sensor per contract."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .calc import Totals
from .const import BASELINE_AVERAGE, CONF_BOILER_EFFICIENCY, CONF_HEAT
from .coordinator import LedgerCoordinator, Summary
from .cost import DayCost
from .entity import LedgerEntity

PERIODS = ("today", "month", "year", "total")
CURRENCY = "EUR"


def period_start(period: str) -> date | None:
    today = dt_util.now().date()
    if period == "today":
        return today
    if period == "month":
        return today.replace(day=1)
    if period == "year":
        return today.replace(month=1, day=1)
    return None


def period_sum(days: list[DayCost] | None, period: str) -> float | None:
    if days is None:
        return None
    start = period_start(period)
    return sum(d.total for d in days if start is None or d.day >= start)


def period_settled(days: list[DayCost] | None, period: str) -> dict[str, float]:
    """Import and export as a supplier app shows them: kWh, euro and average price."""
    start = period_start(period)
    totals: dict[str, float] = {}
    for d in days or []:
        if start is None or d.day >= start:
            for key, value in d.settled.items():
                totals[key] = totals.get(key, 0.0) + value
    if not totals:
        return {}
    imp, exp = totals.get("import_kwh", 0.0), totals.get("export_kwh", 0.0)
    return {
        "import_eur": round(totals.get("import_eur", 0.0), 2),
        "import_price": round(totals["import_eur"] / imp, 4) if imp else None,
        "export_eur": round(totals.get("export_eur", 0.0), 2),
        "export_price": round(totals["export_eur"] / exp, 4) if exp else None,
        "energy_net_eur": round(totals.get("import_eur", 0.0) - totals.get("export_eur", 0.0), 2),
    }


def period_energy(summary: Summary, situation: str, period: str) -> tuple[float, float]:
    start = period_start(period)
    imp = exp = 0.0
    for day, per_situation in summary.energy_by_day.items():
        if start is None or day >= start:
            imp += per_situation[situation][0]
            exp += per_situation[situation][1]
    return imp, exp


# Energy sensors (phase 1) ------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class LedgerSensorDescription(SensorEntityDescription):
    value: Callable[[Summary], object]


def _energy(key: str, period: str, value: Callable[[Totals], float | None]) -> LedgerSensorDescription:
    return LedgerSensorDescription(
        key=f"{key}_{period}",
        translation_key=f"{key}_{period}",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # Net and loss values can go down, so these are "total" (with a daily last_reset
        # for the "today" sensors) rather than "total_increasing".
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value=lambda s, p=period, v=value: v(s.today if p == "today" else s.since_start),
    )


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


ENERGY_DESCRIPTIONS: list[LedgerSensorDescription] = []
for _period in ("today", "total"):
    ENERGY_DESCRIPTIONS += [
        _energy("house_load", _period, lambda t: _rounded(t.house_load)),
        _energy("pv_only_import", _period, lambda t: _rounded(t.situations["B"][0])),
        _energy("pv_only_export", _period, lambda t: _rounded(t.situations["B"][1])),
        _energy("battery_net_ac", _period, lambda t: _rounded(t.battery_ac_net)),
        _energy("battery_losses", _period, lambda t: _rounded(t.battery_losses)),
    ]
ENERGY_DESCRIPTIONS += [
    LedgerSensorDescription(
        key="last_interval",
        translation_key="last_interval",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: s.last_interval_end,
    ),
    LedgerSensorDescription(
        key="incomplete_intervals",
        translation_key="incomplete_intervals",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: s.since_start.incomplete,
    ),
    LedgerSensorDescription(
        key="missing_prices",
        translation_key="missing_prices",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: s.missing_prices,
    ),
]


class LedgerSensor(LedgerEntity, SensorEntity):
    entity_description: LedgerSensorDescription

    def __init__(self, coordinator: LedgerCoordinator, description: LedgerSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return self.coordinator.summary is not None and self.native_value is not None

    @property
    def native_value(self):
        summary = self.coordinator.summary
        return None if summary is None else self.entity_description.value(summary)

    @property
    def last_reset(self):
        if self.entity_description.key.endswith("_today"):
            return dt_util.start_of_local_day()
        return None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        summary = self.coordinator.summary
        if summary is None or not self.entity_description.key.endswith(("_today", "_total")):
            return None
        totals = summary.today if self.entity_description.key.endswith("_today") else summary.since_start
        return {"intervals": totals.intervals, "incomplete_intervals": totals.incomplete}


# Cost and saving sensors (phase 3) ---------------------------------------------

# kind -> (minuend, subtrahend); each a (contract role, situation). None = plain cost.
COST_KINDS: dict[str, tuple[tuple[str, str], tuple[str, str] | None]] = {
    "cost_baseline": (("baseline", "A"), None),
    "cost_pv": (("actual", "B"), None),
    "cost_pv_battery": (("actual", "C"), None),
    "saving_pv": (("baseline", "A"), ("actual", "B")),
    "saving_pv_battery": (("baseline", "A"), ("actual", "C")),
    "saving_battery": (("actual", "B"), ("actual", "C")),
}


class CostSensor(LedgerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(
        self, coordinator: LedgerCoordinator, kind: str, period: str, scenario: bool = False
    ) -> None:
        key = f"scenario_{kind}" if scenario else f"{kind}_{period}"
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self.kind = kind
        self.period = period
        self.scenario = scenario

    def _contract(self, role: str) -> str:
        return self.coordinator.baseline if role == "baseline" else self.coordinator.actual

    def _cost(self, role: str, situation: str) -> float | None:
        days = self.coordinator.costs_for(self._contract(role), situation, self.scenario)
        return period_sum(days, self.period)

    @property
    def native_value(self) -> float | None:
        first, second = COST_KINDS[self.kind]
        a = self._cost(*first)
        if second is None:
            return None if a is None else round(a, 2)
        b = self._cost(*second)
        return None if a is None or b is None else round(a - b, 2)

    @property
    def available(self) -> bool:
        return self.native_value is not None

    @property
    def last_reset(self):
        start = period_start(self.period)
        return dt_util.start_of_local_day(start) if start else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        summary = self.coordinator.summary
        if summary is None:
            return None
        first, second = COST_KINDS[self.kind]
        attrs: dict[str, object] = {"contract": self._contract(first[0]), "situation": first[1]}
        if self.scenario:
            scenario = self.coordinator.selected_scenario
            attrs["scenario"] = scenario.name if scenario else None
            attrs["scenario_note"] = scenario.note if scenario else None
        if self.period == "total" and summary.energy_by_day and self.native_value is not None:
            # Rough: scales the recorded days to a year, ignoring the seasons.
            days = len(summary.energy_by_day)
            attrs["days_recorded"] = days
            attrs["annual_estimate"] = round(self.native_value * 365 / days)
        imp, exp = period_energy(summary, first[1], self.period)
        attrs.update(grid_import_kwh=round(imp, 2), grid_export_kwh=round(exp, 2))
        if second is None:
            days = self.coordinator.costs_for(self._contract(first[0]), first[1], self.scenario)
            attrs.update(period_settled(days, self.period))
        if second is not None:
            imp2, exp2 = period_energy(summary, second[1], self.period)
            attrs.update(
                compared_contract=self._contract(second[0]),
                compared_situation=second[1],
                grid_import_saved_kwh=round(imp - imp2, 2),
                grid_export_extra_kwh=round(exp2 - exp, 2),
            )
            # The same comparison with both sides on the actual supplier, so the effect
            # of the installation can be seen without the effect of the contract.
            actual = self.coordinator.actual
            same_a = period_sum(self.coordinator.costs_for(actual, first[1], self.scenario), self.period)
            same_b = period_sum(self.coordinator.costs_for(actual, second[1], self.scenario), self.period)
            if same_a is not None and same_b is not None:
                attrs["saving_same_contract"] = round(same_a - same_b, 2)
        return attrs


class ContractSensor(LedgerEntity, SensorEntity):
    """What the actual situation (C) would have cost under one contract, plus details."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2
    _attr_translation_key = "contract"
    # Static details; keep them out of the recorder database.
    _unrecorded_attributes = frozenset({"note", "sources", "price_periods"})

    def __init__(self, coordinator: LedgerCoordinator, contract_id: str) -> None:
        super().__init__(coordinator, f"contract_{contract_id}")
        self.contract_id = contract_id
        contract = coordinator.tariffs.contracts[contract_id]
        self._attr_translation_placeholders = {"name": contract.name}

    @property
    def native_value(self) -> float | None:
        value = period_sum(self.coordinator.costs_for(self.contract_id, "C"), "total")
        return None if value is None else round(value, 2)

    @property
    def available(self) -> bool:
        return self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        tariffs = self.coordinator.tariffs
        contract = tariffs.contracts.get(self.contract_id) if tariffs else None
        if contract is None:
            return None
        attrs: dict[str, object] = {
            "contract_id": contract.id,
            "supplier": contract.supplier,
            "kind": contract.kind,
            "checked_on": contract.checked_on.isoformat(),
            "prices_age_days": (dt_util.now().date() - contract.checked_on).days,
            "note": contract.note,
            "sources": contract.sources,
            # All amounts excl. VAT; read by the monthly price check to compare offers.
            "price_periods": [
                {"valid_from": valid_from.isoformat(), **values} for valid_from, values in contract.periods.items
            ],
        }
        for situation, label in (("A", "no_pv"), ("B", "pv"), ("C", "pv_battery")):
            days = self.coordinator.costs_for(self.contract_id, situation)
            for period in ("month", "year", "total"):
                value = period_sum(days, period)
                attrs[f"{label}_{period}"] = None if value is None else round(value, 2)
        return attrs


class HeatSensor(LedgerEntity, SensorEntity):
    """Gas boiler alternative, heat pump electricity, and the difference, under the baseline."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: LedgerCoordinator, kind: str, period: str) -> None:
        super().__init__(coordinator, f"{kind}_{period}")
        self._attr_translation_key = f"{kind}_{period}"
        self.kind = kind
        self.period = period

    def _days(self):
        summary = self.coordinator.summary
        if summary is None:
            return None
        baseline = self.coordinator.baseline
        return summary.heat.get(baseline) or summary.heat.get(BASELINE_AVERAGE)

    @property
    def native_value(self) -> float | None:
        days = self._days()
        if not days:
            return None
        gas, hp = (period_sum(d, self.period) for d in days)
        value = {"gas_boiler_cost": gas, "heat_pump_cost": hp, "saving_heat_pump": gas - hp}[self.kind]
        return round(value, 2)

    @property
    def available(self) -> bool:
        return self.native_value is not None

    @property
    def last_reset(self):
        start = period_start(self.period)
        return dt_util.start_of_local_day(start) if start else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        days = self._days()
        if not days:
            return None
        start = period_start(self.period)
        gas_days = [d for d in days[0] if start is None or d.day >= start]
        hp_days = [d for d in days[1] if start is None or d.day >= start]
        return {
            "heat_kwh": round(sum(d.parts["heat_kwh"] for d in gas_days), 1),
            "gas_m3": round(sum(d.parts["m3"] for d in gas_days), 1),
            "heat_pump_kwh": round(sum(d.parts["kwh"] for d in hp_days), 1),
            "boiler_efficiency": self.coordinator.config.get(CONF_BOILER_EFFICIENCY, 0.9),
            "contract": self.coordinator.baseline,
        }


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: LedgerCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [LedgerSensor(coordinator, d) for d in ENERGY_DESCRIPTIONS]
    entities += [CostSensor(coordinator, kind, period) for kind in COST_KINDS for period in PERIODS]
    entities += [CostSensor(coordinator, kind, "total", scenario=True) for kind in COST_KINDS]
    if coordinator.config.get(CONF_HEAT):
        entities += [
            HeatSensor(coordinator, kind, period)
            for kind in ("gas_boiler_cost", "heat_pump_cost", "saving_heat_pump")
            for period in PERIODS
        ]
    if coordinator.tariffs:
        entities += [ContractSensor(coordinator, cid) for cid in coordinator.contract_options]
    async_add_entities(entities)
