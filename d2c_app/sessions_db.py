"""
Persistence for estimation sessions.

A session is one activity description and everything derived from it: the resource
plan, the cost tree, and how far through the workflow it got. Before this existed the
whole hierarchy lived only in the browser tab that built it, so closing the tab threw
away an estimation that had taken minutes of LLM work to produce.

The table lives in the same SQLite file as the estimation exports. The file is named
for what it originally held; it is the application's database, and keeping one file
means one thing to copy when someone wants to move their work to another machine.

Timestamps are stored as ISO-8601 UTC with a trailing Z, and rendered in local time by
the browser. Storing local time — as the older exports table does — makes rows from
different machines or either side of a DST change impossible to order correctly.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent

#: D2C_DB_PATH moves the database off the deployment directory. It has to be set
#: anywhere the code is redeployed in place of being upgraded — on Azure App Service
#: the application directory is replaced wholesale on every deploy, so a database
#: left there loses every saved session. Point it at the persistent mount
#: (/home/data/d2c/d2c_exports.db) to keep sessions across deploys and restarts.
DB_PATH = Path(os.environ.get("D2C_DB_PATH", "").strip() or (ROOT / "d2c_exports.db"))

#: Distinguishes "caller did not mention this field" from "caller set it to None".
_UNSET: Any = object()

#: Columns returned when listing. The structure blob is deliberately excluded — it can
#: be megabytes per session, and the sidebar only needs what it renders.
_LIST_COLUMNS = "id, title, activity_description, step_completed, created_at, updated_at"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    # SQLite creates the file but not the directory holding it, and D2C_DB_PATH
    # routinely points at a mount that starts out empty.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # timeout: autosave from the browser can land while a long export is writing, and
    # waiting briefly is better than surfacing "database is locked" to the analyst.
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the sessions table. Safe to call on every request."""
    with closing(_connect()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id                   TEXT PRIMARY KEY,
                title                TEXT NOT NULL,
                activity_description TEXT NOT NULL,
                activity_facts       TEXT,
                structure            TEXT,
                step_completed       TEXT,
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL
            )
            """
        )
        # The sidebar always reads most-recently-touched first.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at DESC)"
        )
        conn.commit()


def _loads(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _row_to_dict(row: sqlite3.Row, *, with_structure: bool) -> Dict[str, Any]:
    data = dict(row)
    if with_structure:
        data["structure"] = _loads(data.get("structure"))
        data["activity_facts"] = _loads(data.get("activity_facts"))
    return data


def create_session(
    title: str,
    activity_description: str,
    activity_facts: Optional[dict] = None,
) -> Dict[str, Any]:
    """Insert a new session and return it. `structure` starts empty."""
    init_db()
    now = _now()
    session_id = str(uuid.uuid4())
    with closing(_connect()) as conn:
        conn.execute(
            """INSERT INTO sessions
               (id, title, activity_description, activity_facts, structure,
                step_completed, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                session_id,
                title,
                activity_description,
                json.dumps(activity_facts) if activity_facts else None,
                None,
                None,
                now,
                now,
            ),
        )
        conn.commit()
    return {
        "id": session_id,
        "title": title,
        "activity_description": activity_description,
        "activity_facts": activity_facts,
        "structure": None,
        "step_completed": None,
        "created_at": now,
        "updated_at": now,
    }


def list_sessions() -> List[Dict[str, Any]]:
    """All sessions, most recently modified first, without their structure blobs."""
    init_db()
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"SELECT {_LIST_COLUMNS} FROM sessions ORDER BY updated_at DESC, created_at DESC"
        ).fetchall()
    return [_row_to_dict(row, with_structure=False) for row in rows]


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """One session with its structure and facts parsed back into objects."""
    init_db()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _row_to_dict(row, with_structure=True) if row else None


def update_session(
    session_id: str,
    *,
    title: Any = _UNSET,
    activity_description: Any = _UNSET,
    activity_facts: Any = _UNSET,
    structure: Any = _UNSET,
    step_completed: Any = _UNSET,
) -> Optional[Dict[str, Any]]:
    """Patch the fields that were passed and bump updated_at.

    Only named fields are touched, so an autosave carrying just the structure cannot
    blank out the title. Returns None if there is no such session.
    """
    init_db()
    assignments: List[str] = []
    values: List[Any] = []

    if title is not _UNSET:
        assignments.append("title = ?")
        values.append(title)
    if activity_description is not _UNSET:
        assignments.append("activity_description = ?")
        values.append(activity_description)
    if activity_facts is not _UNSET:
        assignments.append("activity_facts = ?")
        values.append(json.dumps(activity_facts) if activity_facts else None)
    if structure is not _UNSET:
        assignments.append("structure = ?")
        values.append(json.dumps(structure) if structure else None)
    if step_completed is not _UNSET:
        assignments.append("step_completed = ?")
        values.append(step_completed)

    if not assignments:
        return get_session(session_id)

    assignments.append("updated_at = ?")
    values.append(_now())
    values.append(session_id)

    with closing(_connect()) as conn:
        cursor = conn.execute(
            f"UPDATE sessions SET {', '.join(assignments)} WHERE id = ?", values
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
    return get_session(session_id)


def delete_session(session_id: str) -> bool:
    """Remove a session. Returns False if it was already gone."""
    init_db()
    with closing(_connect()) as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cursor.rowcount > 0
