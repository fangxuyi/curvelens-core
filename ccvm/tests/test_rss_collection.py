from datetime import date
from unittest.mock import MagicMock

import pytest

from ccvm.collectors import rss


SOURCE = {"key": "official", "name": "Official news", "url": "https://example.org/news.xml"}
CUTOFF = date(2026, 9, 8)


def item(url, published=None, title="Corn production"):
    stamp = f"<pubDate>{published}</pubDate>" if published else ""
    return f"<item><title>{title}</title><link>{url}</link>{stamp}</item>"


def feed(*items):
    return ('<?xml version="1.0"?><rss version="2.0"><channel>'
            '<title>Official</title><link>https://example.org</link>'
            '<description>News</description>' + ''.join(items) + '</channel></rss>')


def mock_http(monkeypatch, contents):
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.side_effect = lambda url: MagicMock(text=contents[url])
    monkeypatch.setattr(rss.httpx, "Client", lambda **kwargs: client)


def test_undated_old_notice_is_not_invented_as_current_news(monkeypatch):
    old = "https://example.org/2021/notice"
    current = "https://example.org/2026/report"
    mock_http(monkeypatch, {SOURCE['url']: feed(
        item(old), item(current, "Fri, 11 Sep 2026 12:00:00 GMT"),
        item(current, "Fri, 11 Sep 2026 12:00:00 GMT"),
    )})
    seen, diagnostics = set(), {}
    articles = rss._fetch_source(SOURCE, CUTOFF, seen, frozenset({'corn'}),
                                 diagnostics=diagnostics)
    assert [a['url'] for a in articles] == [current]
    assert articles[0]['published_at'] == '2026-09-11'
    assert old not in seen
    assert diagnostics['undated_relevant_excluded'] == 1
    assert any('no publication date' in warning for warning in diagnostics['warnings'])


def test_stale_valid_feed_records_provider_date_and_manifest_warning(monkeypatch):
    mock_http(monkeypatch, {SOURCE['url']: feed(
        item('https://example.org/old', 'Wed, 24 Sep 2025 12:00:00 GMT'),
    )})
    collector = object.__new__(rss.RSSNewsCollector)
    collector.source_id = 'rss_news_test'
    collector.sources, collector.keywords = [SOURCE], frozenset({'corn'})
    collector.raw_store, collector.manifest_db = MagicMock(), MagicMock()
    result = collector.collect(date(2026, 9, 15))
    assert result['status'] == 'warning' and result['articles'] == 0
    assert result['source_diagnostics']['official']['latest_published_at'] == '2025-09-24'
    assert 'stale feed' in result['sources']['official']
    assert '2025-09-24' in collector.manifest_db.complete_run.call_args.kwargs['notes']
    collector.raw_store.persist.assert_not_called()


def test_current_feed_without_relevant_articles_is_not_called_stale(monkeypatch):
    mock_http(monkeypatch, {SOURCE['url']: feed(
        item('https://example.org/other', 'Fri, 11 Sep 2026 12:00:00 GMT', 'Cotton production'),
    )})
    diagnostics = {}
    assert rss._fetch_source(SOURCE, CUTOFF, set(), frozenset({'corn'}),
                             diagnostics=diagnostics) == []
    assert diagnostics['warnings'] == []


def test_html_response_is_not_silently_accepted_as_empty_feed(monkeypatch):
    mock_http(monkeypatch, {SOURCE['url']: '<html><body>Access denied</body></html>'})
    with pytest.raises(ValueError, match='not a recognized RSS/Atom feed'):
        rss._fetch_source(SOURCE, CUTOFF, set(), frozenset({'corn'}))


@pytest.mark.parametrize('unchanged', [False, True])
def test_partial_source_warning_survives_storage_and_deduplication(monkeypatch, unchanged, tmp_path):
    stale = dict(SOURCE, key='stale', url='https://example.org/stale.xml')
    mock_http(monkeypatch, {
        SOURCE['url']: feed(item('https://example.org/new', 'Fri, 11 Sep 2026 12:00:00 GMT')),
        stale['url']: feed(item('https://example.org/old', 'Wed, 24 Sep 2025 12:00:00 GMT')),
    })
    collector = object.__new__(rss.RSSNewsCollector)
    collector.source_id = 'rss_news_test'
    collector.sources, collector.keywords = [SOURCE, stale], frozenset({'corn'})
    collector.raw_store, collector.manifest_db = MagicMock(), MagicMock()
    collector.manifest_db.sha256_exists_for_date.return_value = unchanged
    collector.raw_store.persist.return_value = (tmp_path / 'news.json', 'hash', 100)
    result = collector.collect(date(2026, 9, 15))
    assert result['articles'] == 1
    assert result['status'] == 'warning' and result['warning'] == 1
    assert result['skipped'] == int(unchanged)
    assert 'stale feed' in collector.manifest_db.complete_run.call_args.kwargs['notes']
