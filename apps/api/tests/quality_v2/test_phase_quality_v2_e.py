"""Phase Quality-V2-E — voyage-4-lite default, batched/observable/resumable
full-NEVO benchmark, controlled matcher factory, richer CSV + taxonomy.

All offline: every test uses the deterministic fake provider or a mocked
client. No test calls the real Voyage API or hits the network. V1 stays
the production default and embeddings stay disabled by default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from altera_api.classification_v2 import benchmark_nevo_embeddings as cli
from altera_api.classification_v2.evaluation import load_fixture
from altera_api.classification_v2.nevo_eval_embeddings import (
    evaluate_nevo_embeddings,
    summarize_candidates,
)
from altera_api.classification_v2.nevo_index import (
    NevoVectorIndex,
    load_nevo_reference,
)
from altera_api.classification_v2.nevo_matcher import (
    NevoMatcherError,
    NevoMatcherVersion,
    V1NevoMatcher,
    V2EmbeddingsNevoMatcher,
    V2RulesNevoMatcher,
    get_nevo_matcher,
    resolve_nevo_matcher_version,
)
from altera_api.classification_v2.nevo_rules import NevoCandidate
from altera_api.embeddings import (
    FakeEmbeddingProvider,
    FileEmbeddingCache,
    VoyageEmbeddingProvider,
    get_embedding_provider,
)
from altera_api.embeddings.provider import EmbeddingRateLimitError
from altera_api.quality_config import (
    DEFAULT_EMBEDDING_MODEL,
    embedding_model,
    embeddings_enabled,
)

_FIXTURE = "altera_api/data/eval/nevo/nevo_dataset_embeddings.json"


def _refs(n: int, *, suffix: str = "") -> list[dict[str, str]]:
    return [{"food_name_en": f"Food {i}{suffix}", "nevo_code": f"N{i}"} for i in range(n)]


# ---------------------------------------------------------------------------
# PART A — voyage-4-lite default, embeddings off by default.
# ---------------------------------------------------------------------------
class TestDefaultModel:
    def test_default_model_is_voyage_4_lite(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALTERA_EMBEDDING_MODEL", raising=False)
        assert DEFAULT_EMBEDDING_MODEL == "voyage-4-lite"
        assert embedding_model() == "voyage-4-lite"

    def test_embeddings_disabled_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        assert embeddings_enabled() is False

    def test_key_alone_does_not_activate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOYAGE_API_KEY", "sk-test-not-used")
        monkeypatch.setenv("ALTERA_EMBEDDING_MODEL", "voyage-4-lite")
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        assert isinstance(get_embedding_provider(), FakeEmbeddingProvider)


# ---------------------------------------------------------------------------
# PART B — batching + progress (fake; no network).
# ---------------------------------------------------------------------------
class _SpyProvider:
    """Records the size of each embed_documents batch."""

    def __init__(self) -> None:
        self.doc_batches: list[int] = []

    @property
    def model(self) -> str:
        return "spy-model"

    @property
    def dimensions(self) -> int:
        return 2

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.doc_batches.append(len(texts))
        return [[float(len(t) % 5), 1.0] for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


class TestBatching:
    def test_build_embeds_in_batches(self) -> None:
        spy = _SpyProvider()
        idx = NevoVectorIndex(provider=spy, provider_name="spy", batch_size=64)
        idx.build(_refs(150))
        assert spy.doc_batches == [64, 64, 22]
        assert idx.embedding_calls == 150  # unique texts embedded

    def test_progress_events_emitted(self) -> None:
        events = []
        idx = NevoVectorIndex(
            provider=FakeEmbeddingProvider(), provider_name="fake", batch_size=64
        )
        idx.build(_refs(150), progress=events.append)
        stages = [e.stage for e in events]
        assert stages[0] == "start"
        assert stages.count("batch") == 3  # 64 + 64 + 22
        assert events[-1].embedded == 150


# ---------------------------------------------------------------------------
# PART E — persistent, resumable cache; cache-key invalidation.
# ---------------------------------------------------------------------------
class TestPersistentCache:
    def test_second_run_avoids_embedding_calls(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        refs = _refs(10)
        idx1 = NevoVectorIndex.load_or_build(
            refs, provider=FakeEmbeddingProvider(), provider_name="fake",
            cache=FileEmbeddingCache(path),
        )
        assert idx1.embedding_calls == 10
        idx2 = NevoVectorIndex.load_or_build(
            refs, provider=FakeEmbeddingProvider(), provider_name="fake",
            cache=FileEmbeddingCache(path),
        )
        assert idx2.embedding_calls == 0  # all served from disk cache

    def test_cache_hit_miss_counters(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        refs = _refs(10)
        c1 = FileEmbeddingCache(path)
        NevoVectorIndex.load_or_build(
            refs, provider=FakeEmbeddingProvider(), provider_name="fake", cache=c1
        )
        assert (c1.hits, c1.misses) == (0, 10)
        c2 = FileEmbeddingCache(path)
        NevoVectorIndex.load_or_build(
            refs, provider=FakeEmbeddingProvider(), provider_name="fake", cache=c2
        )
        assert (c2.hits, c2.misses) == (10, 0)

    def test_changing_model_invalidates_cache(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        refs = _refs(10)
        NevoVectorIndex.load_or_build(
            refs, provider=FakeEmbeddingProvider(model="m1"),
            provider_name="fake", cache=FileEmbeddingCache(path),
        )
        idx = NevoVectorIndex.load_or_build(
            refs, provider=FakeEmbeddingProvider(model="m2"),
            provider_name="fake", cache=FileEmbeddingCache(path),
        )
        assert idx.embedding_calls == 10  # different model → different keys

    def test_changing_reference_text_invalidates_cache(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        NevoVectorIndex.load_or_build(
            _refs(10), provider=FakeEmbeddingProvider(), provider_name="fake",
            cache=FileEmbeddingCache(path),
        )
        idx = NevoVectorIndex.load_or_build(
            _refs(10, suffix=" CHANGED"), provider=FakeEmbeddingProvider(),
            provider_name="fake", cache=FileEmbeddingCache(path),
        )
        assert idx.embedding_calls == 10  # changed text → different keys

    def test_cache_persists_across_processes_via_flush(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        c = FileEmbeddingCache(path)
        NevoVectorIndex.load_or_build(
            _refs(5), provider=FakeEmbeddingProvider(), provider_name="fake", cache=c
        )
        assert path.exists() and len(FileEmbeddingCache(path)) == 5


# ---------------------------------------------------------------------------
# PART D — controlled matcher factory.
# ---------------------------------------------------------------------------
class TestMatcherFactory:
    def test_default_returns_v1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALTERA_NEVO_MATCHER_VERSION", raising=False)
        m = get_nevo_matcher()
        assert isinstance(m, V1NevoMatcher)
        assert m.version is NevoMatcherVersion.V1
        assert m.is_production_default is True

    def test_resolver_defaults_and_unknown_fall_back_to_v1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALTERA_NEVO_MATCHER_VERSION", raising=False)
        assert resolve_nevo_matcher_version() is NevoMatcherVersion.V1
        assert resolve_nevo_matcher_version("garbage") is NevoMatcherVersion.V1
        assert resolve_nevo_matcher_version("v2-rules") is NevoMatcherVersion.V2_RULES
        assert (
            resolve_nevo_matcher_version("v2-embeddings")
            is NevoMatcherVersion.V2_EMBEDDINGS
        )

    def test_env_selects_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALTERA_NEVO_MATCHER_VERSION", "v2-rules")
        assert isinstance(get_nevo_matcher(), V2RulesNevoMatcher)

    def test_v2_rules_available_offline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        m = get_nevo_matcher("v2-rules")
        r = m.gate("Tofu nature", NevoCandidate("OK", "Tofu"))
        assert r.accepted

    def test_v2_embeddings_requires_embeddings_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        with pytest.raises(NevoMatcherError):
            get_nevo_matcher("v2-embeddings")

    def test_v2_embeddings_ok_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALTERA_ENABLE_EMBEDDINGS", "true")
        assert isinstance(
            get_nevo_matcher("v2-embeddings"), V2EmbeddingsNevoMatcher
        )

    def test_v2_embeddings_evaluator_mode_allows_offline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        m = get_nevo_matcher("v2-embeddings", evaluator_mode=True)
        assert isinstance(m, V2EmbeddingsNevoMatcher)
        # No index attached → decide must fail clearly (not silently).
        with pytest.raises(NevoMatcherError):
            m.decide({"product_name": "Tofu"})

    def test_routes_do_not_import_matcher_factory(self) -> None:
        api_dir = Path(cli.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name
            for p in api_dir.rglob("*.py")
            if "nevo_matcher" in p.read_text(encoding="utf-8")
            or "get_nevo_matcher" in p.read_text(encoding="utf-8")
        ]
        assert not offenders, f"routes import the matcher factory: {offenders}"


# ---------------------------------------------------------------------------
# PART F — richer candidate CSV + failure taxonomy.
# ---------------------------------------------------------------------------
class TestCsvAndTaxonomy:
    def _run(self):
        cases = load_fixture(_FIXTURE)
        refs = load_nevo_reference("fixture")
        m, rows = evaluate_nevo_embeddings(
            cases, refs, FakeEmbeddingProvider(),
            provider_name="fake", top_k=20, model="voyage-4-lite",
        )
        return cases, refs, m, rows

    def test_candidate_rows_have_new_columns(self) -> None:
        _cases, _refs, _m, rows = self._run()
        assert rows
        r = rows[0]
        for col in ("match_type", "confidence", "model", "provider"):
            assert col in r
        assert r["model"] == "voyage-4-lite"
        assert r["provider"] == "fake"

    def test_taxonomy_has_new_buckets(self) -> None:
        cases, refs, _m, rows = self._run()
        tax = summarize_candidates(cases, rows, refs)
        for key in (
            "expected_rank_1", "expected_rank_2_5", "expected_rank_6_20",
            "expected_retrieved_but_rejected", "expected_missing_from_topk",
            "fixture_expected_not_in_reference", "no_safe_reference",
            "dangerous_ranked_high_but_rejected", "dangerous_incorrectly_accepted",
        ):
            assert key in tax
        # Safety: a forbidden candidate must never be accepted.
        assert tax["dangerous_incorrectly_accepted"] == 0

    def test_no_high_confidence_false_positives(self) -> None:
        _cases, _refs, m, _rows = self._run()
        assert m.false_positive_count == 0


# ---------------------------------------------------------------------------
# PART B/C — CLI flags (limits) + friendly rate-limit handling.
# ---------------------------------------------------------------------------
class TestCli:
    def test_limit_flags_apply(self, tmp_path: Path, capsys) -> None:
        rc = cli.main(
            ["--models", "fake", "--reference-source", "nevo",
             "--limit-references", "50", "--limit-cases", "4",
             "--cache-dir", "", "--output-dir", str(tmp_path)]
        )
        out = capsys.readouterr().out
        assert "(4 cases)" in out
        assert "(50 foods)" in out
        assert rc in (0, 1)  # gates may pass/fail; never a crash

    def test_rate_limit_is_friendly_and_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        class RateLimitError(Exception):
            pass

        class RateLimitClient:
            def embed(self, texts, **kw):
                raise RateLimitError("429 Too Many Requests")

        def fake_build(name, *, model=None, dimensions=None):
            if name == "voyage":
                return VoyageEmbeddingProvider(
                    model=model or "voyage-4-lite", client=RateLimitClient()
                )
            return FakeEmbeddingProvider()

        monkeypatch.setenv("ALTERA_ENABLE_EMBEDDINGS", "true")
        monkeypatch.setattr(cli, "build_embedding_provider", fake_build)
        rc = cli.main(
            ["--models", "voyage-4-lite", "--reference-source", "fixture",
             "--limit-cases", "3", "--cache-dir", "",
             "--output-dir", str(tmp_path)]
        )
        assert rc == 2
        out = capsys.readouterr().out
        assert "RATE LIMIT" in out
        assert "resume" in out.lower()

    def test_provider_raises_rate_limit_error(self) -> None:
        class RateLimitError(Exception):
            pass

        class Client:
            def embed(self, texts, **kw):
                raise RateLimitError("429")

        p = VoyageEmbeddingProvider(model="voyage-4-lite", client=Client())
        with pytest.raises(EmbeddingRateLimitError):
            p.embed_documents(["a"])
