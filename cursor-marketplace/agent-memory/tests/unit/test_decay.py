"""Tests for agent_memory.backend retention scoring."""

from datetime import UTC, datetime, timedelta


class TestRetentionScore:
    """Test the hybrid Ebbinghaus + usage-based decay scoring."""

    def _score(self, **kwargs):
        from agent_memory.backend import MemoryBackend

        return MemoryBackend.retention_score(**kwargs)

    def test_fresh_memory_high_score(self):
        now = datetime.now(UTC)
        score = self._score(
            created_at=now - timedelta(minutes=5),
            last_accessed=now - timedelta(minutes=5),
            access_count=1,
            now=now,
        )
        assert score > 0.3

    def test_old_unused_memory_low_score(self):
        now = datetime.now(UTC)
        score = self._score(
            created_at=now - timedelta(days=365),
            last_accessed=now - timedelta(days=365),
            access_count=1,
            now=now,
        )
        assert score < 0.1

    def test_old_but_frequently_accessed_stays_high(self):
        now = datetime.now(UTC)
        score = self._score(
            created_at=now - timedelta(days=90),
            last_accessed=now - timedelta(hours=1),
            access_count=50,
            now=now,
        )
        assert score > 0.3

    def test_access_count_increases_retention(self):
        now = datetime.now(UTC)
        base = dict(
            created_at=now - timedelta(days=30),
            last_accessed=now - timedelta(days=7),
            now=now,
        )
        low = self._score(access_count=1, **base)
        high = self._score(access_count=20, **base)
        assert high > low

    def test_recent_access_increases_retention(self):
        now = datetime.now(UTC)
        base = dict(
            created_at=now - timedelta(days=30),
            access_count=5,
            now=now,
        )
        old_access = self._score(last_accessed=now - timedelta(days=30), **base)
        recent_access = self._score(last_accessed=now - timedelta(hours=1), **base)
        assert recent_access > old_access

    def test_score_bounded_zero_to_one(self):
        now = datetime.now(UTC)
        for days in [0.001, 1, 7, 30, 90, 365, 1000]:
            for count in [0, 1, 10, 100]:
                score = self._score(
                    created_at=now - timedelta(days=days),
                    last_accessed=now - timedelta(days=days),
                    access_count=count,
                    now=now,
                )
                assert 0 <= score <= 1.0, (
                    f"Score {score} out of bounds for days={days}, count={count}"
                )
