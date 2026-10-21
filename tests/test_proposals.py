"""Tests for price proposals: storing, comparing and accepting them."""

import shutil
from datetime import date
from pathlib import Path

import pytest

from custom_components.energy_cost_compare.tariffs import (
    TariffError,
    accept_proposal,
    load_proposals,
    load_tariffs,
    proposal_changes,
    save_proposal,
    validate_proposal,
)

DATA = Path(__file__).parent.parent / "custom_components" / "energy_cost_compare" / "data"
VATTENFALL = "vattenfall_vast_1jaar"


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    shutil.copytree(DATA, tmp_path / "tariffs")
    return tmp_path / "tariffs"


def _proposal(**electricity) -> dict:
    return {"contract_id": VATTENFALL, "valid_from": "2026-11-01", "electricity": electricity,
            "sources": ["https://example.org/vattenfall"], "note": "From the offer"}


def test_changes_list_only_changed_values(folder: Path):
    contract = load_tariffs(folder).contracts[VATTENFALL]
    changes = proposal_changes(contract, _proposal(supply=0.19, feed_in=0.0772))
    assert changes == [("supply", 0.1846, 0.19)]


def test_validation():
    contract = load_tariffs(DATA).contracts[VATTENFALL]
    with pytest.raises(TariffError):
        validate_proposal(contract, _proposal(price=0.2))
    with pytest.raises(TariffError):
        validate_proposal(contract, _proposal(supply="cheap"))
    with pytest.raises(TariffError):
        validate_proposal(contract, _proposal(markup=0.01))
    with pytest.raises(TariffError):
        validate_proposal(contract, {"contract_id": VATTENFALL, "valid_from": "2026-11-01"})
    validate_proposal(contract, _proposal(supply=0.19))


def test_accept_adds_period_and_carries_forward(folder: Path):
    contract = load_tariffs(folder).contracts[VATTENFALL]
    proposal = _proposal(supply=0.19, feed_in_cost=0.10)
    key = save_proposal(folder, proposal)
    assert key in load_proposals(folder)

    carried = accept_proposal(folder, contract, proposal, date(2026, 11, 2))
    assert carried == ["2027-01-01"]
    assert load_proposals(folder) == {}

    updated = load_tariffs(folder).contracts[VATTENFALL]
    assert updated.checked_on == date(2026, 11, 2)
    assert updated.sources == ["https://example.org/vattenfall"]
    # History keeps the old price.
    assert updated.electricity(date(2026, 10, 15)).supply == pytest.approx(0.1846)
    nov = updated.electricity(date(2026, 11, 15))
    assert nov.supply == pytest.approx(0.19)
    assert nov.feed_in_cost == pytest.approx(0.10)
    assert nov.fixed_per_month == pytest.approx(6.19)
    assert updated.gas(date(2026, 11, 15))["supply"] == pytest.approx(0.9544)
    # 2027 had the same supply price, so it follows; its own feed-in costs stay.
    jan = updated.electricity(date(2027, 2, 1))
    assert jan.supply == pytest.approx(0.19)
    assert jan.feed_in_cost == pytest.approx(0.0443)


def test_accept_without_changes_only_marks_checked(folder: Path):
    contract = load_tariffs(folder).contracts[VATTENFALL]
    proposal = _proposal(supply=0.1846)
    save_proposal(folder, proposal)
    accept_proposal(folder, contract, proposal, date(2026, 11, 2))
    updated = load_tariffs(folder).contracts[VATTENFALL]
    assert len(updated.periods.items) == len(contract.periods.items)
    assert updated.checked_on == date(2026, 11, 2)


async def test_propose_service_and_repair_flow(recorder_mock, enable_custom_integrations, hass, tmp_path):
    hass.config.config_dir = str(tmp_path)  # keep tariff files out of the shared test config
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import issue_registry as ir
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.energy_cost_compare.const import DOMAIN
    from custom_components.energy_cost_compare.repairs import async_create_fix_flow

    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={
        "grid_import": ["sensor.grid_in"], "grid_export": ["sensor.grid_out"], "pv": ["sensor.pv"],
        "start_date": dt_util.now().date().isoformat(),
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    response = await hass.services.async_call(
        DOMAIN, "propose_prices",
        {"contract_id": VATTENFALL, "valid_from": "2026-11-01",
         "electricity": {"supply": 0.19}, "sources": ["https://example.org"]},
        blocking=True, return_response=True,
    )
    assert response["changes"] == [{"field": "supply", "current": 0.1846, "proposed": 0.19}]
    issue_id = f"proposal_{response['proposal']}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.is_fixable
    assert "0.1846 → 0.19" in issue.translation_placeholders["changes"]

    with pytest.raises(Exception):
        await hass.services.async_call(
            DOMAIN, "propose_prices", {"contract_id": VATTENFALL, "electricity": {"price": 1}},
            blocking=True, return_response=True,
        )

    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["type"] == FlowResultType.MENU
    result = await flow.async_step_accept()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)

    coordinator = hass.config_entries.async_get_entry(entry.entry_id).runtime_data
    assert coordinator.proposals == {}
    contract = coordinator.tariffs.contracts[VATTENFALL]
    assert contract.electricity(date(2026, 11, 2)).supply == pytest.approx(0.19)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


def test_tier_proposal(folder: Path):
    budget = "budget_energie_vast_1jaar"
    contract = load_tariffs(folder).contracts[budget]
    tiers = [{"from_kwh": 0, "per_day": 0.0}, {"from_kwh": 5, "per_day": 0.0579}]
    proposal = {"contract_id": budget, "valid_from": "2026-10-01",
                "electricity": {"feed_in_cost": 0.0, "feed_in_cost_tiers": tiers}}
    validate_proposal(contract, proposal)
    with pytest.raises(TariffError):
        validate_proposal(contract, {**proposal, "electricity": {"feed_in_cost_tiers": "lots"}})
    assert ("feed_in_cost_tiers", None, tiers) in proposal_changes(contract, proposal)
    accept_proposal(folder, contract, proposal, date(2026, 10, 21))
    rates = load_tariffs(folder).contracts[budget].electricity(date(2026, 10, 15))
    assert rates.feed_in_cost_tiers == [(0.0, 0.0), (5.0, 0.0579)]
    assert rates.feed_in_cost == 0.0


def test_supply_slots_proposal(folder: Path):
    eneco = "eneco_vast_1jaar"
    contract = load_tariffs(folder).contracts[eneco]
    slots = [{"from": "10:00", "to": "17:00", "supply": 0.15}, {"from": "22:00", "to": "05:00", "supply": 0.15}]
    proposal = {"contract_id": eneco, "valid_from": "2026-10-01",
                "electricity": {"supply": 0.22, "supply_slots": slots}}
    validate_proposal(contract, proposal)
    with pytest.raises(TariffError):
        validate_proposal(contract, {**proposal, "electricity": {"supply_slots": [{"from": "10:00"}]}})
    accept_proposal(folder, contract, proposal, date(2026, 10, 21))
    rates = load_tariffs(folder).contracts[eneco].electricity(date(2026, 10, 15))
    assert rates.supply_slots == [("10:00", "17:00", 0.15), ("22:00", "05:00", 0.15)]
