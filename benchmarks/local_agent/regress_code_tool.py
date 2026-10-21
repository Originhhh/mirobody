"""Regression: the local model's code tool must be Python.

Runs the two probe cases where the JS `eval` arm failed repeatedly
(`bp_q2`, `steps_august`) and requires the Python arm to pass every case with
zero code errors. `--compare` also runs the JS arm and prints the gap without
asserting on it.

This needs a running llama-server (or any OpenAI-compatible endpoint):

    python benchmarks/local_agent/regress_code_tool.py \
        --base http://127.0.0.1:8088/v1 --model minicpm5-2b

Exit code 1 means the Python arm regressed; a turn-level failure is printed
with the failing case, its calls and its answer.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_language_probe import JS_TOOL, PY_TOOL, cases, run_case, score

DEFAULT_CASES = ("bp_q2", "steps_august")


def run(base: str, model: str, names: tuple[str, ...], tool: dict, tool_name: str,
        timeout: float, max_rounds: int) -> list[dict]:
    selected = {c["id"]: c for c in cases()}
    out = []
    for name in names:
        case = selected[name]
        result = run_case(base, model, case, tool, timeout, max_rounds)
        result["score"] = score(case, result)
        mark = "PASS" if result["score"]["passed"] else "fail"
        print(f"[{tool_name}] {name:18s} {mark}  code={result['score']['code_calls']} "
              f"errors={result['score']['code_errors']} {result['seconds']}s", flush=True)
        out.append(result)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8088/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--cases", default=",".join(DEFAULT_CASES))
    parser.add_argument("--compare", action="store_true", help="also run the JS arm")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--max-rounds", type=int, default=6)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    names = tuple(item.strip() for item in args.cases.split(",") if item.strip())

    started = time.monotonic()
    python_results = run(args.base, args.model, names, PY_TOOL, "python",
                         args.timeout, args.max_rounds)
    compare_results = []
    if args.compare:
        compare_results = run(args.base, args.model, names, JS_TOOL, "js",
                              args.timeout, args.max_rounds)

    passed = sum(r["score"]["passed"] for r in python_results)
    errors = sum(r["score"]["code_errors"] for r in python_results)
    print(f"python: {passed}/{len(python_results)} passed, {errors} code errors, "
          f"{round(time.monotonic() - started, 1)}s")
    if compare_results:
        js_passed = sum(r["score"]["passed"] for r in compare_results)
        js_errors = sum(r["score"]["code_errors"] for r in compare_results)
        print(f"js: {js_passed}/{len(compare_results)} passed, {js_errors} code errors")
    if args.out:
        args.out.write_text(json.dumps({"python": python_results, "js": compare_results},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
    if passed != len(python_results) or errors:
        print("REGRESSION: the Python code tool did not pass every case with zero errors")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
