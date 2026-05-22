"""Phase 34O — Bundled NEVO 2025 reference data + import diagnostics.

The full NEVO 2025 v9.0 CSV is committed at
``apps/api/altera_api/data/reference/nevo2025.csv`` so it can be
seeded from Render Shell without local Supabase credentials. The
import script accepts ``--bundled`` to read from that path.

Areas under test:

A. The bundled CSV is shipped and parseable.
B. The importer auto-detects the comma delimiter the staff-common
   "Save as CSV" Excel export produces (the official RIVM file is
   pipe-delimited; both must work).
C. The importer's row-count floor still rejects truncated imports
   unless ``--limit`` is explicitly passed.
D. The admin nutrition-references stats endpoint surfaces a
   ``nevo_sanity_pass`` flag so the wizard can tell at a glance
   whether the table is fully populated.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import AlteraRole, OrganisationType
from altera_api.domain.nevo import NevoEntry
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.main import app
from scripts import import_nevo

# ---------------------------------------------------------------------------
# A. The bundled NEVO 2025 CSV exists and is reasonable
# ---------------------------------------------------------------------------


class TestBundledFile:
    def test_bundled_path_exists(self) -> None:
        assert import_nevo._BUNDLED_NEVO_PATH.is_file(), (
            f"bundled NEVO CSV missing at {import_nevo._BUNDLED_NEVO_PATH}"
        )

    def test_bundled_file_has_expected_size_class(self) -> None:
        # The NEVO 2025 v9.0 export is roughly 1.4 MB / ~2,328 rows.
        # A drastically smaller file likely means the wrong file was
        # committed (e.g. a sample). Floor at 100 KB so the test is
        # robust to whitespace/quoting variations.
        size = import_nevo._BUNDLED_NEVO_PATH.stat().st_size
        assert size >= 100_000, f"bundled CSV is only {size} bytes"

    def test_bundled_file_is_readable_via_importer(self) -> None:
        entries = import_nevo.read_nevo(
            import_nevo._BUNDLED_NEVO_PATH, verbose=False
        )
        assert len(entries) >= import_nevo._EXPECTED_MIN_ROWS, (
            f"bundled NEVO parsed only {len(entries)} rows; expected "
            f">= {import_nevo._EXPECTED_MIN_ROWS}"
        )

    def test_bundled_entries_carry_protein_columns(self) -> None:
        entries = import_nevo.read_nevo(
            import_nevo._BUNDLED_NEVO_PATH, verbose=False
        )
        # Most entries carry a non-null PROT value. The bundled file
        # is the full RIVM dataset so ~95% should have a number.
        with_prot = sum(
            1 for e in entries if e["protein_g_per_100g"] is not None
        )
        assert with_prot >= 0.5 * len(entries)


# ---------------------------------------------------------------------------
# B. Delimiter auto-detection
# ---------------------------------------------------------------------------


_TINY_PIPE_HEADER = (
    "Food group|NEVO-code|Voedingsmiddelnaam/Dutch food name|"
    "Engelse naam/Food name|Hoeveelheid/Quantity|PROT (g)|"
    "PROTPL (g)|PROTAN (g)"
)
_TINY_COMMA_HEADER = (
    "Food group,NEVO-code,Voedingsmiddelnaam/Dutch food name,"
    "Engelse naam/Food name,Hoeveelheid/Quantity,PROT (g),"
    "PROTPL (g),PROTAN (g)"
)


class TestDelimiterSniffing:
    def test_sniff_picks_pipe_when_pipes_dominate(self) -> None:
        assert import_nevo._sniff_delimiter(_TINY_PIPE_HEADER) == "|"

    def test_sniff_picks_comma_when_commas_dominate(self) -> None:
        assert import_nevo._sniff_delimiter(_TINY_COMMA_HEADER) == ","

    def test_parser_handles_comma_delimited(
        self, tmp_path: Path
    ) -> None:
        # Minimal comma-delimited fixture mirroring the Excel-export
        # variant of the file. Two data rows so the floor check
        # doesn't apply (we call read_nevo_csv directly, not main()).
        p = tmp_path / "tiny.csv"
        p.write_text(
            _TINY_COMMA_HEADER + "\n"
            "Fruit,1,Appel,Apple,per 100g,0.3,0.3,0.0\n"
            "Poultry,2,Kipfilet,Chicken breast,per 100g,23.0,0.0,23.0\n"
        )
        entries = import_nevo.read_nevo_csv(p)
        assert len(entries) == 2
        assert entries[0]["nevo_code"] == "1"
        assert entries[1]["food_name_en"] == "Chicken breast"


# ---------------------------------------------------------------------------
# C. Importer floor still rejects truncated data
# ---------------------------------------------------------------------------


class TestImporterFloor:
    def test_expected_min_is_at_least_2000(self) -> None:
        assert import_nevo._EXPECTED_MIN_ROWS >= 2000

    def test_bundled_csv_exceeds_floor(self) -> None:
        entries = import_nevo.read_nevo(
            import_nevo._BUNDLED_NEVO_PATH, verbose=False
        )
        assert len(entries) >= import_nevo._EXPECTED_MIN_ROWS


# ---------------------------------------------------------------------------
# D. Admin stats endpoint surfaces sanity_pass
# ---------------------------------------------------------------------------


def _promote(store: InMemoryStore) -> None:
    org_id = store.default_org_id
    user_id = store.default_user_id
    existing_org = store.organisations[org_id]
    store.organisations[org_id] = Organisation(
        id=org_id,
        name=existing_org.name,
        slug=existing_org.slug,
        organisation_type=OrganisationType.ALTERA_INTERNAL,
        created_at=existing_org.created_at,
    )
    existing_user = store.users[user_id]
    store.upsert_user(
        UserProfile(
            user_id=user_id,
            organisation_id=org_id,
            email=existing_user.email,
            display_name=existing_user.display_name,
            role=AlteraRole.ALTERA_ANALYST,
            created_at=existing_user.created_at,
        )
    )


@pytest.fixture
def altera_store() -> InMemoryStore:
    s = InMemoryStore()
    _promote(s)
    return s


@pytest.fixture
def altera_client(altera_store: InMemoryStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: altera_store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


class TestAdminStatsSanity:
    def test_empty_nevo_fails_sanity(self, altera_client: TestClient) -> None:
        r = altera_client.get("/api/v1/admin/nutrition-references/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["nevo_total"] == 0
        assert body["nevo_sanity_pass"] is False
        assert body["nevo_expected_min"] >= 2000

    def test_full_nevo_passes_sanity(
        self, altera_client: TestClient, altera_store: InMemoryStore
    ) -> None:
        # Seed the floor + 1 entries — cheaper than 2000 real rows.
        # The endpoint reads len(list_nevo_entries()) so any stub
        # entries above the threshold work.
        entries = [
            NevoEntry(
                id=uuid4(),
                source_version="2025_v9.0",
                nevo_code=f"N_{i}",
                food_name_en=f"Food {i}",
                food_name_nl=f"Food {i}",
                food_group="Auto",
                quantity_basis="per 100g",
                protein_g_per_100g=Decimal("5.0"),
                plant_protein_g_per_100g=None,
                animal_protein_g_per_100g=None,
            )
            for i in range(2001)
        ]
        altera_store.seed_nevo_entries(entries)
        body = altera_client.get(
            "/api/v1/admin/nutrition-references/stats"
        ).json()
        assert body["nevo_total"] >= 2001
        assert body["nevo_sanity_pass"] is True
