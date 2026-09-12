"""Unit tests for Observability metrics collector and telemetry."""

from cortexforge.observability.metrics import MetricsCollector


def test_metrics_collector_recording():
    collector = MetricsCollector()

    collector.record_request_latency(15.2)
    collector.record_request_latency(24.8)
    collector.record_retrieval_latency(8.5)
    collector.record_embedding_latency(4.2)
    collector.record_indexing_duration(120.0)

    collector.increment_memory_writes(3)
    collector.increment_memory_revisions(1)
    collector.increment_stale_detections(2)
    collector.increment_conflicts_detected(1)
    collector.increment_consolidations(1)

    collector.record_tokens(1500, 320)
    collector.record_cache_access(hit=True)
    collector.record_cache_access(hit=False)

    snap = collector.get_snapshot()

    assert snap.total_requests == 2
    assert snap.avg_request_latency_ms == 20.0
    assert snap.total_retrievals == 1
    assert snap.avg_retrieval_latency_ms == 8.5
    assert snap.memory_writes == 3
    assert snap.memory_revisions == 1
    assert snap.stale_detections == 2
    assert snap.conflicts_detected == 1
    assert snap.tokens_input == 1500
    assert snap.tokens_output == 320
    assert snap.cache_hits == 1
    assert snap.cache_misses == 1
    assert snap.cache_hit_rate_pct == 50.0


def test_safe_log_event_redaction(caplog):
    collector = MetricsCollector()
    import logging

    with caplog.at_level(logging.INFO):
        collector.safe_log_event(
            "TEST_EVENT",
            {
                "token": "sk-proj-1234567890abcdef1234567890abcdef",
                "message": "Auth succeeded",
            },
        )

    assert "sk-proj-" not in caplog.text
    assert "[REDACTED_OPENAI_KEY]" in caplog.text
