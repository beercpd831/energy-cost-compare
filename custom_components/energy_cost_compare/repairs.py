"""Repair flow for price proposals: accept or reject new contract prices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.util import dt as dt_util

from .coordinator import proposal_placeholders
from .tariffs import TariffError, accept_proposal, delete_proposal


class ProposalRepairFlow(RepairsFlow):
    """Shows the proposed prices; Accept writes them, Reject throws them away."""

    def __init__(self, entry_id: str, key: str) -> None:
        self._entry_id = entry_id
        self._key = key

    def _coordinator(self):
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return getattr(entry, "runtime_data", None) if entry else None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        coordinator = self._coordinator()
        if coordinator is None or self._key not in coordinator.proposals:
            return self.async_abort(reason="proposal_gone")
        proposal = coordinator.proposals[self._key]
        contract = coordinator.tariffs.contracts.get(str(proposal["contract_id"]))
        if contract is None:
            return self.async_abort(reason="proposal_gone")
        return self.async_show_menu(
            step_id="init",
            menu_options=["accept", "reject"],
            description_placeholders=proposal_placeholders(contract, proposal),
        )

    async def async_step_accept(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        coordinator = self._coordinator()
        if coordinator is None or self._key not in coordinator.proposals:
            return self.async_abort(reason="proposal_gone")
        proposal = coordinator.proposals[self._key]
        contract = coordinator.tariffs.contracts[str(proposal["contract_id"])]
        try:
            await self.hass.async_add_executor_job(
                accept_proposal, coordinator.tariff_folder, contract, proposal, dt_util.now().date()
            )
        except (TariffError, OSError):
            return self.async_abort(reason="proposal_invalid")
        # A reload prices the history again with the new prices.
        self.hass.config_entries.async_schedule_reload(self._entry_id)
        return self.async_create_entry(data={})

    async def async_step_reject(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        coordinator = self._coordinator()
        if coordinator is not None:
            await self.hass.async_add_executor_job(delete_proposal, coordinator.tariff_folder, self._key)
            coordinator.proposals.pop(self._key, None)
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    data = data or {}
    return ProposalRepairFlow(str(data.get("entry_id")), str(data.get("proposal")))
