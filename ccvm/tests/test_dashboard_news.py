from ccvm.reporting.dashboard_news import build_validated_news, news_artifacts_ready


def test_validated_news_excludes_routed_but_unused_articles_and_ranks_top_view():
    packets = [{"relevant_news": [
        {"article_id": "news:used", "title": "Used", "published_at": "2026-07-21"},
        {"article_id": "news:ignored", "title": "Ignored", "published_at": "2026-07-22"},
    ]}]
    analysis = {
        "specialist_analyses": {
            "curve": {
                "news_findings": [{"claim": "Used by curve", "evidence_ids": ["news:used"]}],
                "data_news_comparison": [{
                    "claim": "Price and news agree",
                    "evidence_ids": ["feature:curve:date", "news:used"],
                }],
            },
            "vol": {
                "news_findings": [{"claim": "Used by vol", "evidence_ids": ["news:used"]}],
                "data_news_comparison": [],
            },
        },
        "synthesis": {"top_views": [{
            "title": "Main view", "evidence_relationship": "cross_supported",
            "supporting_evidence": [{"evidence_ids": ["news:used"]}],
        }]},
    }

    result = build_validated_news(analysis, packets)

    assert [item["article_id"] for item in result] == ["news:used"]
    assert result[0]["roles"] == ["curve", "vol"]
    assert result[0]["market_comparisons"] == ["Price and news agree"]
    assert result[0]["top_view_titles"] == ["Main view"]
    assert result[0]["top_view_relationships"] == ["cross_supported"]


def test_validated_news_handles_missing_packet_metadata():
    analysis = {"specialist_analyses": {"macro": {
        "news_findings": [{"claim": "A finding", "evidence_ids": ["news:missing"]}],
        "data_news_comparison": [],
    }}}

    result = build_validated_news(analysis, [])

    assert result == []


def test_rejected_news_is_not_promoted_and_context_is_retained():
    packets = [{"packet_id": "packet-1", "relevant_news": [
        {"article_id": "news:enforcement", "title": "Bank enforcement action"},
        {"article_id": "news:context", "title": "Later market context", "published_at": "2026-07-22"},
    ]}]
    analysis = {
        "packet_id": "packet-1", "trade_date": "2026-07-21",
        "specialist_analyses": {"macro": {
            "news_findings": [
                {"claim": "This is not relevant to Gold.", "relevance": "rejected",
                 "evidence_ids": ["news:enforcement"]},
                {"claim": "Published after the settlement.", "relevance": "context_only",
                 "evidence_ids": ["news:context"]},
            ],
            "data_news_comparison": [],
        }},
    }

    result = build_validated_news(analysis, packets, expected_packet_id="packet-1")

    assert [item["article_id"] for item in result] == ["news:context"]
    assert result[0]["relevance"] == "context_only"
    assert result[0]["timing"] == "post_trade_date"


def test_contemporaneous_news_ranks_ahead_of_post_trade_top_view_context():
    packets = [{"packet_id": "packet-1", "relevant_news": [
        {"article_id": "news:current", "title": "Current", "published_at": "2026-07-21"},
        {"article_id": "news:later", "title": "Later", "published_at": "2026-07-22"},
    ]}]
    analysis = {
        "packet_id": "packet-1", "trade_date": "2026-07-21",
        "specialist_analyses": {"macro": {
            "news_findings": [
                {"claim": "Current evidence", "relevance": "relevant",
                 "evidence_ids": ["news:current"]},
                {"claim": "Later context", "relevance": "context_only",
                 "evidence_ids": ["news:later"]},
            ],
            "data_news_comparison": [],
        }},
        "synthesis": {"top_views": [{
            "title": "Main view", "evidence_relationship": "cross_supported",
            "supporting_evidence": [{"evidence_ids": ["news:later"]}],
        }]},
    }

    result = build_validated_news(analysis, packets, expected_packet_id="packet-1")

    assert [item["article_id"] for item in result] == ["news:current", "news:later"]


def test_news_artifacts_must_share_completed_packet_identity():
    analysis = {"packet_id": "old"}
    packets = [{"role": "macro", "packet_id": "new"}]

    assert news_artifacts_ready(analysis, {"phase": "QC_REVIEW_REQUIRED", "packet_id": "new"}, packets)[0] is False
    assert news_artifacts_ready(analysis, {"phase": "COMPLETE", "packet_id": "new"}, packets)[0] is False
    assert news_artifacts_ready(
        {"packet_id": "new"}, {"phase": "COMPLETE", "packet_id": "new"}, packets,
    ) == (True, "")
