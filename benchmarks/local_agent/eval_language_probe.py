"""A/B the code tool, not the model.

Same local model, same synthetic readings, same questions. The only change is
the tool the agent calls to do arithmetic:

    --tool js      ``eval``: JavaScript in QuickJS, the interpreter Mirobody ships.
    --tool python  ``python_exec``: Python in a subprocess.

The readings come from ``query_health_indicators`` (a mock that returns the
shipped table shape), and the last result is exposed to the code tool as
``data``, so the model does not have to copy rows into its code. The prompt is
deliberately neutral about the language; it only says to use the available
code tool for multi-row arithmetic.

Run one arm at a time against a llama-server:

    python benchmarks/local_agent/eval_language_probe.py \
        --base http://127.0.0.1:8088/v1 --model minicpm5-2b \
        --tool js --out /tmp/eval-js.json

This is a probe, not a gate: one run per arm, and a 2B model is noisy. Read the
per-case numbers together with the calls, not as a leaderboard.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import requests


SYSTEM = """You are Mirobody, a health-data assistant. The person's readings are
available only through tools. Answer in the language of the question.

Rules:
- Call query_health_indicators to read the readings a question needs. Do not
  invent a reading or a date.
- For any arithmetic over more than two rows (a mean, a total, a change, a
  percentage), call the available code tool. The rows from the last query are
  already available to it as `data`; do not paste them into the code.
- After the code tool returns, answer with the numbers and units. Keep it
  short. If the data has no rows, say that the reading is not recorded.
