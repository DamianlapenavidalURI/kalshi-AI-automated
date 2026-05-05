from __future__ import annotations

from kalshi_weather.system import datahub


def _mk_shortlisted(n: int):  # type: ignore[no-untyped-def]
    rows = []
    for i in range(n):
        rows.append((f"T{i}", {"ticker": f"T{i}"}, {}, 1.0, "daily_temperature", float(100 - i)))
    return rows


def test_select_shortlisted_ranked_keeps_top_slice() -> None:
    shortlisted = _mk_shortlisted(10)
    out = datahub._select_shortlisted_candidates(  # noqa: SLF001
        shortlisted=shortlisted,
        limit_markets=4,
        selection_mode="ranked",
        selection_pool_multiplier=3,
    )
    assert [r[0] for r in out] == ["T0", "T1", "T2", "T3"]


def test_select_shortlisted_random_samples_within_top_pool(monkeypatch) -> None:
    shortlisted = _mk_shortlisted(10)

    class _FakeRandom:
        def shuffle(self, seq):  # type: ignore[no-untyped-def]
            seq.reverse()

    monkeypatch.setattr(datahub.random, "SystemRandom", lambda: _FakeRandom())
    out = datahub._select_shortlisted_candidates(  # noqa: SLF001
        shortlisted=shortlisted,
        limit_markets=4,
        selection_mode="random",
        selection_pool_multiplier=2,
    )
    # pool_n = top 8; reversed pick should come from T7..T4
    assert [r[0] for r in out] == ["T7", "T6", "T5", "T4"]


def test_select_shortlisted_random_all_samples_across_entire_set(monkeypatch) -> None:
    shortlisted = _mk_shortlisted(10)

    class _FakeRandom:
        def shuffle(self, seq):  # type: ignore[no-untyped-def]
            seq.reverse()

    monkeypatch.setattr(datahub.random, "SystemRandom", lambda: _FakeRandom())
    out = datahub._select_shortlisted_candidates(  # noqa: SLF001
        shortlisted=shortlisted,
        limit_markets=4,
        selection_mode="random_all",
        selection_pool_multiplier=1,
    )
    assert [r[0] for r in out] == ["T9", "T8", "T7", "T6"]
