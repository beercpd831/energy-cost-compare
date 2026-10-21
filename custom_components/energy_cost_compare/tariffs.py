"""Contracts, taxes and grid tariffs: loading, validation and lookup by date.

Free of Home Assistant imports. Every value is in euro excluding VAT; VAT is applied
once, over the whole bill, by the cost engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

FIXED = "fixed"
DYNAMIC = "dynamic"


class TariffError(ValueError):
    """A tariff file is missing a field or has an invalid value."""


def _date(value: Any, where: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as err:
        raise TariffError(f"{where}: '{value}' is not a date (YYYY-MM-DD)") from err


def _num(data: dict, key: str, where: str, default: float | None = None) -> float | None:
    value = data.get(key, default)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as err:
        raise TariffError(f"{where}: {key} must be a number") from err


def _tiers(raw: Any, where: str) -> list[tuple[float, float]]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise TariffError(f"{where}: feed_in_cost_tiers must be a list")
    tiers = []
    for tier in raw:
        if not isinstance(tier, dict):
            raise TariffError(f"{where}: each feed-in cost tier needs from_kwh and per_day")
        tiers.append((_num(tier, "from_kwh", where, 0.0), _num(tier, "per_day", where, 0.0)))
    return sorted(tiers)


def _hhmm(value: Any, where: str) -> str:
    text = str(value)
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", text):
        raise TariffError(f"{where}: '{value}' is not a time (HH:MM)")
    return text


def _supply_slots(raw: Any, where: str) -> list[tuple[str, str, float]]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise TariffError(f"{where}: supply_slots must be a list")
    slots = []
    for slot in raw:
        if not isinstance(slot, dict) or "supply" not in slot:
            raise TariffError(f"{where}: each supply slot needs from, to and supply")
        slots.append((_hhmm(slot.get("from"), where), _hhmm(slot.get("to"), where), _num(slot, "supply", where)))
    return slots


@dataclass
class Periods:
    """Values that change over time: a list of (valid_from, values) sorted by date."""

    items: list[tuple[date, dict[str, Any]]] = field(default_factory=list)

    @classmethod
    def parse(cls, raw: list[dict] | None, where: str) -> Periods:
        if not raw:
            raise TariffError(f"{where}: at least one period with valid_from is needed")
        items = []
        for i, period in enumerate(raw):
            if "valid_from" not in period:
                raise TariffError(f"{where}, period {i + 1}: valid_from is missing")
            values = {k: v for k, v in period.items() if k != "valid_from"}
            items.append((_date(period["valid_from"], where), values))
        items.sort(key=lambda item: item[0])
        return cls(items)

    def at(self, day: date) -> dict[str, Any] | None:
        """Values valid on a day, or None before the first period."""
        found = None
        for valid_from, values in self.items:
            if valid_from <= day:
                found = values
            else:
                break
        return found

    @property
    def first(self) -> date:
        return self.items[0][0]


@dataclass
class ElectricityRates:
    supply: float | None  # fixed EUR/kWh, excl. energy tax
    markup: float  # dynamic: added to the market price
    feed_in: float | None  # fixed EUR/kWh for exported kWh not netted
    feed_in_markup: float  # dynamic: added to the market price for export (may be negative)
    feed_in_cost: float  # EUR per exported kWh charged by the supplier
    fixed_per_month: float
    # dynamic: extra share of the market price for export between sunrise and sunset
    # (Zonneplan's zonnebonus: 0.10), when the market price is at least
    # feed_in_bonus_min_price (Zonneplan: none below -2 cent incl. VAT)
    feed_in_bonus: float = 0.0
    feed_in_bonus_min_price: float = 0.0
    # Feed-in costs per day, by the year's counted feed-in (Budget Energie until 2027):
    # (from_kwh, per_day) sorted by from_kwh. Counted feed-in is the export up to the
    # import, so a surplus over the year doesn't count.
    feed_in_cost_tiers: list[tuple[float, float]] = field(default_factory=list)
    # Fixed contracts with a time-of-use rate (Eneco's voordeeltarief): (from, to, supply)
    # in local HH:MM; a slot may run past midnight. Outside the slots supply applies.
    supply_slots: list[tuple[str, str, float]] = field(default_factory=list)


@dataclass
class Contract:
    id: str
    name: str
    supplier: str
    kind: str
    checked_on: date
    sources: list[str]
    note: str
    periods: Periods

    def electricity(self, day: date) -> ElectricityRates | None:
        values = self.periods.at(day)
        if values is None:
            return None
        e = values.get("electricity") or {}
        where = f"{self.id} electricity"
        return ElectricityRates(
            supply=_num(e, "supply", where),
            markup=_num(e, "markup", where, 0.0),
            feed_in=_num(e, "feed_in", where),
            feed_in_markup=_num(e, "feed_in_markup", where, 0.0),
            feed_in_cost=_num(e, "feed_in_cost", where, 0.0),
            fixed_per_month=_num(e, "fixed_per_month", where, 0.0),
            feed_in_bonus=_num(e, "feed_in_bonus", where, 0.0),
            feed_in_bonus_min_price=_num(e, "feed_in_bonus_min_price", where, 0.0),
            feed_in_cost_tiers=_tiers(e.get("feed_in_cost_tiers"), where),
            supply_slots=_supply_slots(e.get("supply_slots"), where),
        )

    def gas(self, day: date) -> dict[str, float] | None:
        values = self.periods.at(day)
        return None if values is None else values.get("gas")

    @classmethod
    def parse(cls, raw: dict, source: str) -> Contract:
        for key in ("id", "name", "supplier", "kind", "checked_on", "periods"):
            if key not in raw:
                raise TariffError(f"{source}: '{key}' is missing")
        if raw["kind"] not in (FIXED, DYNAMIC):
            raise TariffError(f"{source}: kind must be '{FIXED}' or '{DYNAMIC}'")
        contract = cls(
            id=str(raw["id"]),
            name=str(raw["name"]),
            supplier=str(raw["supplier"]),
            kind=raw["kind"],
            checked_on=_date(raw["checked_on"], source),
            sources=list(raw.get("sources") or []),
            note=str(raw.get("note") or ""),
            periods=Periods.parse(raw["periods"], source),
        )
        for valid_from, _ in contract.periods.items:
            rates = contract.electricity(valid_from)
            if contract.kind == FIXED and rates.supply is None:
                raise TariffError(f"{source}: a fixed contract needs electricity.supply")
        return contract


@dataclass
class Taxes:
    periods: Periods

    def at(self, day: date) -> dict[str, float]:
        values = self.periods.at(day)
        if values is None:
            raise TariffError(f"taxes: no period covers {day}")
        return values


@dataclass
class GridTariff:
    operator: str
    periods: Periods

    def at(self, day: date) -> dict[str, Any]:
        return self.periods.at(day) or {}


@dataclass
class TariffSet:
    contracts: dict[str, Contract]
    taxes: Taxes
    grid: GridTariff
    errors: list[str]
    scenarios: dict[str, Any] = field(default_factory=dict)  # id -> cost.Scenario


SCENARIO_FACTORS = ("supply_factor", "market_factor", "tax_factor", "grid_factor", "feed_in_factor")


def parse_scenario(raw: dict, source: str):
    """Build a cost.Scenario from a scenario file."""
    from .cost import Scenario  # cost imports this module

    for key in ("id", "name"):
        if key not in raw:
            raise TariffError(f"{source}: '{key}' is missing")
    netting = raw.get("netting")
    if netting is not None and not isinstance(netting, bool):
        raise TariffError(f"{source}: netting must be true, false or empty")
    kwargs: dict[str, Any] = {
        "id": str(raw["id"]),
        "name": str(raw["name"]),
        "netting": netting,
        "note": str(raw.get("note") or ""),
    }
    if raw.get("tariff_date"):
        kwargs["tariff_date"] = _date(raw["tariff_date"], source)
    for key in SCENARIO_FACTORS:
        kwargs[key] = _num(raw, key, source, 1.0)
    if raw.get("grid_slots") is not None:
        slots = raw["grid_slots"]
        if not isinstance(slots, list) or any(not {"from", "to"} <= set(s) for s in slots):
            raise TariffError(f"{source}: grid_slots needs entries with from, to and per_kwh")
        kwargs["grid_slots"] = slots
    return Scenario(**kwargs)


def load_tariffs(folder: Path) -> TariffSet:
    """Load taxes.yaml, grid.yaml and contracts/*.yaml. A broken contract is skipped
    and reported in errors; broken taxes or grid files raise, since nothing can be priced."""
    taxes_raw = yaml.safe_load((folder / "taxes.yaml").read_text(encoding="utf-8")) or {}
    grid_raw = yaml.safe_load((folder / "grid.yaml").read_text(encoding="utf-8")) or {}
    taxes = Taxes(Periods.parse(taxes_raw.get("periods"), "taxes.yaml"))
    grid = GridTariff(
        operator=str(grid_raw.get("operator", "")),
        periods=Periods.parse(grid_raw.get("periods"), "grid.yaml"),
    )
    contracts: dict[str, Contract] = {}
    errors: list[str] = []
    for path in sorted((folder / "contracts").glob("*.yaml")):
        try:
            contract = Contract.parse(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, path.name)
        except (TariffError, yaml.YAMLError) as err:
            errors.append(str(err))
            continue
        if contract.id in contracts:
            errors.append(f"{path.name}: id '{contract.id}' is used twice")
            continue
        contracts[contract.id] = contract
    scenarios: dict[str, Any] = {}
    for path in sorted((folder / "scenarios").glob("*.yaml")):
        try:
            scenario = parse_scenario(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, path.name)
        except (TariffError, yaml.YAMLError) as err:
            errors.append(str(err))
            continue
        scenarios[scenario.id] = scenario
    return TariffSet(contracts, taxes, grid, errors, scenarios)


def install_defaults(defaults: Path, folder: Path) -> list[str]:
    """Copy shipped tariff files that don't exist yet; never overwrite the user's edits."""
    copied = []
    for source in sorted(defaults.rglob("*.yaml")):
        target = folder / source.relative_to(defaults)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            copied.append(str(target.relative_to(folder)))
    return copied


def save_period(
    folder: Path,
    contract_id: str,
    valid_from: date,
    checked_on: date,
    electricity: dict[str, float],
    gas: dict[str, float] | None,
) -> None:
    """Add a price period to a contract file, or replace the one with the same start date.

    Older periods are kept, so history stays priced at the prices of the time.
    """
    for path in (folder / "contracts").glob("*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if str(raw.get("id")) != contract_id:
            continue
        periods = [p for p in raw.get("periods") or [] if _date(p["valid_from"], path.name) != valid_from]
        period: dict[str, Any] = {"valid_from": valid_from.isoformat(), "electricity": electricity}
        if gas:
            period["gas"] = gas
        periods.append(period)
        periods.sort(key=lambda p: str(p["valid_from"]))
        raw["periods"] = periods
        raw["checked_on"] = checked_on.isoformat()
        Contract.parse(raw, path.name)  # refuse to write something that won't load
        header = "# All amounts in euro EXCLUDING VAT. Edited from Home Assistant.\n"
        path.write_text(header + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return
    raise TariffError(f"No contract with id '{contract_id}'")


# Price proposals -----------------------------------------------------------------
# A proposal is new prices for one contract from one date, waiting for the user to
# accept or reject them. Anything can send one through the propose_prices service;
# nothing is applied without the user's tap.

PROPOSAL_DIR = "proposals"
ELECTRICITY_FIELDS = (
    "supply", "markup", "feed_in", "feed_in_markup", "feed_in_cost", "fixed_per_month", "feed_in_bonus",
    "feed_in_bonus_min_price", "feed_in_cost_tiers", "supply_slots",
)
LIST_FIELDS = ("feed_in_cost_tiers", "supply_slots")
GAS_FIELDS = ("supply", "markup", "fixed_per_month")


def proposal_key(contract_id: str, valid_from: date | str) -> str:
    return f"{contract_id}_{str(valid_from).replace('-', '')}"


def validate_proposal(contract: Contract, proposal: dict[str, Any]) -> None:
    """Refuse unknown fields and values that aren't numbers."""
    _date(proposal.get("valid_from"), "proposal")
    for part, allowed in (("electricity", ELECTRICITY_FIELDS), ("gas", GAS_FIELDS)):
        values = proposal.get(part) or {}
        if not isinstance(values, dict):
            raise TariffError(f"proposal: {part} must be a mapping")
        for key, value in values.items():
            if key not in allowed:
                raise TariffError(f"proposal: unknown {part} field '{key}'")
            if key == "feed_in_cost_tiers":
                _tiers(value, "proposal electricity")
                continue
            if key == "supply_slots":
                _supply_slots(value, "proposal electricity")
                continue
            if value is None or isinstance(value, bool):
                raise TariffError(f"proposal {part}: {key} must be a number")
            _num(values, key, f"proposal {part}")
    if not (proposal.get("electricity") or proposal.get("gas")):
        raise TariffError("proposal: no prices given")
    if contract.kind == FIXED and "markup" in (proposal.get("electricity") or {}):
        raise TariffError("proposal: a fixed contract has a supply price, not a markup")


def save_proposal(folder: Path, proposal: dict[str, Any]) -> str:
    """Store a proposal, replacing an older one for the same contract and date."""
    key = proposal_key(proposal["contract_id"], proposal["valid_from"])
    target = folder / PROPOSAL_DIR / f"{key}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(proposal, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return key


def load_proposals(folder: Path) -> dict[str, dict[str, Any]]:
    proposals = {}
    for path in sorted((folder / PROPOSAL_DIR).glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if raw.get("contract_id") and raw.get("valid_from"):
            proposals[path.stem] = raw
    return proposals


def delete_proposal(folder: Path, key: str) -> None:
    (folder / PROPOSAL_DIR / f"{key}.yaml").unlink(missing_ok=True)


def current_values(contract: Contract, day: date) -> tuple[dict[str, Any], dict[str, Any]]:
    """The electricity and gas values of the period valid on a day."""
    values = contract.periods.at(day) or {}
    return dict(values.get("electricity") or {}), dict(values.get("gas") or {})


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, list) or isinstance(b, list):
        return a == b
    return abs(float(a) - float(b)) < 1e-9


def proposal_changes(contract: Contract, proposal: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """(field, current, proposed) for every value the proposal changes."""
    day = _date(proposal["valid_from"], "proposal")
    electricity, gas = current_values(contract, day)
    changes = []
    for prefix, now, new in (("", electricity, proposal.get("electricity") or {}),
                             ("gas_", gas, proposal.get("gas") or {})):
        for key, value in new.items():
            if not _same(now.get(key), value):
                changes.append((prefix + key, now.get(key), value))
    return changes


def accept_proposal(folder: Path, contract: Contract, proposal: dict[str, Any], today: date) -> list[str]:
    """Apply a proposal: a new price period, or only a new check date when nothing changed.

    A later period (for example the 2027 terms) gets a changed value too when it had
    the same value as the period being replaced; values it sets differently are kept.
    Returns the start dates of the later periods that were updated.
    """
    valid_from = _date(proposal["valid_from"], "proposal")
    carried: list[str] = []
    if proposal_changes(contract, proposal):
        path = _contract_path(folder, contract.id)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        old_e, old_g = current_values(contract, valid_from)
        new_parts = {"electricity": proposal.get("electricity") or {}, "gas": proposal.get("gas") or {}}
        old_parts = {"electricity": old_e, "gas": old_g}
        periods = [p for p in raw.get("periods") or [] if _date(p["valid_from"], path.name) != valid_from]
        for period in periods:
            if _date(period["valid_from"], path.name) <= valid_from:
                continue
            touched = False
            for part, new in new_parts.items():
                later = period.get(part)
                if not later:
                    continue
                for key, value in new.items():
                    if key in later and _same(later[key], old_parts[part].get(key)):
                        later[key] = value
                        touched = True
            if touched:
                carried.append(str(period["valid_from"]))
        new_period: dict[str, Any] = {"valid_from": valid_from.isoformat(),
                                      "electricity": {**old_e, **new_parts["electricity"]}}
        if old_g or new_parts["gas"]:
            new_period["gas"] = {**old_g, **new_parts["gas"]}
        periods.append(new_period)
        periods.sort(key=lambda p: str(p["valid_from"]))
        raw["periods"] = periods
        raw["checked_on"] = today.isoformat()
        _apply_sources(raw, proposal)
        _write_contract(path, raw)
    else:
        path = _contract_path(folder, contract.id)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw["checked_on"] = today.isoformat()
        _apply_sources(raw, proposal)
        _write_contract(path, raw)
    delete_proposal(folder, proposal_key(contract.id, valid_from))
    return carried


def _apply_sources(raw: dict[str, Any], proposal: dict[str, Any]) -> None:
    if proposal.get("sources"):
        raw["sources"] = [str(s) for s in proposal["sources"]]
    if proposal.get("note"):
        raw["note"] = str(proposal["note"])
    if proposal.get("confidence"):
        raw["confidence"] = str(proposal["confidence"])


def _write_contract(path: Path, raw: dict[str, Any]) -> None:
    Contract.parse(raw, path.name)  # refuse to write something that won't load
    header = "# All amounts in euro EXCLUDING VAT. Edited from Home Assistant.\n"
    path.write_text(header + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _contract_path(folder: Path, contract_id: str) -> Path:
    for path in (folder / "contracts").glob("*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if str(raw.get("id")) == contract_id:
            return path
    raise TariffError(f"No contract with id '{contract_id}'")
