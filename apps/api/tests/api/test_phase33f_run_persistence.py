"""Phase 33F — calculation run persistence tests.

Asserts that a successful run produces a payload that PostgREST/JSONB can
accept. The bug fixed here: ``model_dump()`` left ``Decimal`` and ``UUID``
values in the dicts assigned to ``RunRecord.summary_payload`` and
``RunRecord.rows_payload``; supabase-py then called ``json.dumps()`` on
them and raised ``TypeError``, surfacing as a raw HTTP 500.

The fix converts those Python objects to JSON primitives in the Postgres
mapper (``run_record_to_row``) — at the persistence boundary, so the
in-memory representation keeps its rich types but the wire format is
always JSON-safe. The tests below pin the contract end-to-end:

  1. ``run_record_to_row`` output is fully JSON-serialisable.
  2. ``POST /runs`` succeeds end-to-end against the in-memory store.
  3. Preflight guards (Phase 33D classification_required) still fire.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _create_project(client: TestClient, methodology: str = "protein_tracker") -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "FY 2024",
            "methodologies_enabled": [methodology],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _full_pipeline(client: TestClient, pt_tiny_csv: bytes) -> tuple[str, str]:
    pid = _create_project(client)
    upload = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("data.csv", pt_tiny_csv, "text/csv")},
    ).json()
    client.post(
        f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
        json={"methodology": "protein_tracker"},
    )
    queue = client.get(f"/api/v1/projects/{pid}/review").json()["items"]
    for item in queue:
        client.post(
            f"/api/v1/projects/{pid}/review/{item['product_id']}/protein_tracker/decision",
            json={"decision": "changed", "to_category": "plant_based_core"},
        )
    return pid, upload["id"]


class TestRunRecordSerialisable:
    """The dicts stored on RunRecord must be JSON-serialisable for Postgres."""

    def test_run_record_payloads_json_dumpable(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid, _ = _full_pipeline(client, pt_tiny_csv)
        r = client.post(f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"})
        assert r.status_code == 201, r.text
        # The response includes the summary payload; verify it round-trips.
        body = r.json()
        # Re-encoding the API response should never raise.
        json.dumps(body)

    def test_run_record_to_row_is_json_serialisable(self) -> None:
        """Build a real RunRecord end-to-end and pass it through the
        Postgres mapper. This is the test that would have caught the
        staging 500: the mapper output is what supabase-py json.dumps()'s.
        The legacy ``model_dump()`` left UUID/Decimal leaves in the dicts.
        """
        from datetime import UTC, datetime
        from decimal import Decimal
        from uuid import uuid4

        from altera_api.api.orchestrator import PT_VERSIONS
        from altera_api.api.state import InMemoryStore, RunRecord
        from altera_api.calculation.protein_tracker import calculate_pt_run
        from altera_api.domain.common import ClassificationSource, Methodology
        from altera_api.domain.product import (
            NormalizedProduct,
            ProteinSource,
            PTProductFields,
        )
        from altera_api.domain.protein_tracker import (
            ProteinTrackerGroup,
            ProteinTrackerProductClassification,
        )
        from altera_api.persistence.mappers import run_record_to_row

        oid, pid, upid, prodid = uuid4(), uuid4(), uuid4(), uuid4()
        product = NormalizedProduct(
            id=prodid,
            upload_id=upid,
            project_id=pid,
            organisation_id=oid,
            row_number=1,
            external_product_id="P-001",
            product_name="Red Lentil Soup",
            weight_per_item_kg=Decimal("0.4"),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            pt_fields=PTProductFields(
                items_purchased=Decimal("100"),
                protein_pct=Decimal("5.0"),
                protein_source=ProteinSource.REFERENCE_DB,
            ),
            created_at=datetime.now(UTC),
        )
        classification = ProteinTrackerProductClassification(
            product_id=prodid,
            pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="test",
            updated_at=datetime.now(UTC),
        )
        result = calculate_pt_run(
            [product],
            {prodid: classification},
            run_id=uuid4(),
            reporting_period_label="FY24",
            versions=PT_VERSIONS,
        )
        # Mirror what run_calculation produces in memory: model_dump()
        # keeps Decimal/UUID objects. The Postgres mapper is responsible
        # for converting them to JSON primitives at the persistence boundary.
        record = RunRecord(
            id=result.summary.run_id,
            project_id=pid,
            organisation_id=oid,
            methodology=Methodology.PROTEIN_TRACKER,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            triggered_by=uuid4(),
            rows_payload=[r.model_dump() for r in result.rows],
            summary_payload=result.summary.model_dump(),
            rows_count=len(result.rows),
        )

        # Smoke: in-memory store accepts it.
        store = InMemoryStore()
        store.add_run(record)

        # The crucial assertion: mapper output is JSON-serialisable.
        row = run_record_to_row(record)
        encoded = json.dumps(row)  # must not raise — this is what
        # supabase-py does under the hood, and the prior raw 500 came
        # from this call throwing TypeError on Decimal/UUID.
        assert len(encoded) > 0
        # Spot-check the JSONB payloads contain stringified primitives.
        assert isinstance(row["summary_payload"], dict)
        # plant_protein_kg was a Decimal in memory — must be str on the wire.
        assert isinstance(row["summary_payload"]["plant_protein_kg"], str)
        assert isinstance(row["summary_payload"]["run_id"], str)


class TestRunResponseStructure:
    def test_successful_run_returns_201_with_id(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid, _ = _full_pipeline(client, pt_tiny_csv)
        r = client.post(f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"})
        assert r.status_code == 201
        body = r.json()
        assert "id" in body
        assert body["methodology"] == "protein_tracker"
        assert body["rows_count"] > 0

    def test_summary_payload_contains_protein_totals(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid, _ = _full_pipeline(client, pt_tiny_csv)
        r = client.post(f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"})
        body = r.json()
        # mode="json" serialises Decimal → str; the response carries strings.
        assert "plant_protein_kg" in body["summary"]
        assert "animal_protein_kg" in body["summary"]


class TestPreflightGuardsStillWork:
    """The Phase 33D structured guards must continue to fire."""

    def test_classification_required_remains_structured(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("data.csv", pt_tiny_csv, "text/csv")},
        )
        r = client.post(f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"})
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "classification_required"
