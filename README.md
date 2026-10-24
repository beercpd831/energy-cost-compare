# Energy cost compare

A Home Assistant integration that shows what solar panels and a home battery save,
compared with ordinary fixed-price and dynamic Dutch energy contracts.

Status: in use. Energy ledger, contracts (fixed, time-of-use and dynamic), cost engine,
2027 scenarios, price proposals and the gas comparison are built. Notes for AI
assistants working on the code are in [AGENTS.md](AGENTS.md).

## How it works

The integration reads the recorder's statistics for the energy counters you choose, and
stores 15-minute intervals in its own SQLite file
(`/config/energy_cost_compare/<entry>_ledger.db`). From each interval it derives:

| Situation | Grid import | Grid export |
| --- | --- | --- |
| A. No PV, no battery | house load | 0 |
| B. PV only (simulated) | max(0, house load − PV) | max(0, PV − house load) |
| C. PV + battery (measured) | grid import counter | grid export counter |

House load = grid import − grid export + PV − (AC into battery inverters − AC out of
them). The battery system's own consumption and conversion losses are therefore not part
of the house load; they're reported separately.

History older than about 9 days comes from hourly statistics and is spread evenly over
the four quarters of each hour.

## Where everything is stored

Everything lives in the Home Assistant configuration folder (`/config`, also reachable
through the Samba share or File editor app as `config`):

| What | Where | Notes |
| --- | --- | --- |
| Integration code | `/config/custom_components/energy_cost_compare/` | Installed and updated by HACS. Don't edit: an update overwrites it. |
| Quarter-hour ledger | `/config/energy_cost_compare/<entry_id>_ledger.db` | SQLite. Import, export, PV, battery, heat pump and market price per 15 minutes, and the daily gas market price. The only place quarter-hour data is kept for longer than ~9 days. |
| Tariff files | `/config/energy_cost_compare/tariffs/` | `taxes.yaml`, `grid.yaml`, `contracts/*.yaml`, `scenarios/*.yaml`. Copied from the defaults on first start, then yours: updates never overwrite them. |
| Price proposals | `/config/energy_cost_compare/tariffs/proposals/` | Waiting proposals; removed when accepted or rejected. |
| Settings (counters, price sensor, start date) | `/config/.storage/core.config_entries` | Change them under Settings → Devices & services → Energy cost compare → Configure, never in the file. |
| Proposal notices | `/config/.storage/repairs.issue_registry` | Shown under Settings → Repairs. |
| Sensor history and statistics | `/config/home-assistant_v2.db` | Home Assistant's recorder database. Long-term statistics (the daily bars in graphs) are kept indefinitely. |
| Dashboards | `/config/.storage/lovelace*` | Edit in the dashboard editor. |

A full Home Assistant backup includes all of it (`/config` plus the database). If the
ledger is lost, the integration rebuilds it from the recorder's statistics: 5-minute
detail for the last ~9 days, hourly values spread over four quarters before that.

## Installation (HACS)

1. HACS → three dots → Custom repositories → add this repository as type *Integration*.
2. Install *Energy cost compare* and restart Home Assistant.
3. Settings → Devices & services → Add integration → *Energy cost compare*.
   The fields are pre-filled for a Victron system read through Victron MQTT.

## Entities (phase 1)

For *today* and *since start*: house load, PV-only grid import and export, battery
system net AC energy, and battery losses (when DC counters are configured). Plus
diagnostics: the last recorded interval and the number of incomplete intervals.

Service `energy_cost_compare.recalculate` (optional `from_date`) rereads history.

## Contracts, taxes and scenarios

On first start the integration copies its default tariff files to
`/config/energy_cost_compare/tariffs/`. Your edits there are never overwritten.

- `taxes.yaml`: energy tax, heffingskorting, VAT and whether salderen applies, per period.
- `grid.yaml`: grid operator costs, with optional time slots for a future
  time-dependent grid tariff. Shipped with Liander 3x25A (smart meter) 2026.
