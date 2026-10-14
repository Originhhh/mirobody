"""Measure one llama-server configuration on fixed agent turns (greedy).

TTFT is measured from the first content, reasoning or tool-call fragment. The
derived TTFT is a server-side estimate (prompt_ms + 1/generation_rate), excluding
request/network overhead. http.client.read1 avoids the Windows urllib stream
reset observed on long CPU turns.

  python speed_probe.py <base-url> <label> <cases.json> [out.json]

PROBE_MODEL defaults to minicpm5-2b, including requests through the model router.
Each invocation replaces its result file; any failed case makes the exit nonzero.
"""
import argparse
import http.client
import json
import math
import os
from pathlib import Path
import tempfile
import time
import urllib.parse


class StreamError(Exception):
    """A fixed diagnostic code, never a response body or model output."""


def _events(response):
    pending = b""
    data = []
    while block := response.read1(65536):
        pending += block
        while b"\n" in pending:
            raw, pending = pending.split(b"\n", 1)
            line = raw.rstrip(b"\r").decode("utf-8")
            if not line:
                if data:
                    yield "\n".join(data)
                    data = []
            elif line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
    if pending or data:
        raise StreamError("IncompleteEvent")


def _check_timings(timings):
    if not isinstance(timings, dict):
        raise StreamError("MissingTimings")
    required = ("predicted_per_second", "predicted_n", "prompt_per_second", "prompt_n", "prompt_ms")
    for key in required:
        value = timings.get(key)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0 or (key != "prompt_ms" and value == 0)):
            raise StreamError("InvalidTimings")
    for key in ("draft_n", "draft_n_accepted"):
        value = timings.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value < 0):
            raise StreamError("InvalidTimings")


def post_stream(base, body, timeout=7200):
    """Return (timings, message, ttft_s, status, diagnostic_code)."""
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError("base URL must be an HTTP(S) endpoint")
    prefix = parsed.path.rstrip("/")
    path = prefix + ("/chat/completions" if prefix.endswith("/v1") else "/v1/chat/completions")
    connection = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = connection(parsed.hostname, parsed.port, timeout=timeout)
    timings = None
    first = None
    content, reasoning, tool_calls = "", "", {}
    try:
        conn.connect()
        t0 = time.perf_counter()
        conn.request("POST", path, body=json.dumps(body).encode(),
                     headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
        response = conn.getresponse()
        if response.status != 200:
            return None, None, None, response.status, "HTTPError"
        done = False
        for event in _events(response):
            if event == "[DONE]":
                done = True
                break
            obj = json.loads(event)
            if not isinstance(obj, dict) or obj.get("error"):
                raise StreamError("InvalidChunk")
            if "timings" in obj:
                timings = obj["timings"]
            for choice in obj.get("choices") or []:
                if choice.get("index", 0) != 0:
                    raise StreamError("UnexpectedChoice")
                delta = choice.get("delta") or {}
                reasoning_piece = delta.get("reasoning_content") or ""
                content_piece = delta.get("content") or ""
                fragments = delta.get("tool_calls") or []
                tool_piece = any((fragment.get("function") or {}).get(key)
                                 for fragment in fragments for key in ("name", "arguments"))
                if (reasoning_piece or content_piece or tool_piece) and first is None:
                    first = time.perf_counter()
                reasoning += reasoning_piece
                content += content_piece
                for fragment in fragments:
                    index = fragment.get("index")
                    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                        raise StreamError("InvalidToolIndex")
                    call = tool_calls.setdefault(index, {"function": {"name": "", "arguments": ""}})
                    for key in ("id", "type"):
                        if fragment.get(key):
                            call[key] = fragment[key]
                    function = fragment.get("function") or {}
                    for key in ("name", "arguments"):
                        call["function"][key] += function.get(key) or ""
        if not done:
            raise StreamError("IncompleteStream")
        _check_timings(timings)
        if first is None:
            raise StreamError("EmptyOutput")
    except StreamError as exc:
        return None, None, None, "stream", str(exc)
    except (OSError, http.client.HTTPException, ValueError, TypeError, AttributeError) as exc:
        return None, None, None, "exc", type(exc).__name__
    finally:
        conn.close()
    message = {"reasoning_content": reasoning, "content": content,
               "tool_calls": [tool_calls[index] for index in sorted(tool_calls)]}
    return timings, message, first - t0 if first is not None else None, 200, ""


def _write_results(path, results):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(results, output, ensure_ascii=False, allow_nan=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def run(base, label, cases_file, out_file, model):
    cases_file, out_file = Path(cases_file), Path(out_file)
    if cases_file.resolve() == out_file.resolve():
        raise ValueError("input and output paths must differ")
    # An interrupted or failed rerun must not publish the previous run's rates.
    results = {}
    _write_results(out_file, results)
    with cases_file.open(encoding="utf-8-sig") as source:
        data = json.load(source)
    cases = data.get("cases")
    if not isinstance(cases, dict) or not cases or not isinstance(data.get("tools"), list):
        raise ValueError("cases must be a nonempty mapping and tools must be a list")
    failures = 0
    for cid, messages in cases.items():
        body = {"messages": messages, "tools": data["tools"], "temperature": 0, "max_tokens": 700,
                "chat_template_kwargs": {"enable_thinking": True}, "cache_prompt": False,
                "model": model, "stream": True}
        start = time.perf_counter()
        timings, message, ttft, status, error = post_stream(base, body)
        wall = time.perf_counter() - start
        if status != 200:
            failures += 1
            results[cid] = {"status": "failed", "http_status": status, "error": error, "wall": round(wall, 1)}
            print(f"{label:24s} {cid:22s} FAILED status={status} error={error} wall={wall:.1f}s", flush=True)
        else:
            gen = timings["predicted_per_second"]
            ttft_derived = timings["prompt_ms"] / 1000 + 1 / gen
            result = {"status": "ok", "gen_tps": round(gen, 1), "n": timings["predicted_n"],
                      "pp_tps": round(timings["prompt_per_second"], 1), "pp_n": timings["prompt_n"],
                      "draft": timings.get("draft_n"), "accepted": timings.get("draft_n_accepted"),
                      "ttft_s": round(ttft, 3) if ttft is not None else None,
                      "ttft_derived_s": round(ttft_derived, 3), "wall": round(wall, 1),
                      "text": message["reasoning_content"] + "|" + message["content"]
                              + json.dumps(message["tool_calls"])}
            results[cid] = result
            print(f"{label:24s} {cid:22s} gen {result['gen_tps']} tok/s n={result['n']}"
                  f" pp {result['pp_tps']} tok/s ttft {result['ttft_s']}s"
                  f" (derived {result['ttft_derived_s']}s) draft {result['draft']}"
                  f" acc {result['accepted']} wall {result['wall']}s", flush=True)
        _write_results(out_file, results)
    return int(failures > 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("label")
    parser.add_argument("cases_file", type=Path)
    parser.add_argument("out_file", type=Path, nargs="?")
    args = parser.parse_args(argv)
    output = args.out_file or args.cases_file.with_name(f"{args.cases_file.stem}.{args.label}.out.json")
    try:
        return run(args.base, args.label, args.cases_file, output, os.environ.get("PROBE_MODEL", "minicpm5-2b"))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"probe failed: {type(exc).__name__}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
