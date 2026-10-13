"""Does llama.cpp render a conversation the way training renders it?

Every call the agent makes to a local model is rendered by llama-server from
the chat template; training renders the same messages with Hugging Face's
jinja. Where the two strings differ, the model is trained on one prompt and
served another. This renders fixed conversations both ways and compares them
byte for byte, exiting 1 on the first difference.

    python benchmarks/local_models/template_check.py --server http://127.0.0.1:8080 --model minicpm5-2b
    python benchmarks/local_models/template_check.py --server ... --hf-template <ckpt>/chat_template.jinja

Needs `transformers` (the training environment has it). `--hf-template` is the
template training renders with; it defaults to docker/minicpm5-2b.jinja, the
one the preset serves. Found 2026-10-12 on b11429: the GGUF's own template
renders a list argument as `blood pressure血压` on llama.cpp and as
`['blood pressure', '血压']` on Hugging Face, which is also how the model writes it.

`--swift <model dir>` also encodes each case, with a final answer appended, as
ms-swift trains it (`minicpm5_2b`) and compares the decoded text with the
Hugging Face render. ms-swift's MiniCPM5 agent template wrote non-string
arguments with `json.dumps` (`["blood pressure", "血压"]`, `true`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = HERE.parent.parent / "docker" / "minicpm5-2b.jinja"

TOOLS = [{"type": "function", "function": {
    "name": "query_health_indicators",
    "description": "Readings from the person's record.",
    "parameters": {"type": "object", "properties": {
        "keywords": {"type": "array", "items": {"type": "string"}},
        "view": {"type": "string", "enum": ["raw", "latest", "day", "stats"]},
        "limit": {"type": "integer"}, "include_ranges": {"type": "boolean"}},
        "required": ["keywords"]}}}]

ARGS = {"keywords": ["blood pressure", "血压"], "view": "latest", "limit": 5, "include_ranges": True,
        "ratio": 0.5, "unset": None, "window": {"unit": "month", "n": 2}, "quoted": ["it's", "a\\b"]}
CODE = 'const xs = [64.1, 63.8];\nconst ok = xs[0] < 70 && xs[1] < 70;\n"mean " + (xs[0] + xs[1]) / 2'
TABLE = "| rid | date | value |\n| r1 | 2026-09-01 | 118/76 mmHg |"


def _call(cid: str, name: str, args: dict) -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _assistant(reasoning: str, content: str = "", calls: list | None = None) -> dict:
    message = {"role": "assistant", "content": content, "reasoning_content": reasoning}
    if calls:
        message["tool_calls"] = calls
    return message


CASES = {
    "question": ([{"role": "system", "content": "sys"}, {"role": "user", "content": "我的血压最近怎么样？"}], True),
    "one tool round": ([
        {"role": "system", "content": "sys"}, {"role": "user", "content": "我的血压最近怎么样？"},
        _assistant("先查血压。", calls=[_call("c1", "query_health_indicators", ARGS)]),
        {"role": "tool", "tool_call_id": "c1", "content": TABLE}], True),
    "two rounds, code with < and newlines": ([
        {"role": "system", "content": "sys"}, {"role": "user", "content": "What was my mean weight?"},
        _assistant("Fetch first.", calls=[_call("c1", "query_health_indicators", {"keywords": ["weight"]})]),
        {"role": "tool", "tool_call_id": "c1", "content": TABLE},
        _assistant("Now the mean.", calls=[_call("c2", "eval", {"code": CODE})]),
        {"role": "tool", "tool_call_id": "c2", "content": "<result>mean 63.95</result>"}], True),
    "earlier turn, its reasoning dropped": ([
        {"role": "system", "content": "sys"}, {"role": "user", "content": "LDL?"},
        _assistant("Look it up.", calls=[_call("c1", "query_health_indicators", {"keywords": ["LDL"], "view": "latest"})]),
        {"role": "tool", "tool_call_id": "c1", "content": TABLE},
        _assistant("Answer.", content="<statement>LDL 2.9 mmol/L<cite>[r1]</cite></statement>"),
        {"role": "user", "content": "那 HDL 呢？"}], True),
    "thinking off": ([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}], False),
}


def _for_hf(messages: list[dict]) -> list[dict]:
    """Hugging Face templates take arguments as a mapping, not a JSON string."""
    out = []
    for message in messages:
        message = dict(message)
        if message.get("tool_calls"):
            message["tool_calls"] = [{**c, "function": {**c["function"], "arguments": json.loads(c["function"]["arguments"])}}
                                     for c in message["tool_calls"]]
        out.append(message)
    return out


def _llama(server: str, model: str, messages: list[dict], thinking: bool) -> str:
    body = {"messages": messages, "tools": TOOLS, "chat_template_kwargs": {"enable_thinking": thinking}}
    if model:
        body["model"] = model
    r = requests.post(f"{server}/apply-template", json=body, timeout=120)
    r.raise_for_status()
    return r.json()["prompt"]


def _hf(template: str, bos: str, messages: list[dict], thinking: bool) -> str:
    from transformers.utils.chat_template_utils import render_jinja_template
    rendered, _ = render_jinja_template(conversations=[_for_hf(messages)], tools=TOOLS, chat_template=template,
                                        add_generation_prompt=True, enable_thinking=thinking, bos_token=bos)
    return rendered[0]


ANSWER = {"role": "assistant", "content": "", "reasoning_content": "Done.",
          "final": "<statement>118/76 mmHg<cite>[r1]</cite></statement>"}


def _for_swift(messages: list[dict]) -> list[dict]:
    """ms-swift's agent format: an assistant turn, then one `tool_call` message
    per call, and `tool_response` for a result. Thinking is written into the
    content, which the template splits back out."""
    out = []
    for m in messages:
        if m["role"] == "assistant":
            think = f"<think>\n{m['reasoning_content']}\n</think>\n\n" if m.get("reasoning_content") else ""
            calls = m.get("tool_calls") or []
            body = m.get("content", "")
            out.append({"role": "assistant", "content": think + body + ("\n" if calls and body else "")})
            out += [{"role": "tool_call", "content": json.dumps(
                {"name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"])},
                ensure_ascii=False)} for c in calls]
        elif m["role"] == "tool":
            out.append({"role": "tool_response", "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def _swift_cases(model_dir: str, template_text: str, bos: str) -> int:
    """Encode each case as a training sample with ms-swift and compare it with
    the Hugging Face render of the same messages."""
    from swift import get_processor
    from swift.template import get_template
    from transformers import AutoTokenizer
    from transformers.utils.chat_template_utils import render_jinja_template

    tok = AutoTokenizer.from_pretrained(model_dir, use_fast=True)
    template = get_template(get_processor(model_dir), template_type="minicpm5_2b")
    template.set_mode("train")
    failed = 0
    for name, (messages, thinking) in CASES.items():
        if not thinking:
            continue
        answer = {"role": "assistant", "content": ANSWER["final"], "reasoning_content": ANSWER["reasoning_content"]}
        whole = [*messages, answer]
        trained = render_jinja_template(conversations=[_for_hf(whole)], tools=TOOLS, chat_template=template_text,
                                        add_generation_prompt=False, bos_token=bos)[0][0]
        swift_whole = [*_for_swift(messages), {"role": "assistant",
                                               "content": f"<think>\n{ANSWER['reasoning_content']}\n</think>\n\n{ANSWER['final']}"}]
        encoded = template.encode({"messages": swift_whole, "tools": json.dumps(TOOLS, ensure_ascii=False)},
                                  return_template_inputs=False)
        swift_text = tok.decode(encoded["input_ids"], skip_special_tokens=False)
        if swift_text == trained:
            print(f"same   swift: {name}")
            continue
        failed += 1
        i = _first_difference(swift_text, trained)
        print(f"DIFFER swift: {name} at char {i}\n  ms-swift:     {swift_text[max(0, i - 60):i + 60]!r}"
              f"\n  transformers: {trained[max(0, i - 60):i + 60]!r}")
    return failed


def _first_difference(a: str, b: str) -> int:
    return next((i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--server", default="http://127.0.0.1:8080", help="empty to check only ms-swift (--swift)")
    ap.add_argument("--model", default="", help="the router's section name; empty for a single-model server")
    ap.add_argument("--hf-template", default=str(DEFAULT_TEMPLATE))
    ap.add_argument("--bos", default="<s>")
    ap.add_argument("--dump", default="", help="write both renders of every case to this JSON file")
    ap.add_argument("--swift", default="", help="a model dir: also compare ms-swift's training encode")
    args = ap.parse_args()
    template = Path(args.hf_template).read_text(encoding="utf-8")
    if args.swift and not args.server:
        failed = _swift_cases(args.swift, template, args.bos)
        print(f"ms-swift: {failed} case(s) differ")
        return 1 if failed else 0

    failed, dump = 0, {}
    for name, (messages, thinking) in CASES.items():
        served = _llama(args.server, args.model, messages, thinking)
        trained = _hf(template, args.bos, messages, thinking)
        dump[name] = {"llama.cpp": served, "transformers": trained}
        if served == trained:
            print(f"same   {name}")
            continue
        failed += 1
        i = _first_difference(served, trained)
        print(f"DIFFER {name} at char {i}\n  llama.cpp:    {served[max(0, i - 60):i + 60]!r}"
              f"\n  transformers: {trained[max(0, i - 60):i + 60]!r}")
    if args.dump:
        Path(args.dump).write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(CASES) - failed} of {len(CASES)} cases render the same")
    if args.swift:
        swift_failed = _swift_cases(args.swift, template, args.bos)
        print(f"ms-swift: {swift_failed} case(s) differ")
        failed += swift_failed
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
