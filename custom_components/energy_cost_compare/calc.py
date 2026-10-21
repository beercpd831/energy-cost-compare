"""Pure energy calculations, free of Home Assistant imports so they can be unit tested."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

INTERVAL = timedelta(minutes=15)
INTERVAL_S = int(INTERVAL.total_seconds())

# Raw quantities recorded per interval, all in kWh.
QUANTITIES = (
    "grid_import",
    "grid_export",
    "pv",
    "inv_in",  # AC energy flowing into the battery inverters (AC-in and AC-out side)
    "inv_out",  # AC energy flowing out of the battery inverters
    "dc_charge",  # optional: DC energy into the battery
    "dc_discharge",  # optional: DC energy out of the battery
)


@dataclass
class Interval:
    """Measured energy in one 15-minute interval."""

    start: int  # unix timestamp (UTC) of the interval start
    resolution: int = 5  # minutes of the source statistics (5, or 60 when split from hourly)
    complete: bool = True
    values: dict[str, float] = field(default_factory=dict)

    def get(self, key: str) -> float:
        return self.values.get(key, 0.0)

    @property
    def house_load(self) -> float:
        """Household consumption without the battery system's own consumption.

        Grid net + PV gives everything used behind the meter, including what went into
        the battery inverters; the inverters' net AC intake is removed again.
        """
        return (
            self.get("grid_import")
            - self.get("grid_export")
            + self.get("pv")
            - self.get("inv_in")
            + self.get("inv_out")
        )

    @property
    def battery_ac_net(self) -> float:
        """Net AC energy taken by the battery system (stored energy plus losses)."""
        return self.get("inv_in") - self.get("inv_out")

    @property
    def battery_losses(self) -> float | None:
        """Conversion and standby losses, only known when DC counters are configured."""
        if "dc_charge" not in self.values and "dc_discharge" not in self.values:
            return None
        stored = self.get("dc_charge") - self.get("dc_discharge")
        return self.battery_ac_net - stored

    def situation(self, name: str) -> tuple[float, float]:
        """Return (grid import, grid export) in kWh for situation A, B or C."""
        if name == "A":  # no PV, no battery
            # Counters tick at 10 Wh, so a quiet interval can come out slightly negative.
            return max(0.0, self.house_load), 0.0
        if name == "B":  # PV only, simulated per interval
            net = self.house_load - self.get("pv")
            return max(0.0, net), max(0.0, -net)
        if name == "C":  # PV + battery, as measured
            return self.get("grid_import"), self.get("grid_export")
        raise ValueError(f"Unknown situation {name}")


def floor_interval(moment: datetime) -> datetime:
    """Round a timezone-aware datetime down to the start of its 15-minute interval."""
    ts = int(moment.timestamp())
    return datetime.fromtimestamp(ts - ts % INTERVAL_S, tz=timezone.utc)


def aggregate_changes(
    rows_by_quantity: dict[str, list[tuple[float, float]]],
    start: datetime,
    end: datetime,
    source_minutes: int,
) -> dict[int, Interval]:
    """Turn statistics 'change' rows into 15-minute intervals.

    rows_by_quantity maps a quantity to (period start timestamp, kWh change) rows from
    5-minute or hourly statistics. Hourly rows are split evenly over their four quarters.
    An interval is complete when every configured quantity covers all of its minutes.
    """
    start_ts = int(floor_interval(start).timestamp())
    end_ts = int(floor_interval(end).timestamp())
    intervals: dict[int, Interval] = {}
    coverage: dict[int, dict[str, int]] = {}

    for ts in range(start_ts, end_ts, INTERVAL_S):
        intervals[ts] = Interval(start=ts, resolution=source_minutes)
        coverage[ts] = {}

    for quantity, rows in rows_by_quantity.items():
        for interval in intervals.values():
            interval.values[quantity] = 0.0
        for period_start, change in rows:
            if change is None:
                continue
            period_ts = int(period_start)
            if source_minutes == 60:
                parts = [(period_ts + i * INTERVAL_S, change / 4, 15) for i in range(4)]
            else:
                bucket = period_ts - period_ts % INTERVAL_S
                parts = [(bucket, change, source_minutes)]
            for bucket, kwh, minutes in parts:
                if bucket not in intervals:
                    continue
                intervals[bucket].values[quantity] += kwh
                coverage[bucket][quantity] = coverage[bucket].get(quantity, 0) + minutes

    for ts, interval in intervals.items():
        interval.complete = all(
            coverage[ts].get(quantity, 0) >= 15 for quantity in rows_by_quantity
        )
    return intervals


@dataclass
class Totals:
    """Summed energy over a set of intervals."""

    intervals: int = 0
    incomplete: int = 0
    house_load: float = 0.0
    battery_ac_net: float = 0.0
    battery_losses: float | None = None
    pv: float = 0.0
    situations: dict[str, list[float]] = field(
        default_factory=lambda: {s: [0.0, 0.0] for s in "ABC"}
    )

    def add(self, interval: Interval) -> None:
        self.intervals += 1
        if not interval.complete:
            self.incomplete += 1
        self.house_load += interval.house_load
        self.battery_ac_net += interval.battery_ac_net
        self.pv += interval.get("pv")
        losses = interval.battery_losses
        if losses is not None:
            self.battery_losses = (self.battery_losses or 0.0) + losses
        for name, pair in self.situations.items():
            imp, exp = interval.situation(name)
            pair[0] += imp
            pair[1] += exp


def summarise(intervals: list[Interval]) -> Totals:
    totals = Totals()
    for interval in intervals:
        totals.add(interval)
    return totals


def aggregate_means(
    rows: list[tuple[float, float | None]],
    start: datetime,
    end: datetime,
    source_minutes: int,
) -> dict[int, float]:
    """Average 5-minute or hourly 'mean' rows (such as a price) per 15-minute interval.

    Intervals without any row are left out, so callers can tell a missing price from zero.
    """
    start_ts = int(floor_interval(start).timestamp())
    end_ts = int(floor_interval(end).timestamp())
    sums: dict[int, list[float]] = {}
    for period_start, mean in rows:
        if mean is None:
            continue
        period_ts = int(period_start)
        if source_minutes == 60:
            buckets = [period_ts + i * INTERVAL_S for i in range(4)]
        else:
            buckets = [period_ts - period_ts % INTERVAL_S]
        for bucket in buckets:
            if start_ts <= bucket < end_ts:
                sums.setdefault(bucket, []).append(mean)
    return {ts: sum(v) / len(v) for ts, v in sums.items()}
