"""MatrAIx 실행 결과를 읽어 문항 통계를 내고 최종 128문항을 고른다.

    python3 report.py --jobs <matraix>/jobs/mi-a1-b1 ... <matraix>/jobs/mi-b-child-n96 --out report/

어떤 job이 A1(분류)·A2(평가)·B(가상 학생)인지는 응답의 질문 id로 알아낸다. 일부 job만 있어도 돌아간다.

출력:
    report/report.html            — 문항 검토 보고서
    report/item_stats.csv         — 192문항 × 지표
    report/final_128.csv          — 최종 128문항 (지능별 흥미 8 + 능력 8)
    report/final_checklist.html   — 인쇄용 128문항 검사지 + 답안지 (8열 × 16줄)

MatrAIx 공식 산출물만 읽는다:
    <trial>/persona_meta.json            — persona_id
    <trial>/**/survey_result.json        — answers[]: questionId, value
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, pvariance

from build_tasks import FATIGUE_OPTIONS, a1_display
from child_profiles import build_profiles
from mi_items import FACETS, FINAL_PER_FACET, INTEL_IDS, ITEM_BY_ID, ITEMS, NAME, SHORT
from check_items import CHECKS, check

# 문항 선정 기준 (모자라면 기준 미달 문항 중 종합 점수가 높은 것으로 채우고 표시한다)
MIN_AGREE = 0.5     # A1: 의도한 지능을 고른 전문가 비율
MIN_FACET = 0.5     # A1: 의도한 측면(흥미·능력)을 고른 전문가 비율
MIN_ICVI = 0.78     # A2: 적합성 3~4점 비율 (Lynn 기준)
MIN_CLARITY = 3.0   # A2: 이해 난이도 평균 (1~4)
MIN_ITC = 0.2       # B: 수정된 문항-총점 상관 (모든 아이가 같은 점수면 계산 불가 → 탈락)
MAX_HARD = 0.2      # B: 그 문항을 '모르는 낱말'에 적은 아이 비율
SHORT_PER_FACET = 4  # 비교용 64문항 축약판: 지능·측면당 4


# ---------------------------------------------------------------- 읽기

def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_trials(job_dirs: list[Path]) -> list[dict]:
    trials = []
    for job in job_dirs:
        for trial in sorted(p for p in job.iterdir() if p.is_dir()):
            result = next(trial.rglob("survey_result.json"), None)
            if result is None:
                continue
            payload = _read_json(result) or {}
            meta = _read_json(trial / "persona_meta.json") or {}
            answers = {str(a.get("questionId")): a.get("value")
                       for a in payload.get("answers") or [] if isinstance(a, dict)}
            trials.append({
                "job": job.name,
                "trial": trial.name,
                "persona_id": str(meta.get("persona_id") or meta.get("id") or ""),
                "kind": _kind(answers),
                "answers": answers,
            })
    return trials


def _kind(answers: dict) -> str:
    if any(k.endswith("_intel") or k.endswith("_ambiguous") for k in answers):
        return "A1"
    if any(k.endswith("_clarity") for k in answers):
        return "A2"
    if "b_fatigue" in answers or any(re.fullmatch(r"c\d{3}", k) for k in answers):
        return "B"
    return "unknown"


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _numbers_in(text) -> list[int]:
    return [int(n) for n in re.findall(r"(?<!\d)(\d{1,3})(?!\d)", str(text or "")) if 1 <= int(n) <= len(ITEMS)]


def _lines_by_item(text) -> dict[int, list[str]]:
    """'번호: 내용' 줄을 문항 번호별로 모은다."""
    out: dict[int, list[str]] = defaultdict(list)
    text = str(text or "")
    # 한 줄에 여러 문항을 이어 쓰는 경우("34번: … 66번: …")도 있어, 줄이 아니라 '번호:' 표지로 자른다.
    marks = list(re.finditer(r"(?:^|(?<=[\s,.;\]]))\[?(\d{1,3})\s*번?\]?\s*[:：)]", text))
    for m, nxt in zip(marks, marks[1:] + [None]):
        no = int(m.group(1))
        body = text[m.end(): nxt.start() if nxt else len(text)].strip().split("\n")[0].strip(" -*·,;")
        if 1 <= no <= len(ITEMS) and body:
            out[no].append(body)
    return out


# ---------------------------------------------------------------- 통계

# A1 블록별 표시 번호 → 문항 id (build_tasks.a1_display)
A1_SHOWN = {b: {d: x["id"] for d, x in a1_display(b)} for b in range(1, 5)}

def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    mx, my = mean(xs), mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sxy / (sxx * syy) ** 0.5


def cronbach_alpha(rows: list[list[float]]) -> float | None:
    """rows: 응답자 × 문항."""
    if len(rows) < 3 or len(rows[0]) < 2:
        return None
    k = len(rows[0])
    item_vars = [pvariance([r[i] for r in rows]) for i in range(k)]
    total_var = pvariance([sum(r) for r in rows])
    if total_var == 0:
        return None
    return k / (k - 1) * (1 - sum(item_vars) / total_var)


def child_matrix(trials: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """B 응답을 (아이 프로필, {persona_id: {문항 id: 점수}})로 정리한다. 192문항을 모두 답한 아이만 쓴다."""
    profiles = {p["persona_id"]: p for p in build_profiles()}
    scores: dict[str, dict] = {}
    for t in trials:
        if t["kind"] != "B" or t["persona_id"] not in profiles:
            continue
        row = {x["id"]: _num(t["answers"].get(x["id"])) for x in ITEMS}
        if all(v is not None for v in row.values()):
            scores[t["persona_id"]] = row
    kids = [profiles[pid] for pid in sorted(scores)]
    return kids, scores


def item_stats(trials: list[dict]) -> dict[str, dict]:
    stats = {x["id"]: {"intel_votes": Counter(), "facet_votes": Counter(), "fit": [], "clarity": [],
                       "suggestions": [], "ambiguous": [], "hard_words": 0}
             for x in ITEMS}
    by_no = {x["no"]: x["id"] for x in ITEMS}
    for t in trials:
        a = t["answers"]
        if t["kind"] == "A1":
            for qid, v in a.items():
                m = re.fullmatch(r"a1_b(\d)_(\d{2})_(intel|facet)", qid)
                if m:
                    item = A1_SHOWN[int(m.group(1))].get(int(m.group(2)))
                    if item:
                        stats[item]["intel_votes" if m.group(3) == "intel" else "facet_votes"][str(v)] += 1
                    continue
                m = re.fullmatch(r"a1_b(\d)_ambiguous", qid)
                if m:  # 표시 번호로 적힌 메모
                    for no, notes in _lines_by_item(v).items():
                        item = A1_SHOWN[int(m.group(1))].get(no)
                        if item:
                            stats[item]["ambiguous"] += notes
        elif t["kind"] == "A2":
            for qid, v in a.items():
                n = _num(v)
                if qid.endswith("_fit") and qid[:4] in stats and n is not None:
                    stats[qid[:4]]["fit"].append(n)
                elif qid.endswith("_clarity") and qid[:4] in stats and n is not None:
                    stats[qid[:4]]["clarity"].append(n)
                elif qid.endswith("_revise"):
                    for no, notes in _lines_by_item(v).items():
                        stats[by_no[no]]["suggestions"] += notes
        elif t["kind"] == "B":
            for no in set(_numbers_in(a.get("b_hard_words"))):
                stats[by_no[no]]["hard_words"] += 1

    kids, scores = child_matrix(trials)
    for x in ITEMS:
        s = stats[x["id"]]
        votes = sum(s["intel_votes"].values())
        s["n_a1"] = votes
        s["agree"] = s["intel_votes"][x["intel"]] / votes if votes else None
        others = [(k, v) for k, v in s["intel_votes"].most_common() if k != x["intel"]]
        s["confused_with"] = others[0][0] if others else ""
        fvotes = sum(s["facet_votes"].values())
        s["facet_agree"] = s["facet_votes"][x["facet"]] / fvotes if fvotes else None
        s["n_a2"] = len(s["fit"])
        s["icvi"] = sum(1 for v in s["fit"] if v >= 3) / len(s["fit"]) if s["fit"] else None
        s["clarity_mean"] = mean(s["clarity"]) if s["clarity"] else None
        s["n_b"] = len(kids)
        s["hard_share"] = s["hard_words"] / len(kids) if kids else None
        s["itc"] = s["disc"] = s["mean_b"] = None
        if kids:
            same = [y["id"] for y in ITEMS if y["intel"] == x["intel"] and y["id"] != x["id"]]
            xs = [scores[k["persona_id"]][x["id"]] for k in kids]
            rest = [sum(scores[k["persona_id"]][i] for i in same) for k in kids]
            s["itc"] = _pearson(xs, rest)
            s["mean_b"] = mean(xs)
            strong = [scores[k["persona_id"]][x["id"]] for k in kids
                      if x["intel"] in [k["primary"], *k["secondary"]]]
            other = [scores[k["persona_id"]][x["id"]] for k in kids
                     if x["intel"] not in [k["primary"], *k["secondary"]]]
            if strong and other:
                s["disc"] = mean(strong) - mean(other)
        s["flags"] = check(x["text"])
        s["composite"], s["eligible"], s["fails"] = _composite(s)
    return stats


def _composite(s: dict) -> tuple[float | None, bool, list[str]]:
    parts, fails = [], []
    if s["agree"] is not None:
        parts.append(s["agree"])
        if s["agree"] < MIN_AGREE:
            fails.append("지능 일치율")
    if s["facet_agree"] is not None and s["facet_agree"] < MIN_FACET:
        fails.append("측면 일치율")
    if s["icvi"] is not None:
        parts.append(s["icvi"])
        if s["icvi"] < MIN_ICVI:
            fails.append("I-CVI")
    if s["clarity_mean"] is not None:
        parts.append((s["clarity_mean"] - 1) / 3)
        if s["clarity_mean"] < MIN_CLARITY:
            fails.append("이해도")
    if s["itc"] is not None:
        parts.append(max(0.0, min(1.0, s["itc"])))
        if s["itc"] < MIN_ITC:
            fails.append("문항-총점 상관")
    elif s["n_b"]:
        fails.append("응답 변산 없음")
    if s["hard_share"] is not None and s["hard_share"] >= MAX_HARD:
        fails.append("모르는 낱말")
    if s["disc"] is not None:
        parts.append(max(0.0, min(1.0, s["disc"] / 2)))
    if not parts:
        return None, True, []
    score = mean(parts) - 0.5 * (s["hard_share"] or 0)
    return score, not fails, fails


def select(stats: dict[str, dict], per_facet: int) -> list[dict]:
    """지능·측면마다 기준 충족 → 종합 점수 순으로 per_facet개를 고른다. 자료가 없으면 후보 순서대로."""
    chosen = []
    for intel in INTEL_IDS:
        for facet in FACETS:
            pool = [x for x in ITEMS if x["intel"] == intel and x["facet"] == facet]
            pool.sort(key=lambda x: (not stats[x["id"]]["eligible"],
                                     -(stats[x["id"]]["composite"] if stats[x["id"]]["composite"] is not None else 0),
                                     x["k"]))
            chosen += pool[:per_facet]
    return chosen


def order_checklist(chosen: list[dict], stats: dict[str, dict]) -> list[dict]:
    """최종 번호: 8개 지능을 번갈아, 지능 안에서는 흥미·능력을 번갈아 배치한다."""
    out = []
    for col, intel in enumerate(INTEL_IDS, start=1):
        facets = {f: [x for x in chosen if x["intel"] == intel and x["facet"] == f] for f in FACETS}
        seq = [y for pair in zip(facets["interest"], facets["ability"]) for y in pair]
        for j, x in enumerate(seq, start=1):
            out.append({**x, "final_no": (j - 1) * 8 + col})
    return sorted(out, key=lambda x: x["final_no"])


def recovery(kids: list[dict], scores: dict[str, dict], item_ids: list[str]) -> dict | None:
    """심어 둔 강점·약점이 문항 세트 점수에서 다시 나오는지."""
    if not kids:
        return None
    ids_by_intel = {i: [x for x in item_ids if ITEM_BY_ID[x]["intel"] == i] for i in INTEL_IDS}
    top1 = top3 = weak = 0.0
    for k in kids:
        sc = {i: mean(scores[k["persona_id"]][x] for x in ids) for i, ids in ids_by_intel.items()}
        ranked = sorted(INTEL_IDS, key=lambda i: (-sc[i], INTEL_IDS.index(i)))
        top1 += ranked[0] == k["primary"]
        top3 += len(set(ranked[:3]) & {k["primary"], *k["secondary"]}) / 3
        weak += len(set(ranked[-3:]) & set(k["weak"])) / 2
    n = len(kids)
    return {"n": n, "top1": top1 / n, "top3": top3 / n, "weak": weak / n}


def alphas(kids: list[dict], scores: dict[str, dict], item_ids: list[str]) -> dict[str, float | None]:
    out = {}
    for intel in INTEL_IDS:
        ids = [x for x in item_ids if ITEM_BY_ID[x]["intel"] == intel]
        out[intel] = cronbach_alpha([[scores[k["persona_id"]][x] for x in ids] for k in kids]) if kids else None
    return out


# ---------------------------------------------------------------- 출력

def _f(v, digits=2) -> str:
    return "–" if v is None else (f"{v:.{digits}f}" if isinstance(v, float) else str(v))


def _pct(v) -> str:
    return "–" if v is None else f"{v * 100:.0f}%"


def summary_html(trials: list[dict], stats: dict[str, dict], kids: list[dict], scores: dict[str, dict],
                 final: list[dict]) -> list[str]:
    """보고서 맨 앞 핵심 요약."""
    e = html.escape
    items = []
    src = Counter("기존" if x["orig_no"] else "신규" for x in final)
    items.append(f"최종 128문항 = 기존 80문항 수정본에서 {src['기존']}문항 + 신규 문항에서 {src['신규']}문항. "
                 f"지능마다 흥미 {FINAL_PER_FACET} + 능력 {FINAL_PER_FACET}.")
    failed = [x for x in ITEMS if stats[x["id"]]["fails"]]
    if failed:
        reasons = Counter(r for x in failed for r in stats[x["id"]]["fails"])
        items.append(f"후보 192문항 중 {len(failed)}문항이 기준에 미달해 제외되었다 ("
                     + ", ".join(f"{e(r)} {n}" for r, n in reasons.most_common()) + "). 문항은 아래 3절에 있다.")
    forced = [x for x in final if stats[x["id"]]["fails"]]
    items.append(f"기준 미달인데 자리를 채우려고 뽑힌 문항: {len(forced)}개."
                 if forced else "최종 128문항은 모두 선정 기준을 충족한다.")
    if kids:
        r_all = recovery(kids, scores, [x["id"] for x in ITEMS])
        r_fin = recovery(kids, scores, [x["id"] for x in final])
        a = [v for v in alphas(kids, scores, [x["id"] for x in final]).values() if v is not None]
        items.append(f"가상 학생 {len(kids)}명에게 심어 둔 1순위 강점을 최종 128문항이 {_pct(r_fin['top1'])} 찾아냈고 "
                     f"(후보 192문항 {_pct(r_all['top1'])}), 상위 3개 강점은 평균 {_pct(r_fin['top3'])}가 일치했다. "
                     f"지능별 신뢰도(α)는 {min(a):.2f}~{max(a):.2f}.")
        fat = Counter(str(t["answers"].get("b_fatigue")) for t in trials if t["kind"] == "B")
        if fat and len(fat) == 1:
            items.append("지루해진 지점은 가상 학생 모두가 같은 답을 골라 정보가 없다. 길이 부담은 실제 파일럿에서 확인한다.")
    return ["<h2>요약</h2><ul>"] + [f"<li>{t}</li>" for t in items] + ["</ul>"]


def build_html(trials: list[dict], stats: dict[str, dict]) -> str:
    e = html.escape
    kids, scores = child_matrix(trials)
    final = select(stats, FINAL_PER_FACET)
    short = select(stats, SHORT_PER_FACET)
    final_ids = {x["id"] for x in final}
    kinds = Counter(t["kind"] for t in trials)

    parts = [
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>다중지능 문항 검토</title><style>",
        ":root{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;--bad:#c62828;--good:#2e7d32;--hl:#fff8e1}",
        "@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#f2f2f7;--muted:#a1a1a6;--line:#38383a;--bad:#ef9a9a;--good:#a5d6a7;--hl:#3a3320}}",
        "body{background:var(--bg);color:var(--fg);font-family:system-ui,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;",
        "max-width:1100px;margin:0 auto;padding:16px;line-height:1.5}",
        "table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0 24px}",
        "th,td{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}",
        "th{color:var(--muted);font-weight:600}.num{text-align:right;white-space:nowrap}",
        ".bad{color:var(--bad)}.good{color:var(--good)}.sel{background:var(--hl)}.muted{color:var(--muted)}",
        ".wrap{overflow-x:auto}</style></head><body>",
        "<h1>다중지능 체크리스트 문항 검토</h1>",
        f"<p class='muted'>응답 수: A1 분류 {kinds['A1']}건 · A2 평가 {kinds['A2']}건 · B 가상 학생 {len(kids)}명. "
        "LLM 페르소나 시뮬레이션 결과이므로 실제 학생 파일럿으로 확인해야 한다.</p>",
        *summary_html(trials, stats, kids, scores, final),
        "<h2>1. 강점 지능 판별 정확도 (B)</h2>",
        "<p>가상 학생에게 심어 둔 강점이 문항 점수에서 다시 나오는지 본다. 같은 응답에서 문항 세트만 바꿔 계산한다.</p>",
    ]
    sets = [("후보 192문항", [x["id"] for x in ITEMS]), ("최종 128문항", [x["id"] for x in final]),
            ("축약 64문항 (비교용)", [x["id"] for x in short])]
    parts.append("<div class='wrap'><table><tr><th>문항 세트</th><th class='num'>1순위 일치</th>"
                 "<th class='num'>상위 3개 일치</th><th class='num'>약점이 하위 3개에</th></tr>")
    for label, ids in sets:
        r = recovery(kids, scores, ids)
        parts.append(f"<tr><td>{label}</td>" + (
            f"<td class='num'>{_pct(r['top1'])}</td><td class='num'>{_pct(r['top3'])}</td><td class='num'>{_pct(r['weak'])}</td>"
            if r else "<td colspan='3' class='muted'>B 결과 없음</td>") + "</tr>")
    parts.append("</table></div>")

    if kids:
        a_final, a_all = alphas(kids, scores, [x["id"] for x in final]), alphas(kids, scores, [x["id"] for x in ITEMS])
        parts.append("<h3>지능별 신뢰도 (크론바흐 α, 가상 학생)</h3><div class='wrap'><table><tr><th>지능</th>"
                     "<th class='num'>후보 24문항</th><th class='num'>최종 16문항</th></tr>")
        for i in INTEL_IDS:
            parts.append(f"<tr><td>{NAME[i]}</td><td class='num'>{_f(a_all[i])}</td><td class='num'>{_f(a_final[i])}</td></tr>")
        parts.append("</table></div>")
        fat = Counter(str(t["answers"].get("b_fatigue")) for t in trials if t["kind"] == "B")
        length = [n for t in trials if t["kind"] == "B" and (n := _num(t["answers"].get("b_length"))) is not None]
        parts.append("<h3>길이 부담 (가상 학생)</h3><p>")
        labels = {o["id"]: o["label"] for o in FATIGUE_OPTIONS}
        parts.append(" · ".join(f"{e(labels.get(k, k))} {v}명" for k, v in fat.most_common()))
        parts.append(f"<br>'문장 수가 너무 많았다' 평균 {_f(mean(length)) if length else '–'} / 5</p>")

    a_items = [stats[x["id"]] for x in ITEMS if stats[x["id"]]["n_a1"] or stats[x["id"]]["n_a2"]]
    if a_items:
        def _mean(key):
            vals = [s[key] for s in a_items if s[key] is not None]
            return mean(vals) if vals else None
        def _below(key, cut):
            return sum(1 for s in a_items if s[key] is not None and s[key] < cut)
        parts.append("<h2>전문가 패널 요약 (A)</h2><div class='wrap'><table><tr><th>지표</th>"
                     "<th class='num'>평균</th><th class='num'>기준 미달 문항</th></tr>")
        for label, key, cut, pct in (("지능 일치율", "agree", MIN_AGREE, True),
                                     ("측면 일치율", "facet_agree", MIN_FACET, True),
                                     ("I-CVI", "icvi", MIN_ICVI, False),
                                     ("이해 난이도 (1~4)", "clarity_mean", MIN_CLARITY, False)):
            m = _mean(key)
            parts.append(f"<tr><td>{label}</td><td class='num'>{_pct(m) if pct else _f(m)}</td>"
                         f"<td class='num'>{_below(key, cut)}</td></tr>")
        parts.append("</table></div>")
        if all(s["agree"] == 1 for s in a_items if s["agree"] is not None):
            parts.append("<p>모든 문항에서 교사 페르소나 전원이 의도한 지능을 골랐다. 문항이 뚜렷하다는 뜻이기도 하지만, "
                         "LLM 페르소나끼리 판단이 비슷해 생기는 천장 효과일 수 있어 이 지표로는 문항을 가려내지 못한다. "
                         "선정은 측면 일치율, I-CVI, 이해 난이도, 가상 학생 지표가 가른다.</p>")

    parts.append("<h2>2. 지능별 선정 결과</h2><p>노란 줄이 최종 128문항에 들어간 문항이다. "
                 f"기준: 지능 일치율 ≥ {MIN_AGREE:.0%}, 측면 일치율 ≥ {MIN_FACET:.0%}, I-CVI ≥ {MIN_ICVI}, "
                 f"이해도 ≥ {MIN_CLARITY}, "
                 f"문항-총점 상관 ≥ {MIN_ITC}. 기준 미달인데 뽑힌 문항은 빨간 글씨로 표시한다 "
                 "(그 지능·측면에 기준을 넘는 문항이 모자랐다는 뜻).</p>")
    for intel in INTEL_IDS:
        parts.append(f"<h3>{NAME[intel]}</h3><div class='wrap'><table><tr><th>번호</th><th>측면</th><th>문항</th>"
                     "<th class='num'>일치율</th><th>헷갈린 지능</th><th class='num'>측면 일치</th><th class='num'>I-CVI</th>"
                     "<th class='num'>이해도</th><th class='num'>문항-총점</th><th class='num'>변별</th>"
                     "<th class='num'>모름 표시</th><th class='num'>종합</th></tr>")
        for x in (y for y in ITEMS if y["intel"] == intel):
            s = stats[x["id"]]
            cls = "sel" if x["id"] in final_ids else ""
            text = e(x["text"]) + (f" <span class='bad'>({e(', '.join(s['fails']))})</span>"
                                   if s["fails"] and x["id"] in final_ids else "")
            parts.append(
                f"<tr class='{cls}'><td>{x['no']}</td><td>{FACETS[x['facet']][:2]}</td><td>{text}</td>"
                f"<td class='num'>{_pct(s['agree'])}</td><td>{e(SHORT.get(s['confused_with'], s['confused_with']))}</td>"
                f"<td class='num'>{_pct(s['facet_agree'])}</td><td class='num'>{_f(s['icvi'])}</td>"
                f"<td class='num'>{_f(s['clarity_mean'])}</td><td class='num'>{_f(s['itc'])}</td>"
                f"<td class='num'>{_f(s['disc'])}</td><td class='num'>{_pct(s['hard_share'])}</td>"
                f"<td class='num'>{_f(s['composite'])}</td></tr>")
        parts.append("</table></div>")

    parts.append("<h2>3. 기준 미달로 제외된 문항</h2><div class='wrap'><table><tr><th>번호</th><th>지능·측면</th>"
                 "<th>문항</th><th>미달 기준</th></tr>")
    for x in ITEMS:
        s = stats[x["id"]]
        if s["fails"]:
            parts.append(f"<tr><td>{x['no']}</td><td>{SHORT[x['intel']]}·{FACETS[x['facet']][:2]}</td>"
                         f"<td>{e(x['text'])}</td><td>{e(', '.join(s['fails']))}</td></tr>")
    parts.append("</table></div>")

    parts.append("<h2>4. 전문가 페르소나의 수정 제안 (A)</h2>")
    any_note = False
    for x in ITEMS:
        s = stats[x["id"]]
        notes = s["suggestions"] + [f"(분류 망설임) {n}" for n in s["ambiguous"]]
        if notes:
            any_note = True
            parts.append(f"<p><b>{x['no']}. {e(x['text'])}</b> <span class='muted'>{NAME[x['intel']]}</span></p><ul>")
            parts += [f"<li>{e(n)}</li>" for n in notes[:6]]
            parts.append("</ul>")
    if not any_note:
        parts.append("<p class='muted'>수정 제안 없음 (또는 A 결과 없음)</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


def build_checklist_html(final: list[dict]) -> str:
    """인쇄용 최종 검사지 + 답안지."""
    e = html.escape
    rows = order_checklist(final, {})
    half = len(rows) // 2
    out = [
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'><title>나를 알아보는 체크리스트</title>",
        "<style>:root{--bg:#fff;--fg:#111;--line:#999;--head:#e8e8f4}",
        "@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#eee;--line:#666;--head:#2a2a3a}}",
        "body{background:var(--bg);color:var(--fg);font-family:system-ui,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;",
        "max-width:800px;margin:0 auto;padding:16px;line-height:1.7}ol{padding-left:2.6em}",
        ".break{margin:16px 0;padding:8px;border:1px dashed var(--line);text-align:center}",
        "table{border-collapse:collapse;width:100%;margin-top:12px}td,th{border:1px solid var(--line);text-align:center;",
        "height:28px;font-size:13px}th{background:var(--head)}@media print{.break{break-after:page}}</style></head><body>",
        "<h1>나를 알아보는 체크리스트</h1>",
        "<p>문장을 읽고 나에게 얼마나 맞는지 답안지에 번호를 적어요. 정답은 없어요.<br>"
        "1 전혀 아니에요 · 2 별로 아니에요 · 3 보통이에요 · 4 대체로 그래요 · 5 정말 그래요</p><ol>",
    ]
    for i, x in enumerate(rows):
        if i == half:
            out.append(f"</ol><div class='break'>절반 왔어요! 잠깐 기지개를 켜고 {half + 1}번부터 이어서 해요.</div>"
                       f"<ol start='{half + 1}'>")
        out.append(f"<li value='{x['final_no']}'>{e(x['text'])}</li>")
    out.append("</ol><h2>답안지</h2><p>(&nbsp;&nbsp;&nbsp;&nbsp;)번 이름: ______________ 강점 지능: ______________</p><table>")
    per_row = len(rows) // 8
    for r in range(per_row):
        out.append("<tr>" + "".join(f"<th>{r * 8 + c}</th>" for c in range(1, 9)) + "</tr>")
        out.append("<tr>" + "<td></td>" * 8 + "</tr>")
    out.append("<tr>" + "<th>합계</th>" * 8 + "</tr><tr>" + "<td></td>" * 8 + "</tr>")
    out.append("<tr>" + "".join(f"<th>{SHORT[i]}</th>" for i in INTEL_IDS) + "</tr></table></body></html>")
    return "\n".join(out)


STAT_COLUMNS = ["id", "no", "intel", "facet", "source", "orig_no", "text", "n_a1", "agree", "confused_with",
                "facet_agree", "n_a2", "icvi", "clarity_mean", "n_b", "mean_b", "itc", "disc", "hard_share",
                "composite", "eligible", "fails", "flags", "final_no"]


def write_csvs(stats: dict[str, dict], out: Path) -> list[dict]:
    final = order_checklist(select(stats, FINAL_PER_FACET), stats)
    final_no = {x["id"]: x["final_no"] for x in final}
    with (out / "item_stats.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(STAT_COLUMNS)
        for x in ITEMS:
            s = stats[x["id"]]
            row = {**x, **s, "fails": "; ".join(s["fails"]),
                   "flags": "; ".join(CHECKS[k] for k in s["flags"]), "final_no": final_no.get(x["id"], "")}
            w.writerow(["" if row.get(c) is None else row.get(c) for c in STAT_COLUMNS])
    with (out / "final_128.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["최종 번호", "지능", "측면", "문항", "후보 번호", "출처"])
        for x in final:
            w.writerow([x["final_no"], NAME[x["intel"]], FACETS[x["facet"]], x["text"], x["no"],
                        f"기존 {x['orig_no']}" if x["orig_no"] else "신규"])
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, default=Path("report"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    trials = load_trials([j for j in args.jobs if j.is_dir()])
    stats = item_stats(trials)
    final = write_csvs(stats, args.out)
    (args.out / "report.html").write_text(build_html(trials, stats), encoding="utf-8")
    (args.out / "final_checklist.html").write_text(build_checklist_html(final), encoding="utf-8")
    print(f"{args.out}: trial {len(trials)}개 → report.html, item_stats.csv, final_128.csv, final_checklist.html")


if __name__ == "__main__":
    main()
