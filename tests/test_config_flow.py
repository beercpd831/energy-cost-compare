"""Config flow tests."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.energy_cost_compare.const import DOMAIN

BASE = {
    "grid_import": ["sensor.grid_in"],
    "grid_export": ["sensor.grid_out"],
    "pv": ["sensor.pv"],
    "start_date": "2026-10-07",
}


async def test_flow_requires_both_inverter_fields(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant, tmp_path
) -> None:
    hass.config.config_dir = str(tmp_path)  # keep tariff files out of the shared test config
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**BASE, "inv_in": ["sensor.inv_in"]}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "inverter_pair"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**BASE, "inv_in": ["sensor.inv_in"], "inv_out": ["sensor.inv_out"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["inv_out"] == ["sensor.inv_out"]
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_options_update_contract_prices(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant, tmp_path
) -> None:
    hass.config.config_dir = str(tmp_path)  # keep tariff files out of the shared test config
    from pathlib import Path

    import yaml
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data=BASE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "contract"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"contract": "eneco_vast_1jaar"})
    assert result["step_id"] == "prices"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"supply": 0.19, "feed_in": 0.07, "feed_in_cost": 0.05, "fixed_per_month": 7.0,
         "gas_supply": 0.9, "gas_fixed_per_month": 6.5,
         "valid_from": "2026-11-01", "checked_on": "2026-10-21"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)

    path = Path(hass.config.path("energy_cost_compare", "tariffs", "contracts", "eneco_vast_1jaar.yaml"))
    raw = yaml.safe_load(path.read_text())
    starts = [str(p["valid_from"]) for p in raw["periods"]]
    assert "2026-11-01" in starts and "2026-10-01" in starts
    assert str(raw["checked_on"]) == "2026-10-21"
    path.unlink()  # keep the shared test config clean for other tests
