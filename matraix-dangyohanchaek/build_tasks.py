"""journeys.py의 질문 세트를 MatrAIx 공식 Survey 과제 폴더로 변환한다.

    python3 build_tasks.py --matraix /path/to/MatrAIx-Persona-8B

생성 위치: <matraix>/application/tasks/dangyohanchaek-<여정 slug>/
검증기(tests/)는 MatrAIx 공식 예제 과제(example-survey_product-feedback)의 것을 그대로 복사한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from journeys import JOURNEYS, PROGRAM_PREMISE

TASK_PREFIX = "dangyohanchaek-"
REFERENCE_TASK = "example-survey_product-feedback"

# 비교할 경력 구간 (MatrAIx Persona 1M의 years_experience 값)
EXPERIENCE_BINS = ["0-2", "3-5", "6-10", "11-20", "20+"]
DEFAULT_SAMPLE_SIZE = 50  # 구간당 10명

KIND_IDS = {"여는": "opening", "잇는": "linking", "깊은": "deep"}

INSTRUCTION = """\
# 「당교한책」 {n}번째 여정 질문 응답

교사 교육철학 프로그램 「당교한책」의 {n}번째 여정 질문에 답하고, 각 질문이 지금의 나에게 얼마나
맞는지 평가합니다.

## 답하는 방법

- 먼저 과제 안내(context)를 읽으세요.
- 모든 질문에 답하세요.
- 서술형 질문은 한국어로, 1인칭 시점에서 2~4문장으로 답하세요. 실제로 겪은 일처럼 구체적인 장면,
  사람, 감정을 담아 주세요. 경험이 없거나 떠오르지 않으면 지어내지 말고 그렇다고 솔직하게 쓰세요.
- 척도 질문은 제시된 범위의 정수 하나로 답하세요.
- 선택형 질문은 제시된 선택지 id를 사용하세요. "(해당하는 것 모두)" 질문은 해당하는 id를 모두 고르세요.
"""

CONTEXT = """\
# 「당교한책」 프로그램 안내

이 설문은 학교에서 학생을 가르치는 교사를 대상으로 합니다. 응답자는 현재 교사로 일하고 있으며,
자신의 경력 연수를 교직 경력으로 보고 답합니다.

{premise}
# 이번 회차: {n}번째 여정 ({time_area})

**주제 질문: {theme}**

이 여정에서는 다음을 목표로 합니다.

