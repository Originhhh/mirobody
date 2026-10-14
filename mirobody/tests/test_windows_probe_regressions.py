"""Synthetic transport gates for the Windows local-model benchmark tools."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


ROOT = Path(__file__).resolve().parents[2]
TIMINGS = {"predicted_per_second": 10, "predicted_n": 3,
           "prompt_per_second": 20, "prompt_n": 10, "prompt_ms": 500}


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def probe():
    return load_script("windows_speed_probe", "benchmarks/windows/speed_probe.py")


@pytest.fixture
def analyzer():
    return load_script("windows_speed_analyzer", "benchmarks/windows/results/speed/analyze_speed.py")


def event(value, newline=b"\n"):
    payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return b"data: " + payload.encode() + newline + newline


def complete_stream(choices=None, timings=TIMINGS):
    if choices is None:
        choices = [{"delta": {"content": "synthetic"}}]
    return event({"choices": choices, "timings": timings}) + event("[DONE]")


class Response:
    def __init__(self, blocks, status=200):
        self.blocks = iter(blocks)
        self.status = status
        self.reads = 0

    def read1(self, size):
        self.reads += 1
        return next(self.blocks, b"")


class Connection:
    def __init__(self, response, failure=None):
        self.response = response
        self.failure = failure
        self.closed = False

    def connect(self):
        if self.failure:
            raise self.failure

    def request(self, method, path, body, headers):
        self.method, self.path, self.body, self.headers = method, path, body, headers

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def transport(monkeypatch, probe, response, failure=None, https=False):
    conn = Connection(response, failure)
    constructed = []

    def factory(host, port, timeout):
        constructed.append((host, port, timeout))
        return conn

    monkeypatch.setattr(probe.http.client, "HTTPSConnection" if https else "HTTPConnection", factory)
    return conn, constructed


def test_tool_only_stream_assembles_indexed_fragments_and_measures_ttft(probe, monkeypatch):
    first = {"choices": [{"delta": {"tool_calls": [
        {"index": 1, "id": "call_b", "type": "function", "function": {"name": "second", "arguments": "{"}},
        {"index": 0, "id": "call_a", "type": "function", "function": {"name": "look", "arguments": '{"q":'}},
    ]}}]}
    second = {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"name": "up", "arguments": '"synthetic"}'}},
        {"index": 1, "function": {"arguments": "}"}},
    ]}}], "timings": TIMINGS}
    response = Response([event(first) + event(second) + event("[DONE]")])
    conn, _ = transport(monkeypatch, probe, response)
    clock = iter([10, 10.75])
    monkeypatch.setattr(probe.time, "perf_counter", lambda: next(clock))
    timings, message, ttft, status, error = probe.post_stream("http://localhost:18557", {})
    assert (status, error, timings, ttft) == (200, "", TIMINGS, 0.75)
    assert message["tool_calls"] == [
        {"id": "call_a", "type": "function", "function": {"name": "lookup", "arguments": '{"q":"synthetic"}'}},
        {"id": "call_b", "type": "function", "function": {"name": "second", "arguments": "{}"}},
    ]
    assert response.reads == 1
    assert conn.closed


def test_sse_handles_split_utf8_crlf_multiline_data_and_comments(probe, monkeypatch):
    data = (b": keepalive\r\n\r\ndata: {\"choices\":\r\ndata: "
            + json.dumps([{"delta": {"reasoning_content": "推", "content": "理"}}], ensure_ascii=False).encode()
            + b"}\r\n\r\n" + event({"timings": TIMINGS}, b"\r\n") + event("[DONE]", b"\r\n"))
    response = Response([bytes([byte]) for byte in data])
    transport(monkeypatch, probe, response)
    timings, message, ttft, status, _ = probe.post_stream("http://localhost", {})
    assert status == 200
    assert message == {"reasoning_content": "推", "content": "理", "tool_calls": []}
    assert timings == TIMINGS
    assert ttft is not None


@pytest.mark.parametrize(("stream", "diagnostic"), [
    (event({"timings": TIMINGS}), "IncompleteStream"),
    (event({"timings": TIMINGS}) + b"data: [DONE]\n", "IncompleteEvent"),
    (event("[DONE]"), "MissingTimings"),
    (complete_stream(choices=[]), "EmptyOutput"),
    (complete_stream(choices=[{"delta": {"tool_calls": [{"index": 0, "id": "call_only"}]}}]), "EmptyOutput"),
    (event({"error": {"message": "synthetic private content"}}) + event("[DONE]"), "InvalidChunk"),
    (event([]) + event("[DONE]"), "InvalidChunk"),
    (event({"choices": [{"index": 1, "delta": {}}]}) + event("[DONE]"), "UnexpectedChoice"),
    (event({"choices": [{"delta": {"tool_calls": [{"function": {"name": "lookup"}}]}}]})
     + event("[DONE]"), "InvalidToolIndex"),
], ids=["missing-done", "incomplete-event", "missing-timings", "empty-output", "id-only-output",
        "server-error", "non-object-chunk",
        "extra-choice", "missing-tool-index"])
def test_incomplete_or_invalid_stream_is_a_failure(probe, monkeypatch, stream, diagnostic):
    conn, _ = transport(monkeypatch, probe, Response([stream]))
    assert probe.post_stream("http://localhost", {}) == (None, None, None, "stream", diagnostic)
    assert conn.closed


@pytest.mark.parametrize("value", [None, 0, -1, True, "10", float("nan"), float("inf")])
def test_invalid_generation_metrics_cannot_become_speed_measurements(probe, monkeypatch, value):
    timings = {**TIMINGS, "predicted_per_second": value}
    transport(monkeypatch, probe, Response([complete_stream(timings=timings)]))
    assert probe.post_stream("http://localhost", {}) == (None, None, None, "stream", "InvalidTimings")


def test_malformed_json_is_reported_without_echoing_payload(probe, monkeypatch):
    transport(monkeypatch, probe, Response([event('{"private": synthetic-secret')]))
    assert probe.post_stream("http://localhost", {}) == (None, None, None, "exc", "JSONDecodeError")


@pytest.mark.parametrize("status", [400, 503])
def test_http_error_is_sanitized(probe, monkeypatch, status):
    conn, _ = transport(monkeypatch, probe, Response([], status))
    assert probe.post_stream("http://localhost", {}) == (None, None, None, status, "HTTPError")
    assert conn.closed


def test_connect_failure_is_recordable_and_connection_is_closed(probe, monkeypatch):
    conn, _ = transport(monkeypatch, probe, Response([]), ConnectionRefusedError("synthetic-secret"))
    assert probe.post_stream("http://localhost", {}) == (None, None, None, "exc", "ConnectionRefusedError")
    assert conn.closed


@pytest.mark.parametrize(("base", "https", "host", "port", "path"), [
    ("http://localhost:18557", False, "localhost", 18557, "/v1/chat/completions"),
    ("https://localhost", True, "localhost", None, "/v1/chat/completions"),
    ("https://localhost/proxy/v1/", True, "localhost", None, "/proxy/v1/chat/completions"),
    ("http://localhost/proxy", False, "localhost", None, "/proxy/v1/chat/completions"),
])
def test_url_scheme_and_prefix_are_preserved(probe, monkeypatch, base, https, host, port, path):
    conn, constructed = transport(monkeypatch, probe, Response([complete_stream()]), https=https)
    assert probe.post_stream(base, {})[3] == 200
    assert constructed == [(host, port, 7200)]
    assert conn.path == path


@pytest.mark.parametrize("base", ["ftp://localhost", "http://", "http://localhost?q=1", "http://localhost/#x"])
def test_invalid_base_url_is_rejected(probe, base):
    with pytest.raises(ValueError):
        probe.post_stream(base, {})


def test_rerun_records_failures_replaces_stale_results_and_sets_model(probe, monkeypatch, tmp_path, capsys):
    cases = tmp_path / "cases.json"
    output = tmp_path / "results.json"
    cases.write_text(json.dumps({"cases": {"success": [], "failure": []}, "tools": []}), encoding="utf-8")
    output.write_text('{"stale":{"gen_tps":999},"failure":{"gen_tps":999}}', encoding="utf-8")
    conns = iter([Connection(Response([complete_stream()])), Connection(Response([], 503))])
    requests = []

    def factory(*args, **kwargs):
        conn = next(conns)
        requests.append(conn)
        return conn

    monkeypatch.setattr(probe.http.client, "HTTPConnection", factory)
    monkeypatch.setenv("PROBE_MODEL", "synthetic-model")
    assert probe.main(["http://localhost", "label", str(cases), str(output)]) == 1
    results = json.loads(output.read_text(encoding="utf-8"))
    assert set(results) == {"success", "failure"}
    assert results["success"]["status"] == "ok"
    assert {key: results["failure"][key] for key in ("status", "http_status", "error")} == {
        "status": "failed", "http_status": 503, "error": "HTTPError"}
    assert results["failure"]["wall"] >= 0
    assert all(json.loads(conn.body)["model"] == "synthetic-model" for conn in requests)
    assert not list(tmp_path.glob("*.tmp"))
    assert "synthetic-model" not in capsys.readouterr().out


def test_bad_case_file_invalidates_prior_measurements(probe, tmp_path):
    cases = tmp_path / "cases.json"
    output = tmp_path / "results.json"
    cases.write_text("not-json", encoding="utf-8")
    output.write_text('{"stale":{"gen_tps":999}}', encoding="utf-8")
    assert probe.main(["http://localhost", "label", str(cases), str(output)]) == 1
    assert json.loads(output.read_text(encoding="utf-8")) == {}


def test_probe_cannot_overwrite_case_input(probe, tmp_path):
    cases = tmp_path / "cases.json"
    cases.write_text('{"cases":{"synthetic":[]},"tools":[]}', encoding="utf-8")
    original = cases.read_bytes()
    assert probe.main(["http://localhost", "label", str(cases), str(cases)]) == 1
    assert cases.read_bytes() == original


def valid_turn():
    return {"gen_tps": 10, "pp_tps": 20, "n": 3, "wall": 1,
            "ttft_s": 0.5, "ttft_derived_s": 0.6, "draft": 2, "accepted": 0}


@pytest.mark.parametrize(("turns", "expected", "status"), [
    ({"a": valid_turn()}, ["a"], "ok"),
    ({"a": valid_turn(), "b": {"error": "HTTPError"}}, ["a", "b"], "partial"),
    ({"a": valid_turn()}, ["a", "b"], "partial"),
    ({"a": valid_turn(), "stale": valid_turn()}, ["a"], "partial"),
    ({"a": {**valid_turn(), "status": "failed"}}, ["a"], "failed"),
    ({"a": {**valid_turn(), "gen_tps": float("nan")}}, ["a"], "failed"),
    ({"a": None}, ["a"], "failed"),
    ({}, ["a"], "failed"),
])
def test_summary_distinguishes_failed_partial_and_legacy_complete_runs(analyzer, monkeypatch, tmp_path,
                                                                    turns, expected, status):
    monkeypatch.setattr(analyzer, "KIT", tmp_path)
    monkeypatch.setattr(analyzer, "CONFIGS", [("cuda-base", "CUDA", "32k")])
    (tmp_path / "speed_cases.json").write_text(json.dumps({"cases": dict.fromkeys(expected, [])}), encoding="utf-8")
    (tmp_path / "speed_cases.cuda-base.out.json").write_text(json.dumps(turns), encoding="utf-8")
    row = analyzer.collect()[0]
    assert row["status"] == status
    if status in {"ok", "partial"}:
        assert row["accept_rate"] == 0
        assert row["cases"] == 1


@pytest.mark.parametrize("payload", ["not-json", "[]"])
def test_corrupt_result_file_is_failed_rather_than_not_run(analyzer, monkeypatch, tmp_path, payload):
    monkeypatch.setattr(analyzer, "KIT", tmp_path)
    monkeypatch.setattr(analyzer, "CONFIGS", [("cuda-base", "CUDA", "32k")])
    (tmp_path / "speed_cases.cuda-base.out.json").write_text(payload, encoding="utf-8")
    assert analyzer.collect()[0]["status"] == "failed"


def test_current_results_take_priority_over_historical_file_names(analyzer, monkeypatch, tmp_path):
    monkeypatch.setattr(analyzer, "KIT", tmp_path)
    monkeypatch.setattr(analyzer, "CONFIGS", [("cuda-base", "CUDA", "32k")])
    (tmp_path / "speed_cases.json").write_text('{"cases":{"a":[]}}', encoding="utf-8")
    (tmp_path / "speed_cases.cuda-base.out.json").write_text(json.dumps({"a": valid_turn()}), encoding="utf-8")
    (tmp_path / "cuda-base.out.json").write_text('{"a":{"status":"failed","error":"HTTPError"}}', encoding="utf-8")
    assert analyzer.collect()[0]["status"] == "failed"


def test_complete_historical_speed_files_still_analyze_without_edits(analyzer, monkeypatch):
    monkeypatch.setattr(analyzer, "KIT", ROOT / "benchmarks/windows/results/speed")
    rows = analyzer.collect()
    assert len(rows) == 9
    assert all(row["status"] == "ok" for row in rows)
    assert [row["cases"] for row in rows] == [3] * 6 + [2] * 3


@pytest.mark.parametrize(("scenario", "returncode", "status"), [
    ("complete", 0, "ok"), ("truncated", 1, "failed"), ("http-error", 1, "failed"),
])
def test_cli_exit_status_and_utf8_output_with_real_loopback_stream(tmp_path, monkeypatch,
                                                                scenario, returncode, status):
    monkeypatch.delenv("PROBE_MODEL", raising=False)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.path == "/v1/chat/completions"
            assert body.get("model", "minicpm5-2b") == "minicpm5-2b"
            self.send_response(503 if scenario == "http-error" else 200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            payload = event({"choices": [{"delta": {"content": "合成"}}], "timings": TIMINGS})
            if scenario == "complete":
                payload += event("[DONE]")
            elif scenario == "http-error":
                payload = b"synthetic-private-error"
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cases = tmp_path / "cases.json"
    output = tmp_path / "results.json"
    cases.write_text('{"cases":{"synthetic":[]},"tools":[]}', encoding="utf-8")
    output.write_text('{"stale":{"gen_tps":999}}', encoding="utf-8")
    try:
        result = subprocess.run([sys.executable, str(ROOT / "benchmarks/windows/speed_probe.py"),
                                 f"http://127.0.0.1:{server.server_port}", "label", str(cases), str(output)],
                                capture_output=True, text=True, timeout=10)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result.returncode == returncode, result.stdout + result.stderr
    results = json.loads(output.read_text(encoding="utf-8"))
    assert set(results) == {"synthetic"}
    assert results["synthetic"]["status"] == status
    if scenario == "complete":
        assert "合成" in results["synthetic"]["text"]
    assert "合成" not in result.stdout
    assert "synthetic-private-error" not in result.stdout


def test_summary_zero_acceptance_and_decimal_cpu_load_are_retained(analyzer, monkeypatch, tmp_path, capsys):
    log = tmp_path / "speed_windows.out"
    log.write_text("== cuda-base  cpu_load 12.5%\n", encoding="utf-8")
    monkeypatch.setattr(analyzer, "OUT", log)
    assert analyzer.read_log()[1] == {"cuda-base": 12.5}
    row = {"label": "cuda-base", "backend": "CUDA", "status": "ok", "gen_median": 10,
           "pp_median": 20, "ttft_min": 0.5, "ttft_median": 0.5, "accept_rate": 0,
           "tokens": 3, "wall_total": 1}
    analyzer.table([row])
    values = capsys.readouterr().out.splitlines()[2].split()
    assert values[6] == "0"
