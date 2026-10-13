"""생성 과제와 보고서 스크립트 점검 (API 호출 없음).

    python3 -m pytest test_pipeline.py

보고서 점검에는 MatrAIx 산출물 형식을 흉내 낸 가짜 trial을 쓴다. 실제 결과가 아니다.
"""

from __future__ import annotations

import json
from collections import Counter
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
            qid = f"j{j['n']}_q{k}"
            assert qid in ids and f"{qid}_fit" in ids and f"{qid}_reason" in ids
        for x in q["questions"]:
            if x["type"] == "likert":
                assert (x["minValue"], x["maxValue"]) == (1, 10)
            if x["id"].endswith("_reason"):
                assert x["askRationale"] and [o["id"] for o in x["options"]] == list(report.REASON_LABELS)
    assert sum(len(j["questions"]) for j in JOURNEYS) == 40


def _fake_trial(job: Path, name: str, years: str, fit: int, reason: str, why: str = "가짜 이유",
                dims: dict | None = None) -> None:
    trial = job / name
    out = trial / "artifacts" / "app" / "output"
    out.mkdir(parents=True)
    meta = {"persona_id": name, "dimensions": {"years_experience": years, **(dims or {})}}
    (trial / "persona_meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    answers = [{"questionId": "j2_q1", "value": f"{years}년 차 가짜 응답"},
               {"questionId": "j2_q1_fit", "value": fit},
               {"questionId": "j2_q1_reason", "value": reason, "rationale": why},
               {"questionId": "j2_meaning", "value": 8},
               {"questionId": "j2_timing", "value": fit},
               {"questionId": "j2_revise", "value": "가짜 수정안"},
               {"questionId": "j2_add", "value": "가짜 다루고 싶은 이야기"}]
    (out / "survey_result.json").write_text(json.dumps({"answers": answers, "trajectory": [{}]}, ensure_ascii=False))


def test_report_compares_low_and_high_career(tmp_path: Path):
    job = tmp_path / "dk-j2"
    _fake_trial(job, "t1", "0-2", 2, "lack_experience", "경험이 아직 부족해서 떠오르는 장면이 없다")
    _fake_trial(job, "t2", "3-5", 3, "premise_mismatch", "졸업생이 아직 없어서 전제가 맞지 않는다")
    _fake_trial(job, "t3", "3-5", 1, "lack_experience", "I have no such memories yet")
    _fake_trial(job, "t4", "20+", 9, "no_difficulty", "쉬웠다")
    (job / "result.json").write_text("{}")  # 디렉터리가 아닌 파일은 무시

    trials = report.load_trials([job])
    assert [t["group"] for t in trials] == ["저경력", "저경력", "저경력", "고경력"]
    assert report._mean_by_group(trials, "j2_q1_fit") == {"저경력": 2.0, "중간 경력": None, "고경력": 9.0}
    assert report._hard_share(trials, "j2_q1") == {"저경력": 100, "중간 경력": None, "고경력": 0}
    top = report._top_reasons(trials, "j2_q1")
    assert [(key, cnt) for key, cnt, _ in top] == [("lack_experience", 2), ("premise_mismatch", 1)]

    page = report.build_html(trials)
    assert "-7.0" in page and "재설계 우선 검토" in page
    assert "졸업생이 아직 없어서" in page and "가짜 수정안" in page
    assert "5. 저경력 페르소나가 다루고 싶은 이야기" in page and "가짜 다루고 싶은 이야기" in page
    assert "2-1 " in page and "(2)" in page  # 3장: 이유별로 어떤 질문에서 몇 명이 골랐는지
    assert "[한국어 아님]" in page and "한국어로 답하지 않은 서술 1개" in page
    assert "2번째 여정" in page and "1번째 여정" not in page  # 답이 있는 여정만
    assert report._readable("j1_q4(「경험을 통한 배움」)과 j1_q6_fit, j1_q5의") == "1-4(「경험을 통한 배움」)과 1-6, 1-5의"
    report.write_csv(trials, tmp_path / "answers.csv")
    assert (tmp_path / "answers.csv").read_text(encoding="utf-8-sig").count("\n") == 1 + 4 * 7


def test_pool_split_and_stamp():
    import build_pool

    assert build_pool.split_by_gender(10) == {"Man": 3, "Woman": 7}
    assert build_pool.allocate(10, {"Man": 9, "Woman": 4}) == {"Man": 6, "Woman": 4}
    assert build_pool.allocate(10, {"Man": 50, "Woman": 50}) == {"Man": 3, "Woman": 7}
    assert build_pool.excluded({"dimensions": {"company_size": "Solo / freelance"}})
    assert not build_pool.excluded({"dimensions": {}})
    rows = [{"persona_id": f"p{i}", "dimensions": {"years_experience": y, "gender_identity": g}}
            for i, (y, g) in enumerate([(y, g) for y in ["0-2", "20+"] for g in ["Man"] * 3 + ["Woman"] * 7])]
    build_pool.stamp(rows, seed=1)
    assert {r["dimensions"]["school_level"] for r in rows} == {"초등학교"}
    assert Counter(r["dimensions"]["work_region"] for r in rows) == {"서울": 10, "경기": 10}
    assert len({r["display_name"] for r in rows}) == len(rows)


def test_report_shows_stamped_attributes(tmp_path: Path):
    job = tmp_path / "dk-j2"
    _fake_trial(job, "t1", "0-2", 2, "lack_experience",
                dims={"gender_identity": "Woman", "school_level": "초등학교", "work_region": "경기"})

    trials = report.load_trials([job])
    assert (trials[0]["gender"], trials[0]["work_region"]) == ("여", "경기")
    assert "근무 지역 경기 1" in report.build_html(trials)
