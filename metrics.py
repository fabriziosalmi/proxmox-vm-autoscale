"""
Prometheus metrics for VM Autoscale.

A tiny text-exposition endpoint served from a daemon thread. No dependencies
beyond the standard library, because adding a client library to a service whose
whole install story is three apt packages is not worth one endpoint.

The server is **disabled by default and binds to localhost when enabled**. This
process holds root credentials for hypervisors; the metrics it exposes name
your nodes, your VMIDs and their utilisation, and there is no authentication.
Put it behind something that authenticates if it needs to leave the host.
"""

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 9808
DEFAULT_PATH = "/metrics"


def _escape_label(value: Any) -> str:
    """Escape a label value per the Prometheus exposition format."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _format_labels(labels: Tuple[Tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{name}="{_escape_label(value)}"' for name, value in labels)
    return "{" + inner + "}"


class MetricsRegistry:
    """Thread-safe store for the handful of metrics this service reports.

    Counters only ever increase; gauges are overwritten. Both are keyed by
    their label set, and a metric with no observations is simply not emitted -
    Prometheus treats an absent series and a zero series differently, and
    inventing zeros for VMs that were never polled would be a lie.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = {}
        self._gauges: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = {}
        self._help: Dict[str, Tuple[str, str]] = {}

    def describe(self, name: str, kind: str, help_text: str) -> None:
        with self._lock:
            self._help[name] = (kind, help_text)

    @staticmethod
    def _key(name: str, labels: Optional[Dict[str, Any]]):
        items = tuple(sorted((k, str(v)) for k, v in (labels or {}).items()))
        return name, items

    def inc(self, name: str, labels: Optional[Dict[str, Any]] = None,
            amount: float = 1.0) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + amount

    def set(self, name: str, value: float,
            labels: Optional[Dict[str, Any]] = None) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._gauges[key] = float(value)

    def unset(self, name: str, labels: Optional[Dict[str, Any]] = None) -> None:
        """Drop a gauge series, for a VM that no longer reports a value."""
        key = self._key(name, labels)
        with self._lock:
            self._gauges.pop(key, None)

    def render(self) -> str:
        """Render the whole registry in Prometheus text exposition format."""
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            help_text = dict(self._help)

        by_name: Dict[str, list] = {}
        for (name, labels), value in counters.items():
            by_name.setdefault(name, []).append((labels, value))
        for (name, labels), value in gauges.items():
            by_name.setdefault(name, []).append((labels, value))

        lines = []
        for name in sorted(by_name):
            kind, text = help_text.get(name, ("untyped", ""))
            if text:
                lines.append(f"# HELP {name} {text}")
            lines.append(f"# TYPE {name} {kind}")
            for labels, value in sorted(by_name[name]):
                rendered = f"{value:.6g}" if value % 1 else f"{int(value)}"
                lines.append(f"{name}{_format_labels(labels)} {rendered}")
        return "\n".join(lines) + "\n"


class _Handler(BaseHTTPRequestHandler):
    registry: MetricsRegistry
    metrics_path: str = DEFAULT_PATH
    logger: logging.Logger

    def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        if self.path.split("?", 1)[0] != self.metrics_path:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"not found\n")
            return

        try:
            body = self.registry.render().encode("utf-8")
        except Exception as e:  # pragma: no cover - defensive
            self.logger.error(f"Failed to render metrics: {e}")
            self.send_response(500)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # BaseHTTPRequestHandler logs every request to stderr by default, which
        # would drown the service log under a normal scrape interval.
        self.logger.debug("metrics: " + fmt % args)


class MetricsServer:
    """Serves a registry over HTTP from a daemon thread."""

    def __init__(self, registry: MetricsRegistry, logger: logging.Logger,
                 bind: str = DEFAULT_BIND, port: int = DEFAULT_PORT,
                 path: str = DEFAULT_PATH) -> None:
        self.registry = registry
        self.logger = logger
        self.bind = bind
        self.port = port
        self.path = path if path.startswith("/") else "/" + path
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        """Start serving. Returns False if the endpoint could not be bound.

        A metrics endpoint is not worth taking the autoscaler down for, so a
        bind failure is logged and the service carries on without it.
        """
        handler = type("_BoundHandler", (_Handler,), {
            "registry": self.registry,
            "metrics_path": self.path,
            "logger": self.logger,
        })

        try:
            self._server = ThreadingHTTPServer((self.bind, self.port), handler)
        except OSError as e:
            self.logger.error(
                f"Could not bind the metrics endpoint to {self.bind}:{self.port}: {e}. "
                "Continuing without metrics."
            )
            return False

        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="vm-autoscale-metrics",
            daemon=True,
        )
        self._thread.start()
        self.logger.info(
            f"Metrics available on http://{self.bind}:{self.port}{self.path}"
        )
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def build_registry() -> MetricsRegistry:
    """A registry with every metric this service reports already described."""
    r = MetricsRegistry()

    r.describe("vm_autoscale_up", "gauge",
               "1 when the autoscaler is running.")
    r.describe("vm_autoscale_build_info", "gauge",
               "Build information; the value is always 1.")
    r.describe("vm_autoscale_cycles_total", "counter",
               "Polling cycles completed since start.")
    r.describe("vm_autoscale_cycle_duration_seconds", "gauge",
               "Wall-clock duration of the last completed cycle.")
    r.describe("vm_autoscale_last_cycle_timestamp_seconds", "gauge",
               "Unix time at which the last cycle completed.")
    r.describe("vm_autoscale_cycle_errors_total", "counter",
               "Unexpected errors raised by the main loop.")

    r.describe("vm_autoscale_vm_running", "gauge",
               "1 when the guest was running at the last poll.")
    r.describe("vm_autoscale_vm_cpu_percent", "gauge",
               "Guest CPU usage at the last poll. Absent when unreadable.")
    r.describe("vm_autoscale_vm_ram_percent", "gauge",
               "Guest RAM usage at the last poll. Absent when unreadable.")
    r.describe("vm_autoscale_vm_errors_total", "counter",
               "Failures while processing a VM.")
    r.describe("vm_autoscale_metric_unavailable_total", "counter",
               "Times a usage metric could not be read, by resource.")

    r.describe("vm_autoscale_scaling_actions_total", "counter",
               "Scaling actions applied, by VM, resource and direction.")
    r.describe("vm_autoscale_scaling_failures_total", "counter",
               "Scaling attempts that raised, by VM and resource.")

    r.describe("vm_autoscale_host_cpu_percent", "gauge",
               "Proxmox node CPU usage at the last check.")
    r.describe("vm_autoscale_host_ram_percent", "gauge",
               "Proxmox node RAM usage at the last check.")
    r.describe("vm_autoscale_host_gate_blocked_total", "counter",
               "Times a node was over its ceiling and scaling was skipped.")

    r.set("vm_autoscale_up", 1)
    return r
