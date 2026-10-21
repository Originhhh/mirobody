"""The chat agent's Python tool: arithmetic on rows it already queried.

The tool is chat-only. It is not in `mirobody/agent/tools/`, so the MCP
loader never publishes it to external clients: a caller that holds its own
readings has no use for a code runner, and the sandbox is for the chat
surface's own arithmetic.
"""

from __future__ import annotations

import asyncio
import logging

from langchain_core.tools import tool

from .python_sandbox import default_sandbox

logger = logging.getLogger(__name__)


def parse_table(table: str) -> list[dict]:
    """A `query_health_indicators` table as row dicts.

    The first pipe-separated line is the header; later lines with the same
    number of cells become rows. A markdown separator, a `notes:` line or the
    metadata line the query tool appends is skipped rather than parsed into a
    bogus row.
    """
    rows: list[dict] = []
    header: list[str] | None = None
    for raw in table.splitlines():
        line = raw.strip()
        if not line or "|" not in line:
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if header is None:
            header = cells
            continue
        if len(cells) != len(header):
            continue
        if all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append(dict(zip(header, cells, strict=True)))
    return rows


@tool
async def python_exec(code: str, table: str = "", timeout_s: int = 15) -> str:
    """Run Python for arithmetic on readings you already queried.

    Pass the exact table `query_health_indicators` returned as `table`; it is
    parsed into `data`, a list of dicts with the table's columns (`indicator`,
    `date`, `value`, `unit`, ...); values are strings, so cast with
    `float(row["value"])` before arithmetic. Use `print(...)` for the result. The Python
    standard library is available. Do not use this for anything but
    computation: it has no record access and no inherited environment; with
    `PYTHON_SANDBOX_IMAGE` it also has no network and a read-only root.
    """
    data = parse_table(table)
    seconds = max(1, min(int(timeout_s or 15), 30))
    result = await asyncio.to_thread(default_sandbox().run, code, data, seconds)
    if result.timed_out:
        logger.warning("python sandbox timed out after %ss", seconds)
    return result.output
