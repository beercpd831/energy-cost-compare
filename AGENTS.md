# Handover for AI assistants

Read this before changing the integration. The README explains what it does for users;
this file explains how it is built, the rules the data follows, and the traps found so far.

## What it is

A Home Assistant custom integration (domain `energy_cost_compare`, installed through HACS
from this repository) that prices the household's measured energy, quarter by quarter,
under Dutch energy contracts. It compares three situations (A: no PV and no battery,
B: PV only, simulated, C: PV and battery, measured), compares contracts (fixed and
dynamic), replays the end of salderen in 2027 as a scenario, and compares heating with a
heat pump against a gas boiler.

## Layout

| File | Role |
| --- | --- |
| `source.py` | Reads the recorder's 5-minute statistics (hourly beyond ~9 days, split over 4 quarters) for the configured counters and the market price sensor. |
| `calc.py` | `Interval` (one quarter), aggregation, situations A/B/C, house load and battery losses. |
| `ledger.py` | SQLite file `/config/energy_cost_compare/<entry_id>_ledger.db` with all quarters. Rebuildable from statistics, but only at hourly resolution beyond ~9 days. |
| `tariffs.py` | Loads and validates `taxes.yaml`, `grid.yaml`, `contracts/*.yaml`, `scenarios/*.yaml`; price proposals (`validate_proposal`, `save_proposal`, `accept_proposal`). |
| `cost.py` | Cost engine `price_situation()`: per quarter prices, calendar-year-to-date totals, the bill per day as the change of the year-to-date bill. |
| `gas.py` | Heat pump against gas boiler. |
| `coordinator.py` | Fills the ledger, prices every contract × situation (and the selected scenario), builds `Summary`, creates the repair issues for price proposals. |
| `sensor.py`, `select.py` | Entities. Cost sensors per period (today, month, year, since start), saving sensors, one sensor per contract, scenario sensors, diagnostics. |
| `repairs.py` | Accept/Reject flow for a price proposal. |
| `__init__.py` | Setup and the services `recalculate` and `propose_prices`. |
| `config_flow.py` | Setup (counters, price sensor, start date) and options (contract prices). |
| `data/` | Shipped default tariff files. |
| `scripts/gen_strings.py` | Generates `strings.json` and the `en`/`nl` translations. Edit the script, not the JSON. |

## Rules of the data

- **All amounts in tariff files are euro excluding VAT.** Electricity supply prices
  exclude energy tax. VAT is applied over the whole bill.
- Suppliers quote supply, fixed costs and feed-in costs **including** VAT: divide by 1.21.
- Suppliers pay the feed-in compensation **without** VAT, but the engine adds VAT over the
  whole bill. So store `feed_in = amount paid / 1.21`, so that the bill credits exactly the
  amount paid. (Verified against a supplier's yearly totals.)
- Fixed contracts: `supply`, `feed_in` (pay for the yearly surplus), `feed_in_cost` (per
  kWh), `feed_in_cost_tiers` (per day by yearly feed-in), `supply_slots` (time-of-use
  rate, local `"HH:MM"`, quote the times in YAML), `fixed_per_month`.
- Dynamic contracts: price = market (EPEX excl. VAT) + `markup`; feed-in = market +
  `feed_in_markup` (+ `feed_in_bonus` × market in daylight, not below
  `feed_in_bonus_min_price`).
- Price periods (`valid_from`) are kept forever so old days keep their old prices. A
  later period inherits nothing: give every key it needs (for example
  `feed_in_cost_tiers: []` in the 2027 period when the tiers stop).
- Salderen is computed on calendar-year-to-date totals: netted kWh = min(import, export).
  Fixed contracts net standard-rate import first, then `supply_slots` import. Dynamic
  contracts settle every quarter at market price; netting only returns the energy tax.
  The year starts at the integration's start date (no data from earlier in the year).
- `DayCost.settled` is the "supplier app" view (every kWh at its quarter's price incl.
  energy tax and VAT, fed-in kWh credited with the tax while netting applies). It ignores
  the yearly netting balance and fixed costs, so it differs from the bill by design.
- 2027 onwards: salderen ends (`netting: false` in `taxes.yaml`); the legal minimum
  feed-in compensation for 2027–2030 is 50% of the supply price.

## Tariff files on a running system

- On setup the integration copies `data/` to `/config/energy_cost_compare/tariffs/`
  **only for files that don't exist yet**. The user's files are never overwritten, so a
  changed price in `data/` does not reach an existing install.
- To change prices on a running system: call the `energy_cost_compare.propose_prices`
  service (one call per contract per `valid_from`; it returns the changes). The user
  accepts or rejects each proposal under Settings → Repairs. Accepting writes the
  contract file and carries a changed value into later periods that had the old value.
- A new contract does reach existing installs: ship a new file in `data/contracts/` and
  bump the version.
- The HA API cannot read `/config/energy_cost_compare/`. Contract sensors expose the
  current periods in the `price_periods` attribute instead (not recorded).

## Entities

- Entity ids come from the Dutch translations, e.g.
  `sensor.energy_cost_compare_kosten_met_pv_en_batterij_vandaag`,
  `sensor.energy_cost_compare_<contract>_kosten_van_werkelijk_verbruik`,
  `select.energy_cost_compare_huidige_leverancier`.
- When the integration's device is assigned to an area, Home Assistant prefixes **new**
  entity ids with the area name. Dashboards should match contract sensors by pattern
  (`energy_cost_compare_.+_kosten_van_werkelijk_verbruik$`) rather than by a fixed list.
- Cost sensors carry `grid_import_kwh`, `grid_export_kwh` and the supplier-app view
  (`import_eur`, `import_price`, `export_eur`, `export_price`, `energy_net_eur`).

## Development

- Tests: `pytest` (uses `pytest-homeassistant-custom-component`). Every test that starts
  Home Assistant sets `hass.config.config_dir = str(tmp_path)`, otherwise tariff files
  land in the shared test config.
- Tests count the shipped contracts; update them when adding one.
- After changing user-facing strings: `python scripts/gen_strings.py custom_components/energy_cost_compare`.
- Release: bump `version` in `manifest.json`, commit to `main`, push. HACS follows the
  commits on `main` (no release tags); the user updates in HACS and restarts.
- Keep this repository free of personal data (names, addresses, entity ids of a specific
  home, personal offers). It is public.

## Known gaps

- Missing market prices (price sensor down) are estimated from the same quarter on the
  nearest days (±7); the count is shown in a diagnostic sensor.
- Gas prices for contracts are only used for the heat pump comparison.
- 2027 grid tariffs and energy tax are assumptions until they are published.
