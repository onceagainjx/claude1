"""문항 묶음, 과제 생성, 보고서 점검 (API 호출 없음).

    python3 -m pytest test_pipeline.py

보고서 점검에는 MatrAIx 산출물 형식을 흉내 낸 가짜 trial을 쓴다. 실제 결과가 아니다.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import build_tasks
import report
from child_profiles import build_profiles, system_prompt
from mi_items import FINAL_PER_FACET, INTEL_IDS, ITEMS, NAME, ORIGINAL, REVISED


def test_item_pool_is_balanced():
    assert len(ITEMS) == 192
    assert set(Counter((x["intel"], x["facet"]) for x in ITEMS).values()) == {12}
    assert sorted(x["no"] for x in ITEMS) == list(range(1, 193))
    assert len({x["text"] for x in ITEMS}) == 192
    # 기존 80문항은 모두 원래 지능 열에 남는다
    assert sorted(n for n, *_ in REVISED) == sorted(ORIGINAL) == list(range(1, 81))
    for x in ITEMS:
        assert x["intel"] == INTEL_IDS[(x["no"] - 1) % 8]
        if x["orig_no"]:
            assert INTEL_IDS[(x["orig_no"] - 1) % 8] == x["intel"]


def test_questionnaires_cover_every_item_once():
    a1 = [x["id"] for b in range(1, 5) for _, x in build_tasks.a1_display(b)]
    a2 = [q["id"] for b in range(1, 5) for q in build_tasks.a2_questionnaire(b)["questions"] if q["id"].endswith("_fit")]
    assert sorted(a1) == sorted(q[:4] for q in a2) == sorted(x["id"] for x in ITEMS)
    for b in range(1, 5):
        assert set(Counter(x["intel"] for x in build_tasks.block_items(b)).values()) == {6}
        # A1에는 의도한 지능이 드러나면 안 된다: '의도' 표시도, 지능 순서로 돌아가는 후보 번호도 없어야 한다
        qs = build_tasks.a1_questionnaire(b)["questions"]
        assert not any("의도" in q["prompt"] for q in qs)
        assert not any(re.search(r"c\d{3}", q["id"]) for q in qs)
        shown = [x["intel"] for _, x in build_tasks.a1_display(b)]
        assert shown[:8] != [x["intel"] for x in build_tasks.block_items(b)][:8]
    child = build_tasks.child_questionnaire()["questions"]
    assert [q["id"] for q in child[:192]] == [x["id"] for x in ITEMS]


def test_child_profiles_do_not_leak_intelligence_names():
    profiles = build_profiles()
    assert len(profiles) == 96 and set(Counter(p["primary"] for p in profiles).values()) == {12}
    texts = {x["text"] for x in ITEMS}
    for p in profiles:
        prompt = system_prompt(p)
        assert not any(name in prompt for name in NAME.values())
        assert not any(t.removeprefix("나는 ").rstrip(".") in prompt for t in texts)
        assert len({p["primary"], *p["secondary"], *p["weak"]}) == 5


def _write_trial(job: Path, name: str, persona_id: str, answers: dict) -> None:
    out = job / name / "artifacts" / "app" / "output"
    out.mkdir(parents=True)
    (job / name / "persona_meta.json").write_text(json.dumps({"persona_id": persona_id}))
    payload = {"answers": [{"questionId": k, "value": v} for k, v in answers.items()], "trajectory": [{}]}
    (out / "survey_result.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_report_selects_128_and_recovers_planted_strengths(tmp_path: Path):
    bad = {x["id"] for x in ITEMS if x["k"] == 1}  # 지능마다 첫 문항은 전문가가 다른 지능으로 본다고 가정
    a1 = tmp_path / "a1"
    for b in range(1, 5):
        for r in range(3):
            ans = {}
            for d, x in build_tasks.a1_display(b):
                ans[f"a1_b{b}_{d:02d}_intel"] = INTEL_IDS[(INTEL_IDS.index(x["intel"]) + 1) % 8] if x["id"] in bad else x["intel"]
                ans[f"a1_b{b}_{d:02d}_facet"] = x["facet"]
            shown_c001 = next(d for d, x in build_tasks.a1_display(b) if x["id"] == "c001") if b == 1 else None
            ans[f"a1_b{b}_ambiguous"] = f"{shown_c001}번: 언어와 자성 사이" if shown_c001 else "없음"
            _write_trial(a1, f"b{b}r{r}", f"t{b}{r}", ans)
    a2 = tmp_path / "a2"
    for b in range(1, 5):
        ans = {}
        for x in build_tasks.block_items(b):
            ans[f"{x['id']}_fit"] = 4
            ans[f"{x['id']}_clarity"] = 2 if x["no"] == 187 else 4
        has_187 = any(x["no"] == 187 for x in build_tasks.block_items(b))
        ans[f"a2_b{b}_revise"] = "187: 전개도를 그림으로 바꾼다\n없는 번호 999: 무시" if has_187 else "없음"
        _write_trial(a2, f"b{b}", f"e{b}", ans)
    child = tmp_path / "b"
    for i, p in enumerate(build_profiles()[:40]):
        strong = {p["primary"], *p["secondary"]}
        ans = {x["id"]: (5 if x["intel"] == p["primary"] else 4 if x["intel"] in strong
                         else 1 if x["intel"] in p["weak"] else 3) - (1 if x["k"] % 5 == 0 and i % 2 else 0)
               for x in ITEMS}
        ans.update({"b_hard_words": "187: 전개도", "b_fatigue": "q145_192", "b_length": 4})
        _write_trial(child, f"k{i}", p["persona_id"], ans)
    (child / "result.json").write_text("{}")  # 디렉터리가 아닌 파일은 무시

    trials = report.load_trials([a1, a2, child])
    assert Counter(t["kind"] for t in trials) == {"A1": 12, "A2": 4, "B": 40}
    stats = report.item_stats(trials)
    assert stats["c001"]["agree"] == 0 and stats["c001"]["confused_with"] == "logical"
    assert stats["c001"]["ambiguous"] == ["언어와 자성 사이"] * 3
    assert stats["c187"]["suggestions"] == ["전개도를 그림으로 바꾼다"] and stats["c187"]["hard_share"] == 1
    assert stats["c009"]["icvi"] == 1.0 and stats["c009"]["agree"] == 1.0

    final = report.select(stats, FINAL_PER_FACET)
    assert len(final) == 128 and not ({x["id"] for x in final} & (bad | {"c187"}))
    ordered = report.order_checklist(final, stats)
    assert [x["final_no"] for x in ordered] == list(range(1, 129))
    assert all(x["intel"] == INTEL_IDS[(x["final_no"] - 1) % 8] for x in ordered)

    kids, scores = report.child_matrix(trials)
    rec = report.recovery(kids, scores, [x["id"] for x in final])
    assert rec["top1"] == 1.0 and rec["top3"] == 1.0

    out = tmp_path / "report"
    out.mkdir()
    report.write_csvs(stats, out)
    page = report.build_html(trials, stats)
    assert "최종 128문항" in page and "전개도를 그림으로 바꾼다" in page
    sheet = report.build_checklist_html(final)
    assert sheet.count("<li ") == 128 and "<th>128</th>" in sheet
    assert (out / "final_128.csv").read_text(encoding="utf-8-sig").count("\n") == 129


def test_report_runs_without_any_results(tmp_path: Path):
    stats = report.item_stats([])
    final = report.select(stats, FINAL_PER_FACET)
    assert len(final) == 128
    assert "B 결과 없음" in report.build_html([], stats)


def test_expert_panels_split_evenly_without_overlap():
    import build_panel

    rows = [{"persona_id": f"p{i:02d}",
             "dimensions": {"years_experience": y, "gender_identity": g}}
            for i, (y, g) in enumerate((y, g) for y in build_panel.PANEL_BINS
                                       for g in ["Man"] * 2 + ["Woman"] * 4)]
    a1, a2 = build_panel.split_panels(rows)
    assert len(a1) == len(a2) == 12
    assert not {r["persona_id"] for r in a1} & {r["persona_id"] for r in a2}
    for panel in (a1, a2):
        assert Counter(r["dimensions"]["years_experience"] for r in panel) == {y: 3 for y in build_panel.PANEL_BINS}
