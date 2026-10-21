"""Client-observed SSE latency; status events are not generated tokens."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass
class StreamTiming:
    first_token_s: float | None = None
    first_reasoning_s: float | None = None
    first_tool_s: float | None = None
    first_text_s: float | None = None

    def observe(self, block: dict, elapsed: float) -> None:
        kind = block.get("type")
        if kind == "tool_call" and self.first_tool_s is None:
            self.first_tool_s = elapsed
        field = "reasoning" if kind == "reasoning" else "text"
        content = block.get(field)
        if kind not in ("reasoning", "text") or not isinstance(content, str) or not content.strip():
            return
        # The frontend receives reasoning in its thinking area; that is visible
        # progress even when the final answer comes after several tool rounds.
        if self.first_token_s is None:
            self.first_token_s = elapsed
        attr = "first_reasoning_s" if kind == "reasoning" else "first_text_s"
        if getattr(self, attr) is None:
            setattr(self, attr, elapsed)

    def as_dict(self) -> dict[str, float | None]:
        return {name: round(value, 3) if value is not None else None for name, value in vars(self).items()}


def distribution(values: list[float | None]) -> dict:
    observed = sorted(v for v in values if isinstance(v, (int, float)) and isfinite(v) and v >= 0)

    def quantile(q: float) -> float | None:
        if not observed:
            return None
        offset = (len(observed) - 1) * q
        lower = int(offset)
        upper = min(lower + 1, len(observed) - 1)
        return round(observed[lower] + (observed[upper] - observed[lower]) * (offset - lower), 3)

    return {"n": len(observed), "missing": len(values) - len(observed), "p50": quantile(.5), "p90": quantile(.9)}


def summarize_timings(runs: list[dict]) -> dict:
    return {name: distribution([r.get(name) for r in runs]) for name in
            ("first_token_s", "first_reasoning_s", "first_tool_s", "first_text_s", "seconds")}
