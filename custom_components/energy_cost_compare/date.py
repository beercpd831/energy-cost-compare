"""Date: the day the look-back cost sensors show."""

from __future__ import annotations

from datetime import date

from homeassistant.components.date import DateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import LedgerCoordinator
from .entity import LedgerEntity


class LookbackDate(LedgerEntity, DateEntity):
    """Follows today until another day is picked; picking today follows today again."""

    _attr_translation_key = "lookback"

    def __init__(self, coordinator: LedgerCoordinator) -> None:
        super().__init__(coordinator, "lookback_date")

    @property
    def native_value(self) -> date:
        return self.coordinator.lookback_date

    async def async_set_value(self, value: date) -> None:
        await self.coordinator.async_set_lookback(value)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([LookbackDate(entry.runtime_data)])
