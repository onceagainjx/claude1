"""MatrAIx 실행 결과(jobs/<job>/)를 읽어 경력 구간별 비교 보고서를 만든다.

    python3 report.py --jobs <matraix>/jobs/dk-j1 <matraix>/jobs/dk-j2 ... --out report/

출력:
    report/report.html     — 경력 집단 비교 보고서 (연구소 공유용)
    report/answers.csv     — 모든 응답 원자료 (trial × 문항)

MatrAIx 공식 산출물만 읽는다:
    <trial>/persona_meta.json            — 페르소나 속성(years_experience 등)
    <trial>/**/survey_result.json        — answers[]: questionId, value, rationale
"""

from __future__ import annotations

import argparse
import csv
import html
import json
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


def _share_choosing(trials: list[dict], qid: str, option: str) -> dict[str, float | None]:
    out = {}
    for g in GROUP_ORDER:
        members = [t for t in trials if t["group"] == g and qid in t["answers"]]
        if not members:
            out[g] = None
            continue
        hits = 0
        for t in members:
            v = t["answers"][qid].get("value")
            chosen = v if isinstance(v, list) else [v]
            hits += option in [str(c) for c in chosen]
        out[g] = round(100 * hits / len(members))
    return out


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
    parts: list[str] = []
    parts.append(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>당교한책 경력별 시뮬레이션</title>
<style>
:root {{ --bg:#fbfaf7; --fg:#1f1d1a; --muted:#6b665e; --line:#e4dfd6; --low:#b4532a; --high:#2f5d8a; --warn:#fff3e6; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#191816; --fg:#ece8e1; --muted:#a39d93; --line:#34312c; --low:#e08a5f; --high:#7fa9d6; --warn:#3a2b1d; }} }}
body {{ background:var(--bg); color:var(--fg); font:16px/1.6 -apple-system, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif; margin:0; padding:24px 16px; }}
main {{ max-width:960px; margin:0 auto; }}
h1 {{ font-size:1.6rem; margin:0 0 .25rem; }} h2 {{ margin-top:2.5rem; border-bottom:1px solid var(--line); padding-bottom:.3rem; }}
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

    # 1. 여정 요약
    parts.append("<h2>1. 여정별 요약</h2><p class='muted'>척도 1~5 평균. 차이 = 저경력 − 고경력. "
                 "'시기 적절성'은 '지금 나의 연차는 이 여정을 다루기에 적절한 시기다'에 대한 동의 정도.</p>")
    parts.append("<div class='scroll'><table><tr><th>여정</th><th>주제 질문</th>"
                 "<th>의미 (저/고)</th><th>부담 (저/고)</th><th>시기 적절성 (저/고)</th><th>시기 차이</th></tr>")
    for j in JOURNEYS:
        n = j["n"]
        m, b, s = (_mean_by_group(trials, f"j{n}_{k}") for k in ("meaning", "burden", "timing"))
        gap = _gap(s)
        parts.append(
            f"<tr><td>{n}</td><td>{e(j['theme'])}<br><span class='muted'>{e(j['time_area'])}</span></td>"
            f"<td class='n'>{_fmt(m[LOW])} / {_fmt(m[HIGH])}</td><td class='n'>{_fmt(b[LOW])} / {_fmt(b[HIGH])}</td>"
            f"<td class='n'>{_fmt(s[LOW])} / {_fmt(s[HIGH])}</td>"
            f"<td class='n {'neg' if gap is not None and gap <= -0.5 else ''}'>{_fmt(gap)}</td></tr>")
    parts.append("</table></div>")

    # 2. 재설계 우선순위: 저경력이 답하기 가장 어려워한 질문
    rows = []
    for j in JOURNEYS:
        n = j["n"]
        for k, (kind, title, prompt) in enumerate(j["questions"], start=1):
            qid = f"j{n}_q{k}"
            fit = _mean_by_group(trials, f"{qid}_fit")
            hard = _share_choosing(trials, f"j{n}_hard", qid)
            rows.append((n, k, kind, title, prompt, fit, hard, _gap(fit)))
    ranked = sorted([r for r in rows if r[7] is not None], key=lambda r: r[7])
    parts.append("<h2>2. 재설계 우선순위 — 저경력 교사가 답하기 어려워한 질문</h2>"
                 "<p class='muted'>'답하기 쉬움' 평균(1~5)이 고경력보다 많이 낮은 순. "
                 "'어렵다고 고른 비율'은 여정 끝에서 이 질문을 어려웠다고 고른 응답자 비율.</p>")
    parts.append("<div class='scroll'><table><tr><th>여정·질문</th><th>질문</th><th>답하기 쉬움 (저/중/고)</th>"
                 "<th>차이</th><th>어렵다고 고른 비율 (저/고)</th></tr>")
    for n, k, kind, title, prompt, fit, hard, gap in ranked:
        parts.append(
            f"<tr><td>{n}-{k}<br><span class='muted'>{e(kind)}</span></td><td><b>{e(title)}</b><br>{e(prompt)}</td>"
            f"<td class='n'>{_fmt(fit[LOW])} / {_fmt(fit['중간 경력'])} / {_fmt(fit[HIGH])}</td>"
            f"<td class='n {'neg' if gap <= -0.5 else ''}'>{_fmt(gap)}</td>"
            f"<td class='n'>{_fmt(hard[LOW], '%')} / {_fmt(hard[HIGH], '%')}</td></tr>")
    parts.append("</table></div>")

    # 3. 어려움의 이유
    parts.append("<h2>3. 답하기 어려웠던 가장 큰 이유</h2><div class='scroll'><table><tr><th>여정</th>")
    parts += [f"<th>{e(v)}<br><span class='muted'>저 / 고</span></th>" for v in REASON_LABELS.values()]
    parts.append("</tr>")
    for j in JOURNEYS:
        n = j["n"]
        cells = []
        for key in REASON_LABELS:
            share = _share_choosing(trials, f"j{n}_hard_reason", key)
            cells.append(f"<td class='n'><span class='low'>{_fmt(share[LOW], '%')}</span> / "
                         f"<span class='high'>{_fmt(share[HIGH], '%')}</span></td>")
        parts.append(f"<tr><td>{n}</td>{''.join(cells)}</tr>")
    parts.append("</table></div>")

    # 4. 저경력 페르소나의 목소리
    parts.append("<h2>4. 저경력 페르소나의 목소리</h2><p class='muted'>여정별로 저경력(0~5년) 페르소나가 "
                 "제안한 질문 수정안과 다루고 싶은 이야기, 어려움의 이유.</p>")
    for j in JOURNEYS:
        n = j["n"]
        low = [t for t in trials if t["group"] == LOW]
        parts.append(f"<details><summary><b>{n}번째 여정</b> · {e(j['theme'])}</summary>")
        for qid, label in ((f"j{n}_revise", "질문 수정 제안"), (f"j{n}_add", "다루고 싶은 이야기"),
                           (f"j{n}_hard_reason", "어려움의 이유")):
            texts = []
            for t in low:
                a = t["answers"].get(qid)
                if not a:
                    continue
                text = a.get("rationale") if qid.endswith("hard_reason") else a.get("value")
                if text:
                    texts.append(f"<blockquote>{e(str(text))} <span class='muted'>({e(t['years'])}년)</span></blockquote>")
            parts.append(f"<p><b>{label}</b></p>{''.join(texts) or '<p class=muted>응답 없음</p>'}")
        parts.append("</details>")

    # 5. 원 질문 응답 예시 (저경력 vs 고경력)
    parts.append("<h2>5. 원 질문 응답 비교</h2><p class='muted'>같은 질문에 저경력·고경력 페르소나가 실제로 "
                 "어떻게 답했는지 질문별로 2명씩 보여 줍니다.</p>")
    for n, k, kind, title, prompt, fit, hard, gap in rows:
        qid = f"j{n}_q{k}"
        parts.append(f"<details><summary>{n}-{k} {e(title)}</summary>")
        for g, cls in ((LOW, "low"), (HIGH, "high")):
            picked = [t for t in trials if t["group"] == g and qid in t["answers"]][:2]
            for t in picked:
                parts.append(f"<blockquote><span class='{cls}'>{g} · {e(t['years'])}년</span><br>"
                             f"{e(str(t['answers'][qid].get('value') or ''))}</blockquote>")
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
