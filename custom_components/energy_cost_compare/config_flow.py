"""Config and options flow: energy counters, price sensor, start date, and contract prices."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DC_CHARGE,
    CONF_BOILER_EFFICIENCY,
    CONF_DC_DISCHARGE,
    CONF_GRID_EXPORT,
    CONF_HEAT,
    CONF_HP_ELEC,
    CONF_GRID_IMPORT,
    CONF_INV_IN,
    CONF_INV_OUT,
    CONF_PRICE_INCLUDES_VAT,
    CONF_PRICE_SENSOR,
    CONF_PV,
    CONF_START_DATE,
    LEDGER_DIR,
    TARIFF_DIR,
    DOMAIN,
    NAME,
    QUANTITY_KEYS,
    REQUIRED_KEYS,
)
from .tariffs import FIXED, TariffError, load_tariffs, save_period

# Suggestions matching a Victron system read through Victron MQTT; every field can be changed.
SUGGESTED: dict[str, list[str]] = {
    CONF_GRID_IMPORT: ["sensor.net_meter_consumption"],
    CONF_GRID_EXPORT: ["sensor.net_meter_feed_in"],
    CONF_PV: ["sensor.pv_omvormer_total_yield"],
    CONF_INV_IN: [
        f"sensor.l{p}_rs_48_6000_{s}" for p in (1, 2, 3) for s in ("ac_in_1_to_inverter", "ac_out_to_inverter")
    ],
    CONF_INV_OUT: [
        f"sensor.l{p}_rs_48_6000_{s}" for p in (1, 2, 3) for s in ("inverter_to_ac_out", "inverter_to_ac_in_1")
    ],
    CONF_DC_CHARGE: ["sensor.rs_system_dc_battery_charge_energy"],
    CONF_DC_DISCHARGE: ["sensor.rs_system_dc_battery_discharge_energy"],
    CONF_HEAT: ["sensor.boiler_nrgsuppheating", "sensor.boiler_nrgsuppww"],
    CONF_HP_ELEC: ["sensor.power_meter_wp_s02_total_energy"],
}
# A market price sensor, for example from the ENTSO-e integration.
SUGGESTED_PRICE = "sensor.peter_current_electricity_market_price"

ENERGY_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="energy", multiple=True)
)


def _schema(hass, defaults: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    for key in QUANTITY_KEYS:
        suggested = defaults.get(key)
        if suggested is None:
            suggested = [e for e in SUGGESTED.get(key, []) if hass.states.get(e)]
        marker = vol.Required if key in REQUIRED_KEYS else vol.Optional
        fields[marker(key, description={"suggested_value": suggested})] = ENERGY_SELECTOR
    price = defaults.get(CONF_PRICE_SENSOR)
    if price is None and hass.states.get(SUGGESTED_PRICE):
        price = SUGGESTED_PRICE
    fields[vol.Optional(CONF_PRICE_SENSOR, description={"suggested_value": price})] = (
        selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
    )
    fields[vol.Required(CONF_PRICE_INCLUDES_VAT, default=defaults.get(CONF_PRICE_INCLUDES_VAT, True))] = (
        selector.BooleanSelector()
    )
    fields[vol.Required(CONF_BOILER_EFFICIENCY, default=defaults.get(CONF_BOILER_EFFICIENCY, 0.9))] = (
        selector.NumberSelector(selector.NumberSelectorConfig(min=0.5, max=1.1, step=0.01, mode=selector.NumberSelectorMode.BOX))
    )
    fields[vol.Required(CONF_START_DATE, default=defaults.get(CONF_START_DATE, "2026-10-07"))] = (
        selector.DateSelector()
    )
    return vol.Schema(fields)


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for key in REQUIRED_KEYS:
        if not user_input.get(key):
            errors[key] = "required"
    if bool(user_input.get(CONF_INV_IN)) != bool(user_input.get(CONF_INV_OUT)):
        errors["base"] = "inverter_pair"
    return errors


class EnergyCostCompareConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(title=NAME, data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_schema(self.hass, user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EnergyCostCompareOptionsFlow()


# Price fields shown in the options screen, per contract kind.
FIXED_FIELDS = ("supply", "feed_in", "feed_in_cost", "fixed_per_month")
DYNAMIC_FIELDS = ("markup", "feed_in_markup", "feed_in_bonus", "feed_in_cost", "fixed_per_month")
GAS_FIXED_FIELDS = ("supply", "fixed_per_month")
GAS_DYNAMIC_FIELDS = ("markup", "fixed_per_month")
PRICE_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(min=-1, max=10, step="any", mode=selector.NumberSelectorMode.BOX)
)


class EnergyCostCompareOptionsFlow(OptionsFlow):
    """Menu: change the energy counters, or check and update a contract's prices."""

    def __init__(self) -> None:
        self._contract_id: str | None = None

    @property
    def _tariff_folder(self) -> Path:
        return Path(self.hass.config.path(LEDGER_DIR, TARIFF_DIR))

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["counters", "contract"])

    async def async_step_counters(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="counters", data_schema=_schema(self.hass, user_input or current), errors=errors
        )

    async def async_step_contract(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        tariffs = await self.hass.async_add_executor_job(load_tariffs, self._tariff_folder)
        if user_input is not None:
            self._contract_id = user_input["contract"]
            return await self.async_step_prices()
        options = [
            selector.SelectOptionDict(value=c.id, label=f"{c.name} (checked {c.checked_on})")
            for c in sorted(tariffs.contracts.values(), key=lambda c: c.name)
        ]
        return self.async_show_form(
            step_id="contract",
            data_schema=vol.Schema({vol.Required("contract"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=options)
            )}),
        )

    async def async_step_prices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        tariffs = await self.hass.async_add_executor_job(load_tariffs, self._tariff_folder)
        contract = tariffs.contracts[self._contract_id]
        fixed = contract.kind == FIXED
        e_fields = FIXED_FIELDS if fixed else DYNAMIC_FIELDS
        g_fields = GAS_FIXED_FIELDS if fixed else GAS_DYNAMIC_FIELDS
        errors: dict[str, str] = {}

        if user_input is not None:
            electricity = {f: user_input[f] for f in e_fields if user_input.get(f) is not None}
            gas = {f: user_input[f"gas_{f}"] for f in g_fields if user_input.get(f"gas_{f}") is not None}
            try:
                await self.hass.async_add_executor_job(
                    save_period,
                    self._tariff_folder,
                    contract.id,
                    date.fromisoformat(user_input["valid_from"]),
                    date.fromisoformat(user_input["checked_on"]),
                    electricity,
                    gas or None,
                )
            except TariffError:
                errors["base"] = "invalid_prices"
            else:
                # Reload so the new prices are used and history is priced again.
                self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
                return self.async_create_entry(data=dict(self.config_entry.options))

        today = date.today()
        latest_from, latest = contract.periods.items[-1]
        e_now = latest.get("electricity") or {}
        g_now = latest.get("gas") or {}
        fields: dict[Any, Any] = {}
        for f in e_fields:
            fields[vol.Optional(f, description={"suggested_value": e_now.get(f)})] = PRICE_SELECTOR
        for f in g_fields:
            fields[vol.Optional(f"gas_{f}", description={"suggested_value": g_now.get(f)})] = PRICE_SELECTOR
        fields[vol.Required("valid_from", default=today.isoformat())] = selector.DateSelector()
        fields[vol.Required("checked_on", default=today.isoformat())] = selector.DateSelector()
        return self.async_show_form(
            step_id="prices",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={
                "name": contract.name,
                "latest_from": str(latest_from),
                "note": contract.note or "-",
                "sources": "\n".join(contract.sources) or "-",
            },
        )
