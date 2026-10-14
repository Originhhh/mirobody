"""Builds the E2 speed summary from speed_windows.out + the per-turn JSON files.

Reports, per config: generation rate, prompt-processing rate, time to first token
(measured off the stream and derived from the server's own timings), draft acceptance
for the DSpark configs, memory, and a comparison against README section 9.
"""
import json
import math
import os
import re
import statistics
from pathlib import Path

KIT = Path(os.environ.get("SPEED_DIR", Path(__file__).resolve().parent))
OUT = KIT / "speed_windows.out"

CONFIGS = [
    ("vulkan-base",     "Vulkan", "32k / 16-bit KV"),
    ("vulkan-64k-q8",   "Vulkan", "64k / 8-bit KV / FA"),
    ("vulkan-dspark7",  "Vulkan", "32k + DSpark draft"),
    ("cuda-base",       "CUDA",   "32k / 16-bit KV"),
    ("cuda-64k-q8",     "CUDA",   "64k / 8-bit KV / FA"),
    ("cuda-dspark7",    "CUDA",   "32k + DSpark draft"),
    ("cpu4-base",       "CPU x4", "32k / 16-bit KV"),
    ("cpu4-64k-q8",     "CPU x4", "64k / 8-bit KV / FA"),
    ("cpu4-dspark7",    "CPU x4", "32k + DSpark draft"),
]

REFERENCE = [
    ("Mac Metal 48 GB, 1.5.5",        60.5, 1020, None, None),
    ("A800 Vulkan, df5f62a",           166, 3000,  141, 3000),
    ("Xeon 4-core (Docker), 1.5.5",    6.7, 48.5,  None, None),
    ("Xeon 4-core (Docker), df5f62a",  None, None,  5.1, 12.7),
]


def read_log():
    mem, load, order = {}, {}, []
    if not OUT.exists():
        return mem, load, order
    for line in OUT.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"== (\S+)\s+cpu_load (\d+(?:\.\d+)?)%", line)
        if m:
            load[m.group(1)] = float(m.group(2))
            order.append(m.group(1))
            continue
        m = re.search(r"^\s+(\S+) working_set_mb (\d+) private_mb (\d+) vram_mb (\S+)", line)
        if m:
            mem[m.group(1)] = {"working_set_mb": int(m.group(2)),
                               "private_mb": int(m.group(3)), "vram_mb": m.group(4)}
    return mem, load, order


def load_turns(label):
    for pat in (f"{label}.out.json", f"speed_cases.{label}.out.json", f"speed_cases_cpu.{label}.out.json"):
        f = KIT / pat
        if f.exists():
            turns = json.loads(f.read_text(encoding="utf-8-sig"))
            if not isinstance(turns, dict):
                raise ValueError("turn results must be a mapping")
            return turns
    return None


def expected_cases(label):
    filename = "speed_cases_cpu.json" if label.startswith("cpu") else "speed_cases.json"
    source = KIT / filename
    if not source.exists():
        source = Path(__file__).resolve().parents[2] / filename
    cases = json.loads(source.read_text(encoding="utf-8-sig"))["cases"]
    if not isinstance(cases, dict) or not cases:
        raise ValueError("expected cases must be a nonempty mapping")
    return set(cases)


def valid_turn(turn):
    if not isinstance(turn, dict) or turn.get("status", "ok") != "ok" or "error" in turn:
        return False
    for key in ("gen_tps", "pp_tps", "n", "wall"):
        value = turn.get(key)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0 or (key in {"gen_tps", "pp_tps"} and value == 0)):
            return False
    for key in ("ttft_s", "ttft_derived_s", "draft", "accepted"):
        value = turn.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value < 0):
            return False
    return True


def collect():
    mem, load, _ = read_log()
    rows = []
    for label, backend, setting in CONFIGS:
        try:
            turns = load_turns(label)
            expected = expected_cases(label)
        except (OSError, ValueError, KeyError, TypeError):
            rows.append({"label": label, "backend": backend, "setting": setting, "status": "failed"})
            continue
        if turns is None:
            rows.append({"label": label, "backend": backend, "setting": setting, "status": "not run"})
            continue
        ok = {k: v for k, v in turns.items() if k in expected and valid_turn(v)}
        if not ok:
            rows.append({"label": label, "backend": backend, "setting": setting,
                         "status": "failed", "cases": len(turns)})
            continue
        gen = [t["gen_tps"] for t in ok.values()]
        pp = [t["pp_tps"] for t in ok.values()]
        ttft = [t["ttft_s"] for t in ok.values() if t.get("ttft_s") is not None]
        ttft_d = [t["ttft_derived_s"] for t in ok.values() if t.get("ttft_derived_s") is not None]
        drafts = [t.get("draft") or 0 for t in ok.values()]
        accs = [t.get("accepted") or 0 for t in ok.values()]
        acc_rate = round(100.0 * sum(accs) / sum(drafts), 1) if sum(drafts) else None
        rows.append({
            "label": label, "backend": backend, "setting": setting,
            "status": "ok" if set(ok) == set(turns) == expected else "partial",
            "cases": len(ok), "tokens": sum(t.get("n") or 0 for t in ok.values()),
            "expected_cases": len(expected), "failed_cases": len(set(turns) - set(ok)),
            "missing_cases": len(expected - set(turns)),
            "gen_median": round(statistics.median(gen), 1),
            "gen_min": min(gen), "gen_max": max(gen),
            "pp_median": round(statistics.median(pp), 1),
            "ttft_median": round(statistics.median(ttft), 2) if ttft else None,
            "ttft_min": min(ttft) if ttft else None,
            "ttft_max": max(ttft) if ttft else None,
            "ttft_derived_median": round(statistics.median(ttft_d), 2) if ttft_d else None,
            "accept_rate": acc_rate,
            "wall_total": round(sum(t.get("wall") or 0 for t in ok.values()), 1),
            "cpu_load": load.get(label),
            **mem.get(label, {}),
        })
    return rows


def table(rows):
    hdr = (f"{'config':<17}{'backend':<8}{'gen tok/s':>10}{'pp tok/s':>10}"
           f"{'TTFT s':>8}{'TTFT med':>9}{'acc %':>7}{'tokens':>8}{'wall s':>8}"
           f"{'WS MB':>7}{'VRAM MB':>9}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        if r["status"] != "ok":
            print(f"{r['label']:<17}{r['backend']:<8}  ({r['status']})")
            continue
        print(f"{r['label']:<17}{r['backend']:<8}{r['gen_median']:>10}{r['pp_median']:>10}"
              f"{str(r['ttft_min']):>8}{str(r['ttft_median']):>9}"
              f"{str(r['accept_rate'] if r['accept_rate'] is not None else '-'):>7}"
              f"{r['tokens']:>8}{r['wall_total']:>8}"
              f"{str(r.get('working_set_mb', '-')):>7}{str(r.get('vram_mb', '-')):>9}")
    print()
    print("TTFT s column is the fastest turn; TTFT med is the median over the turns.")
    print("VRAM MB is the delta measured around the server process (see README 6.2).")


if __name__ == "__main__":
    rows = collect()
    table(rows)
    print()
    print("=== README section 9 reference numbers (same candidate version) ===")
    for name, b, pb, q, pq in REFERENCE:
        print(f"  {name:<32} base gen {str(b):>6} / pp {str(pb):>6}   64k-q8 gen {str(q):>5} / pp {str(pq):>6}")
    print()
    print("=== E2 summary JSON ===")
    print(json.dumps(rows, indent=2))
