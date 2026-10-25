"""Ask a stronger model whether an answer actually answers the question.

The structural checks in `score.py` are deliberately literal: a number must appear, bind to
a label, cite a real rid. That keeps them reproducible, and it also rejects correct answers
phrased differently. This is the second reader for those cases.

It is a *review*, not a replacement, and it is built to be auditable:

* the case list defaults to the manifest frozen in `qa.json` (``case_manifest``), so a gold
  edited after the run cannot silently change what was judged;
* the evidence packet carries every tool result of the turn and of the prior turns
  (``prior_blocks``), untruncated up to a generous cap, because a figure printed in an
  earlier turn is still supported;
* the facts keep their kind, tolerance, unit and duration, so "47.6 hours" for a duration
  gold is judged on meaning rather than string equality;
* the judge is told to separate a value the record shows, a value derived from record
  values (a ratio, a difference, a mean, a conversion), and a value from outside;
* a reply is accepted only if it is the documented shape; anything else is an
  *evaluator failure*, never a silent pass, and the summary reports the full denominator.

    export JUDGE_BASE_URL=https://api.deepseek.com/v1
    export JUDGE_API_KEY=...
    python benchmarks/local_models/judge.py results/full --model deepseek-chat
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

#: Bumped whenever the prompt or the packet changes, so cached verdicts from an older
#: rubric are never reused under the new one.
PROMPT_VERSION = "2026-10-25.2"

#: A larger serialized packet is an evaluator failure, never a truncated record
#: silently sent to the judge as if it were complete.
MAX_EVIDENCE_CHARS = 200_000

PROMPT = """You are reviewing one answer from a personal health-record assistant.

Judge the answer against the record the assistant had, not against your own medical
knowledge. Reply with ONE JSON object and nothing else.

Question:
{question}

Expected facts. Every one must be stated (numbers allow the tolerance shown; a duration may
be given in any unit that matches the same length of time):
{facts}

The record the assistant could use, from this turn and from earlier turns of the same
conversation:
{evidence}

The assistant's answer:
{answer}

How to judge a number:
- "recorded"   the record shows this value (possibly in another unit or after conversion);
- "derived"    the answer computed it from record values - a ratio, a difference, a mean, a
               total, a percentage change, a unit conversion - and the inputs are in the
               record. A derived value is correct when the arithmetic is right, even though
               that exact number is not printed anywhere;
- "outside"    neither the record nor a computation over it produces it. This is the one
               that counts as unsupported.

Reply shape:
{{"correct": true|false,
  "missing": ["expected facts the answer does not state"],
  "unsupported": ["numbers that are neither recorded nor derived"],
  "note": "one short sentence when correct is false, naming the fault"}}

