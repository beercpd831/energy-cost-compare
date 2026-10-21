"""Selects: the baseline contract, the actual supplier, and the future scenario."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import BASELINE_AVERAGE
from .coordinator import LedgerCoordinator
from .entity import LedgerEntity


class ContractSelect(LedgerEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: LedgerCoordinator, key: str) -> None:
        super().__init__(coordinator, f"select_{key}")
        self._attr_translation_key = key
        self.key = key

    @property
    def options(self) -> list[str]:
        contracts = self.coordinator.contract_options
        if self.key == "baseline":
            return [BASELINE_AVERAGE, *self.coordinator.fixed_contracts]
        if self.key == "scenario":
            return self.coordinator.scenario_options
        return contracts

    @property
    def current_option(self) -> str | None:
        value = getattr(self.coordinator, self.key)
        return value if value in self.options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_select(self.key, option)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: LedgerCoordinator = entry.runtime_data
    async_add_entities(
        ContractSelect(coordinator, key) for key in ("baseline", "actual", "scenario")
    )
