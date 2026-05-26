"""Phase WWF-I hotfix — PT+WWF upload regression: zero-product CSV.

Before the hotfix, uploading a PT-shaped CSV (``items_purchased`` but no
``items_sold`` / ``retail_channel`` / ``is_own_brand``) to a project
with both PT and WWF methodologies enabled produced **zero** ingested
products: every row's normalizer call collected ``items_sold`` /
``retail_channel`` / ``is_own_brand`` missing-required errors and
returned ``None``, and the pipeline silently dropped the row.

After the hotfix:

  * A row that satisfies only one methodology is ingested with the
    **satisfiable subset** as its ``methodologies_enabled``.
  * The dropped methodology produces ``ValidationWarning``s naming the
    missing fields per row.
  * A row that satisfies **no** methodology produces a single
    ``no_methodology_satisfiable`` ``ValidationError`` — the CSV is
    never silently emptied.

These tests cover the audit checklist (sections A–E) in the bug brief.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from uuid import uuid4

from altera_api.domain.common import Methodology
from altera_api.ingestion.pipeline import ingest_csv_bytes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _csv(rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


def _ingest(
    csv_bytes: bytes, methodologies: frozenset[Methodology]
):
    return ingest_csv_bytes(
        csv_bytes,
        upload_id=uuid4(),
        project_id=uuid4(),
        organisation_id=uuid4(),
        methodologies_enabled=methodologies,
        now=datetime.now(UTC),
    )


# Common shapes -------------------------------------------------------------

_PT_ONLY_ROW = {
    "external_product_id": "P-001",
    "product_name": "Red Lentil Soup",
    "weight_per_item_kg": "0.4",
    "items_purchased": "1200",
    "protein_pct": "4.5",
}

_WWF_ONLY_ROW = {
    "external_product_id": "W-001",
    "product_name": "Pizza Jambon",
    "weight_per_item_kg": "0.4",
    "items_sold": "950",
    "retail_channel": "grocery_ambient",
    "is_own_brand": "false",
}

_DUAL_ROW = {
    "external_product_id": "D-001",
    "product_name": "Lentilles Vertes du Puy",
    "weight_per_item_kg": "0.5",
    "items_purchased": "1000",
    "items_sold": "950",
    "retail_channel": "grocery_ambient",
    "is_own_brand": "false",
    "protein_pct": "9.0",
}


# ---------------------------------------------------------------------------
# A. PT-only still works (non-regression)
# ---------------------------------------------------------------------------


class TestPTOnly:
    def test_pt_only_project_ingests_pt_csv(self) -> None:
        result = _ingest(
            _csv([_PT_ONLY_ROW]),
            methodologies=frozenset({Methodology.PROTEIN_TRACKER}),
        )
        assert result.read_error is None
        assert len(result.products) == 1
        p = result.products[0]
        assert p.pt_fields is not None
        assert p.wwf_fields is None
        assert Methodology.PROTEIN_TRACKER in p.methodologies_enabled
        assert Methodology.WWF not in p.methodologies_enabled


# ---------------------------------------------------------------------------
# B. WWF-only works
# ---------------------------------------------------------------------------


class TestWWFOnly:
    def test_wwf_only_project_ingests_wwf_csv(self) -> None:
        result = _ingest(
            _csv([_WWF_ONLY_ROW]),
            methodologies=frozenset({Methodology.WWF}),
        )
        assert result.read_error is None
        assert len(result.products) == 1
        p = result.products[0]
        assert p.wwf_fields is not None
        assert p.pt_fields is None
        assert Methodology.WWF in p.methodologies_enabled


# ---------------------------------------------------------------------------
# C. PT+WWF with full union fields works
# ---------------------------------------------------------------------------


class TestPTWWFFullUnion:
    def test_dual_methodology_full_csv_ingests(self) -> None:
        result = _ingest(
            _csv([_DUAL_ROW]),
            methodologies=frozenset(
                {Methodology.PROTEIN_TRACKER, Methodology.WWF}
            ),
        )
        assert result.read_error is None
        assert len(result.products) == 1
        p = result.products[0]
        assert p.pt_fields is not None
        assert p.wwf_fields is not None
        assert Methodology.PROTEIN_TRACKER in p.methodologies_enabled
        assert Methodology.WWF in p.methodologies_enabled


# ---------------------------------------------------------------------------
# D. PT+WWF with PT-only CSV ingests as PT-only (the regression scenario)
# ---------------------------------------------------------------------------


class TestPTWWFFallback:
    def test_pt_csv_on_dual_project_ingests_as_pt_only(self) -> None:
        """The exact regression scenario from the user bug report. A
        PT-shaped CSV uploaded to a PT+WWF project must still produce
        products (PT-only); WWF fields missing are warnings, not zero-
        product blockers."""
        result = _ingest(
            _csv([_PT_ONLY_ROW]),
            methodologies=frozenset(
                {Methodology.PROTEIN_TRACKER, Methodology.WWF}
            ),
        )
        assert result.read_error is None
        # The critical assertion — products are NOT silently zeroed out.
        assert len(result.products) == 1, (
            "regression: PT-shaped CSV on PT+WWF project produced "
            f"{len(result.products)} products instead of 1"
        )
        p = result.products[0]
        assert p.pt_fields is not None
        assert p.wwf_fields is None
        # Per-row methodologies_enabled is downgraded to PT only.
        assert Methodology.PROTEIN_TRACKER in p.methodologies_enabled
        assert Methodology.WWF not in p.methodologies_enabled
        # The user sees a clear "WWF block dropped" warning per row.
        codes = {w.code for w in result.report.warnings}
        assert "missing_for_methodology" in codes
        wwf_missing_fields = {
            w.field
            for w in result.report.warnings
            if w.code == "missing_for_methodology"
        }
        assert {"items_sold", "retail_channel", "is_own_brand"} <= (
            wwf_missing_fields
        )

    def test_wwf_csv_on_dual_project_ingests_as_wwf_only(self) -> None:
        """Inverse of the regression: a WWF-shaped CSV on a PT+WWF
        project still ingests, downgraded to WWF-only."""
        result = _ingest(
            _csv([_WWF_ONLY_ROW]),
            methodologies=frozenset(
                {Methodology.PROTEIN_TRACKER, Methodology.WWF}
            ),
        )
        assert result.read_error is None
        assert len(result.products) == 1
        p = result.products[0]
        assert p.wwf_fields is not None
        assert p.pt_fields is None
        assert Methodology.WWF in p.methodologies_enabled
        assert Methodology.PROTEIN_TRACKER not in p.methodologies_enabled
        # Warning about the missing PT field.
        codes = {w.code for w in result.report.warnings}
        assert "missing_for_methodology" in codes
        pt_missing_fields = {
            w.field
            for w in result.report.warnings
            if w.code == "missing_for_methodology"
        }
        assert "items_purchased" in pt_missing_fields

    def test_dual_csv_with_missing_wwf_partial_field(self) -> None:
        """If only SOME WWF fields are missing, WWF block can't be
        built — the row is ingested as PT-only with warnings naming
        the specific WWF fields that were missing."""
        partial = dict(_DUAL_ROW)
        del partial["retail_channel"]  # missing one WWF field
        result = _ingest(
            _csv([partial]),
            methodologies=frozenset(
                {Methodology.PROTEIN_TRACKER, Methodology.WWF}
            ),
        )
        assert len(result.products) == 1
        p = result.products[0]
        assert p.pt_fields is not None
        assert p.wwf_fields is None
        # Warning specifically names the missing field.
        wwf_missing = {
            w.field
            for w in result.report.warnings
            if w.code == "missing_for_methodology"
        }
        assert "retail_channel" in wwf_missing


# ---------------------------------------------------------------------------
# Row with neither methodology satisfiable produces a clear error
# ---------------------------------------------------------------------------


class TestNoMethodologySatisfiable:
    def test_pt_wwf_project_row_missing_both_methodology_blocks(self) -> None:
        """A row with neither PT nor WWF data on a PT+WWF project
        produces a structured ``no_methodology_satisfiable`` error,
        NOT a silent skip. The user can see exactly why each row was
        dropped."""
        broken = {
            "external_product_id": "B-001",
            "product_name": "Mystery Item",
            "weight_per_item_kg": "0.4",
            # No items_purchased (PT-fail), no items_sold (WWF-fail).
        }
        result = _ingest(
            _csv([broken]),
            methodologies=frozenset(
                {Methodology.PROTEIN_TRACKER, Methodology.WWF}
            ),
        )
        assert len(result.products) == 0
        # Exactly the structured error code described in the brief.
        assert any(
            e.code == "no_methodology_satisfiable"
            for e in result.report.errors
        ), [e.code for e in result.report.errors]


# ---------------------------------------------------------------------------
# F. Synonym mapping still satisfies WWF requirements
# ---------------------------------------------------------------------------


class TestSynonymsSatisfyWWF:
    def test_french_aliases_satisfy_dual_methodology(self) -> None:
        """Headers in French/English aliases (Phase WWF-E vocabulary)
        still produce a valid PT+WWF row when both methodologies are
        enabled."""
        row = {
            "Identifiant produit": "FR-001",
            "Nom du produit": "Lentilles Vertes du Puy",
            "Poids unitaire kg": "0.5",
            "Volume": "1000",                # → items_purchased (PT default)
            "Ventes unités": "950",          # → items_sold (Phase WWF-E)
            "Canal": "grocery_ambient",      # → retail_channel
            "Marque distributeur": "false",  # → is_own_brand
            "Proteines totales": "9.0",      # → protein_pct
        }
        result = _ingest(
            _csv([row]),
            methodologies=frozenset(
                {Methodology.PROTEIN_TRACKER, Methodology.WWF}
            ),
        )
        assert result.read_error is None
        assert len(result.products) == 1, [
            (e.field, e.code) for e in result.report.errors
        ]
        p = result.products[0]
        assert p.pt_fields is not None
        assert p.wwf_fields is not None
