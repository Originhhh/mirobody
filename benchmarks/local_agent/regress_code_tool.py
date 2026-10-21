"""Require successful Python computation on original data for a selected model.

The gate uses the production Python tool and common harness. Generated code
is replayed on doubled synthetic values so printing memorized answers cannot
pass. Optional JavaScript comparison is a benchmark only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from code_tool_probe import cases, replay_case, run_case
from mirobody.agent.models.clients import build_chat_model
from mirobody.utils.config import Config
from mirobody.utils.config.llm import chat_entries


async def run(args):
    Config.load_dotenv(".env")
    Config(["config.yaml"], read_only=["config.yaml"])
    entries = chat_entries()
    entry = {**entries[args.entry], "timeout": 120, "max_retries": 0}
    if args.entry == "local":
        entry["base_url"] = args.local_base
    client = build_chat_model(entry, alias=args.entry, thinking="medium")
    selected = [case for case in cases() if case["id"] in args.cases.split(",")]
    if not selected:
        raise ValueError("no benchmark case was selected")
    rows = []
    for arm in (["python", "js"] if args.compare else ["python"]):
        for case in selected:
            result = await run_case(client, case, arm)
            result = await replay_case(result, case)
            result["entry"] = args.entry
            rows.append(result)
            print(f"{arm} {case['id']}: {'PASS' if result['score']['passed'] else 'FAIL'} "
                  f"code={result['score']['code_calls']} errors={result['score']['code_errors']}", flush=True)
    if args.out:
        args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    python = [row for row in rows if row["arm"] == "python"]
    return bool(python) and all(row["score"]["passed"] and row["score"]["code_errors"] == 0 for row in python)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", default="local", help="a MODELS entry: local, deepseek, claude-sonnet or gpt")
    parser.add_argument("--local-base", default="http://127.0.0.1:8088/v1")
    parser.add_argument("--cases", default="bp_q2,steps_august")
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not asyncio.run(run(args)):
        print("REGRESSION: Python must query, calculate and pass changed-data replay without errors")
        sys.exit(1)


if __name__ == "__main__":
    main()