{goals}
{opening}
질문은 여는 질문 → 잇는 질문 → 깊은 질문 순서로 이어집니다. 실제 프로그램에서는 이 질문들로 소그룹
대화를 나눈 뒤 글을 씁니다. 이 설문에서는 각 질문에 직접 답한 뒤, 그 질문이 지금의 나에게 얼마나
답하기 쉬웠는지 평가하고, 마지막에 이 여정 전체에 대한 의견을 남깁니다.
"""

FIT_PROMPT = "방금 질문(「{title}」)에 나의 실제 경험으로 답하기가 얼마나 쉬웠나요? (1 = 매우 어려웠다, 5 = 매우 쉬웠다)"


def build_questionnaire(j: dict) -> dict:
    n = j["n"]
    questions: list[dict] = []
    hard_options: list[dict] = []
    for k, (kind, title, prompt) in enumerate(j["questions"], start=1):
        qid = f"j{n}_q{k}"
        questions.append({
            "id": qid,
            "prompt": f"[{kind} 질문 · {title}] {prompt}",
            "type": "free_text",
            "construct": f"reflection_{KIND_IDS[kind]}",
            "required": True,
        })
        questions.append({
            "id": f"{qid}_fit",
            "prompt": FIT_PROMPT.format(title=title),
            "type": "likert",
            "construct": "answerability",
            "required": True,
            "minValue": 1,
            "maxValue": 5,
        })
        hard_options.append({"id": qid, "label": f"{title}: {prompt[:40]}…"})
    hard_options.append({"id": "none", "label": "어려운 질문이 없었다"})

    questions += [
        {
            "id": f"j{n}_meaning",
            "prompt": "이 여정의 질문들은 나만의 교육철학을 찾는 데 의미가 있었다. (1 = 전혀 그렇지 않다, 5 = 매우 그렇다)",
            "type": "likert",
            "construct": "meaningfulness",
            "required": True,
            "minValue": 1,
            "maxValue": 5,
        },
        {
            "id": f"j{n}_burden",
            "prompt": "이 여정의 질문들에 답하는 것이 심리적으로 부담스러웠다. (1 = 전혀 그렇지 않다, 5 = 매우 그렇다)",
            "type": "likert",
            "construct": "burden",
            "required": True,
            "minValue": 1,
            "maxValue": 5,
        },
        {
            "id": f"j{n}_timing",
            "prompt": "지금 나의 교직 연차는 이 여정의 질문을 다루기에 적절한 시기다. (1 = 전혀 그렇지 않다, 5 = 매우 그렇다)",
            "type": "likert",
            "construct": "career_stage_fit",
            "required": True,
            "minValue": 1,
            "maxValue": 5,
        },
        {
            "id": f"j{n}_hard",
            "prompt": "지금 나의 교직 연차에서 답하기 어려웠던 질문을 모두 고르세요. (해당하는 것 모두)",
            "type": "multi_choice",
            "construct": "hard_questions",
            "required": True,
            "options": hard_options,
        },
        {
            "id": f"j{n}_hard_reason",
            "prompt": "답하기 어려웠던 가장 큰 이유는 무엇인가요?",
            "type": "single_choice",
            "construct": "difficulty_reason",
            "required": True,
            "askRationale": True,
            "options": [
                {"id": "lack_experience", "label": "아직 그만큼의 교직 경험이 쌓이지 않았다"},
                {"id": "premise_mismatch", "label": "질문이 전제하는 상황(오랜 경력, 졸업생, 전환점 등)이 나와 맞지 않는다"},
                {"id": "too_personal", "label": "너무 사적이거나 감정적으로 무겁다"},
                {"id": "too_abstract", "label": "질문이 추상적이라 무엇을 말해야 할지 모르겠다"},
                {"id": "survival_first", "label": "당장 학교생활을 버티는 문제가 더 급해서 성찰할 여유가 없다"},
                {"id": "no_difficulty", "label": "특별히 어렵지 않았다"},
            ],
        },
        {
            "id": f"j{n}_revise",
            "prompt": "교직 5년 차 이하 교사에게 이 여정을 진행한다면, 어떤 질문을 어떻게 바꾸면 좋을까요? 바꿀 질문과 새 질문 문구를 써 주세요.",
            "type": "free_text",
            "construct": "revision_suggestion",
            "required": True,
        },
        {
            "id": f"j{n}_add",
            "prompt": "지금의 나에게 이 여정에서 꼭 다루고 싶은 이야기나 고민이 있다면 무엇인가요?",
            "type": "free_text",
            "construct": "unmet_need",
            "required": True,
        },
    ]
    return {
        "schemaVersion": "1.0",
        "id": f"dangyohanchaek_journey{n}_v1",
        "title": f"「당교한책」 {n}번째 여정: {j['theme']}",
        "description": f"{j['time_area']} · 원 질문 응답 + 질문별 답하기 쉬움 평가 + 여정 전체 평가",
        "questions": questions,
    }


def build_task(j: dict, matraix: Path) -> Path:
    import yaml  # MatrAIx의 uv 환경에 포함되어 있다

    n = j["n"]
    task_dir = matraix / "application" / "tasks" / f"{TASK_PREFIX}{j['slug']}"
    if task_dir.exists():
        shutil.rmtree(task_dir)
    (task_dir / "input").mkdir(parents=True)

    shutil.copytree(matraix / "application" / "tasks" / REFERENCE_TASK / "tests", task_dir / "tests")

    (task_dir / "task.toml").write_text(
        f'''version = "1.0"
artifacts = [ "/app/output",]

[task]
name = "application/dangyohanchaek-journey{n}"

[metadata]
difficulty = "medium"
type = "survey"
domain = "education"
tags = [ "teacher", "educational philosophy", "narrative inquiry", "career stage", "dangyohanchaek",]

[verifier]
timeout_sec = 120.0

[agent]
timeout_sec = 900.0

[environment]
definition = "application/shared-survey-form"
build_timeout_sec = 600.0
cpus = 1
memory_mb = 2048
storage_mb = 10240
gpus = 0
''',
        encoding="utf-8",
    )
    (task_dir / "instruction.md").write_text(INSTRUCTION.format(n=n), encoding="utf-8")

    goals = "\n".join(f"- {g}" for g in j["goals"])
    opening = f"\n**여는글:** {j['opening_text']}\n" if j["opening_text"] else ""
    (task_dir / "input" / "context.md").write_text(
        CONTEXT.format(premise=PROGRAM_PREMISE, n=n, time_area=j["time_area"],
                       theme=j["theme"], goals=goals, opening=opening),
        encoding="utf-8",
    )
    (task_dir / "input" / "questionnaire.yaml").write_text(
        yaml.safe_dump(build_questionnaire(j), allow_unicode=True, sort_keys=False, width=1000),
        encoding="utf-8",
    )

    strategy = {
        "schemaVersion": "1.0",
        "sources": [],
        "dimensionFilters": {
            "role_function": ["Teaching"],
            "years_experience": EXPERIENCE_BINS,
        },
        "sampling": {
            "mode": "stratified",
            "fields": ["years_experience"],
            "allocation": "equalTotal",
            "sampleSize": DEFAULT_SAMPLE_SIZE,
        },
    }
    (task_dir / "persona_strategy.json").write_text(
        json.dumps(strategy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    reporting = {
        "schemaVersion": "1.0",
        "contextRules": [
            {
                "match": {"contextType": "question_response"},
                "distributions": [{
                    "id": "question_response.response_by_experience",
                    "facetKey": "response",
                    "title": "경력 구간별 응답 분포",
                    "groupByPersonaDimensions": ["years_experience"],
                }],
                "summaryAnalyses": [{
                    "id": "question.reason_by_response",
                    "targetFacetKey": "reason",
                    "groupByFacetKey": "response",
                    "groupByMode": "categorical",
                    "summaryKind": "llm_bucket_summary",
                }],
            },
            {
                "match": {"contextType": "trial_summary"},
                "distributions": [{
                    "id": "trial_summary.mean_numeric_by_experience",
                    "facetKey": "mean_numeric_answer",
                    "title": "경력 구간별 평균 척도 점수",
                    "groupByPersonaDimensions": ["years_experience"],
                }],
            },
        ],
    }
    (task_dir / "reporting.json").write_text(
        json.dumps(reporting, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    (task_dir / "README.md").write_text(
        f"# 「당교한책」 {n}번째 여정 ({j['time_area']})\n\n"
        f"주제 질문: {j['theme']}\n\n"
        "교사 페르소나(role_function=Teaching)를 경력 구간(years_experience)별로 같은 수만큼 뽑아, "
        "원 질문에 답하게 하고 질문별 답하기 쉬움·여정 의미·부담·시기 적절성을 평가하게 한다.\n"
        "이 폴더는 matraix-dangyohanchaek/build_tasks.py가 생성한다. 직접 고치지 말고 journeys.py를 고친다.\n",
        encoding="utf-8",
    )
    return task_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--matraix", type=Path, required=True, help="MatrAIx-Persona-8B 저장소 경로")
    args = parser.parse_args()
    for j in JOURNEYS:
        print(build_task(j, args.matraix.resolve()))


if __name__ == "__main__":
    main()