- `contracts/*.yaml`: one file per contract (`kind: fixed` or `dynamic`), with price
  periods (`valid_from`). All amounts are in euro **excluding VAT**; electricity supply
  prices exclude energy tax. Dynamic contracts are priced as market price + markup.
  Feed-in costs can be per kWh (`feed_in_cost`) or per day by yearly feed-in
  (`feed_in_cost_tiers`, a list of `from_kwh` / `per_day`), as Budget Thuis (formerly Budget Energie) charges until
  2027. The tier follows the year's feed-in up to the import, extrapolated to a full year.
  A fixed contract with a time-of-use rate lists it in `supply_slots` (`from`, `to` as
  quoted `"HH:MM"` local time, and `supply`); `supply` applies outside the slots. Netting
  takes the standard-rate import first, then the slots.
- `scenarios/*.yaml`: rule and price changes replayed on your recorded data, for example
  `2027_no_netting` (no salderen, 2027 feed-in terms).

Prices can be checked and updated under Settings → Devices & services → Energy cost
compare → Configure → *Check and update contract prices*. A new start date adds a
period and keeps the old prices for history. After 60 days without a check, a repair
notice asks for one. After editing files by hand, call `energy_cost_compare.reload_tariffs`.

### Price proposals

Instead of typing prices in, anything can propose them with the service
`energy_cost_compare.propose_prices` (contract id, start date, electricity and gas
values, sources, note). Nothing changes until you accept it: each proposal shows up as
a repair notice listing old → new values and the sources, with *Accept* and *Reject*.
Accepting adds a price period from the proposal's start date (older days keep their
prices); a later period, such as the 2027 terms, takes the new value only where it had
the same value as before. A proposal without changes only records the check date.
A proposal can also carry a new `name` and `supplier`, for when a supplier renames itself;
the contract id (and so the entity ids) stays the same.

The shipped prices are **estimates from comparison sites (21 October 2026)**. Each file
lists its sources; check them against a personal offer.

## How costs are calculated

Per 15-minute interval, the import and export of each situation are priced. Salderen
works on yearly totals, so the engine keeps calendar-year-to-date totals and each day's
cost is the change in the year-to-date bill. Fixed contracts net at the supply price
and pay the feed-in rate on the excess; dynamic contracts settle every quarter at the
market price, and netting only returns the energy tax. Fixed costs, grid costs and the
heffingskorting are spread per day. VAT is applied over the whole bill.

## Entities

- Selects: baseline contract (average of fixed contracts, or one), actual supplier,
  future scenario.
- Costs per period (today, this month, this year, since start): baseline without PV
  and battery (A), PV only (B), PV and battery (C), and the savings A−B, A−C and B−C.
  Saving sensors carry the kWh differences and the same comparison on one contract.
  Cost sensors also carry import and export the way a supplier app shows them
  (`import_eur`, `import_price`, `export_eur`, `export_price`, `energy_net_eur`): every
  kWh at its quarter's price incl. energy tax and VAT, fed-in kWh credited with the tax
  while salderen applies, without fixed costs and without the yearly netting balance.
  `bill` holds the period's bill per component (`supply`, `feed_in`, `feed_in_cost`,
  `energy_tax`, `fixed`, `grid`, `tax_reduction`, each incl. VAT, and `vat`).
- Scenario sensors: the same costs and savings since the start, replayed under the
  selected scenario, with a rough annual estimate.
- One sensor per contract: what your actual usage would have cost, with details.
- Gas comparison (when heat counters are set): heating with a gas boiler against the
  heat pump, under the current supplier (or the average of the fixed contracts when that
  contract has no gas price). Dynamic contracts pay the daily gas market price plus their
  markup; the price comes from Home Assistant's EnergyZero integration (no account
  needed), so add that integration to compare them. Attributes split the gas costs into
  `gas_variable_eur` and `gas_fixed_eur` (supplier and grid connection), with the
  average `gas_price_m3` incl. tax and VAT.
- Energy: house load, PV-only import/export, battery net AC energy and losses.

## Development

`scripts/gen_strings.py custom_components/energy_cost_compare` regenerates `strings.json`
and the English and Dutch translations. Tests: `pytest`.
