"""SQLite store of 15-minute energy intervals, kept apart from the HA recorder."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from .calc import Interval

SCHEMA = """
CREATE TABLE IF NOT EXISTS intervals (
    start INTEGER PRIMARY KEY,
    resolution INTEGER NOT NULL,
    complete INTEGER NOT NULL,
    vals TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gas_prices (
    day TEXT PRIMARY KEY,
    price REAL NOT NULL
);
"""


class Ledger:
    """Blocking store; call its methods from an executor thread."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Ledger is not open")
        return self._conn

    def upsert(self, intervals: list[Interval]) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO intervals (start, resolution, complete, vals) "
            "VALUES (?, ?, ?, ?)",
            [
                (i.start, i.resolution, int(i.complete), json.dumps(i.values))
                for i in intervals
            ],
        )
        self.conn.commit()

    def last_start(self) -> int | None:
        row = self.conn.execute("SELECT MAX(start) FROM intervals").fetchone()
        return row[0]

    def delete_from(self, start: int) -> None:
        self.conn.execute("DELETE FROM intervals WHERE start >= ?", (start,))
        self.conn.commit()

    def fetch(self, start: int, end: int) -> list[Interval]:
        """Intervals with start <= interval < end, oldest first."""
        rows = self.conn.execute(
            "SELECT start, resolution, complete, vals FROM intervals "
            "WHERE start >= ? AND start < ? ORDER BY start",
            (start, end),
        ).fetchall()
        return [
            Interval(start=r[0], resolution=r[1], complete=bool(r[2]), values=json.loads(r[3]))
            for r in rows
        ]

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def gas_prices(self) -> dict[date, float]:
        """Gas market price per day (EUR/m3 excl. VAT)."""
        rows = self.conn.execute("SELECT day, price FROM gas_prices").fetchall()
        return {date.fromisoformat(r[0]): r[1] for r in rows}

    def upsert_gas_prices(self, prices: dict[date, float]) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO gas_prices (day, price) VALUES (?, ?)",
            [(d.isoformat(), p) for d, p in prices.items()],
        )
        self.conn.commit()
