"""
Regenerate ``activity_intake/knowledge.py`` from the two source documents.

The taxonomy and the cost-driver families are reference data that the supply chain team
owns and will revise — the Cost Driver Library carries "Should be further reviewed" on its
face. Transcribing 34 families and 90-odd subcategories into Python by hand once is
tolerable; doing it again on every revision is not, and a typo in a driver list is invisible
until an interview quietly stops asking about fuel pilferage.

So the generated module is the artefact, and this is how it is made:

    VF-Egypt-Local-Services-Category-Tree.md   -> TAXONOMY  (L1 / L2 / L3, spine tag, family ref)
    Cost Driver Library (1).docx               -> FAMILIES  (unit of measure, spine, drivers)

Run from the project root after either document changes::

    python agents/build_intake_knowledge.py

Both source documents are expected at the project root and neither is committed — they are
the supply chain team's, and they arrive by email. Everything else in ``activity_intake`` is
hand-written: the agendas in ``agendas.py`` come from the Skill Specification's five
prompts, which are prose rather than tables and carry behaviour in their exact phrasing.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Repository root. This file lives in agents/, alongside build_norms.py, the project's
#: other offline generator.
ROOT = Path(__file__).resolve().parent.parent
TREE = ROOT / "VF-Egypt-Local-Services-Category-Tree.md"
LIBRARY = ROOT / "Cost Driver Library (1).docx"
TARGET = ROOT / "agents" / "activity_intake" / "knowledge.py"

#: The tree tags spines with three-letter codes; the rest of the codebase uses the long
#: names from the Skill Specification, because those are what route to a skill.
SPINE_BY_TAG = {
    "LAB": "labour",
    "DEL": "deliverable",
    "TRX": "transaction",
    "RTS": "rights",
    "PST": "passthrough",
}
SPINE_BY_LABEL = {
    "Labour-built": "labour",
    "Deliverable-built": "deliverable",
    "Transaction-built": "transaction",
    "Rights-built": "rights",
    "Pass-through plus fee": "passthrough",
    "Mixed": "",
}


# ---------------------------------------------------------------------------
# Category tree
# ---------------------------------------------------------------------------

_L1 = re.compile(r"^## (\d+)\.\s+(.+?)\s*$")
_L2 = re.compile(
    r"^\*{2,3}(\d+\.\d+)\s+(.+?)\*{2,3}\s*[—-]+\s*(.+?)\s*$"
)
_TAG = re.compile(r"`([A-Z0-9]+)`")
_L3 = re.compile(r"^-\s+(.+?)\s*$")


def parse_tree(text: str) -> list[dict]:
    """Every L2 subcategory, with its spine tag, family refs and service lines."""
    out: list[dict] = []
    l1 = ""
    current: dict | None = None

    for line in text.splitlines():
        if match := _L1.match(line):
            l1 = f"{match.group(1)}. {match.group(2)}"
            current = None
            continue

        if match := _L2.match(line):
            tags = _TAG.findall(match.group(3))
            spine_tag = next((t for t in tags if t in SPINE_BY_TAG), "")
            current = {
                "code": match.group(1),
                "l1": l1,
                "l2": match.group(2).strip(),
                "spine": SPINE_BY_TAG.get(spine_tag, ""),
                "families": [t for t in tags if t not in SPINE_BY_TAG],
                "lines": [],
            }
            out.append(current)
            continue

        # Service lines only count while an L2 is open; the bullet lists under "How to read
        # this" and the closing notes sit outside one and must not be swept up.
        if current is not None and (match := _L3.match(line)):
            current["lines"].append(match.group(1).strip())
            continue

        if line.startswith(("---", "## ", "#")):
            current = None

    return [row for row in out if row["families"]]


# ---------------------------------------------------------------------------
# Cost Driver Library
# ---------------------------------------------------------------------------

_FAMILY = re.compile(r"^([CD]\d+)\.\s+(.+?)\s*$")


def _docx_paragraphs(path: Path) -> list[tuple[str, str]]:
    """(kind, text) for each paragraph and table row, in document order.

    Written against python-docx rather than a text export so the build has one input
    format and no intermediate file to go stale.
    """
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    out: list[tuple[str, str]] = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            if text := para.text.strip():
                kind = "heading" if (para.style and para.style.name.startswith("Heading")) else "text"
                out.append((kind, text))
        elif child.tag == qn("w:tbl"):
            for row in Table(child, doc).rows:
                out.append(("row", " | ".join(c.text.strip() for c in row.cells)))
    return out


def parse_library(path: Path) -> dict[str, dict]:
    """Every C/D family: unit of measure, model spine, cost structure, drivers."""
    families: dict[str, dict] = {}
    code = ""

    for kind, text in _docx_paragraphs(path):
        if kind == "heading":
            match = _FAMILY.match(text)
            code = match.group(1) if match else ""
            if code:
                families[code] = {
                    "name": match.group(2),
                    "unit": "",
                    "spine": "",
                    "structure": "",
                    "drivers": "",
                }
            continue

        if not code:
            continue

        # The family table is two or three columns depending on the family, and its header
        # row is skipped by looking for the spine label rather than by counting rows.
        if kind == "row":
            cells = [c.strip() for c in text.split("|")]
            spine = next((SPINE_BY_LABEL[c] for c in cells if c in SPINE_BY_LABEL), None)
            if spine is not None and not families[code]["unit"]:
                families[code]["unit"] = cells[0]
                families[code]["spine"] = spine
                families[code]["structure"] = cells[2] if len(cells) > 2 else ""
            continue

        if text.startswith("Cost drivers to ask about"):
            continue
        if not families[code]["drivers"] and families[code]["unit"]:
            families[code]["drivers"] = text

    return families


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------

HEADER = '''"""
Reference data for the intake interview: what kind of service this is, and what drives its
cost.

