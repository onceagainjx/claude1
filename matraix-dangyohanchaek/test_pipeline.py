"""생성 과제와 보고서 스크립트 점검 (API 호출 없음).

    python3 -m pytest test_pipeline.py

보고서 점검에는 MatrAIx 산출물 형식을 흉내 낸 가짜 trial을 쓴다. 실제 결과가 아니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import build_tasks
import report
from journeys import JOURNEYS


def test_questionnaire_covers_every_original_question():
    for j in JOURNEYS:
        q = build_tasks.build_questionnaire(j)
        ids = [x["id"] for x in q["questions"]]
        assert len(ids) == len(set(ids))
        for k in range(1, len(j["questions"]) + 1):
            assert f"j{j['n']}_q{k}" in ids and f"j{j['n']}_q{k}_fit" in ids
        hard = next(x for x in q["questions"] if x["id"] == f"j{j['n']}_hard")
        assert len(hard["options"]) == len(j["questions"]) + 1
    assert sum(len(j["questions"]) for j in JOURNEYS) == 40


def _fake_trial(job: Path, name: str, years: str, fit: int, hard: list[str]) -> None:
    trial = job / name
    out = trial / "artifacts" / "app" / "output"
    out.mkdir(parents=True)
    (trial / "persona_meta.json").write_text(json.dumps({"persona_id": name, "dimensions": {"years_experience": years}}))
    answers = [{"questionId": "j2_q1", "value": f"{years}년 차 가짜 응답"},
               {"questionId": "j2_q1_fit", "value": fit},
               {"questionId": "j2_meaning", "value": 4},
               {"questionId": "j2_timing", "value": fit},
               {"questionId": "j2_hard", "value": hard},
               {"questionId": "j2_hard_reason", "value": "lack_experience", "rationale": "가짜 이유"},
               {"questionId": "j2_revise", "value": "가짜 수정안"}]
    (out / "survey_result.json").write_text(json.dumps({"answers": answers, "trajectory": [{}]}, ensure_ascii=False))


def test_report_compares_low_and_high_career(tmp_path: Path):
    job = tmp_path / "dk-j2"
    _fake_trial(job, "t1", "0-2", 2, ["j2_q1"])
    _fake_trial(job, "t2", "3-5", 1, ["j2_q1"])
    _fake_trial(job, "t3", "20+", 5, ["none"])
    (job / "result.json").write_text("{}")  # 디렉터리가 아닌 파일은 무시

    trials = report.load_trials([job])
    assert [t["group"] for t in trials] == ["저경력", "저경력", "고경력"]
    assert report._mean_by_group(trials, "j2_q1_fit") == {"저경력": 1.5, "중간 경력": None, "고경력": 5.0}
    assert report._share_choosing(trials, "j2_hard", "j2_q1")["저경력"] == 100
    assert report._share_choosing(trials, "j2_hard", "j2_q1")["고경력"] == 0

    page = report.build_html(trials)
    assert "-3.5" in page and "가짜 수정안" in page and "가짜 이유" in page
    report.write_csv(trials, tmp_path / "answers.csv")
    assert (tmp_path / "answers.csv").read_text(encoding="utf-8-sig").count("\n") == 22
