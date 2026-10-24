"""Ask a stronger model whether an answer actually answers the question.

The structural checks in `score.py` are deliberately literal: a number must appear,
bind to a label, cite a real rid. That keeps them reproducible, and it also rejects
correct answers phrased in a way the scorer did not anticipate ("a modest decline" for a
direction, "15.8 g/dL" once a citation sits between the number and its unit).

This is the second reader for those cases: it hands a judge model the question, the
expected facts and the answer, and asks whether the answer states them, which facts it
misses, and whether it claims a number the tool output does not contain. It never
replaces the structural result - both are reported.

    export JUDGE_BASE_URL=https://api.deepseek.com/v1   # any OpenAI-compatible endpoint
    export JUDGE_API_KEY=...                            # or DEEPSEEK_API_KEY / OPENAI_API_KEY
    python benchmarks/local_models/judge.py results/full --model deepseek-chat

Writes `judge.json` beside `qa.json` and prints a table. Cases are cached by id and answer
hash, so re-running after a scoring change only pays for what changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROMPT = """You are reviewing one answer from a personal health-record assistant.

Question:
{question}

The expected facts (the answer is correct only when it states every one of them; numbers
allow a small rounding tolerance):
{facts}

The raw tool output the assistant could rely on (it may be truncated):
{tools}

The assistant's answer:
{answer}

Reply with ONE JSON object and nothing else:
{{"correct": true|false,
  "missing": ["the expected facts the answer does not state"],
  "unsupported": ["numbers in the answer that appear nowhere in the tool output"],
  "note": "one short sentence on what is wrong when correct is false"}}

Judge the content, not the style: a differently worded but complete answer is correct.
An answer that states a number absent from the tool output is not correct, even if the
rest matches."""


def _key() -> str:
    for name in ("JUDGE_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        value = os.environ.get(name)
        if value:
            return value
    raise SystemExit("set JUDGE_API_KEY (or DEEPSEEK_API_KEY / OPENAI_API_KEY)")


def _base_url() -> str:
    return (os.environ.get("JUDGE_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")


def facts_text(case: dict) -> str:
    lines = []
    for fact in case.get("facts") or []:
        if "value" in fact:
            lines.append(f"- {fact.get('label')}: {fact['value']}")
        elif "any" in fact:
            lines.append(f"- {fact.get('label')}: one of {fact['any']}")
        else:
            lines.append(f"- {fact.get('label')}")
    return "\n".join(lines) or "- (none recorded)"


def tools_text(run: dict, limit: int = 6000) -> str:
    chunks = []
    for block in run.get("blocks") or []:
        if block.get("type") == "tool_result":
            chunks.append(str(block.get("content") or ""))
    text = "\n".join(chunks)
    return text[:limit]


def answer_text(run: dict) -> str:
    return "".join(str(b.get("text") or "") for b in run.get("blocks") or [] if b.get("type") == "text")


def ask(prompt: str, model: str, timeout: int = 120) -> dict[str, Any]:
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": 400,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    request = urllib.request.Request(
        f"{_base_url()}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as handle:
        payload = json.loads(handle.read())
    content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("judge did not answer with JSON")
    return json.loads(content[start:end + 1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", type=Path, help="a results directory holding qa.json")
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--cases-file", type=Path,
                    default=Path(__file__).resolve().parent / "cases.jsonl")
    ap.add_argument("--limit", type=int, default=0, help="judge at most N cases (0 = all)")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    args = ap.parse_args()

    run_data = json.loads((args.results / "qa.json").read_text())
    cases = {c["id"]: c for c in (json.loads(line) for line in args.cases_file.open() if line.strip())}
    cache_path = args.results / "judge.json"
    cache = {} if args.force or not cache_path.exists() else json.loads(cache_path.read_text())
    done = failed = 0

    for run in run_data.get("runs") or []:
        case = cases.get(run["id"])
        if not case:
            continue
        answer = answer_text(run).strip()
        if not answer:
            continue
        if args.limit and done + failed >= args.limit:
            break
        digest = hashlib.sha256(answer.encode()).hexdigest()[:16]
        cached = cache.get(run["id"])
        if cached and cached.get("answer_sha") == digest and not args.force:
            done += 1
            continue
        prompt = PROMPT.format(question=case.get("question", ""), facts=facts_text(case),
                               tools=tools_text(run), answer=answer)
        try:
            verdict = ask(prompt, args.model)
        except (urllib.error.URLError, ValueError, TimeoutError) as error:
            failed += 1
            print(f"  {run['id']}: judge failed ({type(error).__name__})", file=sys.stderr)
            continue
        verdict["answer_sha"] = digest
        verdict["model"] = args.model
        cache[run["id"]] = verdict
        done += 1
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))

    judged = [v for v in cache.values() if "correct" in v]
    correct = sum(1 for v in judged if v.get("correct") is True)
    print(f"\njudged {len(judged)} answers with {args.model}: {correct} correct, "
          f"{len(judged) - correct} not correct ({failed} failures)")
    for case_id, verdict in sorted(cache.items()):
        if verdict.get("correct") is False:
            print(f"  {case_id}: {verdict.get('note') or ''} missing={verdict.get('missing')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
