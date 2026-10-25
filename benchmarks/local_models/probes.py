"""Capability probes: one question per capability, with the answer computed from the build.

A benchmark gives a score; a probe gives a diagnosis. Each probe here asks the product one
question whose answer is known from the corpus itself, so a failure can be attributed to a
capability instead of averaged into a total:

* **C3 window** - "average <metric> from <start> to <end>" over a slice of the record, not
  "last week": the model has to name the dates, and naming the wrong ones is visible.
* **C4 aggregate** - a count, a minimum or a maximum over that window: the value has to come
  from the tool, not from arithmetic in the head.
* **C6 provenance** - a numeric answer should carry a `<cite>`; a figure with no source is a
  figure the person cannot check.
* **C7 absence** - a metric this person never recorded must be reported as not recorded.

The questions are generated from the build, so the same suite runs on any corpus:

    python benchmarks/local_models/probes.py --corpus <build> --person p001 \
        --email probe-p001@example.org --out results/probes/p001.json

Writes per-capability pass counts and every answer with its tool calls, and prints one line
per probe so a failure reads as "wrong window", "wrong aggregate", "no citation" or "guessed
a value it never had".
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import common      # the build reader and the account helpers
import run as bench  # ask(): one scored chat turn against the app

NUM = re.compile(r"-?\d+(?:\.\d+)?")
ABSENT_WORDS = ("not recorded", "no record", "never recorded", "no data", "没有记录", "未记录", "没有数据")


def series(records: list[dict]) -> list[tuple[str, float]]:
    """(date, value) for one indicator's records, oldest first, unparseable values dropped."""
    out = []
    for record in records:
        try:
            out.append((str(record.get("date") or ""), float(record["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(item for item in out if item[0])


def windows(points: list[tuple[str, float]], size: int = 30, count: int = 3) -> list[list[tuple[str, float]]]:
    """`count` disjoint slices spread over the record, so a "last week" habit cannot pass."""
    if len(points) < size * count:
        step = max(1, len(points) // count)
        return [points[i * step:(i + 1) * step] for i in range(count) if points[i * step:(i + 1) * step]]
    step = (len(points) - size) // max(count - 1, 1)
    return [points[i * step:i * step + size] for i in range(count)]


def build(corpus, person: str) -> list[dict]:
    device = corpus.devices.get(person) or {}
    indicators = list(device.get("series") or [])
    if not indicators:
        # A build may keep every indicator in one row's `records` rather than per series.
        indicators = [{"indicator": device.get("indicator"), "records": device.get("records") or []}]
    usable = [(row.get("indicator"), series(list(row.get("records") or []))) for row in indicators]
    usable = [(name, points) for name, points in usable if name and len(points) >= 30]
    if not usable:
        return []
    probes: list[dict] = []
    for name, points in usable[:3]:
        for block in windows(points):
            start, end = block[0][0], block[-1][0]
            values = [value for _, value in block]
            probes.append({
                "capability": "C3", "metric": name,
                "question": (f"What was my average {name} from {start} to {end}? "
                             "Reply with the number only."),
                "gold": round(statistics.fmean(values), 3), "tol": max(0.05, abs(statistics.fmean(values)) * 0.01),
            })
            probes.append({
                "capability": "C4", "metric": name,
                "question": f"How many days between {start} and {end} have a {name} record? Reply with the integer only.",
                "gold": float(len(values)), "tol": 0.01,
            })
            probes.append({
                "capability": "C4", "metric": name,
                "question": f"Between {start} and {end}, what was my highest {name}? Reply with the number only.",
                "gold": float(max(values)), "tol": max(0.05, abs(max(values)) * 0.01),
            })
    absent = "blood glucose" if not any("glucose" in (name or "").lower() for name, _ in usable) else "peak flow"
    probes.append({
        "capability": "C7", "metric": absent, "gold": None, "tol": None, "expect_absent": True,
        "question": (f"What was my {absent} reading last month? "
                     "If it was never recorded, say that it was not recorded."),
    })
    return probes


def number_in(text: str) -> str:
    match = NUM.search(text.replace(",", ""))
    return match.group(0) if match else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--person", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--timeout", type=float, default=300)
    args = ap.parse_args()

    corpus = common.Corpus(args.corpus)
    probes = build(corpus, args.person)
    if not probes:
        raise SystemExit(f"{args.person} has no series long enough for a window probe")
    account = common.sign_in(args.email, tz="Asia/Shanghai")

    results, passed = [], {}
    for probe in probes:
        turn = bench.ask(account, probe["question"], args.timeout)
        text = "".join(b.get("text") or "" for b in turn.get("blocks", []) if b.get("type") == "text")
        tools = [b.get("name") for b in turn.get("blocks", []) if b.get("type") == "tool_call"]
        cited = "<cite>" in text
        if probe.get("expect_absent"):
            ok = any(word in text.lower() for word in ABSENT_WORDS)
            detail = "said not recorded" if ok else f"answered {text[:40]!r}"
        else:
            got = number_in(text)
            try:
                ok = bool(got) and abs(float(got) - probe["gold"]) <= probe["tol"]
            except ValueError:
                ok = False
            detail = f"got {got or '-'} want {probe['gold']}"
        results.append({**{k: v for k, v in probe.items() if k != "question"},
                        "answer": text[:300], "tools": tools, "ok": ok, "detail": detail, "cited": cited})
        bucket = passed.setdefault(probe["capability"], [0, 0])
        bucket[0] += 1 if ok else 0
        bucket[1] += 1
        print(f"{'OK ' if ok else 'X  '} {probe['capability']} {probe['metric'][:20]:20s} {detail} "
              f"tools={len(tools)} cite={cited}", flush=True)

    numeric = [r for r in results if r.get("gold") is not None]
    if numeric:
        passed["C6"] = [sum(1 for r in numeric if r["cited"]), len(numeric)]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"person": args.person, "pass": passed, "probes": results},
                                   ensure_ascii=False, indent=1))
    print()
    for capability, (ok, total) in sorted(passed.items()):
        print(f"  {capability}: {ok}/{total}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
