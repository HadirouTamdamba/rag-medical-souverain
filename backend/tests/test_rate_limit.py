import pytest

from rate_limit import SlidingWindowRateLimiter


def test_allows_up_to_the_quota_then_rejects():
    limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)

    for expected_remaining in (2, 1, 0):
        allowed, remaining, retry_after = limiter.check("ip:10.0.0.1")
        assert allowed is True
        assert remaining == expected_remaining
        assert retry_after == 0

    allowed, remaining, retry_after = limiter.check("ip:10.0.0.1")
    assert allowed is False
    assert remaining == 0
    assert retry_after >= 1


def test_quotas_are_independent_per_identity():
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)

    assert limiter.check("ip:10.0.0.1")[0] is True
    assert limiter.check("ip:10.0.0.1")[0] is False
    assert limiter.check("ip:10.0.0.2")[0] is True


def test_window_slides_forward(monkeypatch):
    clock = {"now": 1_000.0}
    monkeypatch.setattr("rate_limit.time.monotonic", lambda: clock["now"])

    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=10)
    assert limiter.check("k")[0] is True
    assert limiter.check("k")[0] is True
    assert limiter.check("k")[0] is False

    clock["now"] += 11
    assert limiter.check("k")[0] is True


def test_idle_identities_are_evicted(monkeypatch):
    clock = {"now": 1_000.0}
    monkeypatch.setattr("rate_limit.time.monotonic", lambda: clock["now"])

    limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=10)
    for index in range(50):
        limiter.check(f"ip:10.0.0.{index}")
    assert len(limiter._hits) == 50

    clock["now"] += 100
    limiter.check("ip:10.0.0.1")
    assert len(limiter._hits) == 1


def test_rejects_nonsensical_configuration():
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(max_requests=0, window_seconds=60)
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(max_requests=10, window_seconds=0)
