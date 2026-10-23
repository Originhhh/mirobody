"""Generate extra harness100-style cases for the two dimensions the suite barely covers.

`harness100-20261021` asks for a chart on 2 of its 100 cases and is 60% English, so the
`chart` and `language` checks in `score.py` are nearly free for a model: a reader of the
report cannot tell whether the model would draw a chart at all, or hold a language on a
question it has not seen. This script builds extra cases from a `mirobody-gen` build in
the same shape as `cases.jsonl`, with the answer computed from the build itself (never
from product output):

  device-latest   newest value and its date                (number + date)
  device-sum      total over a 30-day window               (number)
  device-count    how many days carry a reading            (number)
  chart           a week of daily values plus a vis-chart  (7 numbers + chart)
  journal-count   how many journal entries mention a symptom (number)
  absence         a metric the person never recorded       (words: say it is absent)

Every case is emitted in the frontend's four locales (zh-CN, zh-TW, ja, en), so
answering in the wrong language fails instead of slipping by.

usage:
  python benchmarks/local_models/expand_cases.py --corpus <build> --out extra-cases.jsonl
  python benchmarks/local_models/run.py --cases $(python -c "print(','.join(...))") ...

The file is deliberately separate from `cases.jsonl`: the maintained suite keeps its
provenance and its baseline, and these extras are layered on top.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

AS_OF = "2026-09-02T00:00:00+00:00"
EXTRA_SUITE = "harness-extra-20261023"
LANGS = ("zh-CN", "zh-TW", "ja", "en")
METRICS = {
    "steps": {"unit": "count", "additive": True,
              "labels": {"zh-CN": "步数", "zh-TW": "步數", "ja": "歩数", "en": "steps"}},
    "rhr": {"unit": "bpm", "additive": False,
            "labels": {"zh-CN": "静息心率", "zh-TW": "靜止心率", "ja": "安静時心拍数", "en": "resting heart rate"}},
    "weight": {"unit": "kg", "additive": False,
               "labels": {"zh-CN": "体重", "zh-TW": "體重", "ja": "体重", "en": "weight"}},
}

# Only symptoms with a translation are emitted in that locale. Falling back to the
# English name would mix languages in exactly the dimension this generator is for.
SYMPTOM_LABELS = {
    "headache": {"zh-CN": "头痛", "zh-TW": "頭痛", "ja": "頭痛"},
    "fatigue": {"zh-CN": "疲劳", "zh-TW": "疲勞", "ja": "疲労"},
    "nausea": {"zh-CN": "恶心", "zh-TW": "噁心", "ja": "吐き気"},
    "dizziness": {"zh-CN": "头晕", "zh-TW": "頭暈", "ja": "めまい"},
    "cough": {"zh-CN": "咳嗽", "zh-TW": "咳嗽", "ja": "咳"},
    "fever": {"zh-CN": "发热", "zh-TW": "發燒", "ja": "発熱"},
    "pain": {"zh-CN": "疼痛", "zh-TW": "疼痛", "ja": "痛み"},
    "insomnia": {"zh-CN": "失眠", "zh-TW": "失眠", "ja": "不眠"},
    "anxiety": {"zh-CN": "焦虑", "zh-TW": "焦慮", "ja": "不安"},
    "stress": {"zh-CN": "压力", "zh-TW": "壓力", "ja": "ストレス"},
    "sore throat": {"zh-CN": "喉咙痛", "zh-TW": "喉嚨痛", "ja": "喉の痛み"},
    "runny nose": {"zh-CN": "流鼻涕", "zh-TW": "流鼻水", "ja": "鼻水"},
    "stomach ache": {"zh-CN": "胃痛", "zh-TW": "胃痛", "ja": "腹痛"},
    "diarrhea": {"zh-CN": "腹泻", "zh-TW": "腹瀉", "ja": "下痢"},
    "constipation": {"zh-CN": "便秘", "zh-TW": "便秘", "ja": "便秘"},
    "back pain": {"zh-CN": "背痛", "zh-TW": "背痛", "ja": "背中の痛み"},
    "chest pain": {"zh-CN": "胸痛", "zh-TW": "胸痛", "ja": "胸の痛み"},
    "shortness of breath": {"zh-CN": "气短", "zh-TW": "呼吸急促", "ja": "息切れ"},
}


def label_for(metric: str, lang: str) -> str:
    return METRICS[metric]["labels"][lang]

TEXT = {
    "latest": {
        "zh-CN": "请查询 {person} 在 {start} 至 {end} 的{label}。最新一条的数值和日期是什么？",
        "zh-TW": "請查詢 {person} 在 {start} 至 {end} 的{label}。最新一筆的數值和日期是什麼？",
        "ja": "{person} の {start} から {end} までの{label}を調べてください。最新の値と日付は何ですか。",
        "en": "Check {person}'s {label} for {start} through {end}. What are the most recent value and its date?",
    },
    "sum": {
        "zh-CN": "请查询 {person} 在 {start} 至 {end} 的{label}。所有已记录数值的总和是多少？",
        "zh-TW": "請查詢 {person} 在 {start} 至 {end} 的{label}。所有已記錄數值的總和是多少？",
        "ja": "{person} の {start} から {end} までの{label}について、記録されたすべての値の合計はいくつですか。",
        "en": "Check {person}'s {label} for {start} through {end}. What is the total of every recorded value?",
    },
    "count": {
        "zh-CN": "请查询 {person} 在 {start} 至 {end} 的{label}。一共有多少天有记录？",
        "zh-TW": "請查詢 {person} 在 {start} 至 {end} 的{label}。一共有多少天有記錄？",
        "ja": "{person} の {start} から {end} までの{label}について、記録がある日は何日ありますか。",
        "en": "Check {person}'s {label} for {start} through {end}. On how many days is there a record?",
    },
    "chart": {
        "zh-CN": "请查询 {person} 在 {start} 至 {end} 的{label}。请把每天的值列出来，并画一张折线图展示这几天的变化。",
        "zh-TW": "請查詢 {person} 在 {start} 至 {end} 的{label}。請把每天的值列出來，並畫一張折線圖展示這幾天的變化。",
        "ja": "{person} の {start} から {end} までの{label}を調べてください。毎日の値を一覧にし、この数日の変化を折れ線グラフで示してください。",
        "en": "Check {person}'s {label} for {start} through {end}. List each day's value and draw a line chart of the change.",
    },
    "journal": {
        "zh-CN": "我的日记里一共记录过多少条{symptom}？按已记录条目计数，不推断持续天数。",
        "zh-TW": "我的日記裡一共記錄過多少條{symptom}？按已記錄條目計數，不推斷持續天數。",
        "ja": "私の日記には{symptom}の記録が何件ありますか。記録された件数で数えてください。",
        "en": "How many journal entries mention {symptom}? Count recorded entries, not days with symptoms.",
    },
    "absence": {
        "zh-CN": "请查询 {person} 的{label}记录。如果从来没有记录，请直接说明没有这项记录。",
        "zh-TW": "請查詢 {person} 的{label}記錄。如果從來沒有記錄，請直接說明沒有這項記錄。",
        "ja": "{person} の{label}の記録を調べてください。記録がまったくない場合は、そのまま無いと伝えてください。",
        "en": "Check {person}'s {label} records. If there is no record at all, say so plainly.",
    },
}


def load_devices(corpus: Path):
    series = defaultdict(lambda: defaultdict(dict))
    for line in (corpus / "devices.jsonl").open():
        person = json.loads(line)
        for rec in person.get("records") or []:
            metric = rec.get("metric")
            if metric not in METRICS:
                continue
            day, value = str(rec.get("time", ""))[:10], rec.get("value")
            if isinstance(value, (int, float)) and day:
                series[person["person_id"]][metric][day] = float(value)
    return series


def load_journal(corpus: Path):
    by_person = defaultdict(list)
    for line in (corpus / "journal.jsonl").open():
        row = json.loads(line)
        names = [e.get("name") for e in (row.get("expected") or []) if e.get("kind") == "symptom"]
        by_person[row["person_id"]].append({"date": row.get("date"), "text": row.get("text") or "", "names": names})
    return by_person


def consecutive(days: dict[str, float], length: int):
    ordered = sorted(days)
    for i in range(len(ordered) - length + 1):
        chunk = ordered[i:i + length]
        if (date.fromisoformat(chunk[-1]) - date.fromisoformat(chunk[0])).days == length - 1:
            return chunk[0], chunk[-1], [(d, days[d]) for d in chunk]
    return None


def evidence(person: str, metric: str, start: str, end: str, records: int) -> list[dict]:
    return [{"file": "devices.jsonl", "person_id": person, "metric": metric, "start": start, "end": end,
             "tz": "Asia/Shanghai", "records": records, "day_boundary": "00:00",
             "value_policy": "reported_numeric_value"}]


def build(corpus: Path, people: int, as_of: str) -> list[dict]:
    devices = load_devices(corpus)
    journal = load_journal(corpus)
    out: list[dict] = []
    for person in sorted(devices)[:people]:
        for metric in ("steps", "rhr", "weight"):
            days = devices[person].get(metric) or {}
            picked = consecutive(days, 30)
            if not picked:
                continue
            start, end, points = picked
            latest_day, latest_value = max(points, key=lambda kv: kv[0])
            total = round(sum(v for _, v in points), 2)
            tol = 0.11 if metric == "weight" else 0.6
            for lang in LANGS:
                label = label_for(metric, lang)
                common = {"suite_id": EXTRA_SUITE, "synthetic": True, "person": person, "lang": lang,
                          "domain": "device", "sources": ["devices.jsonl"],
                          "expect_tools": ["query_health_indicators"], "chart": False, "as_of": as_of,
                          "evidence": evidence(person, metric, start, end, len(points)), "forbidden": []}
                out.append({**common, "id": f"x-device-{metric}-latest-{person}-{lang}",
                            "question": TEXT["latest"][lang].format(person=person, start=start, end=end, label=label),
                            "facts": [{"kind": "number", "value": round(latest_value, 2), "tol": tol,
                                       "label": "latest", "scope": {"date": latest_day}},
                                      {"kind": "date", "value": latest_day, "label": "latest date"}],
                            "tags": [metric, "latest"], "right": f"{latest_day} {latest_value}"})
                if METRICS[metric]["additive"]:
                    out.append({**common, "id": f"x-device-{metric}-sum-{person}-{lang}",
                                "question": TEXT["sum"][lang].format(person=person, start=start, end=end, label=label),
                                "facts": [{"kind": "number", "value": total, "tol": max(1.0, abs(total) * 0.002),
                                           "label": "total"}],
                                "tags": [metric, "sum"], "right": str(total)})
                out.append({**common, "id": f"x-device-{metric}-count-{person}-{lang}",
                            "question": TEXT["count"][lang].format(person=person, start=start, end=end, label=label),
                            "facts": [{"kind": "number", "value": len(points), "tol": 0.01, "label": "days"}],
                            "tags": [metric, "count"], "right": str(len(points))})
            week = consecutive(days, 7)
            if week:
                wstart, wend, wpoints = week
                for lang in LANGS:
                    label = label_for(metric, lang)
                    out.append({"suite_id": EXTRA_SUITE, "synthetic": True, "person": person, "lang": lang,
                                "domain": "device", "sources": ["devices.jsonl"],
                                "expect_tools": ["query_health_indicators"], "chart": True, "as_of": as_of,
                                "id": f"x-chart-{metric}-{person}-{lang}",
                                "question": TEXT["chart"][lang].format(person=person, start=wstart, end=wend, label=label),
                                "facts": [{"kind": "number", "value": round(v, 2), "tol": tol, "label": d,
                                           "scope": {"date": d}} for d, v in wpoints],
                                "evidence": evidence(person, metric, wstart, wend, len(wpoints)),
                                "forbidden": [], "tags": [metric, "chart"],
                                "right": ", ".join(f"{d}={v}" for d, v in wpoints)})

    for person in sorted(journal)[:people]:
        names = [n for row in journal[person] for n in row["names"] if n]
        if not names:
            continue
        counts = Counter(names)
        symptom = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        count = sum(1 for row in journal[person] if symptom in row["names"] and row["text"].strip())
        for lang in LANGS:
            local = SYMPTOM_LABELS.get(symptom, {}).get(lang)
            if lang != "en" and not local:
                continue
            out.append({"suite_id": EXTRA_SUITE, "synthetic": True, "person": person, "lang": lang, "domain": "journal",
                        "sources": ["journal.jsonl"], "expect_tools": ["query_health_indicators"], "chart": False,
                        "as_of": as_of, "evidence": [], "forbidden": [],
                        "id": f"x-journal-{person}-{lang}",
                        "question": TEXT["journal"][lang].format(symptom=local or symptom),
                        "facts": [{"kind": "number", "value": count, "tol": 0.01, "label": "entries"}],
                        "tags": ["journal", symptom], "right": str(count)})

    for person in sorted(devices)[:people]:
        missing = [m for m in ("steps", "rhr") if not devices[person].get(m)]
        if not missing:
            continue
        metric = missing[0]
        for lang in LANGS:
            label = label_for(metric, lang)
            out.append({"suite_id": EXTRA_SUITE, "synthetic": True, "person": person, "lang": lang, "domain": "absence",
                        "sources": ["devices.jsonl"], "expect_tools": ["query_health_indicators"], "chart": False,
                        "as_of": as_of, "evidence": [], "forbidden": [],
                        "id": f"x-absence-{metric}-{person}-{lang}",
                        "question": TEXT["absence"][lang].format(person=person, label=label),
                        "facts": [{"kind": "words", "label": "states absence",
                                   "any": ["没有", "无记录", "未记录", "no record", "no data", "not recorded",
                                           "記録がありません", "ありません"]}],
                        "tags": ["absence", metric], "right": "states absence plainly"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True, type=Path, help="a mirobody-gen build (devices.jsonl, journal.jsonl)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--people", type=int, default=3, help="how many people to draw cases from")
    ap.add_argument("--as-of", default=AS_OF, help="evaluation clock recorded on every case")
    args = ap.parse_args()

    cases = build(args.corpus, args.people, args.as_of)
    with args.out.open("w") as fh:
        for case in cases:
            fh.write(json.dumps(case, ensure_ascii=False) + "\n")
    langs = sorted({c["lang"] for c in cases})
    domains = sorted({c["domain"] for c in cases})
    charts = sum(1 for c in cases if c["chart"])
    print(f"wrote {args.out}: {len(cases)} cases (charts {charts}); langs={langs}; domains={domains}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
