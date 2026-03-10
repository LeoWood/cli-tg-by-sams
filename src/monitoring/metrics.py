"""Lightweight runtime metrics and HTTP exposition."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping, Optional, Sequence

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
                    if self.path == "/metrics":
                        payload = registry.render_prometheus_text().encode("utf-8")
                        content_type = "text/plain; version=0.0.4; charset=utf-8"
                    elif self.path == "/metricsz":
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
            summary = (
                f"- {label} [{engine}]: count={int(series.count)} " f"avg={avg:.2f}s"
            )
            if p50 is not None:
                summary += f" p50<={p50:.2f}s"
            if p90 is not None:
                summary += f" p90<={p90:.2f}s"
            lines.append(summary)
        return lines

    def render_human_summary(self) -> str:
        """Render a compact human-readable metrics summary."""
        snapshot = self.status_snapshot()
        success_total = self._aggregate_counter_by_label_value(
            "clitg_text_requests_total",
            label_name="result",
        ).get("success", 0.0)
        failure_total = self._aggregate_counter_by_label_value(
            "clitg_text_requests_total",
            label_name="result",
        ).get("failure", 0.0)
        queued_total = sum(
            self._aggregate_counter_by_label_value(
                "clitg_text_requests_queued_total",
                label_name="engine",
            ).values()
        )
        status = (
            "healthy"
            if snapshot["bot_running"] >= 1
            and snapshot["polling_up"] >= 1
            and snapshot["storage_up"] >= 1
            else "degraded"
        )

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
            f"- success_total: {int(success_total)}",
            f"- failure_total: {int(failure_total)}",
            f"- queued_total: {int(queued_total)}",
            "",
            "latency:",
        ]
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
            section_lines = self._build_histogram_summary_lines(
                metric_name, label=label
            )
            if section_lines:
                lines.extend(section_lines)
        if lines[-1] == "latency:":
            lines.append("- no successful latency samples yet")
        return "\n".join(lines) + "\n"

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