"""


def _d(start: str, days: int) -> str:
    return (date.fromisoformat(start) + timedelta(days=days)).isoformat()


ROWS: list[dict] = []


def add(indicator: str, when: str, value: float, unit: str) -> None:
    ROWS.append({"indicator": indicator, "date": when, "value": value, "unit": unit})


def build_rows() -> None:
    if ROWS:
        return
    # Resting heart rate, every third day, March through August.
    for i in range(61):
        add("Resting heart rate", _d("2026-03-01", 3 * i),
            round(45.0 + 0.8 * math.sin(i / 3.0) + 0.02 * i, 1), "bpm")
    # One month of nightly sleep.
    for i in range(31):
        add("Sleep duration", f"2026-07-{i + 1:02d}", 407 + ((i * 7) % 23) - 11, "min")
    # Weekly weight, January through August.
    for week in range(32):
        add("Weight", _d("2026-01-05", 7 * week),
            round(84.2 + 0.035 * week + 0.15 * math.sin(week), 2), "kg")
    # Blood pressure twice a week, April through June.
    for i in range(24):
        when = _d("2026-04-03", 3 * i)
        add("Systolic blood pressure", when, 112 + (i % 5), "mmHg")
        add("Diastolic blood pressure", when, 68 + (i % 4), "mmHg")
    # Three HbA1c results.
    for when, value in (("2024-04-10", 6.6), ("2025-04-05", 6.1), ("2026-03-31", 6.1)):
        add("HbA1c", when, value, "%")
    # Daily steps in August.
    for i in range(31):
        add("Steps", f"2026-08-{i + 1:02d}", 2000 + ((i * 977) % 10000), "steps")
    # Monthly LDL, January through June.
    for month in range(1, 7):
        add("LDL cholesterol", f"2026-{month:02d}-10",
            round(3.3 - 0.1 * month + 0.05 * (month % 2), 2), "mmol/L")
    # Monthly fasting glucose, January through June.
    for month in range(1, 7):
        add("Fasting glucose", f"2026-{month:02d}-10", 95 + (month * 3) % 10, "mg/dL")


ALIASES = {
    "Resting heart rate": ("resting heart rate", "rhr", "静息心率", "心率"),
    "Sleep duration": ("sleep", "睡眠"),
    "Weight": ("weight", "体重"),
    "Systolic blood pressure": ("systolic", "收缩压", "blood pressure", "血压"),
    "Diastolic blood pressure": ("diastolic", "舒张压", "blood pressure", "血压"),
    "HbA1c": ("hba1c", "glycated", "糖化血红蛋白", "糖化"),
    "Steps": ("steps", "步数", "走路"),
    "LDL cholesterol": ("ldl", "低密度脂蛋白", "低密度"),
    "Fasting glucose": ("fasting glucose", "空腹血糖", "血糖"),
}


def select_rows(keywords: list[str]) -> list[dict]:
    if not keywords:
        return list(ROWS)
    chosen: set[str] = set()
    for row in ROWS:
        haystack = " ".join((row["indicator"],) + ALIASES.get(row["indicator"], ())).casefold()
        if any(str(k).casefold() in haystack for k in keywords):
            chosen.add(row["indicator"])
    return [row for row in ROWS if row["indicator"] in chosen]


def table(rows: list[dict]) -> str:
    lines = ["indicator|date|value|unit"]
    lines.extend(f"{r['indicator']}|{r['date']}|{r['value']}|{r['unit']}" for r in rows)
    lines.append(f"notes: {len(rows)} rows; no row means the reading was never recorded")
    return "\n".join(lines)


def monthly_mean(indicator: str, month: str) -> float:
    values = [float(r["value"]) for r in ROWS if r["indicator"] == indicator and r["date"].startswith(month)]
    return round(sum(values) / len(values), 4)


def mean(indicator: str, start: str = "", end: str = "") -> float:
    values = [float(r["value"]) for r in ROWS
              if r["indicator"] == indicator and (not start or r["date"] >= start)
              and (not end or r["date"] <= end)]
    return round(sum(values) / len(values), 4)


def _case(case_id: str, question: str, keywords: list[str], expect: list[dict]) -> dict:
    return {"id": case_id, "question": question, "keywords": keywords, "expect": expect}


def cases() -> list[dict]:
    build_rows()
    jan_weight = monthly_mean("Weight", "2026-01")
    aug_weight = monthly_mean("Weight", "2026-08")
    ldl_first = float(next(r["value"] for r in ROWS if r["indicator"] == "LDL cholesterol"
                           and r["date"].startswith("2026-01")))
    ldl_last = float(next(r["value"] for r in ROWS if r["indicator"] == "LDL cholesterol"
                          and r["date"].startswith("2026-06")))
    glucose = mean("Fasting glucose", "2026-01-01", "2026-06-30")
    steps = [float(r["value"]) for r in ROWS if r["indicator"] == "Steps" and r["date"].startswith("2026-08")]
    hba1c = [float(r["value"]) for r in ROWS if r["indicator"] == "HbA1c"]
    return [
        _case("rhr_monthly",
              "我 2026 年 3 月到 8 月的静息心率月均值分别是多少？请按月列出。",
              ["静息心率"],
              [{"label": month, "value": monthly_mean("Resting heart rate", month), "tol": 0.06}
               for month in ("2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08")]),
        _case("sleep_july",
              "我 2026 年 7 月平均每晚睡多少分钟？",
              ["睡眠"],
              [{"label": "July mean sleep", "value": mean("Sleep duration", "2026-07-01", "2026-07-31"),
                "tol": 2.0}]),
        _case("weight_change",
              "我 2026 年 1 月和 8 月的平均体重分别是多少？这期间变化了多少公斤？",
              ["体重"],
              [{"label": "January mean", "value": jan_weight, "tol": 0.1},
               {"label": "August mean", "value": aug_weight, "tol": 0.1},
               {"label": "change kg", "value": round(aug_weight - jan_weight, 4), "tol": 0.15}]),
        _case("bp_q2",
              "我 2026 年 4 到 6 月的平均收缩压和舒张压分别是多少？",
              ["血压"],
              [{"label": "mean systolic",
                "value": mean("Systolic blood pressure", "2026-04-01", "2026-06-30"), "tol": 0.6},
               {"label": "mean diastolic",
                "value": mean("Diastolic blood pressure", "2026-04-01", "2026-06-30"), "tol": 0.6}]),
        _case("hba1c_delta",
              "我最近三次糖化血红蛋白分别是多少？从第一次到最后一次变化了多少？",
              ["糖化血红蛋白"],
              [{"label": "first HbA1c", "value": hba1c[0], "tol": 0.05},
               {"label": "second HbA1c", "value": hba1c[1], "tol": 0.05},
               {"label": "third HbA1c", "value": hba1c[2], "tol": 0.05},
               {"label": "delta", "value": round(hba1c[-1] - hba1c[0], 4), "tol": 0.05}]),
        _case("steps_august",
              "我 2026 年 8 月一共走了多少步？日均多少步？",
              ["步数"],
              [{"label": "total steps", "value": sum(steps), "tol": 1},
               {"label": "daily mean", "value": round(sum(steps) / len(steps), 4), "tol": 2}]),
        _case("ldl_pct",
              "我 2026 年 1 月到 6 月的 LDL 胆固醇从第一次到最后一次变化了百分之几？",
              ["LDL"],
              [{"label": "first LDL", "value": ldl_first, "tol": 0.03},
               {"label": "last LDL", "value": ldl_last, "tol": 0.03},
               {"label": "percent change", "value": round((ldl_last - ldl_first) / ldl_first * 100, 4),
                "tol": 1.0}]),
        _case("glucose_convert",
              "我 2026 年上半年空腹血糖的平均值是多少 mg/dL？换算成 mmol/L 是多少？",
              ["空腹血糖"],
              [{"label": "mean mg/dL", "value": glucose, "tol": 0.6},
               {"label": "mean mmol/L", "value": round(glucose / 18.018, 4), "tol": 0.06}]),
    ]


QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "query_health_indicators",
        "description": "Read this person's recorded health readings. Returns a table with indicator, date, value and unit.",
        "parameters": {
            "type": "object",
            "properties": {"keywords": {"type": "array", "items": {"type": "string"}}},
            "required": ["keywords"],
        },
    },
}

JS_TOOL = {
    "type": "function",
    "function": {
        "name": "eval",
        "description": ("Run JavaScript in a sandboxed REPL. `data` is the array of row objects "
                        "returned by the last query, each with indicator, date, value and unit. "
                        "Use console.log(...) to return the result."),
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "JavaScript source."}},
            "required": ["code"],
        },
    },
}

PY_TOOL = {
    "type": "function",
    "function": {
        "name": "python_exec",
        "description": ("Run Python in an isolated process. `data` is the list of dicts "
                        "returned by the last query, each with indicator, date, value and unit. "
                        "Use print(...) to return the result."),
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python source."}},
            "required": ["code"],
        },
    },
}


def _run(cmd: list[str], cwd: str, timeout: float) -> str:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, env={"PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/local/bin"})
    except subprocess.TimeoutExpired:
        return "error: timed out"
    out = (proc.stdout or "") + (proc.stderr or "")
    out = out.strip()
    if len(out) > 4000:
        out = out[:4000] + "\n[truncated]"
    if proc.returncode != 0:
        return f"error (exit {proc.returncode}): {out or '(no output)'}"
    return out or "(no output; use console.log/print)"


def run_js(code: str, data: list[dict], timeout: float) -> str:
    from quickjs_rs import UNDEFINED, Context, Runtime

    runtime = Runtime()
    ctx = Context(runtime, timeout=timeout)
    try:
        ctx.eval("globalThis.__logs = []; globalThis.console = {"
                 "log: (...a) => __logs.push(a.map(String).join(' ')),"
                 "warn: (...a) => __logs.push(a.map(String).join(' ')),"
                 "error: (...a) => __logs.push(a.map(String).join(' '))}; 0")
        ctx.eval("globalThis.data = " + json.dumps(data, ensure_ascii=False) + ";")
        result = ctx.eval("(function(){\n" + code + "\n})(); 0")
        logs = ctx.eval("__logs.join('\\n')") or ""
        if result is not UNDEFINED and result is not None:
            logs = (logs + "\n" + str(result)).strip()
        return (logs.strip() or "(no output; use console.log)")[:4000]
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}"[:4000]
    finally:
        ctx.close()


def run_python(code: str, data: list[dict], timeout: float) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        script = ("import json\n"
                  "data = json.load(open('data.json', encoding='utf-8'))\n"
                  + code + "\n")
        Path(tmp, "main.py").write_text(script, encoding="utf-8")
        return _run([sys.executable, "-I", "main.py"], tmp, timeout)


def send(base: str, model: str, messages: list[dict], tools: list[dict],
         max_tokens: int = 4096) -> dict:
    payload = {"model": model, "messages": messages, "temperature": 0,
               "max_tokens": max_tokens, "stream": False,
               "tools": tools, "tool_choice": "auto"}
    response = requests.post(f"{base}/chat/completions", json=payload, timeout=300)
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:1200]}")
    return response.json()


def run_case(base: str, model: str, case: dict, tool: dict, code_timeout: float,
             max_rounds: int) -> dict:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": case["question"]}]
    tools = [QUERY_TOOL, tool]
    data: list[dict] = []
    calls: list[dict] = []
    started = time.monotonic()
    answer = ""
    finish = "round_limit"
    for _ in range(max_rounds):
        try:
            body = send(base, model, messages, tools)
        except Exception as exc:
            finish = f"http_error: {exc}"
            break
        message = body["choices"][0]["message"]
        answer = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            finish = body["choices"][0].get("finish_reason") or "stop"
            break
        messages.append({"role": "assistant", "content": answer, "tool_calls": tool_calls})
        for call in tool_calls:
            name = call["function"]["name"]
            raw = call["function"].get("arguments", "")
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = None
            record = {"name": name, "arguments": raw, "result": ""}
            if not isinstance(args, dict):
                record["result"] = "error: malformed JSON arguments"
            elif name == "query_health_indicators":
                data = select_rows([str(k) for k in args.get("keywords", [])])
                record["result"] = table(data)
            elif name == tool["function"]["name"]:
                code = args.get("code", "")
                record["code"] = code
                if not data:
                    record["result"] = "error: no query result yet; call query_health_indicators first"
                elif name == "eval":
                    record["result"] = run_js(code, data, code_timeout)
                else:
                    record["result"] = run_python(code, data, code_timeout)
            else:
                record["result"] = f"error: unknown tool {name}"
            calls.append(record)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": record["result"]})
    elapsed = round(time.monotonic() - started, 1)
    return {"case": case["id"], "question": case["question"], "answer": answer,
            "finish": finish, "calls": calls, "seconds": elapsed}


def numbers(text: str) -> list[float]:
    text = text.replace(",", "")
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", text)]


def score(case: dict, result: dict) -> dict:
    found = numbers(result["answer"])
    checks = []
    for item in case["expect"]:
        hit = any(abs(value - item["value"]) <= item["tol"] for value in found)
        checks.append({"label": item["label"], "expected": item["value"], "hit": hit})
    code_calls = [c for c in result["calls"] if c["name"] in ("eval", "python_exec")]
    errors = [c for c in code_calls if c["result"].startswith("error")]
    return {"passed": all(c["hit"] for c in checks), "checks": checks,
            "code_calls": len(code_calls), "code_errors": len(errors),
            "query_calls": len([c for c in result["calls"] if c["name"] == "query_health_indicators"])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8088/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--tool", choices=("js", "python"), required=True)
    parser.add_argument("--case", choices=[c["id"] for c in cases()])
    parser.add_argument("--cases", help="comma-separated case ids (for a regression subset)")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-rounds", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()
    wanted = {item.strip() for item in (args.cases or "").split(",") if item.strip()}
    selected = [c for c in cases()
                if (not args.case or c["id"] == args.case) and (not wanted or c["id"] in wanted)]
    tool = JS_TOOL if args.tool == "js" else PY_TOOL
    results = []
    for case in selected:
        result = run_case(args.base, args.model, case, tool, args.timeout, args.max_rounds)
        result["score"] = score(case, result)
        results.append(result)
        mark = "PASS" if result["score"]["passed"] else "fail"
        print(f"{case['id']:18s} {mark}  code={result['score']['code_calls']} "
              f"errors={result['score']['code_errors']} {result['seconds']}s", flush=True)
    payload = {"tool": args.tool, "model": args.model, "results": results,
               "summary": {"passed": sum(r["score"]["passed"] for r in results), "n": len(results)}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
