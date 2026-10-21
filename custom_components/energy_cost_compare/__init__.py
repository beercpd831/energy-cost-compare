"""Energy cost compare: what PV and battery save compared with ordinary contracts."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    LEDGER_DIR,
    LEDGER_FILE,
    SERVICE_PROPOSE_PRICES,
    SERVICE_RECALCULATE,
    SERVICE_RELOAD_TARIFFS,
)
from .coordinator import LedgerCoordinator
from .ledger import Ledger
from .tariffs import TariffError, proposal_changes, save_proposal, validate_proposal

PLATFORMS = [Platform.SELECT, Platform.SENSOR]

RECALCULATE_SCHEMA = vol.Schema({vol.Optional("from_date"): cv.date})
PROPOSE_SCHEMA = vol.Schema({
    vol.Required("contract_id"): cv.string,
    vol.Optional("valid_from"): cv.date,
    vol.Optional("electricity"): dict,
    vol.Optional("gas"): dict,
    vol.Optional("sources"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("note"): cv.string,
    vol.Optional("confidence"): cv.string,
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    path = Path(hass.config.path(LEDGER_DIR, f"{entry.entry_id}_{LEDGER_FILE}"))
    ledger = Ledger(path)
    await hass.async_add_executor_job(ledger.open)

    coordinator = LedgerCoordinator(hass, entry, ledger)
    await coordinator.async_prepare()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Backfill can take a while; don't block startup on it.
    entry.async_create_background_task(
        hass, coordinator.async_start(), f"{DOMAIN}_start"
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    if not hass.services.has_service(DOMAIN, SERVICE_RECALCULATE):

        async def _recalculate(call: ServiceCall) -> None:
            from_date: date | None = call.data.get("from_date")
            for loaded in hass.config_entries.async_loaded_entries(DOMAIN):
                await loaded.runtime_data.async_recalculate(from_date)

        hass.services.async_register(
            DOMAIN, SERVICE_RECALCULATE, _recalculate, schema=RECALCULATE_SCHEMA
        )

        async def _reload_tariffs(call: ServiceCall) -> None:
            # A reload also adds or removes contract sensors for added or removed files.
            for loaded in hass.config_entries.async_loaded_entries(DOMAIN):
                await hass.config_entries.async_reload(loaded.entry_id)

        hass.services.async_register(DOMAIN, SERVICE_RELOAD_TARIFFS, _reload_tariffs)

        async def _propose_prices(call: ServiceCall) -> ServiceResponse:
            return await _async_propose(hass, call)

        hass.services.async_register(
            DOMAIN, SERVICE_PROPOSE_PRICES, _propose_prices, schema=PROPOSE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
    return True


async def _async_propose(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Store proposed prices; they are applied only when the user accepts the repair."""
    loaded = hass.config_entries.async_loaded_entries(DOMAIN)
    if not loaded:
        raise ServiceValidationError("Energy cost compare is not loaded")
    coordinator: LedgerCoordinator = loaded[0].runtime_data
    contract_id = call.data["contract_id"]
    contract = coordinator.tariffs.contracts.get(contract_id) if coordinator.tariffs else None
    if contract is None:
        raise ServiceValidationError(f"Unknown contract '{contract_id}'")
    today = dt_util.now().date()
    valid_from = call.data.get("valid_from") or today
    try:
        validate_proposal(contract, {**call.data, "valid_from": valid_from.isoformat()})
    except TariffError as err:
        raise ServiceValidationError(str(err)) from err
    proposal = {
        "contract_id": contract_id,
        "valid_from": valid_from.isoformat(),
        "proposed_on": today.isoformat(),
        "electricity": {k: float(v) for k, v in (call.data.get("electricity") or {}).items()},
        "gas": {k: float(v) for k, v in (call.data.get("gas") or {}).items()},
        "sources": call.data.get("sources") or [],
        "note": call.data.get("note") or "",
        "confidence": call.data.get("confidence") or "",
    }
    key = await hass.async_add_executor_job(save_proposal, coordinator.tariff_folder, proposal)
    coordinator.proposals[key] = proposal
    coordinator.update_proposal_issues()
    return {
        "proposal": key,
        "changes": [
            {"field": f, "current": now, "proposed": new}
            for f, now, new in proposal_changes(contract, proposal)
        ],
    }


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: LedgerCoordinator = entry.runtime_data
        coordinator.async_stop()
        await hass.async_add_executor_job(coordinator.ledger.close)
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Changed counters or start date: rebuild the ledger with the new settings."""
    await hass.config_entries.async_reload(entry.entry_id)
    await entry.runtime_data.async_recalculate()
