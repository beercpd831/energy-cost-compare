"""Read energy counters from the recorder's statistics."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .calc import Interval, aggregate_changes, aggregate_means
from .const import SHORT_TERM_DAYS


async def async_read_intervals(
    hass: HomeAssistant,
    quantities: dict[str, list[str]],
    start: datetime,
    end: datetime,
    price_sensor: str | None = None,
    price_factor: float = 1.0,
) -> list[Interval]:
    """Build 15-minute intervals for [start, end) from statistics.

    Uses 5-minute statistics where the recorder still has them and hourly statistics
    for older periods. Several counters configured for one quantity are summed. The
    price sensor's mean, divided by price_factor, is stored as "epex" (EUR/kWh excl. VAT).
    """
    short_term_from = dt_util.utcnow() - timedelta(days=SHORT_TERM_DAYS)
    result: list[Interval] = []
    if start < short_term_from:
        hourly_end = min(end, short_term_from.replace(minute=0, second=0, microsecond=0))
        if hourly_end > start:
            result += await _read(hass, quantities, start, hourly_end, 60, price_sensor, price_factor)
            start = hourly_end
    if end > start:
        result += await _read(hass, quantities, start, end, 5, price_sensor, price_factor)
    return result


async def _read(
    hass: HomeAssistant,
    quantities: dict[str, list[str]],
    start: datetime,
    end: datetime,
    minutes: int,
    price_sensor: str | None,
    price_factor: float,
) -> list[Interval]:
    entity_ids = {e for ids in quantities.values() for e in ids}
    period = "5minute" if minutes == 5 else "hour"
    recorder = get_instance(hass)
    stats = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        entity_ids,
        period,
        {"energy": "kWh"},
        {"change"},
    )
    prices: dict[int, float] = {}
    if price_sensor:
        price_stats = await recorder.async_add_executor_job(
            statistics_during_period, hass, start, end, {price_sensor}, period, None, {"mean"}
        )
        price_rows = [(row["start"], row.get("mean")) for row in price_stats.get(price_sensor, [])]
        prices = aggregate_means(price_rows, start, end, minutes)
    rows_by_entity = {
        entity_id: [(row["start"], row.get("change")) for row in stats.get(entity_id, [])]
        for entity_id in entity_ids
    }
    per_entity = aggregate_changes(rows_by_entity, start, end, minutes)

    intervals: list[Interval] = []
    for ts in sorted(per_entity):
        source = per_entity[ts]
        interval = Interval(start=ts, resolution=minutes, complete=source.complete)
        for quantity, ids in quantities.items():
            interval.values[quantity] = sum(source.get(e) for e in ids)
        if ts in prices:
            interval.values["epex"] = prices[ts] / price_factor
        intervals.append(interval)
    return intervals
