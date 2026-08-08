"""Stage 3 — turn OSCAL catalogs and profiles into rows.

This is the highest-value stage: it is the only place a row may acquire a
`control_id`, because it is the only place control identity comes from
structured data rather than from prose that happens to mention an ID.

Shapes verified empirically against the retrieved catalog (SP 800-53 Rev 5,
OSCAL content version 5.2.0):

    catalog.groups[]            20 groups, ids == the control-family whitelist
      .controls[]               324 base controls
        .controls[]             872 enhancements (recursive, one level deep)
        .props[]                label, sort-id, status ("withdrawn", 182 of them)
        .params[]               ODPs: either {label} or {select:{how-many, choice[]}}
        .parts[]                statement / guidance / assessment-objective /
                                assessment-method (with nested assessment-objects)
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .manifest import Document, Manifest
from .schema import Row, make_id, normalize_text

# Assessment content lives inside the 800-53 catalog but is published as SP
# 800-53A, so its rows are attributed there (manifest notes on SP-800-53Ar5).
ASSESSMENT_DOC_ID = "SP-800-53Ar5"

_INSERT_RE = re.compile(r"\{\{\s*insert:\s*param,\s*([^}\s]+)\s*\}\}")


@dataclass
class Rejection:
    """A deliberate drop. Rejected content is logged, never silently discarded."""

    doc_id: str
    ref: str          # control id or part id the drop relates to
    rule: str
    detail: str
    stage: str = "parse_oscal"

    def to_json_line(self) -> str:
        return json.dumps(
            {
                "doc_id": self.doc_id,
                "ref": self.ref,
                "rule": self.rule,
                "detail": self.detail,
                "stage": self.stage,
            },
            ensure_ascii=False,
        )


@dataclass
class ParseStats:
    part_names: Counter = field(default_factory=Counter)
    by_family: dict[str, Counter] = field(default_factory=dict)
    unresolved_params: Counter = field(default_factory=Counter)

    def count(self, family: str, kind: str) -> None:
        self.by_family.setdefault(family, Counter())[kind] += 1

    def total(self, kind: str) -> int:
        return sum(counter[kind] for counter in self.by_family.values())


# --- ODP parameter rendering -------------------------------------------------

def build_param_index(catalog: dict[str, Any]) -> dict[str, str]:
    """Map every parameter id to its rendered human-readable form.

    Built across the whole catalog rather than per control, because insertion
    markers inside an enhancement routinely reference a parameter declared on
    its parent control.
    """
    index: dict[str, str] = {}

    def visit(control: dict[str, Any]) -> None:
        for param in control.get("params", []):
            index[param["id"]] = render_param(param)
        for child in control.get("controls", []):
            visit(child)

    for group in catalog.get("groups", []):
        for param in group.get("params", []):
            index[param["id"]] = render_param(param)
        for control in group.get("controls", []):
            visit(control)
    return index


def render_param(param: dict[str, Any]) -> str:
    """Render one ODP the way SP 800-53 itself prints it.

    Assignment for a free-text ODP, Selection for a choice list — the
    distinction every RMF practitioner reads at a glance.
    """
    if "select" in param:
        select = param["select"]
        choices = "; ".join(str(choice) for choice in select.get("choice", []))
        how_many = select.get("how-many")
        qualifier = f"; {how_many.replace('-', ' ')}" if how_many else ""
        return f"[Selection{qualifier}: {choices}]"
    label = param.get("label") or param.get("id", "")
    return f"[Assignment: organization-defined {label}]"


MAX_PARAM_PASSES = 5


def render_prose(prose: str, params: dict[str, str], stats: ParseStats | None = None) -> str:
    """Replace every `{{ insert: param, X }}` marker with its rendered ODP.

    Substitution repeats because markers nest: a Selection choice string can
    itself contain an insertion (AC-7's "lock the account or node for
    {{ insert: param, ac-07_odp.04 }}"). The pass limit stops a malformed
    self-referencing parameter from looping forever.

    No raw template marker may survive into published text (Stage 6 enforces
    this), so an unresolved id still gets a readable placeholder and is counted
    for the stage report rather than left as-is.
    """

    def substitute(match: re.Match[str]) -> str:
        param_id = match.group(1).rstrip(",")
        if param_id in params:
            return params[param_id]
        if stats is not None:
            stats.unresolved_params[param_id] += 1
        return "[Assignment: organization-defined parameter]"

    text = prose or ""
    for _ in range(MAX_PARAM_PASSES):
        if not _INSERT_RE.search(text):
            break
        text = _INSERT_RE.sub(substitute, text)
    return text


# --- part flattening ---------------------------------------------------------

def part_label(part: dict[str, Any]) -> str:
    """The label to print for a part: 'a.', '1.', or 'AC-02a.[01]'.

    Statement items carry a plain label ('a.'); assessment objectives carry
    only a class='sp800-53a' label ('AC-02a.[01]'), which is the numbering an
    assessor actually cites, so it is used when no plain label exists.
    """
    labels = [prop for prop in part.get("props", []) if prop.get("name") == "label"]
    for prop in labels:
        if not prop.get("class"):
            return prop.get("value", "")
    for prop in labels:
        if prop.get("class") == "sp800-53a":
            return prop.get("value", "")
    return ""


def flatten_part(
    part: dict[str, Any],
    params: dict[str, str],
    stats: ParseStats | None = None,
    depth: int = 0,
    include_own_label: bool = True,
) -> list[str]:
    """Depth-first render of a part tree into one labelled line per part.

    Nesting is carried by the labels ('a.', then '1.', or 'AC-02a.[01]') rather
    than by indentation, because whitespace normalization collapses leading
    spaces out of the published text anyway.
    """
    lines: list[str] = []
    label = part_label(part) if include_own_label else ""
    prose = render_prose(part.get("prose", ""), params, stats)
    children = part.get("parts", [])

    # A label with no prose that only introduces children (assessment objective
    # 'AC-02a.') is noise: every child already prints the fuller label
    # 'AC-02a.[01]'. A label WITH prose ('d. Specify:') is real content.
    if prose or (label and not children):
        prefix = f"{label} " if label else ""
        lines.append(f"{prefix}{prose}".rstrip())

    for child in children:
        lines.extend(flatten_part(child, params, stats, depth + 1))
    return lines


def collect_part_names(part: dict[str, Any], stats: ParseStats) -> None:
    stats.part_names[part.get("name")] += 1
    for child in part.get("parts", []):
        collect_part_names(child, stats)


# --- control identity --------------------------------------------------------

def display_id(control_id: str) -> str:
    """OSCAL `ac-2.3` -> the printed form `AC-2(3)`."""
    base, _, enhancement = control_id.partition(".")
    printed = base.upper()
    return f"{printed}({enhancement})" if enhancement else printed


def is_withdrawn(control: dict[str, Any]) -> bool:
    return any(
        prop.get("name") == "status" and prop.get("value") == "withdrawn"
        for prop in control.get("props", [])
    )


def parts_named(control: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [part for part in control.get("parts", []) if part.get("name") == name]


# --- the catalog parser ------------------------------------------------------

class CatalogParser:
    def __init__(self, doc: Document, assessment_doc: Document | None,
                 sha256_source: str, catalog: dict[str, Any]):
        self.doc = doc
        self.assessment_doc = assessment_doc
        self.sha256 = sha256_source
        self.catalog = catalog
        self.params = build_param_index(catalog)
        self.stats = ParseStats()
        self.rejections: list[Rejection] = []

    # -- row builders --------------------------------------------------------

    def _row(self, doc: Document, chunk_type: str, slug: str, text: str,
             control_id: str | None, section_path: str) -> Row:
        return Row(
            id=make_id(doc.doc_id, chunk_type, slug),
            text=normalize_text(text),
            doc_id=doc.doc_id,
            doc_title=doc.title,
            revision=doc.revision,
            pub_date=doc.pub_date,
            tier=doc.tier,
            chunk_type=chunk_type,
            control_id=control_id,
            section_path=section_path,
            source_url=doc.effective_source_url,
            sha256_source=self.sha256,
        )

    def parse(self) -> list[Row]:
        rows: list[Row] = []
        for group in self.catalog.get("groups", []):
            family = group["id"]
            family_title = group.get("title", family.upper())
            for control in group.get("controls", []):
                rows.extend(self._parse_control(control, family, family_title, parent=None))
        return rows

    def _parse_control(
        self,
        control: dict[str, Any],
        family: str,
        family_title: str,
        parent: dict[str, Any] | None,
    ) -> list[Row]:
        rows: list[Row] = []
        control_id = control["id"]
        for part in control.get("parts", []):
            collect_part_names(part, self.stats)

        if is_withdrawn(control):
            # Withdrawn controls carry no statement worth publishing, and
            # shipping them is exactly the superseded-content failure mode this
            # dataset exists to avoid.
            self.rejections.append(
                Rejection(
                    doc_id=self.doc.doc_id,
                    ref=control_id,
                    rule="withdrawn_control",
                    detail=f"{display_id(control_id)} '{control.get('title', '')}' marked withdrawn in OSCAL",
                )
            )
            self.stats.count(family, "withdrawn")
            # Enhancements of a withdrawn control are themselves withdrawn in
            # this catalog, but recurse anyway so each one is logged by name.
            for child in control.get("controls", []):
                rows.extend(self._parse_control(child, family, family_title, parent=control))
            return rows

        is_enhancement = parent is not None
        chunk_type = "control_enhancement" if is_enhancement else "control"
        printed = display_id(control_id)
        title = control.get("title", "")

        if is_enhancement:
            parent_printed = display_id(parent["id"])
            # NIST prints enhancements as "AC-2(1) Account Management |
            # Automated System Account Management" - parent title, then the
            # enhancement's own title.
            heading = f"{printed} {parent.get('title', '')} | {title}"
            base_path = f"{family.upper()} > {parent_printed} > {printed}"
            context = f"Family: {family_title} ({family.upper()}) > {parent_printed} {parent.get('title', '')}"
        else:
            heading = f"{printed} {title}"
            base_path = f"{family.upper()} > {printed}"
            context = f"Family: {family_title} ({family.upper()})"

        statements = parts_named(control, "statement")
        if statements:
            body_lines: list[str] = []
            for statement in statements:
                body_lines.extend(
                    flatten_part(statement, self.params, self.stats, include_own_label=False)
                )
            body = "\n".join(line for line in body_lines if line.strip())
            # The heading and family line make the chunk self-describing: a
            # retrieved control should identify itself without its neighbours.
            text = f"{heading}\n{context}\n\n{body}"
            rows.append(
                self._row(self.doc, chunk_type, control_id, text, control_id, base_path)
            )
            self.stats.count(family, "enhancement" if is_enhancement else "control")
        else:
            self.rejections.append(
                Rejection(
                    doc_id=self.doc.doc_id,
                    ref=control_id,
                    rule="no_statement",
                    detail=f"{printed} has no 'statement' part",
                )
            )

        for guidance in parts_named(control, "guidance"):
            body = "\n".join(flatten_part(guidance, self.params, self.stats, include_own_label=False))
            if not body.strip():
                continue
            text = f"{printed} {title} - Discussion\n{context}\n\n{body}"
            rows.append(
                self._row(
                    self.doc, "control_discussion", control_id, text,
                    control_id, f"{base_path} > Discussion",
                )
            )
            self.stats.count(family, "discussion")

        rows.extend(self._parse_assessment(control, family, family_title, base_path, printed, title))

        for child in control.get("controls", []):
            rows.extend(self._parse_control(child, family, family_title, parent=control))
        return rows

    def _parse_assessment(
        self, control: dict[str, Any], family: str, family_title: str,
        base_path: str, printed: str, title: str,
    ) -> list[Row]:
        """Assessment objectives and methods, attributed to SP 800-53A.

        Objectives are emitted as one row per control (the whole objective tree)
        and methods as one row per control (all methods together) rather than
        one row per leaf: individual leaves are single clauses that carry no
        meaning on their own once retrieved out of context.
        """
        if self.assessment_doc is None:
            return []
        rows: list[Row] = []
        control_id = control["id"]

        objectives = parts_named(control, "assessment-objective")
        if objectives:
            lines: list[str] = []
            for objective in objectives:
                lines.extend(flatten_part(objective, self.params, self.stats, include_own_label=False))
            body = "\n".join(line for line in lines if line.strip())
            if body.strip():
                text = (
                    f"{printed} {title} - Assessment Objective\n"
                    f"Determine if the following are satisfied:\n\n{body}"
                )
                rows.append(
                    self._row(
                        self.assessment_doc, "assessment_objective", control_id, text,
                        control_id, f"{base_path} > Assessment Objective",
                    )
                )
                self.stats.count(family, "objective")

        methods = parts_named(control, "assessment-method")
        if methods:
            blocks: list[str] = []
            for method in methods:
                method_name = next(
                    (p.get("value") for p in method.get("props", []) if p.get("name") == "method"),
                    "METHOD",
                )
                object_lines: list[str] = []
                for part in method.get("parts", []):
                    object_lines.extend(
                        flatten_part(part, self.params, self.stats, include_own_label=False)
                    )
                objects = "\n".join(line for line in object_lines if line.strip())
                if objects.strip():
                    blocks.append(f"{method_name.title()}: {objects.strip()}")
            if blocks:
                text = (
                    f"{printed} {title} - Assessment Methods and Objects\n\n"
                    + "\n\n".join(blocks)
                )
                rows.append(
                    self._row(
                        self.assessment_doc, "assessment_method", control_id, text,
                        control_id, f"{base_path} > Assessment Methods",
                    )
                )
                self.stats.count(family, "method")
        return rows


def parse_catalog(
    catalog_path: Path,
    doc: Document,
    assessment_doc: Document | None,
    sha256_source: str,
) -> tuple[list[Row], list[Rejection], ParseStats]:
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))["catalog"]
    parser = CatalogParser(doc, assessment_doc, sha256_source, catalog)
    rows = parser.parse()
    return rows, parser.rejections, parser.stats


# --- profiles (the 800-53B baselines) ---------------------------------------

def parse_profile(profile_path: Path, doc: Document, sha256_source: str) -> list[Row]:
    """One row per baseline: a naming sentence plus the sorted control list.

    A baseline is a membership list, so the useful chunk is the list itself —
    splitting it would destroy the only thing it says.
    """
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))["profile"]

    control_ids: list[str] = []
    for import_entry in profile.get("imports", []):
        for include in import_entry.get("include-controls", []):
            control_ids.extend(include.get("with-ids", []))

    unique_ids = sorted(set(control_ids))
    baseline_name = doc.doc_id.rsplit("-", 1)[-1]
    enhancements = [cid for cid in unique_ids if "." in cid]
    base_controls = [cid for cid in unique_ids if "." not in cid]

    text = (
        f"SP 800-53B {baseline_name} baseline. "
        f"{profile.get('metadata', {}).get('title', doc.title)}. "
        f"The {baseline_name} baseline selects {len(unique_ids)} controls and control "
        f"enhancements ({len(base_controls)} base controls, {len(enhancements)} enhancements) "
        f"from the SP 800-53 Rev 5 catalog. Selected control identifiers:\n\n"
        + ", ".join(display_id(cid) for cid in unique_ids)
    )

    return [
        Row(
            id=make_id(doc.doc_id, "baseline", baseline_name),
            text=normalize_text(text),
            doc_id=doc.doc_id,
            doc_title=doc.title,
            revision=doc.revision,
            pub_date=doc.pub_date,
            tier=doc.tier,
            chunk_type="baseline",
            # A baseline is a set of controls, not one control; giving it a
            # control_id would misrepresent what the row is.
            control_id=None,
            section_path=f"SP 800-53B Baselines > {baseline_name}",
            source_url=doc.effective_source_url,
            sha256_source=sha256_source,
        )
    ]


# --- output ------------------------------------------------------------------

def write_rows(path: Path, rows: Iterable[Row]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(row.to_json_line() + "\n")
            count += 1
    return count


def write_rejections(path: Path, rejections: Iterable[Rejection]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for rejection in rejections:
            handle.write(rejection.to_json_line() + "\n")
            count += 1
    return count
