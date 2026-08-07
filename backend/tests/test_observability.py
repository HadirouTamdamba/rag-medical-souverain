import json
import logging

import observability as obs


def test_counter_exposes_labelled_series():
    counter = obs.Counter("unit_requests_total", "doc", ("method", "status"))
    counter.inc("GET", "200")
    counter.inc("GET", "200")
    counter.inc("POST", "500")

    output = "\n".join(counter.render())
    assert "# TYPE unit_requests_total counter" in output
    assert 'unit_requests_total{method="GET",status="200"} 2.0' in output
    assert 'unit_requests_total{method="POST",status="500"} 1.0' in output


def test_gauge_tracks_increments_and_decrements():
    gauge = obs.Gauge("unit_inflight", "doc")
    gauge.inc()
    gauge.inc()
    gauge.dec()

    assert "unit_inflight 1.0" in "\n".join(gauge.render())


def test_histogram_buckets_are_cumulative():
    histogram = obs.Histogram("unit_latency_seconds", "doc", buckets=(1, 5))
    for value in (0.5, 2.0, 10.0):
        histogram.observe(value)

    lines = histogram.render()
    output = "\n".join(lines)
    assert 'unit_latency_seconds_bucket{le="1"} 1' in output
    assert 'unit_latency_seconds_bucket{le="5"} 2' in output
    assert 'unit_latency_seconds_bucket{le="+Inf"} 3' in output
    assert "unit_latency_seconds_count 3" in output
    assert "unit_latency_seconds_sum 12.5" in output


def test_label_cardinality_is_checked():
    counter = obs.Counter("unit_labelled_total", "doc", ("a", "b"))
    try:
        counter.inc("only-one")
    except ValueError as exc:
        assert "expected 2 labels" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a wrong label count must raise")


def test_render_metrics_produces_a_parsable_exposition():
    obs.HTTP_REQUESTS.inc("GET", "/health", "200")
    payload = obs.render_metrics()

    assert payload.endswith("\n")
    for line in payload.splitlines():
        assert line.startswith("#") or " " in line
    assert "medibot_http_requests_total" in payload


def test_label_values_are_escaped():
    counter = obs.Counter("unit_escaped_total", "doc", ("path",))
    counter.inc('/a"b\\c')
    assert '/a\\"b\\\\c' in "\n".join(counter.render())


def test_json_formatter_emits_one_object_per_record():
    obs.request_id_var.set("abc123")
    record = logging.LogRecord(
        name="medibot", level=logging.INFO, pathname=__file__, lineno=1,
        msg="Request handled", args=(), exc_info=None,
    )
    record.request_id = obs.request_id_var.get()
    record.duration_ms = 42

    payload = json.loads(obs.JsonFormatter().format(record))
    assert payload["message"] == "Request handled"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "abc123"
    assert payload["duration_ms"] == 42
