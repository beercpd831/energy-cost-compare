"""Keeps the ledger up to date, prices it, and summarises it for the entities."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from astral import Observer
from astral.sun import sun

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .calc import INTERVAL, INTERVAL_S, Totals, floor_interval, summarise
from .const import (
    BASELINE_AVERAGE,
    CONF_BOILER_EFFICIENCY,
    CONF_HEAT,
    CONF_PRICE_INCLUDES_VAT,
    CONF_PRICE_SENSOR,
    CONF_START_DATE,
    DEFAULT_ACTUAL,
    DEFAULT_SCENARIO,
    DOMAIN,
    GIVE_UP_AFTER_MINUTES,
    LEDGER_DIR,
    PROCESS_LAG_MINUTES,
    PROPOSAL_ISSUE_PREFIX,
    QUANTITY_KEYS,
    SIGNAL_LEDGER_UPDATED,
    STALE_PRICES_DAYS,
    TARIFF_DIR,
)
from .cost import DayCost, Scenario, price_situation
from .gas import price_heat
from .ledger import Ledger
from .source import async_read_intervals
from .tariffs import (
    FIXED,
    TariffError,
    TariffSet,
    install_defaults,
    load_proposals,
    load_tariffs,
    proposal_changes,
)

_LOGGER = logging.getLogger(__name__)

# Read at most this much history per pass, so a long backfill doesn't hold the recorder.
CHUNK = timedelta(days=1)
# Gas market prices come from Home Assistant's EnergyZero integration, when it is set up.
GAS_PRICE_DOMAIN = "energyzero"
GAS_DAY_START_HOUR = 6
GAS_PRICE_RETRY = timedelta(hours=1)
GAS_PRICE_CHUNK_DAYS = 31
SITUATIONS = ("A", "B", "C")


@dataclass
class Summary:
    """What the entities show."""

    since_start: Totals
    today: Totals
    last_interval_end: datetime | None
    # contract id -> situation -> daily costs
    costs: dict[str, dict[str, list[DayCost]]] = field(default_factory=dict)
    # Day totals for the average of all fixed contracts, per situation.
    average_fixed: dict[str, list[DayCost]] = field(default_factory=dict)
    # The same, replayed under the selected scenario.
    scenario_costs: dict[str, dict[str, list[DayCost]]] = field(default_factory=dict)
    scenario_average_fixed: dict[str, list[DayCost]] = field(default_factory=dict)
    # Gas comparison per contract and for the fixed average: (gas boiler, heat pump) days.
    # Dynamic contracts are only included once gas market prices are known.
    heat: dict[str, tuple[list[DayCost], list[DayCost]]] = field(default_factory=dict)
    # Energy per local day: day -> situation -> (import, export)
    energy_by_day: dict[date, dict[str, tuple[float, float]]] = field(default_factory=dict)
    missing_prices: int = 0


class LedgerCoordinator:
    """Fills the ledger from statistics and publishes summaries."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, ledger: Ledger) -> None:
        self.hass = hass
        self.entry = entry
        self.ledger = ledger
        self.summary: Summary | None = None
        self.tariffs: TariffSet | None = None
        self.proposals: dict[str, dict] = {}
        self.tariff_error: str | None = None
        self.baseline: str = BASELINE_AVERAGE
        self.actual: str = DEFAULT_ACTUAL
        self.scenario: str = DEFAULT_SCENARIO
        self._lock = asyncio.Lock()
        self._unsub = None
        self._gas_prices_tried: datetime | None = None

    @property
    def config(self) -> dict:
        return {**self.entry.data, **self.entry.options}

    @property
    def quantities(self) -> dict[str, list[str]]:
        return {key: list(self.config[key]) for key in QUANTITY_KEYS if self.config.get(key)}

    @property
    def start(self) -> datetime:
        return dt_util.start_of_local_day(date.fromisoformat(self.config[CONF_START_DATE]))

    @property
    def tariff_folder(self) -> Path:
        return Path(self.hass.config.path(LEDGER_DIR, TARIFF_DIR))

    async def async_prepare(self) -> None:
        """Load selections and tariffs; quick, done before the entities are created."""
        await self.hass.async_add_executor_job(self._load_settings)
        await self.async_load_tariffs()

    async def async_start(self) -> None:
        """Backfill and start the 15-minute schedule; can take a while."""
        await self.async_refresh()
        minutes = [m + PROCESS_LAG_MINUTES for m in (0, 15, 30, 45)]
        self._unsub = async_track_time_change(
            self.hass, self._handle_tick, minute=minutes, second=30
        )

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _handle_tick(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_refresh())

    # Selections ---------------------------------------------------------------

    def _load_settings(self) -> None:
        self.baseline = self.ledger.get_meta("baseline") or BASELINE_AVERAGE
        self.actual = self.ledger.get_meta("actual") or DEFAULT_ACTUAL
        self.scenario = self.ledger.get_meta("scenario") or DEFAULT_SCENARIO

    async def async_select(self, key: str, value: str) -> None:
        setattr(self, key, value)
        await self.hass.async_add_executor_job(self.ledger.set_meta, key, value)
        if key == "scenario":
            async with self._lock:
                self.summary = await self.hass.async_add_executor_job(self._summarise)
        async_dispatcher_send(self.hass, SIGNAL_LEDGER_UPDATED, self.entry.entry_id)

    @property
    def scenario_options(self) -> list[str]:
        return sorted(self.tariffs.scenarios) if self.tariffs else []

    @property
    def selected_scenario(self) -> Scenario | None:
        if not self.tariffs:
            return None
        return self.tariffs.scenarios.get(self.scenario)

    @property
    def contract_options(self) -> list[str]:
        return sorted(self.tariffs.contracts) if self.tariffs else []

    @property
    def fixed_contracts(self) -> list[str]:
        if not self.tariffs:
            return []
        return sorted(c.id for c in self.tariffs.contracts.values() if c.kind == FIXED)

    # Tariffs ------------------------------------------------------------------

    async def async_load_tariffs(self) -> None:
        defaults = Path(__file__).parent / "data"
        folder = self.tariff_folder

        def _load() -> tuple[TariffSet, dict]:
            install_defaults(defaults, folder)
            return load_tariffs(folder), load_proposals(folder)

        try:
            self.tariffs, self.proposals = await self.hass.async_add_executor_job(_load)
            self.tariff_error = None
        except (TariffError, OSError, ValueError) as err:
            self.tariffs = None
            self.tariff_error = str(err)
            _LOGGER.error("Tariff files can't be read: %s", err)
        self._update_issues()

    def _update_issues(self) -> None:
        errors = list(self.tariffs.errors) if self.tariffs else []
        if self.tariff_error:
            errors.append(self.tariff_error)
        if errors:
            ir.async_create_issue(
                self.hass, DOMAIN, "tariff_errors", is_fixable=False,
                severity=ir.IssueSeverity.ERROR, translation_key="tariff_errors",
                translation_placeholders={"errors": "; ".join(errors), "folder": str(self.tariff_folder)},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, "tariff_errors")

        stale = []
        if self.tariffs:
            today = dt_util.now().date()
            stale = sorted(
                c.name for c in self.tariffs.contracts.values()
                if (today - c.checked_on).days > STALE_PRICES_DAYS
            )
        if stale:
            ir.async_create_issue(
                self.hass, DOMAIN, "stale_prices", is_fixable=False,
                severity=ir.IssueSeverity.WARNING, translation_key="stale_prices",
                translation_placeholders={"contracts": ", ".join(stale), "days": str(STALE_PRICES_DAYS)},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, "stale_prices")
        self.update_proposal_issues()

    def update_proposal_issues(self) -> None:
        """One fixable repair per stored proposal; the fix flow accepts or rejects it."""
        wanted = set()
        for key, proposal in self.proposals.items():
            contract = self.tariffs.contracts.get(str(proposal["contract_id"])) if self.tariffs else None
            if contract is None:
                continue
            issue_id = f"{PROPOSAL_ISSUE_PREFIX}{key}"
            wanted.add(issue_id)
            ir.async_create_issue(
                self.hass, DOMAIN, issue_id, is_fixable=True, is_persistent=False,
                severity=ir.IssueSeverity.WARNING, translation_key="price_proposal",
                translation_placeholders=proposal_placeholders(contract, proposal),
                data={"entry_id": self.entry.entry_id, "proposal": key},
            )
        registry = ir.async_get(self.hass)
        for (domain, issue_id) in list(registry.issues):
            if domain == DOMAIN and issue_id.startswith(PROPOSAL_ISSUE_PREFIX) and issue_id not in wanted:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    # Ledger -------------------------------------------------------------------

    async def async_recalculate(self, from_date: date | None = None) -> None:
        """Drop stored intervals from a date (or the start) and read them again."""
        start = dt_util.start_of_local_day(from_date) if from_date else self.start
        start = max(start, self.start)
        async with self._lock:
            await self.hass.async_add_executor_job(
                self.ledger.delete_from, int(start.timestamp())
            )
        await self.async_refresh()

    async def async_refresh(self) -> None:
        async with self._lock:
            try:
                await self._fill()
            except Exception:  # keep the schedule alive; the next tick retries
                _LOGGER.exception("Updating the energy ledger failed")
            if self.config.get(CONF_HEAT):
                try:
                    await self._fill_gas_prices()
                except Exception:  # noqa: BLE001 - optional source; retried later
                    _LOGGER.warning("Reading gas market prices failed", exc_info=True)
            self._update_issues()
            self.summary = await self.hass.async_add_executor_job(self._summarise)
        async_dispatcher_send(self.hass, SIGNAL_LEDGER_UPDATED, self.entry.entry_id)

    async def _fill(self) -> None:
        now = dt_util.utcnow()
        end = floor_interval(now - timedelta(minutes=PROCESS_LAG_MINUTES))
        last = await self.hass.async_add_executor_job(self.ledger.last_start)
        start = self.start
        if last is not None:
            start = max(start, dt_util.utc_from_timestamp(last) + INTERVAL)
        # Re-read the last hour so intervals stored incomplete get a second chance.
        start = max(self.start, min(start, end - timedelta(minutes=GIVE_UP_AFTER_MINUTES)))

        quantities = self.quantities
        price_sensor = self.config.get(CONF_PRICE_SENSOR) or None
        price_factor = 1.21 if self.config.get(CONF_PRICE_INCLUDES_VAT, True) else 1.0
        while start < end:
            chunk_end = min(end, start + CHUNK)
            intervals = await async_read_intervals(
                self.hass, quantities, start, chunk_end, price_sensor, price_factor
            )
            give_up = int((now - timedelta(minutes=GIVE_UP_AFTER_MINUTES)).timestamp())
            # A recent interval without full statistics is probably not compiled yet.
            keep = [i for i in intervals if i.complete or i.start + INTERVAL_S <= give_up]
            if keep:
                await self.hass.async_add_executor_job(self.ledger.upsert, keep)
            if len(keep) < len(intervals):
                break
            start = chunk_end

    async def _fill_gas_prices(self) -> None:
        """Store the daily gas market price for days that don't have one yet."""
        entries = [
            e for e in self.hass.config_entries.async_entries(GAS_PRICE_DOMAIN)
            if e.state is ConfigEntryState.LOADED
        ]
        if not entries:
            return
        now = dt_util.now()
        if self._gas_prices_tried and now - self._gas_prices_tried < GAS_PRICE_RETRY:
            return
        known = await self.hass.async_add_executor_job(self.ledger.gas_prices)
        # The gas day runs from 06:00 to 06:00; today's price exists from 06:00.
        last = now.date() if now.hour >= GAS_DAY_START_HOUR else now.date() - timedelta(days=1)
        day = self.start.date()
        missing = []
        while day <= last:
            if day not in known:
                missing.append(day)
            day += timedelta(days=1)
        if not missing:
            return
        self._gas_prices_tried = now
        found: dict[date, float] = {}
        first = missing[0]
        while first <= missing[-1]:
            end = min(missing[-1], first + timedelta(days=GAS_PRICE_CHUNK_DAYS - 1))
            response = await self.hass.services.async_call(
                GAS_PRICE_DOMAIN,
                "get_gas_prices",
                {
                    "config_entry": entries[0].entry_id,
                    "incl_vat": False,
                    "start": f"{first.isoformat()} {GAS_DAY_START_HOUR:02d}:00:00",
                    "end": f"{end.isoformat()} {GAS_DAY_START_HOUR + 1:02d}:00:00",
                },
                blocking=True,
                return_response=True,
            )
            found.update(_gas_prices_by_day(response))
            first = end + timedelta(days=1)
        found = {d: p for d, p in found.items() if d in missing}
        if found:
            await self.hass.async_add_executor_job(self.ledger.upsert_gas_prices, found)

    def _summarise(self) -> Summary:
        tz = dt_util.get_default_time_zone()
        start_ts = int(self.start.timestamp())
        today_ts = int(dt_util.start_of_local_day().timestamp())
        intervals = self.ledger.fetch(start_ts, 2**40)
        last = intervals[-1].start + INTERVAL_S if intervals else None
        summary = Summary(
            since_start=summarise(intervals),
            today=summarise([i for i in intervals if i.start >= today_ts]),
            last_interval_end=dt_util.utc_from_timestamp(last) if last else None,
        )

        for interval in intervals:
            day = datetime.fromtimestamp(interval.start, tz).date()
            per_day = summary.energy_by_day.setdefault(day, {s: (0.0, 0.0) for s in SITUATIONS})
            for s in SITUATIONS:
                imp, exp = interval.situation(s)
                per_day[s] = (per_day[s][0] + imp, per_day[s][1] + exp)

        if self.tariffs:
            summary.costs, summary.average_fixed = self._price_all(intervals, tz, Scenario())
            summary.missing_prices = max(
                (sum(d.missing_prices for d in by_s["C"]) for by_s in summary.costs.values()),
                default=0,
            )
            if self.config.get(CONF_HEAT):
                efficiency = float(self.config.get(CONF_BOILER_EFFICIENCY, 0.9))
                gas_market = self.ledger.gas_prices()
                for contract_id, contract in self.tariffs.contracts.items():
                    days = price_heat(intervals, contract, self.tariffs, tz, efficiency, gas_market)
                    if days[0]:
                        summary.heat[contract_id] = days
                per_contract = [summary.heat[c] for c in self.fixed_contracts if c in summary.heat]
                summary.heat[BASELINE_AVERAGE] = (
                    _average([g for g, _ in per_contract]),
                    _average([h for _, h in per_contract]),
                )
            scenario = self.selected_scenario
            if scenario is not None:
                summary.scenario_costs, summary.scenario_average_fixed = self._price_all(
                    intervals, tz, scenario
                )
        return summary

    def _price_all(self, intervals, tz, scenario: Scenario):
        daylight = _daylight(self.hass.config.latitude, self.hass.config.longitude, tz)
        costs = {
            contract_id: {
                s: price_situation(intervals, contract, self.tariffs, s, tz, scenario, daylight)
                for s in SITUATIONS
            }
            for contract_id, contract in self.tariffs.contracts.items()
        }
        fixed = [costs[c] for c in self.fixed_contracts]
        average = {s: _average([by_s[s] for by_s in fixed]) for s in SITUATIONS}
        return costs, average

    def costs_for(
        self, contract: str, situation: str, scenario: bool = False
    ) -> list[DayCost] | None:
        """Daily costs of a situation under a contract id, or under the fixed average."""
        if self.summary is None:
            return None
        costs = self.summary.scenario_costs if scenario else self.summary.costs
        average = self.summary.scenario_average_fixed if scenario else self.summary.average_fixed
        if contract == BASELINE_AVERAGE:
            return average.get(situation)
        by_situation = costs.get(contract)
        return by_situation.get(situation) if by_situation else None


