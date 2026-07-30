"""
Runtime lookup for the historical resource norms library.

The norms file (norms/resource_norms.json) is produced offline by build_norms.py.
Each norm row is a normalized productivity/resourcing ratio extracted from a
historical project, e.g. "0.08 trucks per site". Ratios transfer between projects;
raw quantities do not — that is the whole point of this library.

If the norms file does not exist, every function degrades gracefully (returns ""
or None) so the planner still works without it.
"""

import json
import re
import statistics
from pathlib import Path
from typing import List, Optional

NORMS_PATH = Path(__file__).resolve().parent.parent / "norms" / "resource_norms.json"

_cache = {"mtime": None, "rows": None}


def _normalize_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    tokens = [t.rstrip("s") for t in s.split() if len(t) > 2]
    return " ".join(sorted(set(tokens)))


def load_norms() -> List[dict]:
    """Load raw norm rows, cached against file mtime. [] when file missing/invalid."""
    try:
        mtime = NORMS_PATH.stat().st_mtime
    except OSError:
        return []
    if _cache["rows"] is not None and _cache["mtime"] == mtime:
        return _cache["rows"]
    try:
        with open(NORMS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("norms", []) if isinstance(data, dict) else []
    except (OSError, json.JSONDecodeError):
        rows = []
    _cache["rows"] = rows
    _cache["mtime"] = mtime
    return rows


def aggregate_norms(rows: Optional[List[dict]] = None) -> List[dict]:
    """
    Group norm rows by (normalized resource name, per-unit) and compute
    min/median/max ratio plus the number of historical projects backing it.
    """
    rows = load_norms() if rows is None else rows
    groups = {}
    for r in rows:
        ratio = r.get("ratio")
        if ratio is None:
            continue
        try:
            ratio = float(ratio)
        except (TypeError, ValueError):
            continue
        key = (_normalize_name(r.get("resource_name", "")), (r.get("per_unit") or "").strip().lower())
        g = groups.setdefault(key, {"resource_name": r.get("resource_name", ""), "per_unit": r.get("per_unit", ""), "ratios": [], "projects": set()})
        g["ratios"].append(ratio)
        if r.get("project"):
            g["projects"].add(r["project"])
    out = []
    for g in groups.values():
        ratios = sorted(g["ratios"])
        out.append({
            "resource_name": g["resource_name"],
            "per_unit": g["per_unit"],
            "n_projects": max(len(g["projects"]), 1),
            "min": ratios[0],
            "median": statistics.median(ratios),
            "max": ratios[-1],
        })
    out.sort(key=lambda x: (-x["n_projects"], x["resource_name"]))
    return out


def lookup_norm(resource_name: str) -> Optional[dict]:
    """Best-effort match of a resource name against aggregated norms (token overlap)."""
    target = set(_normalize_name(resource_name).split())
    if not target:
        return None
    best, best_score = None, 0.0
    for agg in aggregate_norms():
        cand = set(_normalize_name(agg["resource_name"]).split())
        if not cand:
            continue
        score = len(target & cand) / len(target | cand)
        if score > best_score:
            best, best_score = agg, score
    return best if best_score >= 0.5 else None


def format_norms_block(max_items: int = 40) -> str:
    """
    Format aggregated norms for the planner prompt. "" when no norms exist.
    """
    aggs = aggregate_norms()
    if not aggs:
        return ""
    lines = [
        "HISTORICAL RESOURCE NORMS (normalized ratios from past projects — use these to size resources,",
        "then round to a practical whole number; e.g. 40 sites x 0.08 trucks/site = 3.2 -> 3 trucks):",
    ]
    for agg in aggs[:max_items]:
        lines.append(
            f"- {agg['resource_name']}: median {agg['median']:.3g} per {agg['per_unit'] or 'unit'} "
            f"(range {agg['min']:.3g}–{agg['max']:.3g}, from {agg['n_projects']} project(s))"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    block = format_norms_block()
    print(block if block else f"No norms found at {NORMS_PATH}. Run build_norms.py to generate them.")
