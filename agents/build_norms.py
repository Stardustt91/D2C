"""
Offline builder for the historical resource norms library.

Walks the cost_parameters FAISS docstore, groups records by project, and uses the
LLM to convert each project's scale + formulas into NORMALIZED ratios:
    "0.08 trucks per site", "0.5 sites per crew per day", "1 engineer per crew"

Raw historical quantities don't transfer between projects (a formula literal '3'
is meaningless without that project's scale); ratios do. The planner consumes the
output at runtime via norms.py.

Usage (one LLM call per historical project — run manually, review the output):
    python agents/build_norms.py            # writes norms/resource_norms.json
    python agents/build_norms.py --limit 5  # only first 5 projects (smoke test)
    python agents/build_norms.py --dry-run  # print, don't write
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.chdir(Path(__file__).resolve().parent.parent)

from cost_parameter import parameters_db, llm  # reuse loaded FAISS store + LLM config
from norms import NORMS_PATH


class NormRow(BaseModel):
    resource_name: str = Field(..., description="Generic resource name, singular, e.g. 'Light truck', 'RF engineer', 'Crane'")
    derivation: str = Field(..., description="How the ratio was computed from this project's formulas and scale")
    quantity_in_project: float = Field(..., description="Absolute quantity of this resource used in the project")
    per_unit: str = Field(..., description="The volume unit the ratio is expressed per, e.g. 'site', 'km', 'trip'")
    ratio: float = Field(..., description="quantity_in_project divided by the project's volume driver value")


class ProjectNorms(BaseModel):
    volume_driver_name: str = Field(..., description="Volume driver of the project, e.g. 'sites'")
    volume_driver_value: Optional[float] = Field(None, description="Numeric volume, e.g. 30. Null if not determinable.")
    duration_months: Optional[float] = Field(None, description="Project duration in months. Null if not determinable.")
    norms: List[NormRow] = Field(
        default_factory=list,
        description="One row per countable resource whose quantity can be inferred from the formulas/description. "
                    "Skip resources whose count cannot be inferred with reasonable confidence.",
    )


def _s(v, default=""):
    if v is None:
        return default
    if isinstance(v, float) and math.isnan(v):
        return default
    return str(v).strip()


def collect_projects() -> dict:
    """Group all docstore records by project: description + list of (driver, component, input, params, formula)."""
    projects = defaultdict(lambda: {"description": "", "records": []})
    docstore = getattr(parameters_db, "docstore", None)
    records = getattr(docstore, "_dict", {}).values() if docstore else []
    for doc in records:
        meta = doc.metadata or {}
        project = _s(meta.get("Project"), "Unknown Project")
        desc = _s(meta.get("Project Description"))
        if desc:
            projects[project]["description"] = desc
        cp_raw = meta.get("Cost Parameters", meta.get("Cost parameters"))
        if isinstance(cp_raw, dict):
            params = ", ".join(str(k) for k in cp_raw.keys())
        elif isinstance(cp_raw, (list, tuple)):
            params = ", ".join(str(p) for p in cp_raw)
        else:
            params = _s(cp_raw)
        projects[project]["records"].append({
            "driver": _s(meta.get("Cost Driver")),
            "component": _s(meta.get("Cost Component")),
            "input": _s(meta.get("Cost Input", meta.get("Cost input"))),
            "parameters": params,
            "formula": _s(meta.get("Formula")),
        })
    return dict(projects)


def extract_project_norms(project: str, description: str, records: List[dict]) -> Optional[ProjectNorms]:
    lines = []
    for r in records:
        line = f"- Driver: {r['driver']} | Component: {r['component']} | Input: {r['input']} | Parameters: {r['parameters']}"
        if r["formula"]:
            line += f" | Formula: {r['formula']}"
        lines.append(line)

    prompt = f"""You are building a resourcing norms library from a historical Design-to-Cost project.

Project: {project}
Project Description: {description or '(no description)'}

Cost records (formulas contain numeric literals that encode resource counts, durations and working days):
{chr(10).join(lines)}

Your task:
1. Determine the project's VOLUME DRIVER (what it counts, e.g. sites) and its numeric value, and the duration
   in months. Read them from the description and/or formula literals. Use null when genuinely not determinable.
2. For each countable resource (vehicles, equipment, personnel roles), infer the ABSOLUTE quantity used in this
   project from the formulas and description. A formula like '3*22*[Daily Rental Rate]' on a Truck Rental input
   typically means 3 trucks x 22 working days — so 3 trucks. Explain this reading in the derivation.
3. Express each as a NORMALIZED ratio: quantity / volume driver value (e.g. 3 trucks / 30 sites = 0.1 per site).
4. Only output rows you can infer with reasonable confidence. Skip pure prices, taxes, percentages and anything
   whose count is ambiguous.
5. Use generic singular resource names ('Light truck', not 'Trucks for Cairo rollout')."""

    extractor = llm.with_structured_output(ProjectNorms)
    try:
        return extractor.invoke(prompt)
    except Exception as e:
        print(f"  [skip] extraction failed for '{project}': {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Build the historical resource norms library")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N projects (0 = all)")
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing the norms file")
    args = parser.parse_args()

    projects = collect_projects()
    names = sorted(projects.keys())
    if args.limit:
        names = names[: args.limit]
    print(f"Found {len(projects)} projects in the cost_parameters docstore; processing {len(names)}.")

    all_rows = []
    for i, name in enumerate(names, 1):
        info = projects[name]
        print(f"[{i}/{len(names)}] {name} ({len(info['records'])} records)")
        result = extract_project_norms(name, info["description"], info["records"])
        if result is None:
            continue
        vol = result.volume_driver_value
        for row in result.norms:
            if row.ratio is None or row.ratio <= 0:
                continue
            all_rows.append({
                "resource_name": row.resource_name,
                "per_unit": row.per_unit,
                "ratio": row.ratio,
                "quantity_in_project": row.quantity_in_project,
                "project": name,
                "project_volume": vol,
                "project_volume_unit": result.volume_driver_name,
                "project_duration_months": result.duration_months,
                "derivation": row.derivation,
            })
        print(f"  extracted {len(result.norms)} norm row(s); volume: {vol} {result.volume_driver_name}, duration: {result.duration_months} months")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_projects": len(names),
        "norms": all_rows,
    }

    if args.dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    NORMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(NORMS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {len(all_rows)} norm rows to {NORMS_PATH}")
    print("Review the file manually — extraction quality matters more than coverage.")


if __name__ == "__main__":
    main()