def _gas_prices_by_day(response) -> dict[date, float]:
    """EnergyZero's get_gas_prices response as price per local date (gas day start)."""
    prices: dict[date, float] = {}
    for item in (response or {}).get("prices") or []:
        start = item.get("start") or item.get("timestamp")
        if isinstance(start, str):
            start = dt_util.parse_datetime(start)
        if not isinstance(start, datetime) or item.get("price") is None:
            continue
        prices[dt_util.as_local(start).date()] = float(item["price"])
    return prices


def _daylight(latitude: float, longitude: float, tz):
    """daylight(timestamp): True between sunrise and sunset at the home location."""
    observer = Observer(latitude=latitude, longitude=longitude)
    cache: dict[date, tuple[float, float]] = {}

    def is_day(timestamp: int) -> bool:
        day = datetime.fromtimestamp(timestamp, tz).date()
        if day not in cache:
            try:
                times = sun(observer, date=day, tzinfo=tz)
                cache[day] = (times["sunrise"].timestamp(), times["sunset"].timestamp())
            except ValueError:  # no sunrise or sunset (polar day or night)
                cache[day] = (0.0, 0.0)
        sunrise, sunset = cache[day]
        return sunrise <= timestamp < sunset

    return is_day


FIELD_LABELS = {
    "supply": "electricity supply (EUR/kWh)",
    "markup": "electricity markup (EUR/kWh)",
    "feed_in": "feed-in compensation (EUR/kWh)",
    "feed_in_markup": "feed-in markup (EUR/kWh)",
    "feed_in_cost": "feed-in costs (EUR/kWh)",
    "fixed_per_month": "fixed costs electricity (EUR/month)",
    "feed_in_bonus": "daytime feed-in bonus (share of market price)",
    "feed_in_bonus_min_price": "no feed-in bonus below this market price (EUR/kWh)",
    "feed_in_cost_tiers": "feed-in costs per day by yearly feed-in (EUR/day)",
    "supply_slots": "electricity supply at set times of day (EUR/kWh)",
    "name": "name",
    "supplier": "supplier",
    "gas_supply": "gas supply (EUR/m³)",
    "gas_markup": "gas markup (EUR/m³)",
    "gas_fixed_per_month": "fixed costs gas (EUR/month)",
}


