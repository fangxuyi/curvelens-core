from ccvm.reporting.dashboard_news import build_validated_news


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

    assert result[0]["title"] == "news:missing"
    assert result[0]["url"] is None
