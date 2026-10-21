"""Shared base for the integration's entities."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, NAME, SIGNAL_LEDGER_UPDATED
from .coordinator import LedgerCoordinator


class LedgerEntity(Entity):
    """Updates whenever the coordinator publishes a new summary or selection."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: LedgerCoordinator, key: str) -> None:
        self.coordinator = coordinator
        entry_id = coordinator.entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)}, name=NAME)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_LEDGER_UPDATED, self._handle_update)
        )

    @callback
    def _handle_update(self, entry_id: str) -> None:
        if entry_id == self.coordinator.entry.entry_id:
            self.async_write_ha_state()