def proposal_placeholders(contract, proposal: dict) -> dict[str, str]:
    """Text for the repair notice: what changes, from when, and where it was found."""
    lines = []
    for key, now, new in proposal_changes(contract, proposal):
        label = FIELD_LABELS.get(key, key)
        if isinstance(new, list) or isinstance(now, list):
            if key == "supply_slots":
                text = ", ".join(f"{s['from']}-{s['to']} {s['supply']:.4g}" for s in new or [])
            else:
                text = ", ".join(f"from {t['from_kwh']:g} kWh {t['per_day']:.4g}/day" for t in new or [])
            lines.append(f"- {label}: {text or 'none'}")
        elif isinstance(new, str):
            lines.append(f"- {label}: {now} → {new}")
        elif now is None:
            lines.append(f"- {label}: new, {float(new):.5g}")
        else:
            change = f" ({(float(new) - float(now)) / abs(float(now)):+.1%})" if float(now) else ""
            lines.append(f"- {label}: {float(now):.5g} → {float(new):.5g}{change}")
    return {
        "contract": contract.name,
        "valid_from": str(proposal["valid_from"]),
        "changes": "\n".join(lines) or "- No changes: accepting only records today's check.",
        "sources": "\n".join(f"- {s}" for s in proposal.get("sources") or []) or "- none given",
        "note": str(proposal.get("note") or "-"),
        "proposed_on": str(proposal.get("proposed_on") or "-"),
    }


def _average(series: list[list[DayCost]]) -> list[DayCost]:
    """Average day by day over several contracts (days missing in one are skipped)."""
    if not series:
        return []
    by_day: dict[date, list[DayCost]] = {}
    for days in series:
        for d in days:
            by_day.setdefault(d.day, []).append(d)
    averaged = []
    for day, found in sorted(by_day.items()):
        if len(found) != len(series):
            continue
        keys = {k for d in found for k in d.parts}
        parts = {k: sum(d.parts.get(k, 0.0) for d in found) / len(found) for k in keys}
        settled_keys = {k for d in found for k in d.settled}
        settled = {k: sum(d.settled.get(k, 0.0) for d in found) / len(found) for k in settled_keys}
        averaged.append(DayCost(
            day,
            sum(d.total for d in found) / len(found),
            parts,
            max(d.missing_prices for d in found),
            settled,
        ))
    return averaged
