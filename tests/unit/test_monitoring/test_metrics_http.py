"""Tests for runtime metrics HTTP exposition."""

from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from src.monitoring import RuntimeMetrics


def test_runtime_metrics_http_server_exposes_metrics() -> None:
    """Metrics HTTP server should expose Prometheus text on /metrics."""
    metrics = RuntimeMetrics(enabled=True, host="127.0.0.1", port=0)
    metrics.set_gauge("clitg_bot_running", 1.0)
    metrics.increment_text_requests_total(engine="claude", result="success")
    metrics.start_http_server()

    try:
        with urlopen(metrics.metrics_address(), timeout=2.0) as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert "clitg_bot_running 1" in body
        assert 'clitg_text_requests_total{engine="claude",result="success"} 1' in body
    finally:
        metrics.stop_http_server()


def test_runtime_metrics_http_server_exposes_human_summary() -> None:
    """Human-readable summary should be available on /metricsz."""
    metrics = RuntimeMetrics(enabled=True, host="127.0.0.1", port=0)
    metrics.set_gauge("clitg_bot_running", 1.0)
    metrics.set_gauge("clitg_polling_up", 1.0)
    metrics.set_gauge("clitg_storage_up", 1.0)
    metrics.increment_text_requests_total(engine="claude", result="success")
    metrics.observe_text_latency(
        "clitg_text_end_to_first_reply_seconds",
        engine="claude",
        seconds=2.4,
    )
    metrics.start_http_server()

    try:
        with urlopen(metrics.metrics_summary_address(), timeout=2.0) as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert "status: healthy" in body
        assert "text_requests:" in body
        assert "end_to_first_reply [claude]" in body
    finally:
        metrics.stop_http_server()


def test_runtime_metrics_http_server_exposes_html_summary_for_browser() -> None:
    """Browser requests should receive an HTML dashboard on /metricsz."""
    metrics = RuntimeMetrics(enabled=True, host="127.0.0.1", port=0)
    metrics.set_gauge("clitg_bot_running", 1.0)
    metrics.set_gauge("clitg_polling_up", 1.0)
    metrics.set_gauge("clitg_storage_up", 1.0)
    metrics.increment_text_requests_total(engine="codex", result="success")
    metrics.observe_text_latency(
        "clitg_text_end_to_first_reply_seconds",
        engine="codex",
        seconds=18.72,
    )
    metrics.start_http_server()

    try:
        request = Request(
            metrics.metrics_summary_address(),
            headers={"Accept": "text/html"},
        )
        with urlopen(request, timeout=2.0) as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("text/html")
        assert "<title>CLI TG Bot Metrics</title>" in body
        assert "Metrics Overview" in body
        assert "Prometheus raw" in body
        assert "end_to_first_reply" in body
    finally:
        metrics.stop_http_server()


def test_runtime_metrics_http_server_rejects_unknown_path() -> None:
    """Unknown paths should return 404 instead of exposing other surfaces."""
    metrics = RuntimeMetrics(enabled=True, host="127.0.0.1", port=0)
    metrics.start_http_server()

    try:
        with pytest.raises(HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{metrics.port}/healthz", timeout=2.0)
        assert exc_info.value.code == 404
    finally:
        metrics.stop_http_server()
