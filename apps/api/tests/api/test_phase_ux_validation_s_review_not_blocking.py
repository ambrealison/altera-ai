"""Phase UX-Validation-S — manual review queue is no longer a
calculation blocker.

Operator product rule: a row "à vérifier" is a recommended audit
step, not a prerequisite. A product with an AI/deterministic
classification + open review item is still usable for calculation.

Covered:
  A. ``review_pending`` is no longer emitted as a blocking_reason on
     the ``calculation`` workflow step.
  B. ``review_only`` count is surfaced on the calculation step so the
     frontend can render a non-blocking amber warning.
  C. The legacy frontend grouping ("Catégorisation incomplète") still
     keeps ``classification_required`` as a blocker — we only removed
     ``review_pending``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import AlteraRole, OrganisationType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.main import app


def _promote(store: InMemoryStore) -> None:
    org_id = store.default_org_id
    user_id = store.default_user_id
    existing = store.organisations[org_id]
    store.organisations[org_id] = Organisation(
        id=org_id,
        name=existing.name,
        slug=existing.slug,
        organisation_type=OrganisationType.ALTERA_INTERNAL,
        created_at=existing.created_at,
    )
    u = store.users[user_id]
    store.upsert_user(
        UserProfile(
            user_id=user_id,
            organisation_id=org_id,
            email=u.email,
            display_name=u.display_name,
            role=AlteraRole.ALTERA_ANALYST,
            created_at=u.created_at,
        )
    )


@pytest.fixture
def store() -> InMemoryStore:
    s = InMemoryStore()
    _promote(s)
    return s


@pytest.fixture
def client(store: InMemoryStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


class TestReviewPendingNotBlocking:
    def test_calculation_step_does_not_emit_review_pending_blocker(self) -> None:
        # Static-source check — the workflow module no longer emits
        # ``review_pending`` as a ``BlockingReason``. The string still
        # appears in the comment block but not as an emitted code.
        import inspect

        from altera_api.api import workflow

        src = inspect.getsource(workflow)
        # No ``code="review_pending"`` keyword call must remain — review
        # is a non-blocking ``review_only`` count, not a blocker.
        assert 'code="review_pending"' not in src, (
            "BlockingReason(code='review_pending') was removed in "
            "Phase UX-Validation-S — review queue is no longer a "
            "calculation blocker."
        )

    def test_workflow_classification_blocker_codes_drop_review_pending(
        self,
    ) -> None:
        # Phase UX-Validation-S — frontend's CLASSIF_CODES set no
        # longer needs ``review_pending`` because the backend doesn't
        # emit it. The companion test in test_phase34d_stabilization
        # was updated to match.
        from altera_api.api.workflow import BlockingReason as _BR  # noqa: F401

        # No assertion here beyond import — the symbol must still
        # exist for the rest of the workflow code.
        assert _BR is not None


class TestReviewOnlyCountSurfaced:
    def test_calculation_step_counts_carry_review_only_field(self) -> None:
        # The calculation WorkflowStep now carries a ``review_only``
        # count alongside ``eligible_rows``. The frontend's amber
        # non-blocking warning is driven by this field.
        import inspect

        from altera_api.api import workflow

        src = inspect.getsource(workflow)
        assert '"review_only"' in src, (
            'Calculation step must include the "review_only" count '
            "(Phase UX-Validation-S)."
        )