GENERATED by ``agents/build_intake_knowledge.py`` — edit that script or the source documents, not
this file.

    TAXONOMY   from VF-Egypt-Local-Services-Category-Tree.md
    FAMILIES   from Cost Driver Library (1).docx

Why this is code rather than a retrieval index: the whole library is about 12,000 tokens and
the interview needs exactly one family out of thirty-four, chosen deterministically from a
classification the model has already committed to. A vector store would answer the same
question less reliably, need rebuilding whenever the library is revised, and put an
embedding call on the critical path of every turn. Classification picks the family; the
family's driver list goes into the prompt whole.

The spine is the routing decision this file exists to support. From the Cost Driver
Library, Part 3: "Getting this wrong is the commonest failure: running a full FTE build-up
on a celebrity endorsement produces a confident, meaningless number."
"""

from __future__ import annotations

from typing import NamedTuple


class Family(NamedTuple):
    """One cost-driver family from the library."""

    code: str
    name: str
    unit: str
    spine: str
    structure: str
    drivers: str


class Category(NamedTuple):
    """One L2 subcategory of the local services tree."""

    code: str
    l1: str
    l2: str
    spine: str
    families: tuple[str, ...]
    lines: tuple[str, ...]

'''

FOOTER = '''

#: Long names for the five spines, as the Skill Specification writes them. Shown to the
#: analyst when the classification is confirmed, because "labour" alone does not say what
#: is about to happen to their interview.
SPINE_NAMES = {
    "labour": "Labour-built",
    "deliverable": "Deliverable-built",
    "transaction": "Transaction-built",
    "rights": "Rights-built",
    "passthrough": "Pass-through plus fee",
}

#: How each spine builds the cost. Cost Driver Library, Part 3.
SPINE_DESCRIPTIONS = {
    "labour": (
        "Bottom-up from headcount: workload or coverage FTE, then fully loaded employment "
        "cost. The full staffing build applies."
    ),
    "deliverable": (
        "Priced per deliverable, per project or per man-day of a named grade. Staffing is "
        "the vendor's problem, not yours - grade mix and days per deliverable replace FTE."
    ),
    "transaction": (
        "Unit price times volume, with tier breaks: per payslip, per hire, per delegate-day, "
        "per shipment. Headcount only applies to a dedicated on-site team."
    ),
    "rights": (
        "The cost is the licence, not the work: territory, term, exclusivity and channels "
        "granted. Production is a separate line and the headcount build does not apply."
    ),
    "passthrough": (
        "Third-party spend passes through at cost with a commission or handling fee on top. "
        "The fee basis and the gross-versus-net question are the whole negotiation."
    ),
}


def family(code: str) -> Family | None:
    """The library entry for a family code, or None if the code is unknown."""
    return FAMILIES.get((code or "").strip().upper())


def category(code: str) -> Category | None:
    """The tree entry for an L2 code such as "2.3", or None."""
    return CATEGORIES_BY_CODE.get((code or "").strip())


def resolve_spine(category_code: str, family_code: str) -> str:
    """The spine to route on, preferring the tree's tag over the family's.

    The two agree almost everywhere. Where they do not, the tree wins: it tags the specific
    subcategory being bought, while a family's spine is the one that fits most of its
    members. D15 is the clear case - the library records its spine as "Mixed" because
    corporate services span three of them, and only the tree knows that travel management is
    pass-through while translation is transaction-built.
    """
    if entry := category(category_code):
        if entry.spine:
            return entry.spine
    if entry := family(family_code):
        return entry.spine
    return ""


#: Compact index for the classifier prompt: one line per subcategory. Built rather than
#: stored so it cannot drift from the table above.
TAXONOMY_INDEX = "\\n".join(
    f"{c.code} {c.l2} [{c.l1}] -> spine={c.spine} family={'/'.join(c.families)}"
    f"  ({'; '.join(c.lines[:4])})"
    for c in CATEGORIES
)


#: Cost categories the analyst will not volunteer and the supplier will not itemise.
#: Cost Driver Library, Part 8. Prompted for explicitly in the closing stage.
OFTEN_OMITTED = {
    "Indirect roles": (
        "Planners and dispatch, QA and audit, HSE officers, trainers, spares controllers, "
        "admin and HR support, service delivery or contract manager, SLA reporting analyst."
    ),
    "Workforce churn": (
        "Recruitment cost per hire times attrition, onboarding, a productivity ramp of 4-12 "
        "weeks, backfill lag. Egyptian technical and contact-centre roles commonly run "
        "20-35% annual attrition."
    ),
    "Certification and compliance": (
        "Working at height, first aid, defensive driving, HV/LV electrical, OSHA or NEBOSH, "
        "vendor certifications, fibre splicing, ISO 27001 or PCI-DSS, regulator clearance "
        "for field staff. All recur."
    ),
    "Equipment upkeep": (
        "Calibration of test instruments per year, tool loss and breakage allowance, spares "
        "obsolescence write-off."
    ),
    "Financial costs": (
        "Performance bonds and advance payment guarantees, working capital from receivable "
        "days, FX hedging or an unhedged exposure allowance, SLA penalty expected value."
    ),
    "Lifecycle": (
        "Mobilisation (recruitment wave, initial training, tooling, transition from the "
        "incumbent), parallel run and knowledge transfer, demobilisation and exit. Amortise "
        "across the term and show separately."
    ),
}


#: Egypt-specific drivers that change the answer and that a generic interview misses.
#: Cost Driver Library, Part 5, plus the Ramadan productivity rule from Step 3.
EGYPT_NOTES = (
    "Ramadan is a cost event twice over: media rates rise sharply for the window and the "
    "best inventory is committed months ahead, while delivery productivity falls by roughly "
    "25% for about 29 days. Never smooth either across the year.\\n"
    "Islamic holidays move about 11 days earlier each Gregorian year, so a calendar cannot "
    "be reused from last year's model.\\n"
    "Arabic versioning is a count of versions, not of assets: Egyptian dialect, Modern "
    "Standard Arabic and Gulf variants are separate deliverables.\\n"
    "Pan-Arab talent and imported goods price in hard currency and escalate on the FX path, "
    "not on local CPI.\\n"
    "Public-location shooting and governorate permits carry real lead times that belong in "
    "the schedule as well as the cost.\\n"
    "Density beats volume: 500 sites in one governorate cost far less per site than 500 "
    "across eight. Always capture the geographic split, never just the total."
)
'''


def emit(categories: list[dict], families: dict[str, dict]) -> str:
    parts = [HEADER]

    parts.append("\n#: Every cost-driver family in the library, keyed by code.\nFAMILIES = {\n")
    for code, entry in families.items():
        parts.append(
            f"    {code!r}: Family(\n"
            f"        code={code!r},\n"
            f"        name={entry['name']!r},\n"
            f"        unit={entry['unit']!r},\n"
            f"        spine={entry['spine']!r},\n"
            f"        structure={entry['structure']!r},\n"
            f"        drivers={entry['drivers']!r},\n"
            f"    ),\n"
        )
    parts.append("}\n")

    parts.append("\n\n#: Every L2 subcategory of the local services tree, in document order.\nCATEGORIES = (\n")
    for row in categories:
        parts.append(
            f"    Category(\n"
            f"        code={row['code']!r},\n"
            f"        l1={row['l1']!r},\n"
            f"        l2={row['l2']!r},\n"
            f"        spine={row['spine']!r},\n"
            f"        families={tuple(row['families'])!r},\n"
            f"        lines={tuple(row['lines'])!r},\n"
            f"    ),\n"
        )
    parts.append(")\n\nCATEGORIES_BY_CODE = {c.code: c for c in CATEGORIES}\n")

    parts.append(FOOTER)
    return "".join(parts)


def main() -> None:
    categories = parse_tree(TREE.read_text(encoding="utf-8"))
    families = parse_library(LIBRARY)

    missing = sorted(
        {f for row in categories for f in row["families"]} - set(families)
    )
    if missing:
        print(f"warning: tree references families absent from the library: {missing}")
    untagged = [row["code"] for row in categories if not row["spine"]]
    if untagged:
        print(f"warning: subcategories with no spine tag: {untagged}")

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(emit(categories, families), encoding="utf-8")
    print(f"{len(categories)} subcategories, {len(families)} families -> {TARGET}")


if __name__ == "__main__":
    main()
