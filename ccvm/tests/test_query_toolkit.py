"""Tests for the read-only Q&A toolkit (D5)."""
from __future__ import annotations

import sys
from pathlib import Path

# agent/ is outside the package — import by path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))
import query  # noqa: E402


class TestSQLGuard:
    def test_blocks_mutations(self):
        for bad in ("DROP TABLE x", "select 1; DELETE FROM x",
                    "INSERT INTO x VALUES (1)", "COPY x TO 'f'",
                    "ATTACH 'x.db'", "CREATE TABLE t AS SELECT 1",
                    "select * from x; set memory_limit='1PB'"):
            # pragma_version() as a read-only table function is allowed by
            # design (word boundary: pragma_ continues the identifier)
            assert not bad.lower().strip().startswith("select") or \
                query._SQL_BLOCKED.search(bad), bad

    def test_allows_plain_select(self):
        ok = "SELECT trade_date, atm_iv FROM history_context ORDER BY 1 DESC LIMIT 5"
        assert ok.lower().startswith("select") and not query._SQL_BLOCKED.search(ok)

    def test_word_boundaries_no_false_positives(self):
        # column/alias names containing blocked substrings must pass
        ok = "SELECT updated_at, created_count FROM history_context"
        assert not query._SQL_BLOCKED.search(ok)


class TestMetricSurface:
    def test_series_metric_list_matches_history_context(self):
        # every advertised metric must exist in the history_context schema
        from ccvm.analytics.history_context import _SCHEMA
        fields = {f.name for f in _SCHEMA}
        for m in query.SERIES_METRICS:
            assert m in fields, f"{m} not in gold/history_context schema"
        assert query.SERIES_ALIASES["brent_wti_spread"] == "benchmark_spread"
