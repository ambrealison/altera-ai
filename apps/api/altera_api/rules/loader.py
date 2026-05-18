"""YAML rule loader.

Each YAML file under ``altera_api/rules/data/<methodology>/`` contains a
top-level list of rule documents. The loader returns a typed
``RuleSet`` keyed by methodology, with rules sorted by ``(priority, id)``
for deterministic application.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter

from altera_api.domain.common import Methodology
from altera_api.rules.schema import PTRule, Rule, WWFRule

_RULE_ADAPTER = TypeAdapter(Rule)

#: Directory bundled with the package, holding the default rule set.
DEFAULT_DATA_DIR: Path = Path(__file__).parent / "data"


@dataclass(frozen=True)
class RuleSet:
    pt: tuple[PTRule, ...]
    wwf: tuple[WWFRule, ...]

    def for_methodology(self, methodology: Methodology) -> tuple[PTRule, ...] | tuple[WWFRule, ...]:
        return self.pt if methodology is Methodology.PROTEIN_TRACKER else self.wwf

    def __len__(self) -> int:
        return len(self.pt) + len(self.wwf)


def load_rules_from_yaml(documents: list[dict[str, Any]]) -> list[Rule]:
    """Parse a list of YAML documents into typed ``Rule`` instances."""
    return [_RULE_ADAPTER.validate_python(doc) for doc in documents]


def load_rules_from_file(path: Path) -> list[Rule]:
    """Load one YAML file. The file's top-level must be a list."""
    with path.open("rb") as f:
        payload = yaml.safe_load(f)
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise ValueError(f"{path}: top-level must be a list of rule documents")
    return load_rules_from_yaml(payload)


def load_rules_from_dir(root: Path | None = None) -> RuleSet:
    """Load every ``*.yaml`` file under ``root`` (recursively).

    A rule's methodology determines the bucket it lands in. Duplicate
    rule ids across files are rejected.
    """
    base = root or DEFAULT_DATA_DIR
    pt: list[PTRule] = []
    wwf: list[WWFRule] = []
    seen_ids: set[str] = set()

    if not base.exists():
        return RuleSet(pt=(), wwf=())

    for path in sorted(base.rglob("*.yaml")):
        for rule in load_rules_from_file(path):
            if rule.id in seen_ids:
                raise ValueError(f"duplicate rule id {rule.id!r} (file {path})")
            seen_ids.add(rule.id)
            if isinstance(rule, PTRule):
                pt.append(rule)
            else:
                wwf.append(rule)

    pt.sort(key=lambda r: (r.priority, r.id))
    wwf.sort(key=lambda r: (r.priority, r.id))
    return RuleSet(pt=tuple(pt), wwf=tuple(wwf))
