"""Observability: correlated logs and Prometheus metrics, with no extra dependency.

This project must remain buildable on an unstable or fully offline network
(pre-downloaded wheels). We therefore avoid `prometheus-client` and implement
only what is needed of the 0.0.4 text exposition format, which is stable and
trivial.
"""

from __future__ import annotations

import bisect
import json
import logging
import threading
import time
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

CONTENT_TYPE_PROMETHEUS = "text/plain; version=0.0.4; charset=utf-8"

# Bounds in seconds, sized for CPU LLM generation: the useful range spans from
# hundreds of milliseconds (retrieval) to a minute (generation).
LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120)

_RESERVED_LOG_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "asctime",
    "message",
    "taskName",
    # Uvicorn attaches a colourised copy of its message: ANSI noise in logs
    # meant for machine ingestion.
    "color_message",
}


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per event, ready for Loki/ELK."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Extra fields passed through logger.info(..., extra={...}).
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_FIELDS and key not in payload:
                payload[key] = value

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(message)s")
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Uvicorn installs its own handlers: neutralise them to avoid duplicated,
    # uncorrelated lines.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True

    # The application access log (see main.py) already carries request_id and duration.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_labels(labelnames: tuple[str, ...], values: tuple[str, ...], extra: str = "") -> str:
    pairs = [
        f'{name}="{_escape_label(str(value))}"'
        for name, value in zip(labelnames, values, strict=True)
    ]
    if extra:
        pairs.append(extra)
    return "{" + ",".join(pairs) + "}" if pairs else ""


class _Metric:
    kind = "untyped"

    def __init__(self, name: str, documentation: str, labelnames: tuple[str, ...] = ()):
        self.name = name
        self.documentation = documentation
        self.labelnames = labelnames
        self._lock = threading.Lock()

    def _key(self, labels: tuple[str, ...]) -> tuple[str, ...]:
        if len(labels) != len(self.labelnames):
            raise ValueError(f"{self.name}: expected {len(self.labelnames)} labels, got {len(labels)}")
        return tuple(str(label) for label in labels)

    def _header(self) -> list[str]:
        return [f"# HELP {self.name} {self.documentation}", f"# TYPE {self.name} {self.kind}"]


class Counter(_Metric):
    kind = "counter"

    def __init__(self, name: str, documentation: str, labelnames: tuple[str, ...] = ()):
        super().__init__(name, documentation, labelnames)
        self._values: dict[tuple[str, ...], float] = {}

    def inc(self, *labels: str, amount: float = 1.0) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def render(self) -> list[str]:
        with self._lock:
            samples = sorted(self._values.items())
        lines = self._header()
        for key, value in samples:
            lines.append(f"{self.name}{_render_labels(self.labelnames, key)} {value}")
        return lines


class Gauge(_Metric):
    kind = "gauge"

    def __init__(self, name: str, documentation: str, labelnames: tuple[str, ...] = ()):
        super().__init__(name, documentation, labelnames)
        self._values: dict[tuple[str, ...], float] = {}

    def set(self, value: float, *labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = value

    def inc(self, *labels: str, amount: float = 1.0) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, *labels: str, amount: float = 1.0) -> None:
        self.inc(*labels, amount=-amount)

    def render(self) -> list[str]:
        with self._lock:
            samples = sorted(self._values.items())
        lines = self._header()
        for key, value in samples:
            lines.append(f"{self.name}{_render_labels(self.labelnames, key)} {value}")
        return lines


class Histogram(_Metric):
    kind = "histogram"

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: tuple[str, ...] = (),
        buckets: tuple[float, ...] = LATENCY_BUCKETS,
    ):
        super().__init__(name, documentation, labelnames)
        self.buckets = tuple(sorted(buckets))
        self._counts: dict[tuple[str, ...], list[int]] = {}
        self._sums: dict[tuple[str, ...], float] = {}

    def observe(self, value: float, *labels: str) -> None:
        key = self._key(labels)
        # Only one bucket is incremented; Prometheus expects cumulative counters,
        # so the running sum is computed at render time.
        index = bisect.bisect_left(self.buckets, value)
        with self._lock:
            counts = self._counts.setdefault(key, [0] * (len(self.buckets) + 1))
            counts[index] += 1
            self._sums[key] = self._sums.get(key, 0.0) + value

    def render(self) -> list[str]:
        with self._lock:
            samples = sorted(self._counts.items())
            sums = dict(self._sums)
        lines = self._header()
        for key, counts in samples:
            cumulative = 0
            for index, bound in enumerate(self.buckets):
                cumulative += counts[index]
                labels = _render_labels(self.labelnames, key, f'le="{bound}"')
                lines.append(f"{self.name}_bucket{labels} {cumulative}")
            total = cumulative + counts[-1]
            inf_labels = _render_labels(self.labelnames, key, 'le="+Inf"')
            base_labels = _render_labels(self.labelnames, key)
            lines.append(f"{self.name}_bucket{inf_labels} {total}")
            lines.append(f"{self.name}_sum{base_labels} {sums.get(key, 0.0)}")
            lines.append(f"{self.name}_count{base_labels} {total}")
        return lines


HTTP_REQUESTS = Counter(
    "medibot_http_requests_total",
    "Total HTTP requests served.",
    ("method", "path", "status"),
)
HTTP_DURATION = Histogram(
    "medibot_http_request_duration_seconds",
    "HTTP request latency.",
    ("method", "path"),
)
RAG_RETRIEVAL_DURATION = Histogram(
    "medibot_rag_retrieval_duration_seconds",
    "Qdrant vector search latency.",
)
RAG_GENERATION_DURATION = Histogram(
    "medibot_rag_generation_duration_seconds",
    "LLM generation latency.",
)
RAG_QUERIES = Counter(
    "medibot_rag_queries_total",
    "RAG queries handled, by outcome.",
    ("outcome",),
)
RAG_CHUNKS_RETRIEVED = Histogram(
    "medibot_rag_chunks_retrieved",
    "Chunks retrieved above the relevance threshold.",
    buckets=(0, 1, 2, 3, 5, 8, 13, 21),
)
LLM_INFLIGHT = Gauge(
    "medibot_llm_inflight_requests",
    "In-flight LLM generations (capped by LLM_MAX_CONCURRENCY).",
)
LLM_ERRORS = Counter(
    "medibot_llm_errors_total",
    "LLM call failures, by kind.",
    ("kind",),
)
DOCUMENTS_INDEXED = Counter(
    "medibot_documents_indexed_total",
    "Documents indexed successfully.",
)
CHUNKS_INDEXED = Counter(
    "medibot_chunks_indexed_total",
    "Chunks written to Qdrant.",
)
RATE_LIMITED = Counter(
    "medibot_rate_limited_total",
    "Requests rejected by the rate limiter.",
    ("path",),
)

_REGISTRY: tuple[_Metric, ...] = (
    HTTP_REQUESTS,
    HTTP_DURATION,
    RAG_RETRIEVAL_DURATION,
    RAG_GENERATION_DURATION,
    RAG_QUERIES,
    RAG_CHUNKS_RETRIEVED,
    LLM_INFLIGHT,
    LLM_ERRORS,
    DOCUMENTS_INDEXED,
    CHUNKS_INDEXED,
    RATE_LIMITED,
)


def render_metrics() -> str:
    lines: list[str] = []
    for metric in _REGISTRY:
        lines.extend(metric.render())
    return "\n".join(lines) + "\n"
