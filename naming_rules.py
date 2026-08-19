"""Business rules for identifying, filtering and grouping production PDFs.

The module deliberately has no Streamlit dependency so the same behaviour can
be reused by the current prototype, the future web application and tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping


_CORRECTION_RE = re.compile(r"(?:[_ -]cor)$", flags=re.IGNORECASE)
_PAGE_RE = re.compile(r"^(?P<base>.*)_(?P<page>\d{2,3})$")
_PROCESSED_RE = re.compile(
    r"(?:[-_ ](?P<suffix>net|clean|moyen|leger|light|format-final|ecran|g20))$",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedPdfName:
    original_name: str
    base: str
    page_number: int | None
    page_token: str | None
    corrected: bool
    processed_suffix: str | None = None


@dataclass(frozen=True)
class GroupIssue:
    code: str
    message: str
    blocking: bool = False


@dataclass
class DocumentGroup:
    key: str
    label: str
    folder: str
    items: list[dict[str, Any]] = field(default_factory=list)
    discarded: list[dict[str, Any]] = field(default_factory=list)
    issues: list[GroupIssue] = field(default_factory=list)

    @property
    def output_name(self) -> str:
        value = f"{self.folder}_{self.label}" if self.folder else self.label
        return re.sub(r"[\\/:]+", "_", value).strip(" ._-") or "document-regroupe"

    @property
    def total_pages(self) -> int | None:
        counts = [item.get("page_count") for item in self.items]
        if not counts or any(not isinstance(count, int) for count in counts):
            return None
        return sum(counts)

    @property
    def corrected_pages(self) -> list[int]:
        return sorted(
            item["page_int"]
            for item in self.items
            if item.get("corrected") and item.get("page_int") is not None
        )

    @property
    def is_blocked(self) -> bool:
        return any(issue.blocking for issue in self.issues)


@dataclass(frozen=True)
class IgnoredItem:
    name: str
    reason: str


@dataclass
class GroupingResult:
    groups: list[DocumentGroup]
    ignored: list[IgnoredItem] = field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return any(group.is_blocked for group in self.groups)


def _normalise_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"\s+", " ", value.strip())
    return value.casefold()


def parse_pdf_name(name: str) -> ParsedPdfName:
    """Parse the production suffixes while preserving a human-friendly base."""

    stem = Path(name).stem.strip()
    processed_suffix = None
    corrected = False
    # Production suffixes occasionally arrive in either order, for example
    # ``_04_COR-ecran`` or ``_04-ecran_COR``. Peel both kinds until stable.
    while True:
        processed_match = _PROCESSED_RE.search(stem)
        correction_match = _CORRECTION_RE.search(stem)
        match = processed_match or correction_match
        if match is None:
            break
        if processed_match is not None:
            processed_suffix = processed_match.group("suffix").casefold()
            stem = stem[: processed_match.start()].rstrip(" _-")
        else:
            corrected = True
            stem = stem[: correction_match.start()].rstrip(" _-")

    page_token = None
    page_number = None
    page_match = _PAGE_RE.match(stem)
    if page_match:
        stem = page_match.group("base").rstrip(" _-")
        page_token = page_match.group("page")
        page_number = int(page_token)

    return ParsedPdfName(
        original_name=name,
        base=stem or Path(name).stem,
        page_number=page_number,
        page_token=page_token,
        corrected=corrected,
        processed_suffix=processed_suffix,
    )


def _is_hidden_or_temporary(name: str) -> bool:
    return name.startswith("._") or name.startswith("~$") or name == ".DS_Store"


def _same_payload(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_data = left.get("data")
    right_data = right.get("data")
    if isinstance(left_data, bytes) and isinstance(right_data, bytes):
        return left_data == right_data
    return str(left.get("name", "")).casefold() == str(right.get("name", "")).casefold()


def analyse_groups(items: Iterable[Mapping[str, Any]]) -> GroupingResult:
    """Group PDF inputs, choose corrections and report ambiguous situations.

    Corrected files win over the original for the same logical page. Nothing
    ambiguous is silently guessed: competing corrections, missing pages and a
    numbered multi-page file inside a page series are surfaced as issues.
    """

    builders: dict[str, DocumentGroup] = {}
    slots: dict[str, dict[tuple[str, int | None], dict[str, Any]]] = {}
    ignored: list[IgnoredItem] = []

    for source in items:
        name = str(source.get("name", ""))
        if not name or _is_hidden_or_temporary(name):
            ignored.append(IgnoredItem(name=name or "(sans nom)", reason="fichier temporaire"))
            continue
        if Path(name).suffix.casefold() != ".pdf":
            ignored.append(IgnoredItem(name=name, reason="format non PDF"))
            continue

        parsed = parse_pdf_name(name)
        folder = str(source.get("folder") or "")
        key = f"{_normalise_key(folder)}/{_normalise_key(parsed.base)}"
        group = builders.setdefault(
            key,
            DocumentGroup(key=key, label=parsed.base, folder=folder),
        )
        group_slots = slots.setdefault(key, {})

        item = dict(source)
        item.update(
            {
                "base": parsed.base,
                "page": parsed.page_token,
                "page_int": parsed.page_number,
                "corrected": parsed.corrected,
                "processed_suffix": parsed.processed_suffix,
            }
        )
        slot = ("page", parsed.page_number) if parsed.page_number is not None else ("document", None)
        existing = group_slots.get(slot)

        if existing is None:
            group_slots[slot] = item
            continue

        # A previous generated delivery found beside its production source is
        # stale input, not a competing master. Prefer the unprocessed source.
        if item.get("processed_suffix") and not existing.get("processed_suffix"):
            group.discarded.append({**item, "discard_reason": "version déjà préparée"})
            continue
        if existing.get("processed_suffix") and not item.get("processed_suffix"):
            group.discarded.append({**existing, "discard_reason": "version déjà préparée"})
            group_slots[slot] = item
            continue

        if item["corrected"] and not existing.get("corrected"):
            group.discarded.append({**existing, "discard_reason": "remplacé par la correction"})
            group_slots[slot] = item
            continue
        if existing.get("corrected") and not item["corrected"]:
            group.discarded.append({**item, "discard_reason": "correction déjà retenue"})
            continue

        group.discarded.append({**item, "discard_reason": "doublon"})
        if item["corrected"] and existing.get("corrected"):
            group.issues.append(
                GroupIssue(
                    code="multiple_corrections",
                    message=(
                        f"Plusieurs corrections existent pour la page {parsed.page_number:02d}."
                        if parsed.page_number is not None
                        else "Plusieurs versions corrigées existent pour ce document."
                    ),
                    blocking=True,
                )
            )
        elif not _same_payload(existing, item):
            group.issues.append(
                GroupIssue(
                    code="conflicting_duplicate",
                    message=(
                        f"Deux fichiers différents correspondent à la page {parsed.page_number:02d}."
                        if parsed.page_number is not None
                        else "Deux fichiers différents portent le même nom de document."
                    ),
                    blocking=True,
                )
            )

    groups: list[DocumentGroup] = []
    for key, group in builders.items():
        selected = list(slots[key].values())
        selected.sort(
            key=lambda item: (
                item.get("page_int") is None,
                item.get("page_int") or 0,
                str(item.get("name", "")).casefold(),
            )
        )
        group.items = selected

        numbered = [item for item in selected if item.get("page_int") is not None]
        standalone = [item for item in selected if item.get("page_int") is None]
        if numbered and standalone:
            group.issues.append(
                GroupIssue(
                    code="mixed_complete_and_parts",
                    message="Un document complet et des pages séparées portent le même nom.",
                    blocking=True,
                )
            )

        if len(numbered) > 1:
            for item in numbered:
                page_count = item.get("page_count")
                if isinstance(page_count, int) and page_count > 1:
                    group.issues.append(
                        GroupIssue(
                            code="multipage_component",
                            message=(
                                f"{item['name']} contient déjà {page_count} pages et ne peut pas être "
                                "fusionné automatiquement avec les pages séparées."
                            ),
                            blocking=True,
                        )
                    )

        page_numbers = sorted({item["page_int"] for item in numbered})
        if len(page_numbers) > 1:
            missing = sorted(set(range(1, page_numbers[-1] + 1)) - set(page_numbers))
            if missing:
                formatted = ", ".join(f"{page:02d}" for page in missing)
                group.issues.append(
                    GroupIssue(
                        code="missing_pages",
                        message=f"Pages manquantes : {formatted}.",
                        blocking=True,
                    )
                )

        if any(item.get("processed_suffix") for item in selected):
            group.issues.append(
                GroupIssue(
                    code="already_processed",
                    message="Le groupe contient au moins un PDF déjà traité.",
                    blocking=False,
                )
            )

        groups.append(group)

    groups.sort(key=lambda group: (group.folder.casefold(), group.label.casefold()))
    return GroupingResult(groups=groups, ignored=ignored)
