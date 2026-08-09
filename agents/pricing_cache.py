"""
Persistent store for price evidence.

Two decisions shape this module:

  * The key is the NORMALIZED ITEM SPEC, not the cost parameter name. Parameter
    names are model-generated and vary between runs — "Truck Rental Cost", "Truck
    Daily Rate", "Cost of Truck Hire" all name the same thing. Keying on the raw
    name would produce a cache that almost never hits.

  * What gets stored are the OBSERVATIONS, not the final number. Evidence ages
    differently from the arithmetic applied to it: when the exchange rate moves or
    the escalation model improves, cached observations can simply be re-normalized,
    while a cached total would have to be thrown away and re-searched.

Time to live varies by volatility class, since an imported hardware price and a
local labour rate go stale at very different speeds.

Estimation runs across a thread pool, so every operation opens its own connection
and writes are serialised behind a lock.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from pricing_models import ItemSpec, PriceObservation

#: Bump whenever extraction or validation logic changes in a way that would have
#: produced different observations. Cached evidence carrying an older version is
#: treated as a miss and re-collected.
#:
#: This exists because guards that run at extraction time do not run again on a
#: cache hit. Without versioning, a fix that rejects a bad observation would keep
#: serving that same bad observation from cache until its TTL expired.
#:
#: 2 — added the specification-misread guard (a price equal to the item's own
#:     stated capacity is a misread, not a price)
#: 3 — cache key rebuilt from name tokens and numeric dimensions instead of raw
#:     LLM attribute labels, which drifted between runs and made hits impossible
#: 4 — key moved onto the caller-supplied parameter name; every model-generated
#:     field drifted, so none of them could serve as an identity
ENGINE_VERSION = 4

#: How much of the item's name two specs must share to be treated as the same item
#: on the similarity fallback. 0.6 accepts "mobile crane hire" against "mobile
#: crane rental" while rejecting "crane hire" against "generator hire".
NAME_SIMILARITY_THRESHOLD = 0.6

# Days before cached evidence is considered stale, by volatility class.
TTL_DAYS = {
    "high": 14,     # imported hardware, fuel, anything FX-linked
    "medium": 45,   # equipment hire, subcontracted services
    "low": 120,     # local labour rates, regulated fees
}

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "d2c_price_cache.db"
_db_path = Path(os.environ.get("D2C_PRICE_CACHE", str(_DEFAULT_PATH)))
_write_lock = threading.Lock()
_initialised = False
_init_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    # SQLite will create the file but never the directory above it. When the path is
    # pointed at a mount that starts empty — D2C_PRICE_CACHE=/home/data/d2c/... on
    # Azure App Service — every connect raises "unable to open database file", and
    # because _ensure_schema runs before the callers' try/except that surfaces on
    # every cost parameter as a failed estimate rather than as a missing directory.
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(_db_path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def _ensure_schema() -> None:
    global _initialised
    if _initialised:
        return
    with _init_lock:
        if _initialised:
            return
        with _connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS price_evidence (
                    cache_key        TEXT PRIMARY KEY,
                    canonical_name   TEXT NOT NULL,
                    required_unit    TEXT NOT NULL,
                    region           TEXT NOT NULL,
                    volatility_class TEXT NOT NULL,
                    spec_json        TEXT NOT NULL,
                    observations_json TEXT NOT NULL,
                    queries_json     TEXT NOT NULL DEFAULT '[]',
                    observation_count INTEGER NOT NULL DEFAULT 0,
                    engine_version   INTEGER NOT NULL DEFAULT 1,
                    name_tokens      TEXT NOT NULL DEFAULT '',
                    dimensions_json  TEXT NOT NULL DEFAULT '{}',
                    unit_period      TEXT NOT NULL DEFAULT '',
                    fetched_at       TEXT NOT NULL
                )
                """
            )
            # Bring forward databases created before these columns existed.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(price_evidence)")}
            for name, ddl in (
                ("engine_version", "INTEGER NOT NULL DEFAULT 1"),
                ("name_tokens", "TEXT NOT NULL DEFAULT ''"),
                ("dimensions_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("unit_period", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE price_evidence ADD COLUMN {name} {ddl}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_price_evidence_name ON price_evidence(canonical_name)"
            )
            connection.commit()
        _initialised = True


def ttl_for(spec: ItemSpec) -> timedelta:
    return timedelta(days=TTL_DAYS.get(spec.volatility_class, TTL_DAYS["medium"]))


def _find_similar(connection: sqlite3.Connection, spec: ItemSpec):
    """
    Fallback lookup for when the exact key misses.

    The model rarely names the same item identically twice, so an exact hash alone
    leaves the cache almost never hitting. Candidates are narrowed in SQL by the
    things that must match exactly — region, unit period, engine version — and then
    scored in Python on name overlap and numeric specification agreement. A crane
    of a different tonnage is never a hit, however similar its name.
    """
    wanted_tokens = set(spec.name_tokens())
    if not wanted_tokens:
        return None
    wanted_dims = spec.dimensions()

    try:
        candidates = connection.execute(
            "SELECT observations_json, queries_json, fetched_at, name_tokens, dimensions_json "
            "FROM price_evidence WHERE region = ? AND unit_period = ? AND engine_version = ?",
            (spec.region, spec.unit_period(), ENGINE_VERSION),
        ).fetchall()
    except sqlite3.Error:
        return None

    best, best_score = None, 0.0
    for candidate in candidates:
        tokens = set((candidate["name_tokens"] or "").split())
        if not tokens:
            continue
        overlap = len(wanted_tokens & tokens) / len(wanted_tokens | tokens)
        if overlap < NAME_SIMILARITY_THRESHOLD:
            continue

        try:
            stored_dims = json.loads(candidate["dimensions_json"] or "{}")
        except json.JSONDecodeError:
            stored_dims = {}
        # Any shared dimension must agree within 10%, or these are different items.
        conflict = any(
            dimension in stored_dims
            and wanted > 0
            and not (0.9 <= float(stored_dims[dimension]) / wanted <= 1.11)
            for dimension, wanted in wanted_dims.items()
        )
        if conflict:
            continue

        if overlap > best_score:
            best, best_score = candidate, overlap

    return best


def get(spec: ItemSpec) -> Optional[Tuple[List[PriceObservation], List[str], datetime]]:
    """
    Return cached evidence for this spec, or None when absent or stale.

    Gives back the observations, the queries that found them, and when they were
    fetched — the caller re-normalizes them against current rates, so a hit is
    still an up-to-date estimate.
    """
    _ensure_schema()
    try:
        with _connect() as connection:
            row = connection.execute(
                "SELECT observations_json, queries_json, fetched_at FROM price_evidence "
                "WHERE cache_key = ? AND engine_version = ?",
                (spec.cache_key(), ENGINE_VERSION),
            ).fetchone()
            if row is None:
                row = _find_similar(connection, spec)
    except sqlite3.Error as exc:
        print(f"[pricing_cache] read failed: {exc}")
        return None

    if row is None:
        return None

    try:
        fetched_at = datetime.fromisoformat(row["fetched_at"])
    except (ValueError, TypeError):
        return None

    if datetime.now(timezone.utc) - fetched_at > ttl_for(spec):
        return None

    try:
        raw_observations = json.loads(row["observations_json"])
        observations = [PriceObservation.model_validate(item) for item in raw_observations]
        queries = json.loads(row["queries_json"] or "[]")
    except Exception as exc:
        print(f"[pricing_cache] could not decode cached evidence: {exc}")
        return None

    return observations, queries, fetched_at


def put(spec: ItemSpec, observations: List[PriceObservation], queries: Optional[List[str]] = None) -> None:
    """
    Store evidence for this spec, replacing anything already held.

    Empty result sets are stored too: knowing that a thorough search found nothing
    is worth remembering, and prevents re-running an expensive fan-out that is
    likely to come back empty again.
    """
    _ensure_schema()
    payload = json.dumps([o.model_dump(mode="json") for o in observations], ensure_ascii=False)
    try:
        with _write_lock, _connect() as connection:
            connection.execute(
                """
                INSERT INTO price_evidence (
                    cache_key, canonical_name, required_unit, region, volatility_class,
                    spec_json, observations_json, queries_json, observation_count,
                    engine_version, name_tokens, dimensions_json, unit_period, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    spec_json = excluded.spec_json,
                    observations_json = excluded.observations_json,
                    queries_json = excluded.queries_json,
                    observation_count = excluded.observation_count,
                    engine_version = excluded.engine_version,
                    name_tokens = excluded.name_tokens,
                    dimensions_json = excluded.dimensions_json,
                    unit_period = excluded.unit_period,
                    fetched_at = excluded.fetched_at
                """,
                (
                    spec.cache_key(),
                    spec.canonical_name,
                    spec.required_unit,
                    spec.region,
                    spec.volatility_class,
                    spec.model_dump_json(),
                    payload,
                    json.dumps(queries or [], ensure_ascii=False),
                    len(observations),
                    ENGINE_VERSION,
                    " ".join(spec.name_tokens()),
                    json.dumps(spec.dimensions()),
                    spec.unit_period(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()
    except sqlite3.Error as exc:
        print(f"[pricing_cache] write failed: {exc}")


def invalidate(spec: ItemSpec) -> None:
    """Drop cached evidence for one spec — used when an analyst overrides a value."""
    _ensure_schema()
    try:
        with _write_lock, _connect() as connection:
            connection.execute("DELETE FROM price_evidence WHERE cache_key = ?", (spec.cache_key(),))
            connection.commit()
    except sqlite3.Error as exc:
        print(f"[pricing_cache] invalidate failed: {exc}")


def purge_stale() -> int:
    """
    Remove entries past the longest possible TTL. Safe to call on startup.

    Uses the maximum TTL rather than each row's own class so that a spec whose
    volatility was reclassified is not deleted prematurely.
    """
    _ensure_schema()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(TTL_DAYS.values()))).isoformat()
    try:
        with _write_lock, _connect() as connection:
            cursor = connection.execute("DELETE FROM price_evidence WHERE fetched_at < ?", (cutoff,))
            connection.commit()
            return cursor.rowcount
    except sqlite3.Error as exc:
        print(f"[pricing_cache] purge failed: {exc}")
        return 0


def stats() -> dict:
    """Summary for the cache viewer and for debugging hit rates."""
    _ensure_schema()
    try:
        with _connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS entries, COALESCE(SUM(observation_count), 0) AS observations FROM price_evidence"
            ).fetchone()
            return {"entries": row["entries"], "observations": row["observations"], "path": str(_db_path)}
    except sqlite3.Error as exc:
        return {"entries": 0, "observations": 0, "path": str(_db_path), "error": str(exc)}
