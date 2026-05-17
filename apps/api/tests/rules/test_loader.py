from __future__ import annotations

from pathlib import Path

import pytest

from altera_api.rules.loader import (
    DEFAULT_DATA_DIR,
    load_rules_from_dir,
    load_rules_from_file,
    load_rules_from_yaml,
)
from altera_api.rules.schema import PTRule, WWFRule


def test_loads_packaged_default_rules() -> None:
    rs = load_rules_from_dir()
    assert len(rs.pt) > 0
    assert len(rs.wwf) > 0
    assert all(isinstance(r, PTRule) for r in rs.pt)
    assert all(isinstance(r, WWFRule) for r in rs.wwf)


def test_rules_sorted_deterministically() -> None:
    rs = load_rules_from_dir()
    keys = [(r.priority, r.id) for r in rs.pt]
    assert keys == sorted(keys)


def test_duplicate_rule_id_rejected(tmp_path: Path) -> None:
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text(
        "- id: same\n"
        "  methodology: protein_tracker\n"
        "  category: plant_based_core\n"
        "  match: {product_name_contains: [x]}\n"
    )
    b.write_text(
        "- id: same\n"
        "  methodology: protein_tracker\n"
        "  category: animal_core\n"
        "  match: {product_name_contains: [y]}\n"
    )
    with pytest.raises(ValueError, match="duplicate rule id"):
        load_rules_from_dir(tmp_path)


def test_load_rules_from_yaml_pt_and_wwf() -> None:
    docs = [
        {
            "id": "test.pt.x",
            "methodology": "protein_tracker",
            "category": "plant_based_core",
            "match": {"product_name_contains": ["lentil"]},
        },
        {
            "id": "test.wwf.x",
            "methodology": "wwf",
            "category": {
                "wwf_food_group": "FG1",
                "wwf_fg1_subgroup": "red_meat",
            },
            "match": {"product_name_contains": ["beef"]},
        },
    ]
    out = load_rules_from_yaml(docs)
    assert len(out) == 2
    assert isinstance(out[0], PTRule)
    assert isinstance(out[1], WWFRule)


def test_load_file_top_level_must_be_list(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("key: value\n")  # mapping, not list
    with pytest.raises(ValueError, match="must be a list"):
        load_rules_from_file(p)


def test_empty_yaml_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "empty.yaml"
    p.write_text("")
    assert load_rules_from_file(p) == []


def test_default_data_dir_exists() -> None:
    assert DEFAULT_DATA_DIR.is_dir()
