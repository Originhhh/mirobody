"""Generate extra harness100-style cases for the two dimensions the suite barely covers.

The maintained 100 questions ask for a chart on 2 of them and are 60% English, so the
`chart` and `language` checks in `score.py` are nearly free for a model: a reader of the
report cannot tell whether the model would draw a chart at all, or hold a language on a
question it has not seen. This script builds extra cases from the same `mirobody-gen`
build and `plan.json` as `cases.py`, in the shape of `cases.jsonl`, with every answer
computed from the build (never from product output):

  device-latest   newest value and its date                (number + date)
  device-sum      total over a 30-day window (steps only)  (number)
  device-count    how many days carry a reading            (number)
  chart           a week of daily values plus a vis-chart  (7 numbers + chart)
  journal-count   how many journal entries name a symptom  (number)
  absence         a metric the person never recorded       (words: say it is absent)

Device, chart and absence cases are emitted in the frontend's four locales (zh-CN,
zh-TW, ja, en), so answering in the wrong language fails instead of slipping by. A
journal case is asked in its journal's own language: the symptom is named in the
person's words, and a translated name would be a word their journal never used.

Questions are in the first person: the benchmark account is the person, and a corpus id
such as `p001` is a name neither they nor the product has seen.

usage:
  python benchmarks/local_models/expand_cases.py --corpus <build> --out extra-cases.jsonl

The file is deliberately separate from `cases.jsonl`: the maintained suite keeps its
provenance and its baseline, and these extras are layered on top.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cases import NO_DATA, day_fact, device_series, doc_rows, half_step, number, words
from common import HERE, Corpus

AS_OF = "2026-09-02T00:00:00+00:00"
EXTRA_SUITE = "harness-extra-20261023"
LANGS = ("zh-CN", "zh-TW", "ja", "en")
METRICS = {
    "steps": {"additive": True, "labels": {"zh-CN": "步数", "zh-TW": "步數", "ja": "歩数", "en": "steps"}},
    "rhr": {"additive": False,
            "labels": {"zh-CN": "静息心率", "zh-TW": "靜止心率", "ja": "安静時心拍数", "en": "resting heart rate"}},
    "weight": {"additive": False, "labels": {"zh-CN": "体重", "zh-TW": "體重", "ja": "体重", "en": "weight"}},
}
#: The journal's language as the frontend locale a question is asked in.
JOURNAL_LOCALE = {"zh": "zh-CN", "en": "en"}
ABSENT = NO_DATA + ["沒有", "無記錄", "未記錄", "記録がありません", "記録はありません", "見つかりません"]

TEXT = {
    "latest": {
        "zh-CN": "请查询我 {start} 至 {end} 的{label}。最新一条的数值和日期是什么？",
        "zh-TW": "請查詢我 {start} 至 {end} 的{label}。最新一筆的數值和日期是什麼？",
        "ja": "{start} から {end} までの私の{label}を調べてください。最新の値と日付は何ですか。",
        "en": "Check my {label} for {start} through {end}. What are the most recent value and its date?",
    },
    "sum": {
        "zh-CN": "请查询我 {start} 至 {end} 的{label}。所有已记录数值的总和是多少？",
        "zh-TW": "請查詢我 {start} 至 {end} 的{label}。所有已記錄數值的總和是多少？",
        "ja": "{start} から {end} までの私の{label}について、記録されたすべての値の合計はいくつですか。",
        "en": "Check my {label} for {start} through {end}. What is the total of every recorded value?",
    },
    "count": {
        "zh-CN": "请查询我 {start} 至 {end} 的{label}。一共有多少天有记录？",
        "zh-TW": "請查詢我 {start} 至 {end} 的{label}。一共有多少天有記錄？",
        "ja": "{start} から {end} までの私の{label}について、記録がある日は何日ありますか。",
        "en": "Check my {label} for {start} through {end}. On how many days is there a record?",
    },
    "chart": {
        "zh-CN": "请查询我 {start} 至 {end} 的{label}。请把每天的值列出来，并画一张折线图展示这几天的变化。",
        "zh-TW": "請查詢我 {start} 至 {end} 的{label}。請把每天的值列出來，並畫一張折線圖展示這幾天的變化。",
        "ja": "{start} から {end} までの私の{label}を調べてください。毎日の値を一覧にし、この数日の変化を折れ線グラフで示してください。",
        "en": "Check my {label} for {start} through {end}. List each day's value and draw a line chart of the change.",
    },
    "journal": {
        "zh-CN": "我的日记里一共有多少条记录提到“{symptom}”？按已记录条目计数，不推断持续天数。",
        "en": "How many of my journal entries mention {symptom}? Count recorded entries, not days with symptoms.",
    },
    "absence": {
        "zh-CN": "请查询我的{label}记录。如果从来没有记录，请直接说明没有这项记录。",
        "zh-TW": "請查詢我的{label}記錄。如果從來沒有記錄，請直接說明沒有這項記錄。",
        "ja": "私の{label}の記録を調べてください。記録がまったくない場合は、そのまま無いと伝えてください。",
        "en": "Check my records for {label}. If there is no record at all, say so plainly.",
    },
}


def daily(corpus: Corpus, person: str, metric: str, tz: str) -> dict[str, float] | None:
    """One value per local day in the account's time zone, as the product's day view
    files it. A record's own offset is not the account's: p006's readings carry +08:00
    in a UTC account, and the date in the timestamp put 839 of them on the wrong day.
    None when a day holds two readings, where a mean and a last value differ."""
    days: dict[str, float] = {}
    for when, value in device_series(corpus, person, metric, tz):
        day = when.date().isoformat()
        if day in days:
            return None
        days[day] = value
    return days


def window(days: dict[str, float], length: int, blocked: set[str]) -> tuple[str, str, list[tuple[str, float]]] | None:
    """The earliest run of `length` consecutive recorded days with no loaded document
    reading of the metric on any of them (the product would return that reading too)."""
    ordered = sorted(days)
    for i in range(len(ordered) - length + 1):
        chunk = ordered[i:i + length]
        if (date.fromisoformat(chunk[-1]) - date.fromisoformat(chunk[0])).days == length - 1 \
                and not blocked & set(chunk):
            return chunk[0], chunk[-1], [(d, days[d]) for d in chunk]
    return None


def evidence(person: str, metric: str, start: str, end: str, records: int, tz: str) -> list[dict]:
    return [{"file": "devices.jsonl", "person_id": person, "metric": metric, "start": start, "end": end,
             "tz": tz, "records": records, "day_boundary": "00:00", "value_policy": "reported_numeric_value"}]


def case(cid: str, person: str, lang: str, domain: str, question: str, facts: list[dict], refs: list[dict],
         as_of: str, *, chart: bool = False, tags: tuple[str, ...] = ()) -> dict:
    return {"id": cid, "suite_id": EXTRA_SUITE, "synthetic": True, "person": person, "lang": lang, "domain": domain,
            "question": question, "facts": facts, "evidence": refs, "sources": sorted({r["file"] for r in refs}),
            "expect_tools": ["query_health_indicators"], "chart": chart, "tags": list(tags), "as_of": as_of,
            "forbidden": [], "right": "; ".join(f"{f['label']}={f.get('value', f.get('any'))}" for f in facts)}


def journal_symptom(entries: list[dict]) -> tuple[str, int] | None:
    """The most frequent symptom whose entries all use one name, so "entries that
    mention X" and "entries coded as X" are the same count; a symptom written two ways
    ("no energy", "tired") would make the question ambiguous."""
    names: dict[str, set[str]] = defaultdict(set)
    count: dict[str, int] = defaultdict(int)
    for entry in entries:
        ids = set()
        for e in entry["expected"]:
            if e["kind"] == "symptom":
                names[e["symptom_id"]].add(e["name"])
                ids.add(e["symptom_id"])
        for sid in ids:
            count[sid] += 1
    single = sorted((sid for sid in count if len(names[sid]) == 1), key=lambda s: (-count[s], s))
    return (next(iter(names[single[0]])), count[single[0]]) if single else None


def build(corpus: Corpus, plan: dict, people: int, as_of: str) -> list[dict]:
    out: list[dict] = []
    for person in sorted(plan["qa_people"])[:people]:
        config = plan["qa_people"][person]
        tz = config["tz"]
        loaded = [r for f in config["documents"] for r in doc_rows(corpus, f)]
        asked_absence = False
        for metric, spec in METRICS.items():
            recorded = any(r["metric"] == metric for r in corpus.devices[person]["records"])
            in_documents = any(r["key"] == metric for r in loaded)
            if not recorded:
                # One absence per person; weight is on almost every printed report.
                if metric != "weight" and not in_documents and not asked_absence:
                    asked_absence = True
                    refs = [{"file": "devices.jsonl", "person_id": person, "metric": metric,
                             "assertion": "no device or loaded-document reading of this metric"}]
                    for lang in LANGS:
                        out.append(case(f"x-absence-{metric}-{person}-{lang}", person, lang, "absence",
                                        TEXT["absence"][lang].format(label=spec["labels"][lang]),
                                        [words(ABSENT, "states absence")], refs, as_of, tags=("absence", metric)))
                continue
            days = daily(corpus, person, metric, tz)
            if days is None:
                continue
            blocked = {r["observed"][:10] for r in loaded if r["key"] == metric}
            month = window(days, 30, blocked)
            if month:
                start, end, points = month
                latest_day, latest_value = points[-1]
                refs = evidence(person, metric, start, end, len(points), tz)
                for lang in LANGS:
                    fill = {"start": start, "end": end, "label": spec["labels"][lang]}
                    out.append(case(f"x-device-{metric}-latest-{person}-{lang}", person, lang, "device",
                                    TEXT["latest"][lang].format(**fill),
                                    [number(latest_value, "latest", tol=half_step(metric), day=latest_day),
                                     day_fact(latest_day, "latest date")], refs, as_of, tags=(metric, "latest")))
                    if spec["additive"]:
                        out.append(case(f"x-device-{metric}-sum-{person}-{lang}", person, lang, "device",
                                        TEXT["sum"][lang].format(**fill),
                                        [number(sum(v for _, v in points), "total", tol=half_step(metric))],
                                        refs, as_of, tags=(metric, "sum")))
                    out.append(case(f"x-device-{metric}-count-{person}-{lang}", person, lang, "device",
                                    TEXT["count"][lang].format(**fill), [number(len(points), "days", tol=0)],
                                    refs, as_of, tags=(metric, "count")))
            week = window(days, 7, blocked)
            if week:
                start, end, points = week
                refs = evidence(person, metric, start, end, len(points), tz)
                for lang in LANGS:
                    out.append(case(f"x-chart-{metric}-{person}-{lang}", person, lang, "device",
                                    TEXT["chart"][lang].format(start=start, end=end, label=spec["labels"][lang]),
                                    [number(v, d, tol=half_step(metric), day=d) for d, v in points], refs, as_of,
                                    chart=True, tags=(metric, "chart")))
        entries = corpus.journal_of(person)
        picked = journal_symptom(entries)
        locale = JOURNAL_LOCALE.get(entries[0]["lang"]) if entries else None
        if picked and locale:
            name, count = picked
            refs = [{"file": "journal.jsonl", "person_id": person, "symptom": name, "source_kind": "self_reported"}]
            out.append(case(f"x-journal-{person}-{locale}", person, locale, "journal",
                            TEXT["journal"][locale].format(symptom=name),
                            [number(count, "entries", tol=0)], refs, as_of, tags=("journal", "count")))
    for c in out:
        if re.search(r"\bp\d{3}\b", c["question"]):
            raise ValueError(f"{c['id']} names a corpus person id")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True, type=Path, help="the mirobody-gen build cases.py was built from")
    ap.add_argument("--plan", type=Path, default=HERE / "plan.json",
                    help="the plan whose accounts were loaded: time zones and loaded documents")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--people", type=int, default=8, help="how many people to draw cases from")
    ap.add_argument("--as-of", default=AS_OF, help="evaluation clock recorded on every case")
    args = ap.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    cases = build(Corpus(args.corpus), plan, args.people, args.as_of)
    with args.out.open("w", encoding="utf-8") as fh:
        for c in cases:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    langs = sorted({c["lang"] for c in cases})
    domains = sorted({c["domain"] for c in cases})
    charts = sum(1 for c in cases if c["chart"])
    print(f"wrote {args.out}: {len(cases)} cases (charts {charts}); langs={langs}; domains={domains}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
