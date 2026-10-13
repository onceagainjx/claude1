"""MatrAIx 실행 결과(jobs/<job>/)를 읽어 경력 구간별 비교 보고서를 만든다.

    python3 report.py --jobs <matraix>/jobs/dk-j1 <matraix>/jobs/dk-j2 ... --out report/

출력:
    report/report.html     — 경력 집단 비교 보고서 (연구소 공유용)
    report/answers.csv     — 모든 응답 원자료 (trial × 문항)

MatrAIx 공식 산출물만 읽는다:
    <trial>/persona_meta.json            — 페르소나 속성(years_experience 등)
    <trial>/**/survey_result.json        — answers[]: questionId, value, rationale
                                           (질문별 j<n>_q<k>, _fit(1~10), _reason(이유 + rationale))
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from journeys import JOURNEYS

GROUPS = {
    "0-2": "저경력", "3-5": "저경력",
    "6-10": "중간 경력",
    "11-20": "고경력", "20+": "고경력",
}
GROUP_ORDER = ["저경력", "중간 경력", "고경력"]
LOW, HIGH = "저경력", "고경력"
SCALE_MAX = 10  # build_tasks.py의 척도 (1~10점)
GAP_ALERT = 1.0  # 저경력 − 고경력 차이가 이만큼 낮으면 강조 (10점 척도 기준)

REASON_LABELS = {
    "lack_experience": "교직 경험이 아직 부족",
    "premise_mismatch": "질문의 전제가 나와 맞지 않음",
    "too_personal": "너무 사적·감정적",
    "too_abstract": "추상적임",
    "survival_first": "당장 버티는 문제가 더 급함",
    "no_difficulty": "어렵지 않았음",
}


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


GENDER_LABELS = {"Man": "남", "Woman": "여"}


def _dimensions(meta: dict) -> dict:
    """persona_meta의 속성, 없으면 persona_path의 YAML에서 읽는다 (build_pool.py가 붙인 속성 포함)."""
    dims = dict(meta.get("dimensions") or {})
    if "years_experience" not in dims and meta.get("persona_path"):
        try:
            import yaml

            loaded = yaml.safe_load(Path(meta["persona_path"]).read_text(encoding="utf-8"))
            dims.update((loaded or {}).get("dimensions") or {})
        except (OSError, ImportError, ValueError):
            pass
    return dims


def load_trials(job_dirs: list[Path]) -> list[dict]:
    trials = []
    for job in job_dirs:
        for trial in sorted(p for p in job.iterdir() if p.is_dir()):
            result = next(trial.rglob("survey_result.json"), None)
            if result is None:
                continue
            payload = _read_json(result) or {}
            meta = _read_json(trial / "persona_meta.json") or {}
            dims = _dimensions(meta)
            years = str(dims.get("years_experience") or "unknown")
            trials.append({
                "job": job.name,
                "trial": trial.name,
                "persona_id": str(meta.get("persona_id") or meta.get("id") or ""),
                "years": years,
                "group": GROUPS.get(years, "unknown"),
                "gender": GENDER_LABELS.get(str(dims.get("gender_identity")), str(dims.get("gender_identity") or "")),
                "school_level": str(dims.get("school_level") or ""),
                "work_region": str(dims.get("work_region") or ""),
                "answers": {
                    str(a.get("questionId")): a for a in payload.get("answers") or [] if isinstance(a, dict)
                },
            })
    return trials


def _num(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _mean_by_group(trials: list[dict], qid: str) -> dict[str, float | None]:
    vals: dict[str, list[float]] = defaultdict(list)
    for t in trials:
        a = t["answers"].get(qid)
        v = _num(a.get("value")) if a else None
        if v is not None:
            vals[t["group"]].append(v)
    return {g: (round(mean(vals[g]), 2) if vals[g] else None) for g in GROUP_ORDER}


def _members(trials: list[dict], group: str, qid: str) -> list[dict]:
    return [t for t in trials if t["group"] == group and qid in t["answers"]]


def _hard_share(trials: list[dict], qid: str) -> dict[str, float | None]:
    """질문별 이유 문항에서 '특별히 어렵지 않았다' 외의 이유를 고른 비율(%)."""
    out = {}
    for g in GROUP_ORDER:
        members = _members(trials, g, f"{qid}_reason")
        hard = [t for t in members if str(t["answers"][f"{qid}_reason"].get("value")) != "no_difficulty"]
        out[g] = round(100 * len(hard) / len(members)) if members else None
    return out


def _reasons(trials: list[dict], qid: str, group: str) -> list[tuple[str, list[dict]]]:
    """어려움의 이유별 응답자 (많은 순, '어렵지 않았음' 제외)."""
    by: dict[str, list[dict]] = defaultdict(list)
    for t in _members(trials, group, f"{qid}_reason"):
        key = str(t["answers"][f"{qid}_reason"].get("value"))
        if key != "no_difficulty":
            by[key].append(t)
    return sorted(by.items(), key=lambda kv: (-len(kv[1]), list(REASON_LABELS).index(kv[0])
                                              if kv[0] in REASON_LABELS else 99))


def _top_reasons(trials: list[dict], qid: str, group: str = LOW, k: int = 2) -> list[tuple[str, int, dict]]:
    """대표 이유 k개: 많이 고른 이유부터 이유마다 한 명의 서술, 이유가 하나뿐이면 같은 이유의 다른 서술."""
    ranked = _reasons(trials, qid, group)
    picks: list[tuple[str, int, dict]] = []
    for key, members in ranked:
        quoted = [t for t in members if (t["answers"][f"{qid}_reason"].get("rationale") or "").strip()]
        if quoted:
            picks.append((key, len(members), quoted[0]))
        if len(picks) == k:
            return picks
    for key, members in ranked:
        quoted = [t for t in members if (t["answers"][f"{qid}_reason"].get("rationale") or "").strip()]
        for t in quoted[1:]:
            if len(picks) == k:
                return picks
            picks.append((key, len(members), t))
    return picks


def _analysis(fit: dict, hard: dict, top: list[tuple[str, int, dict]]) -> str:
    """질문 한 줄 분석 (수치에서 규칙으로 만든 문장)."""
    gap = _gap(fit)
    if gap is None:
        return "비교할 응답이 부족합니다."
    if gap <= -2 * GAP_ALERT:
        verdict = "저경력에게 특히 어려운 질문 → 재설계 우선 검토"
    elif gap <= -GAP_ALERT:
        verdict = "저경력에게 다소 어려운 질문"
    elif gap >= GAP_ALERT:
        verdict = "저경력이 오히려 더 쉽게 답한 질문"
    else:
        verdict = "경력에 따른 차이가 작은 질문"
    text = f"{verdict}. 답하기 쉬움 저경력 {_fmt(fit[LOW])}점, 고경력 {_fmt(fit[HIGH])}점 (차이 {gap:+})"
    if hard.get(LOW) is not None:
        text += f", 저경력 {hard[LOW]}%가 어려웠다고 답함"
    if top:
        text += f". 저경력의 주된 이유: {REASON_LABELS.get(top[0][0], top[0][0])}"
    return text + "."


def _readable(text: str) -> str:
    """페르소나가 쓴 문항 id(j1_q4, j1_q4_fit 등)를 보고서의 질문 번호(1-4)로 바꾼다."""
    return re.sub(r"(?<![A-Za-z0-9])j(\d+)_q(\d+)(?:_[a-z]+)?(?![A-Za-z0-9])", r"\1-\2", text)


def _is_korean(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return not letters or sum("가" <= c <= "힣" for c in letters) / len(letters) >= 0.5


def _non_korean(trials: list[dict]) -> int:
    count = 0
    for t in trials:
        for a in t["answers"].values():
            for text in (a.get("value"), a.get("rationale")):
                if isinstance(text, str) and len(text) > 20 and not _is_korean(text):
                    count += 1
    return count


def _fmt(v, suffix="") -> str:
    return "–" if v is None else f"{v}{suffix}"


def _gap(d: dict) -> float | None:
    if d.get(LOW) is None or d.get(HIGH) is None:
        return None
    return round(d[LOW] - d[HIGH], 2)


def write_csv(trials: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["job", "trial", "persona_id", "years_experience", "group", "gender", "school_level",
                    "work_region", "questionId", "value", "rationale"])
        for t in trials:
            for qid, a in t["answers"].items():
                v = a.get("value")
                w.writerow([t["job"], t["trial"], t["persona_id"], t["years"], t["group"], t["gender"],
                            t["school_level"], t["work_region"], qid,
                            json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v,
                            a.get("rationale") or ""])


def build_html(trials: list[dict]) -> str:
    e = html.escape
    counts = Counter(t["group"] for t in trials)
    stamped = any(t["work_region"] for t in trials)
    if stamped:
        genders = Counter(t["gender"] for t in trials)
        regions = Counter(t["work_region"] for t in trials)
        levels = "·".join(sorted({t["school_level"] for t in trials if t["school_level"]}))
        who = (f"한국어 모국어 교사 페르소나 · 학교급 {e(levels)} · 근무 지역 "
               + ", ".join(f"{e(k)} {v}" for k, v in sorted(regions.items()))
               + " · 성별 " + ", ".join(f"{e(k)} {v}" for k, v in sorted(genders.items())))
        caveat = ("페르소나에는 국가 속성이 없어 한국어가 모국어인 동아시아 교사 페르소나를 골랐고, 학교급과 근무 지역은 "
                  "표집 뒤에 덧붙인 속성입니다. 원래 페르소나 이야기(전공, 근무 기관 등)와 어긋날 수 있습니다.")
    else:
        who = "교사 페르소나(role_function = Teaching)"
        caveat = "페르소나에는 국가 속성이 없어 한국 교사로 한정되지 않았습니다."
    foreign = _non_korean(trials)
    if foreign:
        caveat += (f" <b>한국어로 답하지 않은 서술 {foreign}개</b>가 섞여 있어 '[한국어 아님]'으로 표시했습니다.")
    parts: list[str] = []
    parts.append(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>당교한책 경력별 시뮬레이션</title>
<style>
:root {{ --bg:#fbfaf7; --fg:#1f1d1a; --muted:#6b665e; --line:#e4dfd6; --low:#b4532a; --high:#2f5d8a; --warn:#fff3e6; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#191816; --fg:#ece8e1; --muted:#a39d93; --line:#34312c; --low:#e08a5f; --high:#7fa9d6; --warn:#3a2b1d; }} }}
body {{ background:var(--bg); color:var(--fg); font:16px/1.6 -apple-system, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif; margin:0; padding:24px 16px; }}
main {{ max-width:960px; margin:0 auto; }}
h1 {{ font-size:1.6rem; margin:0 0 .25rem; }} h3 {{ margin:1.6rem 0 .4rem; font-size:1.1rem; }} h2 {{ margin-top:2.5rem; border-bottom:1px solid var(--line); padding-bottom:.3rem; }}
.muted {{ color:var(--muted); }} .note {{ background:var(--warn); padding:12px 14px; border-radius:8px; }}
.scroll {{ overflow-x:auto; }} table {{ border-collapse:collapse; width:100%; font-size:.92rem; margin:.75rem 0; }}
th, td {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; }} td.n {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.neg {{ color:var(--low); font-weight:600; }} .low {{ color:var(--low); }} .high {{ color:var(--high); }}
details {{ margin:.4rem 0; }} summary {{ cursor:pointer; }} blockquote {{ margin:.4rem 0; padding-left:10px; border-left:3px solid var(--line); }}
</style></head><body><main>
<h1>「당교한책」 질문 세트 경력별 시뮬레이션 보고서</h1>
<p class="muted">MatrAIx 공식 Survey 파이프라인 · {who} ·
응답 수: 저경력(0~5년) {counts.get(LOW, 0)}명, 중간 경력(6~10년) {counts.get('중간 경력', 0)}명,
고경력(11년 이상) {counts.get(HIGH, 0)}명 (여정별 응답의 합)</p>
<p class="note">이 결과는 LLM이 가상 교사 페르소나를 연기해 만든 <b>시뮬레이션</b>입니다. {caveat} 질문 재설계의 <b>가설</b>을 세우는 데 쓰고, 실제 저경력 교사 파일럿으로
확인해야 합니다.</p>""")

    def q_label(n: int, k: int, title: str) -> str:
        return f"{n}-{k} {e(title)}"

    def quote(t: dict, text: str) -> str:
        flag = "" if _is_korean(text) else " <span class='neg'>[한국어 아님]</span>"
        return (f"<blockquote>{e(_readable(text))}{flag} <span class='muted'>({e(t['years'])}년"
                f"{' · ' + e(t['gender']) if t['gender'] else ''}"
                f"{' · ' + e(t['work_region']) if t['work_region'] else ''})</span></blockquote>")

    answered = {qid for t in trials for qid in t["answers"]}
    journeys = [j for j in JOURNEYS if any(q.startswith(f"j{j['n']}_") for q in answered)]

    rows = []
    for j in journeys:
        n = j["n"]
        for k, (kind, title, prompt) in enumerate(j["questions"], start=1):
            qid = f"j{n}_q{k}"
            fit = _mean_by_group(trials, f"{qid}_fit")
            hard = _hard_share(trials, qid)
            top = _top_reasons(trials, qid)
            rows.append({"n": n, "k": k, "kind": kind, "title": title, "prompt": prompt, "qid": qid,
                         "fit": fit, "hard": hard, "top": top, "gap": _gap(fit)})

    def neg(gap) -> str:
        return "neg" if gap is not None and gap <= -GAP_ALERT else ""

    # 1. 여정별 요약 + 질문별 분석
    parts.append(f"<h2>1. 여정별 요약</h2><p class='muted'>척도 1~{SCALE_MAX}점 평균. 차이 = 저경력 − 고경력. "
                 "'시기 적절성'은 '지금 나의 연차는 이 여정을 다루기에 적절한 시기다'에 대한 동의 정도. "
                 "'어려웠다'는 질문마다 '특별히 어렵지 않았다' 외의 이유를 고른 비율.</p>")
    for j in journeys:
        n = j["n"]
        m, b, s_ = (_mean_by_group(trials, f"j{n}_{k}") for k in ("meaning", "burden", "timing"))
        gap = _gap(s_)
        parts.append(f"<h3>{n}번째 여정 · {e(j['theme'])} <span class='muted'>({e(j['time_area'])})</span></h3>")
        parts.append("<div class='scroll'><table><tr><th>여정 평가</th><th>저경력</th><th>중간 경력</th><th>고경력</th>"
                     "<th>차이</th></tr>")
        for label, d in (("의미", m), ("심리적 부담", b), ("시기 적절성", s_)):
            g = _gap(d)
            parts.append(f"<tr><td>{label}</td><td class='n'>{_fmt(d[LOW])}</td><td class='n'>{_fmt(d['중간 경력'])}</td>"
                         f"<td class='n'>{_fmt(d[HIGH])}</td><td class='n {neg(g) if label != '심리적 부담' else ''}'>"
                         f"{_fmt(g)}</td></tr>")
        parts.append("</table></div>")
        parts.append("<div class='scroll'><table><tr><th>질문</th><th>답하기 쉬움 (저/중/고)</th><th>차이</th>"
                     "<th>어려웠다 (저/고)</th><th>분석</th></tr>")
        for r in (r for r in rows if r["n"] == n):
            parts.append(
                f"<tr><td>{r['n']}-{r['k']} <span class='muted'>{e(r['kind'])}</span><br><b>{e(r['title'])}</b><br>"
                f"<span class='muted'>{e(r['prompt'])}</span></td>"
                f"<td class='n'>{_fmt(r['fit'][LOW])} / {_fmt(r['fit']['중간 경력'])} / {_fmt(r['fit'][HIGH])}</td>"
                f"<td class='n {neg(r['gap'])}'>{_fmt(r['gap'])}</td>"
                f"<td class='n'>{_fmt(r['hard'][LOW], '%')} / {_fmt(r['hard'][HIGH], '%')}</td>"
                f"<td>{e(_analysis(r['fit'], r['hard'], r['top']))}</td></tr>")
        parts.append("</table></div>")

    # 2. 재설계 우선순위 + 대표 이유 2개
    ranked = sorted([r for r in rows if r["gap"] is not None], key=lambda r: r["gap"])
    parts.append("<h2>2. 재설계 우선순위 — 저경력 교사가 답하기 어려워한 질문</h2>"
                 f"<p class='muted'>'답하기 쉬움' 평균(1~{SCALE_MAX})이 고경력보다 많이 낮은 순. "
                 "'대표 이유'는 저경력이 많이 고른 이유부터 2개와 그 이유를 고른 페르소나의 서술.</p>")
    parts.append("<div class='scroll'><table><tr><th>여정·질문</th><th>질문</th><th>답하기 쉬움 (저/중/고)</th>"
                 "<th>차이</th><th>어려웠다 (저/고)</th><th>저경력의 대표 이유</th></tr>")
    for r in ranked:
        reasons = "".join(
            f"<p><b>{e(REASON_LABELS.get(key, key))}</b> <span class='muted'>({cnt}명)</span></p>"
            + quote(t, str(t["answers"][f"{r['qid']}_reason"].get("rationale")))
            for key, cnt, t in r["top"]) or "<span class='muted'>어려웠다는 응답 없음</span>"
        parts.append(
            f"<tr><td>{r['n']}-{r['k']}<br><span class='muted'>{e(r['kind'])}</span></td>"
            f"<td><b>{e(r['title'])}</b><br>{e(r['prompt'])}</td>"
            f"<td class='n'>{_fmt(r['fit'][LOW])} / {_fmt(r['fit']['중간 경력'])} / {_fmt(r['fit'][HIGH])}</td>"
            f"<td class='n {neg(r['gap'])}'>{_fmt(r['gap'])}</td>"
            f"<td class='n'>{_fmt(r['hard'][LOW], '%')} / {_fmt(r['hard'][HIGH], '%')}</td><td>{reasons}</td></tr>")
    parts.append("</table></div>")

    # 3. 어려움의 이유 — 이유별로 어떤 질문에서 골랐는지
    parts.append("<h2>3. 답하기 어려웠던 가장 큰 이유</h2><p class='muted'>질문마다 고른 '가장 큰 이유'를 이유별로 "
                 "모았습니다. 괄호 안은 그 질문에서 이 이유를 고른 사람 수.</p>")
    parts.append("<div class='scroll'><table><tr><th>이유</th><th>선택 수 (저/고)</th>"
                 "<th>저경력이 이 이유를 고른 질문</th><th>고경력이 이 이유를 고른 질문</th></tr>")
    for key, label in REASON_LABELS.items():
        totals, lists = {}, {}
        for g in (LOW, HIGH):
            hits = []
            for r in rows:
                cnt = sum(1 for t in _members(trials, g, f"{r['qid']}_reason")
                          if str(t["answers"][f"{r['qid']}_reason"].get("value")) == key)
                if cnt:
                    hits.append((cnt, r))
            totals[g] = sum(c for c, _ in hits)
            hits.sort(key=lambda h: (-h[0], h[1]["n"], h[1]["k"]))
            lists[g] = ", ".join(f"{q_label(r['n'], r['k'], r['title'])} ({c})" for c, r in hits) or "–"
        if key == "no_difficulty":
            lists = {LOW: "–", HIGH: "–"}
        parts.append(f"<tr><td>{e(label)}</td><td class='n'><span class='low'>{totals[LOW]}</span> / "
                     f"<span class='high'>{totals[HIGH]}</span></td><td>{lists[LOW]}</td><td>{lists[HIGH]}</td></tr>")
    parts.append("</table></div>")

    # 4. 저경력 페르소나의 목소리: 질문 수정 제안, 질문별 어려움의 이유
    low = [t for t in trials if t["group"] == LOW]
    parts.append("<h2>4. 저경력 페르소나의 목소리</h2><p class='muted'>여정별로 저경력(0~5년) 페르소나가 "
                 "제안한 질문 수정안과, 질문마다 답하기 어려웠던 이유.</p>")
    for j in journeys:
        n = j["n"]
        parts.append(f"<details><summary><b>{n}번째 여정</b> · {e(j['theme'])}</summary>")
        texts = [quote(t, str(t["answers"][f"j{n}_revise"].get("value"))) for t in low
                 if t["answers"].get(f"j{n}_revise", {}).get("value")]
        parts.append(f"<p><b>질문 수정 제안</b></p>{''.join(texts) or '<p class=muted>응답 없음</p>'}")
        parts.append("<p><b>어려움의 이유 (질문별)</b></p>")
        any_reason = False
        for r in (r for r in rows if r["n"] == n):
            items = []
            for key, members in _reasons(trials, r["qid"], LOW):
                for t in members:
                    text = (t["answers"][f"{r['qid']}_reason"].get("rationale") or "").strip()
                    if text:
                        items.append(f"<p class='muted'>{e(REASON_LABELS.get(key, key))}</p>" + quote(t, text))
            if items:
                any_reason = True
                parts.append(f"<p>▸ <b>{q_label(r['n'], r['k'], r['title'])}</b></p>{''.join(items)}")
        if not any_reason:
            parts.append("<p class=muted>어려웠다는 응답 없음</p>")
        parts.append("</details>")

    # 5. 다루고 싶은 이야기 (저경력)
    parts.append("<h2>5. 저경력 페르소나가 다루고 싶은 이야기</h2><p class='muted'>'지금의 나에게 이 여정에서 꼭 "
                 "다루고 싶은 이야기나 고민'에 대한 저경력(0~5년) 페르소나의 답.</p>")
    for j in journeys:
        n = j["n"]
        texts = [quote(t, str(t["answers"][f"j{n}_add"].get("value"))) for t in low
                 if t["answers"].get(f"j{n}_add", {}).get("value")]
        parts.append(f"<details><summary><b>{n}번째 여정</b> · {e(j['theme'])} "
                     f"<span class='muted'>({len(texts)}명)</span></summary>"
                     f"{''.join(texts) or '<p class=muted>응답 없음</p>'}</details>")

    # 6. 원 질문 응답 예시 (저경력 vs 고경력)
    parts.append("<h2>6. 원 질문 응답 비교</h2><p class='muted'>같은 질문에 저경력·고경력 페르소나가 실제로 "
                 "어떻게 답했는지 질문별로 2명씩 보여 줍니다.</p>")
    for r in rows:
        parts.append(f"<details><summary>{q_label(r['n'], r['k'], r['title'])}</summary>")
        for g, cls in ((LOW, "low"), (HIGH, "high")):
            for t in _members(trials, g, r["qid"])[:2]:
                parts.append(f"<p class='{cls}'>{g}</p>" + quote(t, str(t["answers"][r["qid"]].get("value") or "")))
        parts.append("</details>")

    parts.append("</main></body></html>")
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs", type=Path, nargs="+", required=True, help="MatrAIx jobs/<job> 폴더들")
    parser.add_argument("--out", type=Path, default=Path("report"))
    args = parser.parse_args()

    trials = load_trials(args.jobs)
    if not trials:
        raise SystemExit("survey_result.json이 있는 trial을 찾지 못했습니다.")
    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(trials, args.out / "answers.csv")
    (args.out / "report.html").write_text(build_html(trials), encoding="utf-8")
    print(f"trials={len(trials)} → {args.out / 'report.html'}, {args.out / 'answers.csv'}")


if __name__ == "__main__":
    main()