A differently worded but complete answer is correct. An answer that mixes several plans or
subjects contradicts itself is not correct even when the numbers it quotes are real."""


def _key() -> str:
    for name in ("JUDGE_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        value = os.environ.get(name)
        if value:
            return value
    raise SystemExit("set JUDGE_API_KEY (or DEEPSEEK_API_KEY / OPENAI_API_KEY)")


def _base_url() -> str:
    return (os.environ.get("JUDGE_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")


# ----------------------------------------------------------------------------- evidence

def facts_text(case: dict) -> str:
    """Keep the complete typed fact, including duration alternatives and scope."""
    return "\n".join(f"- {fact.get('label') or 'fact'}: {json.dumps(fact, ensure_ascii=False, sort_keys=True)}"
                     for fact in case.get("facts") or []) or "- (none recorded)"


def _tool_blocks(run: dict) -> list[dict]:
    blocks = list(run.get("prior_blocks") or []) + list(run.get("blocks") or [])
    return [b for b in blocks if b.get("type") == "tool_result"]


def evidence_text(run: dict) -> str:
    """Every tool result of this turn and of the earlier ones, in order."""
    chunks = []
    for index, block in enumerate(_tool_blocks(run), start=1):
        status = block.get("status") or "ok"
        name = block.get("name") or block.get("tool") or "tool"
        content = str(block.get("content") or "")
        chunks.append(f"[{index}] {name} ({status})\n{content}")
    text = "\n\n".join(chunks)
    if len(text) > MAX_EVIDENCE_CHARS:
        raise ValueError("Serialized tool evidence exceeds the complete-packet budget")
    return text or "(no tool result)"


def answer_text(run: dict) -> str:
    return "".join(str(b.get("text") or "") for b in run.get("blocks") or [] if b.get("type") == "text")


# ----------------------------------------------------------------------------- verdicts

VERDICT_TYPES = {"correct": bool, "missing": list, "unsupported": list}


def validate(verdict: Any) -> dict[str, Any]:
    """The documented shape or an exception: a malformed reply is not a verdict."""
    if not isinstance(verdict, dict):
        raise ValueError("verdict is not an object")
    for field, expected in VERDICT_TYPES.items():
        if field not in verdict:
            raise ValueError(f"verdict lacks {field}")
        if not isinstance(verdict[field], expected):
            raise ValueError(f"verdict.{field} is not {expected.__name__}")
    for field in ("missing", "unsupported"):
        if any(not isinstance(value, str) or not value.strip() for value in verdict[field]):
            raise ValueError(f"verdict.{field} must contain nonempty strings")
    if verdict["correct"] and (verdict["missing"] or verdict["unsupported"]):
        raise ValueError("A correct verdict cannot have missing or unsupported facts")
    note = verdict.get("note", "")
    if not isinstance(note, str):
        raise ValueError("verdict.note is not a string")
    if not verdict["correct"] and not note.strip():
        raise ValueError("An incorrect verdict requires an explanation")
    return {"correct": verdict["correct"], "missing": list(verdict["missing"]),
            "unsupported": list(verdict["unsupported"]), "note": note}


def cache_key(case: dict, run: dict, answer: str, model: str) -> str:
    """Everything a verdict depends on: the rubric, the judge, the gold and the evidence."""
    payload = {
        "rubric": PROMPT_VERSION,
        "model": model,
        "endpoint": _base_url(),
        "case": {"id": case.get("id"), "question": case.get("question"), "facts": case.get("facts")},
        "evidence": evidence_text(run),
        "answer": answer,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def ask(prompt: str, model: str, timeout: int = 180) -> dict[str, Any]:
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": 500,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    request = urllib.request.Request(
        f"{_base_url()}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as handle:
        payload = json.loads(handle.read())
    if not isinstance(payload, dict) or not isinstance(payload.get("choices"), list) or not payload["choices"]:
        raise ValueError("Judge response has no completion")
    choice = payload["choices"][0]
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        raise ValueError("Judge response did not finish normally")
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Judge response is empty")
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("judge did not answer with JSON")
    return {**validate(json.loads(content[start:end + 1])), "response_model": payload.get("model")}


# ----------------------------------------------------------------------------- driver

def manifest_sha256(cases: list[dict]) -> str:
    """Use the same canonical JSON identity as provenance.digest_json."""
    return hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def saved_manifest(run_data: dict) -> list[dict]:
    """The verified question version that produced this run."""
    manifest = run_data.get("case_manifest") or []
    if not manifest:
        raise SystemExit("qa.json carries no case_manifest; re-run the benchmark or pass --cases-file")
    if manifest_sha256(manifest) != run_data.get("cases_sha256"):
        raise ValueError("Saved case manifest failed its own hash check")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", type=Path, help="a results directory holding qa.json")
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--cases-file", type=Path, default=None,
                    help="override the frozen manifest (recorded as a new evaluation version)")
    ap.add_argument("--limit", type=int, default=0, help="judge the first N selected cases (0 = all)")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    args = ap.parse_args()

    if args.limit < 0:
        ap.error("--limit must be nonnegative")
    run_data = json.loads((args.results / "qa.json").read_text())
    if args.cases_file:
        manifest = [json.loads(line) for line in args.cases_file.read_text().splitlines() if line.strip()]
        source = {"kind": "explicit_override", "file": args.cases_file.name,
                  "original_cases_sha256": run_data.get("cases_sha256")}
    else:
        manifest = saved_manifest(run_data)
        source = {"kind": "frozen_manifest"}
    cases = {c["id"]: c for c in manifest}
    if len(cases) != len(manifest):
        raise ValueError("Duplicate case ids in the selected manifest")
    selected = run_data.get("selected_ids") or [r.get("id") for r in run_data.get("runs") or []]
    if len(selected) != len(set(selected)):
        raise ValueError("Duplicate selected case ids")
    if args.limit:
        selected = selected[:args.limit]
    runs = {r["id"]: r for r in run_data.get("runs") or []}
    if len(runs) != len(run_data.get("runs") or []):
        raise ValueError("Duplicate attempted case ids")
    cache_path = args.results / "judge.json"
    cache = {} if args.force or not cache_path.exists() else json.loads(cache_path.read_text())
    counts = {"selected": len(selected), "attempted": 0, "text_present": 0, "answered": 0,
              "judged": 0, "unknown_case": 0, "evaluator_failure": 0, "correct": 0,
              "run_failure": 0, "missing_run": 0}
    outcomes = {}
    for case_id in selected:
        run = runs.get(case_id)
        if run is None:
            counts["missing_run"] += 1
            outcomes[case_id] = {"status": "missing_run"}
            continue
        counts["attempted"] += 1
        answer = answer_text(run).strip()
        counts["text_present"] += bool(answer)
        finished = run.get("finish_reason") == "stop" and not run.get("timeout") and not run.get("error")
        if not finished or not answer:
            counts["run_failure"] += 1
            outcomes[case_id] = {"status": "run_failure", "finish_reason": run.get("finish_reason")}
            continue
        counts["answered"] += 1
        case = cases.get(case_id)
        if case is None:
            counts["unknown_case"] += 1
            outcomes[case_id] = {"status": "unknown_case"}
            continue
        try:
            key = cache_key(case, run, answer, args.model)
            cached = cache.get(case_id)
            if cached and cached.get("key") == key and not args.force:
                verdict = validate(cached)
                response_model = cached.get("response_model")
            else:
                prompt = PROMPT.format(question=case.get("question", ""), facts=facts_text(case),
                                       evidence=evidence_text(run), answer=answer)
                reply = ask(prompt, args.model)
                response_model = reply.get("response_model")
                verdict = validate(reply)
        except (urllib.error.URLError, ValueError, TimeoutError, TypeError) as error:
            counts["evaluator_failure"] += 1
            outcomes[case_id] = {"status": "evaluator_failure", "error_type": type(error).__name__}
            print(f"  {case_id}: evaluator failure ({type(error).__name__})", file=sys.stderr)
            continue
        verdict.update({"key": key, "model": args.model, "response_model": response_model,
                        "endpoint": _base_url(), "rubric": PROMPT_VERSION,
                        "cases_sha256": manifest_sha256(manifest), "cases_source": source})
        cache[case_id] = verdict
        counts["judged"] += 1
        counts["correct"] += verdict["correct"]
        outcomes[case_id] = {"status": "judged", "correct": verdict["correct"]}
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    metadata = {"rubric": PROMPT_VERSION, "model": args.model, "endpoint": _base_url(),
                "cases_sha256": manifest_sha256(manifest), "cases_source": source,
                "selected_ids": selected, "counts": counts, "outcomes": outcomes}
    (args.results / "judge-meta.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(counts, ensure_ascii=False))
    print(f"judged {counts['judged']}/{counts['selected']} selected with {args.model}: "
          f"{counts['correct']} correct, {counts['evaluator_failure']} evaluator failures")
    for case_id in selected:
        if outcomes.get(case_id, {}).get("status") == "judged" and not cache[case_id]["correct"]:
            print(f"  {case_id}: {cache[case_id].get('note') or ''} missing={cache[case_id].get('missing')}")
    return int(any(counts[k] for k in ("evaluator_failure", "unknown_case", "run_failure", "missing_run")))


if __name__ == "__main__":
    raise SystemExit(main())
