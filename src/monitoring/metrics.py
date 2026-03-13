"""Lightweight runtime metrics and HTTP exposition."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlsplit

import structlog

logger = structlog.get_logger()

_DEFAULT_SECONDS_BUCKETS: tuple[float, ...] = (
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
)


def _normalize_metric_value(value: Any) -> float:
    """Normalize metric input to a finite float."""
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(normalized) or math.isinf(normalized):
        return 0.0
    return normalized


def _format_metric_value(value: float) -> str:
    """Render a Prometheus-safe numeric literal."""
    if value.is_integer():
        return str(int(value))
    return f"{value:.17g}"


def _escape_help_text(text: str) -> str:
    """Escape HELP text according to the Prometheus exposition format."""
    return str(text).replace("\\", r"\\").replace("\n", r"\n")


def _escape_label_value(value: Any) -> str:
    """Escape label value according to the Prometheus exposition format."""
    return str(value).replace("\\", r"\\").replace("\n", r"\n").replace('"', r"\"")


@dataclass(frozen=True)
class _MetricDefinition:
    """Static metric metadata."""

    name: str
    metric_type: str
    help_text: str
    label_names: tuple[str, ...] = ()
    buckets: tuple[float, ...] = ()


@dataclass
class _HistogramSeries:
    """Series state for one histogram label-set."""

    bucket_counts: list[float]
    count: float = 0.0
    sum: float = 0.0


@dataclass
class _MetricState:
    """Runtime state for one metric."""

    definition: _MetricDefinition
    samples: dict[tuple[str, ...], float] = field(default_factory=dict)
    histograms: dict[tuple[str, ...], _HistogramSeries] = field(default_factory=dict)


@dataclass(frozen=True)
class _LatencySummaryRow:
    """Presentation-friendly snapshot for one latency series."""

    label: str
    engine: str
    count: int
    avg_seconds: float
    p50_seconds: Optional[float]
    p90_seconds: Optional[float]


class RuntimeMetrics:
    """Minimal in-process metrics registry with optional HTTP exposition."""

    def __init__(
        self,
        *,
        enabled: bool,
        host: str = "127.0.0.1",
        port: int = 9464,
    ) -> None:
        self.enabled = bool(enabled)
        self.host = str(host or "127.0.0.1").strip() or "127.0.0.1"
        self.port = int(port)
        self._lock = threading.RLock()
        self._metric_states: dict[str, _MetricState] = {}
        self._server: Optional[ThreadingHTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._process_start_time_epoch: float = time.time()
        self._register_defaults()
        self.set_gauge(
            "clitg_process_start_time_seconds", self._process_start_time_epoch
        )

    def _register_defaults(self) -> None:
        seconds_buckets = _DEFAULT_SECONDS_BUCKETS
        self._register_gauge(
            "clitg_process_start_time_seconds",
            "Unix wall-clock timestamp when the bot process started.",
        )
        self._register_gauge(
            "clitg_bot_running", "Whether the Telegram bot main loop is running."
        )
        self._register_gauge(
            "clitg_polling_up", "Whether Telegram polling is currently running."
        )
        self._register_gauge(
            "clitg_polling_restart_requested",
            "Whether polling self-recovery has been requested.",
        )
        self._register_gauge(
            "clitg_watchdog_tick_age_seconds",
            "Age in seconds since the latest polling watchdog tick.",
        )
        self._register_gauge(
            "clitg_last_health_probe_age_seconds",
            "Age in seconds since the latest successful Telegram health probe.",
        )
        self._register_gauge(
            "clitg_pending_update_count",
            "Latest Telegram pending update count reported by health probe.",
        )
        self._register_gauge(
            "clitg_storage_up", "Whether storage health check is currently healthy."
        )
        self._register_gauge(
            "clitg_active_tasks", "Current number of running user tasks."
        )
        self._register_gauge(
            "clitg_cli_active_processes",
            "Current number of active CLI subprocesses across integrations.",
        )
        self._register_counter(
            "clitg_polling_restarts_total",
            "Total polling restart attempts by reason.",
            label_names=("reason",),
        )
        self._register_counter(
            "clitg_telegram_transport_failures_total",
            "Total transient Telegram transport failures by source.",
            label_names=("source",),
        )
        self._register_counter(
            "clitg_text_requests_total",
            "Executed text requests by engine and result.",
            label_names=("engine", "result"),
        )
        self._register_counter(
            "clitg_text_requests_queued_total",
            "Queued text requests by engine.",
            label_names=("engine",),
        )
        self._register_counter(
            "clitg_text_request_failures_total",
            "Failed text request executions by engine and stage.",
            label_names=("engine", "stage"),
        )
        for name, help_text in (
            (
                "clitg_text_end_to_first_reply_seconds",
                "End-to-first-reply latency for successful text requests.",
            ),
            (
                "clitg_text_telegram_delivery_seconds",
                "Latency from Telegram message timestamp to local handler processing.",
            ),
            (
                "clitg_text_queue_wait_seconds",
                "Time spent waiting in the inbound queue before execution.",
            ),
            (
                "clitg_text_preprocess_seconds",
                "Time spent preparing a text request before CLI execution starts.",
            ),
            (
                "clitg_text_cli_exec_seconds",
                "CLI execution time for text requests.",
            ),
            (
                "clitg_text_reply_send_seconds",
                "Time spent sending the first formal Telegram reply.",
            ),
        ):
            self._register_histogram(
                name,
                help_text,
                label_names=("engine",),
                buckets=seconds_buckets,
            )

    def _register_gauge(
        self,
        name: str,
        help_text: str,
        *,
        label_names: Sequence[str] = (),
    ) -> None:
        self._metric_states[name] = _MetricState(
            definition=_MetricDefinition(
                name=name,
                metric_type="gauge",
                help_text=help_text,
                label_names=tuple(label_names),
            )
        )

    def _register_counter(
        self,
        name: str,
        help_text: str,
        *,
        label_names: Sequence[str] = (),
    ) -> None:
        self._metric_states[name] = _MetricState(
            definition=_MetricDefinition(
                name=name,
                metric_type="counter",
                help_text=help_text,
                label_names=tuple(label_names),
            )
        )

    def _register_histogram(
        self,
        name: str,
        help_text: str,
        *,
        label_names: Sequence[str] = (),
        buckets: Sequence[float],
    ) -> None:
        normalized_buckets = tuple(sorted(float(bucket) for bucket in buckets))
        self._metric_states[name] = _MetricState(
            definition=_MetricDefinition(
                name=name,
                metric_type="histogram",
                help_text=help_text,
                label_names=tuple(label_names),
                buckets=normalized_buckets,
            )
        )

    def _label_tuple(
        self,
        definition: _MetricDefinition,
        labels: Optional[Mapping[str, Any]],
    ) -> tuple[str, ...]:
        label_map = labels or {}
        return tuple(
            str(label_map.get(label_name, "")) for label_name in definition.label_names
        )

    def increment_counter(
        self,
        name: str,
        amount: float = 1.0,
        *,
        labels: Optional[Mapping[str, Any]] = None,
    ) -> None:
        normalized = max(0.0, _normalize_metric_value(amount))
        with self._lock:
            state = self._metric_states.get(name)
            if state is None or state.definition.metric_type != "counter":
                return
            key = self._label_tuple(state.definition, labels)
            state.samples[key] = state.samples.get(key, 0.0) + normalized

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Optional[Mapping[str, Any]] = None,
    ) -> None:
        normalized = _normalize_metric_value(value)
        with self._lock:
            state = self._metric_states.get(name)
            if state is None or state.definition.metric_type != "gauge":
                return
            key = self._label_tuple(state.definition, labels)
            state.samples[key] = normalized

    def observe_histogram(
        self,
        name: str,
        value: float,
        *,
        labels: Optional[Mapping[str, Any]] = None,
    ) -> None:
        normalized = max(0.0, _normalize_metric_value(value))
        with self._lock:
            state = self._metric_states.get(name)
            if state is None or state.definition.metric_type != "histogram":
                return
            key = self._label_tuple(state.definition, labels)
            series = state.histograms.get(key)
            if series is None:
                series = _HistogramSeries(
                    bucket_counts=[0.0 for _ in state.definition.buckets]
                )
                state.histograms[key] = series
            series.count += 1.0
            series.sum += normalized
            for idx, upper_bound in enumerate(state.definition.buckets):
                if normalized <= upper_bound:
                    series.bucket_counts[idx] += 1.0

    def start_http_server(self) -> None:
        """Start local HTTP exposition server when enabled."""
        if not self.enabled:
            return
        with self._lock:
            if self._server is not None:
                return

            registry = self

            class _Handler(BaseHTTPRequestHandler):
                def do_GET(self) -> None:  # noqa: N802
                    parsed_url = urlsplit(self.path)
                    if parsed_url.path == "/metrics":
                        payload = registry.render_prometheus_text().encode("utf-8")
                        content_type = "text/plain; version=0.0.4; charset=utf-8"
                    elif parsed_url.path == "/metricsz":
                        summary_format = registry._select_summary_response_format(
                            query_string=parsed_url.query,
                            accept_header=self.headers.get("Accept"),
                        )
                        if summary_format == "html":
                            payload = registry.render_human_summary_html().encode(
                                "utf-8"
                            )
                            content_type = "text/html; charset=utf-8"
                        else:
                            payload = registry.render_human_summary().encode("utf-8")
                            content_type = "text/plain; charset=utf-8"
                    else:
                        self.send_response(HTTPStatus.NOT_FOUND)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(b"404 not found\n")
                        return

                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

                def log_message(self, format: str, *args: Any) -> None:
                    return

            try:
                server = ThreadingHTTPServer((self.host, self.port), _Handler)
            except OSError as exc:
                logger.warning(
                    "Failed to start metrics HTTP server",
                    host=self.host,
                    port=self.port,
                    error=str(exc),
                )
                return

            self._server = server
            self.port = int(server.server_address[1])
            self._server_thread = threading.Thread(
                target=server.serve_forever,
                name="clitg-metrics-http",
                daemon=True,
            )
            self._server_thread.start()
            logger.info(
                "Metrics HTTP server started",
                host=self.host,
                port=self.port,
            )

    def stop_http_server(self) -> None:
        """Stop the exposition server if it was started."""
        with self._lock:
            server = self._server
            thread = self._server_thread
            self._server = None
            self._server_thread = None

        if server is None:
            return

        try:
            server.shutdown()
            server.server_close()
        except Exception as exc:
            logger.warning("Failed to stop metrics HTTP server", error=str(exc))

        if thread is not None:
            thread.join(timeout=2.0)

    def render_prometheus_text(self) -> str:
        """Render registry content using Prometheus text exposition format."""
        with self._lock:
            lines: list[str] = []
            for name in sorted(self._metric_states.keys()):
                state = self._metric_states[name]
                definition = state.definition
                lines.append(f"# HELP {name} {_escape_help_text(definition.help_text)}")
                lines.append(f"# TYPE {name} {definition.metric_type}")
                if definition.metric_type in {"counter", "gauge"}:
                    for label_key, value in sorted(state.samples.items()):
                        lines.append(
                            self._render_sample_line(
                                name=name,
                                label_names=definition.label_names,
                                label_values=label_key,
                                value=value,
                            )
                        )
                    continue

                for label_key, series in sorted(state.histograms.items()):
                    running_count = 0.0
                    base_label_names = definition.label_names
                    base_label_values = label_key
                    for idx, upper_bound in enumerate(definition.buckets):
                        running_count += series.bucket_counts[idx]
                        lines.append(
                            self._render_sample_line(
                                name=f"{name}_bucket",
                                label_names=base_label_names + ("le",),
                                label_values=base_label_values + (str(upper_bound),),
                                value=running_count,
                            )
                        )
                    lines.append(
                        self._render_sample_line(
                            name=f"{name}_bucket",
                            label_names=base_label_names + ("le",),
                            label_values=base_label_values + ("+Inf",),
                            value=series.count,
                        )
                    )
                    lines.append(
                        self._render_sample_line(
                            name=f"{name}_count",
                            label_names=base_label_names,
                            label_values=base_label_values,
                            value=series.count,
                        )
                    )
                    lines.append(
                        self._render_sample_line(
                            name=f"{name}_sum",
                            label_names=base_label_names,
                            label_values=base_label_values,
                            value=series.sum,
                        )
                    )
            return "\n".join(lines) + "\n"

    def _render_sample_line(
        self,
        *,
        name: str,
        label_names: Sequence[str],
        label_values: Sequence[str],
        value: float,
    ) -> str:
        if not label_names:
            return f"{name} {_format_metric_value(value)}"
        labels = ",".join(
            f'{label_name}="{_escape_label_value(label_value)}"'
            for label_name, label_value in zip(label_names, label_values)
        )
        return f"{name}{{{labels}}} {_format_metric_value(value)}"

    def refresh_active_cli_processes(
        self, cli_integrations: Optional[Mapping[str, Any]]
    ) -> int:
        """Refresh active CLI subprocess count from integration objects."""
        total = 0
        if isinstance(cli_integrations, Mapping):
            for integration in cli_integrations.values():
                process_manager = getattr(integration, "process_manager", None)
                active_processes = getattr(process_manager, "active_processes", None)
                if isinstance(active_processes, Mapping):
                    total += len(active_processes)
        self.set_gauge("clitg_cli_active_processes", total)
        return total

    def increment_text_requests_total(self, *, engine: str, result: str) -> None:
        self.increment_counter(
            "clitg_text_requests_total",
            labels={"engine": engine or "unknown", "result": result or "unknown"},
        )

    def increment_text_requests_queued(self, *, engine: str) -> None:
        self.increment_counter(
            "clitg_text_requests_queued_total",
            labels={"engine": engine or "unknown"},
        )

    def increment_text_request_failure(self, *, engine: str, stage: str) -> None:
        self.increment_counter(
            "clitg_text_request_failures_total",
            labels={"engine": engine or "unknown", "stage": stage or "unknown"},
        )

    def observe_text_latency(
        self, metric_name: str, *, engine: str, seconds: float
    ) -> None:
        self.observe_histogram(
            metric_name,
            seconds,
            labels={"engine": engine or "unknown"},
        )

    def metrics_address(self) -> str:
        """Return user-facing listen address."""
        return f"http://{self.host}:{self.port}/metrics"

    def metrics_summary_address(self) -> str:
        """Return human-readable summary endpoint address."""
        return f"http://{self.host}:{self.port}/metricsz"

    def get_gauge_value(
        self,
        name: str,
        *,
        labels: Optional[Mapping[str, Any]] = None,
    ) -> float:
        with self._lock:
            state = self._metric_states.get(name)
            if state is None or state.definition.metric_type != "gauge":
                return 0.0
            key = self._label_tuple(state.definition, labels)
            return state.samples.get(key, 0.0)

    def get_counter_value(
        self,
        name: str,
        *,
        labels: Optional[Mapping[str, Any]] = None,
    ) -> float:
        """Read one counter value by exact label set."""
        with self._lock:
            state = self._metric_states.get(name)
            if state is None or state.definition.metric_type != "counter":
                return 0.0
            key = self._label_tuple(state.definition, labels)
            return state.samples.get(key, 0.0)

    def _iter_counter_samples(
        self, name: str
    ) -> list[tuple[tuple[str, ...], float, _MetricDefinition]]:
        """Return all counter samples with definitions."""
        with self._lock:
            state = self._metric_states.get(name)
            if state is None or state.definition.metric_type != "counter":
                return []
            return [
                (label_key, value, state.definition)
                for label_key, value in state.samples.items()
            ]

    def _iter_histogram_series(
        self, name: str
    ) -> list[tuple[tuple[str, ...], _HistogramSeries, _MetricDefinition]]:
        """Return all histogram series with definitions."""
        with self._lock:
            state = self._metric_states.get(name)
            if state is None or state.definition.metric_type != "histogram":
                return []
            return [
                (
                    label_key,
                    _HistogramSeries(
                        bucket_counts=list(series.bucket_counts),
                        count=series.count,
                        sum=series.sum,
                    ),
                    state.definition,
                )
                for label_key, series in state.histograms.items()
            ]

    def _aggregate_counter_by_label_value(
        self, name: str, *, label_name: str
    ) -> dict[str, float]:
        """Sum a labeled counter by one chosen label value."""
        results: dict[str, float] = {}
        for label_key, value, definition in self._iter_counter_samples(name):
            try:
                label_index = definition.label_names.index(label_name)
            except ValueError:
                continue
            label_value = label_key[label_index]
            results[label_value] = results.get(label_value, 0.0) + value
        return results

    def _estimate_histogram_quantile(
        self,
        series: _HistogramSeries,
        *,
        buckets: Sequence[float],
        quantile: float,
    ) -> Optional[float]:
        """Approximate a quantile using bucket upper bounds."""
        if series.count <= 0:
            return None
        target = max(0.0, min(1.0, quantile)) * series.count
        running_count = 0.0
        for idx, upper_bound in enumerate(buckets):
            running_count += series.bucket_counts[idx]
            if running_count >= target:
                return upper_bound
        return buckets[-1] if buckets else None

    def _build_histogram_summary_lines(
        self, metric_name: str, *, label: str
    ) -> list[str]:
        """Build human-readable histogram summary lines."""
        lines: list[str] = []
        for row in self._build_histogram_summary_rows(metric_name, label=label):
            lines.append(self._format_latency_summary_row(row))
        return lines

    def _build_histogram_summary_rows(
        self, metric_name: str, *, label: str
    ) -> list[_LatencySummaryRow]:
        """Build presentation-friendly histogram rows."""
        rows: list[_LatencySummaryRow] = []
        for label_key, series, definition in sorted(
            self._iter_histogram_series(metric_name),
            key=lambda item: item[0],
        ):
            if series.count <= 0:
                continue
            engine = label_key[0] if label_key else "all"
            avg = series.sum / series.count if series.count > 0 else 0.0
            p50 = self._estimate_histogram_quantile(
                series, buckets=definition.buckets, quantile=0.50
            )
            p90 = self._estimate_histogram_quantile(
                series, buckets=definition.buckets, quantile=0.90
            )
            rows.append(
                _LatencySummaryRow(
                    label=label,
                    engine=engine,
                    count=int(series.count),
                    avg_seconds=avg,
                    p50_seconds=p50,
                    p90_seconds=p90,
                )
            )
        return rows

    def _summary_status(self, snapshot: Mapping[str, Any]) -> str:
        """Return overall health status for summary surfaces."""
        return (
            "healthy"
            if snapshot["bot_running"] >= 1
            and snapshot["polling_up"] >= 1
            and snapshot["storage_up"] >= 1
            else "degraded"
        )

    def _summary_text_request_totals(self) -> dict[str, int]:
        """Aggregate request counters for summary surfaces."""
        request_totals = self._aggregate_counter_by_label_value(
            "clitg_text_requests_total",
            label_name="result",
        )
        queued_total = sum(
            self._aggregate_counter_by_label_value(
                "clitg_text_requests_queued_total",
                label_name="engine",
            ).values()
        )
        return {
            "success_total": int(request_totals.get("success", 0.0)),
            "failure_total": int(request_totals.get("failure", 0.0)),
            "queued_total": int(queued_total),
        }

    def _format_latency_summary_row(self, row: _LatencySummaryRow) -> str:
        """Render one latency row for the plain-text summary."""
        summary = (
            f"- {row.label} [{row.engine}]: "
            f"count={row.count} avg={row.avg_seconds:.2f}s"
        )
        if row.p50_seconds is not None:
            summary += f" p50<={row.p50_seconds:.2f}s"
        if row.p90_seconds is not None:
            summary += f" p90<={row.p90_seconds:.2f}s"
        return summary

    def _format_latency_duration(self, value: Optional[float]) -> str:
        """Render one latency duration for summary views."""
        if value is None:
            return "-"
        return f"{value:.2f}s"

    def _render_latency_table_row(self, row: _LatencySummaryRow) -> str:
        """Render one latency table row for the HTML dashboard."""
        return "".join(
            [
                "<tr>",
                f'<td data-label="Metric">{escape(row.label)}</td>',
                f'<td data-label="Engine">{escape(row.engine)}</td>',
                f'<td data-label="Count">{row.count}</td>',
                f'<td data-label="Avg">{row.avg_seconds:.2f}s</td>',
                (
                    f'<td data-label="P50">'
                    f"{self._format_latency_duration(row.p50_seconds)}</td>"
                ),
                (
                    f'<td data-label="P90">'
                    f"{self._format_latency_duration(row.p90_seconds)}</td>"
                ),
                "</tr>",
            ]
        )

    def _latency_summary_rows(self) -> list[_LatencySummaryRow]:
        """Build all latency rows for human-facing summaries."""
        rows: list[_LatencySummaryRow] = []
        latency_sections = [
            (
                "clitg_text_end_to_first_reply_seconds",
                "end_to_first_reply",
            ),
            ("clitg_text_cli_exec_seconds", "cli_exec"),
            ("clitg_text_reply_send_seconds", "reply_send"),
            ("clitg_text_queue_wait_seconds", "queue_wait"),
            ("clitg_text_preprocess_seconds", "preprocess"),
            ("clitg_text_telegram_delivery_seconds", "telegram_delivery"),
        ]
        for metric_name, label in latency_sections:
            rows.extend(self._build_histogram_summary_rows(metric_name, label=label))
        return rows

    def _select_summary_response_format(
        self,
        *,
        query_string: str,
        accept_header: Optional[str],
    ) -> str:
        """Pick HTML for browsers while keeping scripts on plain text."""
        format_values = parse_qs(query_string).get("format", [])
        if format_values:
            requested_format = format_values[0].strip().lower()
            if requested_format in {"text", "html"}:
                return requested_format

        normalized_accept = str(accept_header or "").lower()
        if (
            "text/html" in normalized_accept
            or "application/xhtml+xml" in normalized_accept
        ):
            return "html"
        return "text"

    def render_human_summary(self) -> str:
        """Render a compact human-readable metrics summary."""
        snapshot = self.status_snapshot()
        request_totals = self._summary_text_request_totals()
        status = self._summary_status(snapshot)

        lines = [
            f"status: {status}",
            f"metrics_address: {snapshot['address']}",
            f"metrics_raw: {self.metrics_address()}",
            "",
            f"bot_running: {'yes' if snapshot['bot_running'] >= 1 else 'no'}",
            f"polling_up: {'yes' if snapshot['polling_up'] >= 1 else 'no'}",
            (
                "polling_restart_requested: "
                f"{'yes' if snapshot['polling_restart_requested'] >= 1 else 'no'}"
            ),
            f"storage_up: {'yes' if snapshot['storage_up'] >= 1 else 'no'}",
            f"active_tasks: {int(snapshot['active_tasks'])}",
            f"cli_active_processes: {int(snapshot['cli_active_processes'])}",
            (
                "last_health_probe_age: "
                f"{snapshot['last_health_probe_age_seconds']:.2f}s"
            ),
            f"watchdog_tick_age: {snapshot['watchdog_tick_age_seconds']:.2f}s",
            f"pending_updates: {int(snapshot['pending_update_count'])}",
            "",
            "text_requests:",
            f"- success_total: {request_totals['success_total']}",
            f"- failure_total: {request_totals['failure_total']}",
            f"- queued_total: {request_totals['queued_total']}",
            "",
            "latency:",
        ]
        latency_lines = [
            self._format_latency_summary_row(row)
            for row in self._latency_summary_rows()
        ]
        lines.extend(latency_lines)
        if lines[-1] == "latency:":
            lines.append("- no successful latency samples yet")
        return "\n".join(lines) + "\n"

    def render_human_summary_html(self) -> str:
        """Render a browser-friendly summary dashboard."""
        snapshot = self.status_snapshot()
        request_totals = self._summary_text_request_totals()
        latency_rows = self._latency_summary_rows()
        status = self._summary_status(snapshot)
        is_healthy = status == "healthy"
        status_label = "HEALTHY" if is_healthy else "DEGRADED"
        status_note = (
            "Bot、轮询和存储都处于正常状态。"
            if is_healthy
            else "至少有一个关键运行信号异常，需要检查日志与原始指标。"
        )

        state_cards = [
            {
                "title": "Bot Loop",
                "value": "Running" if snapshot["bot_running"] >= 1 else "Stopped",
                "tone": "healthy" if snapshot["bot_running"] >= 1 else "degraded",
                "detail": "主事件循环",
            },
            {
                "title": "Polling",
                "value": "Up" if snapshot["polling_up"] >= 1 else "Down",
                "tone": "healthy" if snapshot["polling_up"] >= 1 else "degraded",
                "detail": "Telegram 轮询",
            },
            {
                "title": "Storage",
                "value": "Healthy" if snapshot["storage_up"] >= 1 else "Offline",
                "tone": "healthy" if snapshot["storage_up"] >= 1 else "degraded",
                "detail": "存储健康检查",
            },
            {
                "title": "Restart Flag",
                "value": (
                    "Requested"
                    if snapshot["polling_restart_requested"] >= 1
                    else "Idle"
                ),
                "tone": (
                    "degraded"
                    if snapshot["polling_restart_requested"] >= 1
                    else "neutral"
                ),
                "detail": "轮询重启请求",
            },
        ]
        runtime_facts = [
            ("Active tasks", str(int(snapshot["active_tasks"]))),
            ("CLI processes", str(int(snapshot["cli_active_processes"]))),
            ("Pending updates", str(int(snapshot["pending_update_count"]))),
            (
                "Health probe age",
                f"{snapshot['last_health_probe_age_seconds']:.2f}s",
            ),
            ("Watchdog age", f"{snapshot['watchdog_tick_age_seconds']:.2f}s"),
            (
                "Summary format",
                '<a href="?format=text">text</a> / <a href="?format=html">html</a>',
            ),
        ]

        cards_html = "\n".join(
            (
                '<article class="state-card">'
                f'<p class="state-title">{escape(card["title"])}</p>'
                f'<p class="state-value">{escape(card["value"])}</p>'
                f'<span class="state-chip {escape(card["tone"])}">'
                f'{escape(card["detail"])}</span>'
                "</article>"
            )
            for card in state_cards
        )
        runtime_html = "\n".join(
            (
                "<div class=\"fact-row\">"
                f"<dt>{escape(label)}</dt>"
                f"<dd>{value}</dd>"
                "</div>"
            )
            for label, value in runtime_facts
        )
        request_cards_html = "\n".join(
            (
                '<article class="metric-card">'
                f'<p class="metric-value">{value}</p>'
                f'<p class="metric-label">{escape(label)}</p>'
                "</article>"
            )
            for label, value in (
                ("Success", request_totals["success_total"]),
                ("Failure", request_totals["failure_total"]),
                ("Queued", request_totals["queued_total"]),
            )
        )
        if latency_rows:
            latency_rows_html = "\n".join(
                self._render_latency_table_row(row) for row in latency_rows
            )
            latency_html = (
                "<table>"
                "<thead><tr>"
                "<th>Metric</th><th>Engine</th><th>Count</th>"
                "<th>Avg</th><th>P50</th><th>P90</th>"
                "</tr></thead>"
                f"<tbody>{latency_rows_html}</tbody>"
                "</table>"
            )
        else:
            latency_html = (
                '<div class="empty-state">暂无成功请求的延迟样本，先跑一条消息再看。</div>'
            )

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="5">
    <title>CLI TG Bot Metrics</title>
    <style>
        :root {{
            color-scheme: light;
            --bg: #eef3f1;
            --panel: rgba(255, 255, 255, 0.86);
            --panel-strong: #ffffff;
            --ink: #14221c;
            --muted: #607066;
            --line: rgba(20, 34, 28, 0.10);
            --accent: #0e8f6f;
            --accent-soft: rgba(14, 143, 111, 0.12);
            --danger: #b5442f;
            --danger-soft: rgba(181, 68, 47, 0.12);
            --neutral: rgba(20, 34, 28, 0.08);
            --shadow: 0 18px 50px rgba(31, 45, 39, 0.10);
        }}
        * {{
            box-sizing: border-box;
        }}
        body {{
            margin: 0;
            font-family: "Avenir Next", "PingFang SC", "Helvetica Neue", sans-serif;
            color: var(--ink);
            background:
                radial-gradient(
                    circle at top left,
                    rgba(14, 143, 111, 0.18),
                    transparent 32%
                ),
                radial-gradient(
                    circle at top right,
                    rgba(230, 147, 32, 0.12),
                    transparent 24%
                ),
                linear-gradient(180deg, #f7faf8 0%, var(--bg) 100%);
            min-height: 100vh;
            padding: 32px 18px 48px;
        }}
        main {{
            max-width: 1180px;
            margin: 0 auto;
        }}
        .hero {{
            display: flex;
            justify-content: space-between;
            gap: 20px;
            align-items: flex-start;
            margin-bottom: 20px;
        }}
        .hero-copy {{
            max-width: 760px;
        }}
        .eyebrow {{
            margin: 0 0 10px;
            font-size: 12px;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            color: var(--muted);
        }}
        h1 {{
            margin: 0;
            font-size: clamp(30px, 4vw, 44px);
            line-height: 1.05;
        }}
        .hero-copy p {{
            margin: 12px 0 0;
            color: var(--muted);
            font-size: 15px;
        }}
        .status-pill {{
            flex-shrink: 0;
            border-radius: 999px;
            padding: 12px 18px;
            font-size: 13px;
            letter-spacing: 0.08em;
            font-weight: 700;
            text-transform: uppercase;
            border: 1px solid transparent;
        }}
        .status-pill.healthy {{
            color: var(--accent);
            background: var(--accent-soft);
            border-color: rgba(14, 143, 111, 0.18);
        }}
        .status-pill.degraded {{
            color: var(--danger);
            background: var(--danger-soft);
            border-color: rgba(181, 68, 47, 0.18);
        }}
        .panel {{
            background: var(--panel);
            backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.7);
            border-radius: 24px;
            box-shadow: var(--shadow);
            padding: 24px;
            margin-bottom: 18px;
        }}
        .panel-head {{
            display: flex;
            justify-content: space-between;
            gap: 16px;
            align-items: baseline;
            margin-bottom: 18px;
        }}
        .panel h2 {{
            margin: 0;
            font-size: 20px;
        }}
        .panel-head p {{
            margin: 0;
            color: var(--muted);
            font-size: 14px;
        }}
        .links {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }}
        .links a,
        .fact-row a {{
            color: inherit;
        }}
        .link-chip {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 10px 14px;
            border-radius: 999px;
            background: var(--panel-strong);
            border: 1px solid var(--line);
            text-decoration: none;
        }}
        .state-grid,
        .request-grid {{
            display: grid;
            gap: 14px;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        }}
        .state-card,
        .metric-card {{
            background: var(--panel-strong);
            border-radius: 20px;
            border: 1px solid var(--line);
            padding: 18px;
        }}
        .state-title,
        .metric-label {{
            margin: 0;
            font-size: 13px;
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }}
        .state-value,
        .metric-value {{
            margin: 10px 0 12px;
            font-size: 30px;
            line-height: 1;
            font-weight: 700;
        }}
        .state-chip {{
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: 6px 10px;
            font-size: 12px;
            font-weight: 600;
        }}
        .state-chip.healthy {{
            color: var(--accent);
            background: var(--accent-soft);
        }}
        .state-chip.degraded {{
            color: var(--danger);
            background: var(--danger-soft);
        }}
        .state-chip.neutral {{
            color: var(--ink);
            background: var(--neutral);
        }}
        .facts {{
            display: grid;
            gap: 14px;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        }}
        .fact-row {{
            background: var(--panel-strong);
            border: 1px solid var(--line);
            border-radius: 18px;
            padding: 16px 18px;
        }}
        .fact-row dt {{
            color: var(--muted);
            font-size: 13px;
            margin-bottom: 8px;
        }}
        .fact-row dd {{
            margin: 0;
            font-size: 22px;
            font-weight: 650;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--panel-strong);
            border-radius: 20px;
            overflow: hidden;
        }}
        thead {{
            background: rgba(20, 34, 28, 0.04);
        }}
        th,
        td {{
            padding: 14px 16px;
            text-align: left;
            border-bottom: 1px solid var(--line);
        }}
        th {{
            font-size: 12px;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: var(--muted);
        }}
        td {{
            font-family: "SFMono-Regular", "JetBrains Mono", monospace;
            font-size: 14px;
        }}
        tbody tr:last-child td {{
            border-bottom: none;
        }}
        .empty-state {{
            border: 1px dashed var(--line);
            border-radius: 18px;
            padding: 24px;
            background: rgba(255, 255, 255, 0.55);
            color: var(--muted);
        }}
        @media (max-width: 760px) {{
            .hero,
            .panel-head {{
                flex-direction: column;
            }}
            body {{
                padding-top: 24px;
            }}
            .panel {{
                padding: 20px;
            }}
            table,
            thead,
            tbody,
            th,
            td,
            tr {{
                display: block;
            }}
            thead {{
                display: none;
            }}
            tbody tr {{
                border-bottom: 1px solid var(--line);
                padding: 10px 0;
            }}
            td {{
                border-bottom: none;
                padding: 6px 0;
            }}
            td::before {{
                content: attr(data-label);
                display: block;
                font-family: "Avenir Next", "PingFang SC", sans-serif;
                font-size: 12px;
                letter-spacing: 0.06em;
                text-transform: uppercase;
                color: var(--muted);
                margin-bottom: 4px;
            }}
        }}
    </style>
</head>
<body>
    <main>
        <section class="hero">
            <div class="hero-copy">
                <p class="eyebrow">CLI TG Bot Runtime Monitor</p>
                <h1>Metrics Overview</h1>
                <p>{escape(status_note)} 每 5 秒自动刷新，适合浏览器快速看状态。</p>
            </div>
            <div class="status-pill {status}">{status_label}</div>
        </section>

        <section class="panel">
            <div class="panel-head">
                <h2>Access</h2>
                <p>兼容浏览器查看，也保留原始文本与 Prometheus 指标。</p>
            </div>
            <div class="links">
                <a class="link-chip" href="{escape(snapshot['address'])}?format=text">
                    Plain summary
                </a>
                <a class="link-chip" href="{escape(snapshot['raw_address'])}">
                    Prometheus raw
                </a>
                <a class="link-chip" href="{escape(snapshot['address'])}?format=html">
                    Force HTML
                </a>
            </div>
        </section>

        <section class="panel">
            <div class="panel-head">
                <h2>Runtime State</h2>
                <p>核心健康信号</p>
            </div>
            <div class="state-grid">
                {cards_html}
            </div>
        </section>

        <section class="panel">
            <div class="panel-head">
                <h2>Runtime Facts</h2>
                <p>关键计数与时延年龄</p>
            </div>
            <dl class="facts">
                {runtime_html}
            </dl>
        </section>

        <section class="panel">
            <div class="panel-head">
                <h2>Text Requests</h2>
                <p>请求结果总览</p>
            </div>
            <div class="request-grid">
                {request_cards_html}
            </div>
        </section>

        <section class="panel">
            <div class="panel-head">
                <h2>Latency Breakdown</h2>
                <p>按指标与引擎聚合</p>
            </div>
            {latency_html}
        </section>
    </main>
</body>
</html>
"""

    def status_snapshot(self) -> dict[str, Any]:
        """Build a compact runtime snapshot for ops output."""
        return {
            "enabled": self.enabled,
            "address": self.metrics_summary_address(),
            "raw_address": self.metrics_address(),
            "bot_running": self.get_gauge_value("clitg_bot_running"),
            "polling_up": self.get_gauge_value("clitg_polling_up"),
            "polling_restart_requested": self.get_gauge_value(
                "clitg_polling_restart_requested"
            ),
            "watchdog_tick_age_seconds": self.get_gauge_value(
                "clitg_watchdog_tick_age_seconds"
            ),
            "last_health_probe_age_seconds": self.get_gauge_value(
                "clitg_last_health_probe_age_seconds"
            ),
            "pending_update_count": self.get_gauge_value("clitg_pending_update_count"),
            "storage_up": self.get_gauge_value("clitg_storage_up"),
            "active_tasks": self.get_gauge_value("clitg_active_tasks"),
            "cli_active_processes": self.get_gauge_value("clitg_cli_active_processes"),
        }
