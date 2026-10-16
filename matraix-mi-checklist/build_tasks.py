"""192문항 후보 묶음을 MatrAIx 공식 Survey 과제 폴더로 변환한다.

    python3 build_tasks.py --matraix /path/to/MatrAIx-Persona-8B

생성하는 과제 (모두 <matraix>/application/tasks/ 아래):
    mi-a1-classify-b1 ~ b4   A. 가상 전문가: 의도한 지능을 모르는 상태에서 지능·측면 고르기 (블록당 48문항)
    mi-a2-rate-b1 ~ b4       A. 가상 전문가: 의도한 지능을 알려 주고 적합성·이해 난이도 평가 (블록당 48문항)
    mi-b-child               B. 가상 학생: 192문항 전체에 1~5점 응답 + 모르는 낱말·지루함

A1과 A2를 나눈 이유: 같은 설문에서 의도한 지능을 먼저 보면 분류 응답이 그쪽으로 쏠린다.
검증기(tests/)는 MatrAIx 공식 예제 과제(example-survey_product-feedback)의 것을 그대로 복사한다.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path

from mi_items import FACETS, INTELLIGENCES, ITEMS, NAME

TASK_PREFIX = "mi-"
REFERENCE_TASK = "example-survey_product-feedback"
N_BLOCKS = 4
PANEL_SIZE = 12  # 블록당 전문가 수
PANEL_EXPERIENCE = ["3-5", "6-10", "11-20", "20+"]  # 구간당 3명

INTEL_GUIDE = "\n".join(f"- **{name}**: {desc}" for _, _, name, desc in INTELLIGENCES)


def block_of(item: dict) -> int:
    """지능 안 순번 k를 4블록에 고르게 나눈다 → 블록마다 지능별 6문항."""
    return (item["k"] - 1) % N_BLOCKS + 1


def block_items(b: int) -> list[dict]:
    return [x for x in ITEMS if block_of(x) == b]


def a1_display(b: int) -> list[tuple[int, dict]]:
    """A1에 보여 줄 (표시 번호, 문항). 후보 번호는 8개 지능 순서로 돌아가 번호만 봐도 지능을 알 수 있으므로,
    블록 안에서 순서를 섞고 1~48의 표시 번호를 새로 붙인다. 질문 id에도 후보 번호를 넣지 않는다."""
    items = block_items(b)
    random.Random(1000 + b).shuffle(items)
    return list(enumerate(items, start=1))


def a1_question_id(b: int, d: int, kind: str) -> str:
    return f"a1_b{b}_{d:02d}_{kind}"


# ---------------------------------------------------------------- A. 가상 전문가

EXPERT_INSTRUCTION = """\
# 초등 다중지능 체크리스트 문항 검토 ({part})

초등학교 5~6학년용 다중지능 간이 체크리스트의 후보 문항을 검토합니다.

## 답하는 방법

- 먼저 과제 안내(context)를 읽으세요.
- 모든 질문에 답하세요. 선택형은 제시된 선택지 id로, 척도형은 범위 안의 정수 하나로 답하세요.
- 서술형 질문은 한국어로 답하세요.
"""

EXPERT_CONTEXT = """\
# 과제 안내

이 설문의 응답자는 학교에서 학생을 가르치는 교사입니다. 자신의 교직 경험을 바탕으로, **초등학교 5~6학년
담임 교사의 관점**에서 문항을 판단해 주세요.

이 체크리스트는 학기 초 5~6학년 학생이 스스로 읽고 각 문장이 자기에게 얼마나 맞는지 5점 척도
(1 = 전혀 아니에요, 5 = 정말 그래요)로 답합니다. 결과는 학생의 강점 지능 상위 3개를 찾아 진로 활동
모둠을 짜는 데 씁니다. 진단 도구가 아니라 자기 이해와 진로 탐색을 돕는 도구입니다.

## 8개 지능

{guide}

## 두 가지 측면

- **흥미(좋아해요)**: 그 활동을 좋아하거나 자주 하는지 묻는 문항
- **능력(잘해요)**: 그 활동을 잘하는지 묻는 문항

{task}
"""

A1_TASK = """\
## 이번 과제: 문항 분류 (블록 {b}/4, {n}문항)

각 문항에 대해 두 가지를 고릅니다.

1. 이 문항이 **주로** 재는 지능 하나
2. 이 문항이 흥미 문항인지 능력 문항인지

문항을 만든 사람이 의도한 지능은 알려 주지 않습니다. 문장만 보고 판단하세요. 두 지능에 걸쳐 있다고 느껴도
가장 가까운 하나를 고르고, 마지막 서술형 질문에 그런 문항을 적어 주세요.
"""

A2_TASK = """\
## 이번 과제: 문항 평가 (블록 {b}/4, {n}문항)

각 문항에는 문항을 만든 사람이 의도한 지능과 측면이 함께 적혀 있습니다. 문항마다 두 가지를 평가합니다.

1. **적합성**: 이 문항이 의도한 지능을 재기에 적절한가 (1 = 적절하지 않다, 2 = 많이 고쳐야 한다,
   3 = 조금 고치면 적절하다, 4 = 매우 적절하다)
2. **이해 난이도**: 5~6학년 학생이 혼자 읽고 뜻을 바로 이해할 수 있는가 (1 = 대부분 이해하지 못한다,
   2 = 어려워하는 학생이 많다, 3 = 대부분 이해한다, 4 = 모두 쉽게 이해한다)

마지막에 고쳐야 할 문항의 번호와 수정안을 적어 주세요.
"""


def a1_questionnaire(b: int) -> dict:
    questions = []
    intel_options = [{"id": i, "label": f"{name}: {desc}"} for i, _, name, desc in INTELLIGENCES]
    facet_options = [{"id": f, "label": label} for f, label in FACETS.items()]
    for d, x in a1_display(b):
        questions += [
            {
                "id": a1_question_id(b, d, "intel"),
                "prompt": f"[{d}번] 「{x['text']}」 이 문항이 주로 재는 지능은 무엇인가요?",
                "type": "single_choice",
                "construct": "intelligence_classification",
                "required": True,
                "options": intel_options,
            },
            {
                "id": a1_question_id(b, d, "facet"),
                "prompt": f"[{d}번] 「{x['text']}」 이 문항은 흥미 문항인가요, 능력 문항인가요?",
                "type": "single_choice",
                "construct": "facet_classification",
                "required": True,
                "options": facet_options,
            },
        ]
    questions.append({
        "id": f"a1_b{b}_ambiguous",
        "prompt": "두 지능 이상에 걸쳐 있어 고르기 어려웠던 문항이 있다면 번호와 함께 어떤 지능 사이에서 "
                  "망설였는지 적어 주세요. 없으면 '없음'이라고 쓰세요.",
        "type": "free_text",
        "construct": "ambiguity_note",
        "required": True,
    })
    return {
        "schemaVersion": "1.0",
        "id": f"mi_a1_classify_b{b}_v2",
        "title": f"다중지능 체크리스트 문항 분류 (블록 {b}/4)",
        "description": "교사 페르소나가 의도한 지능을 모르는 상태에서 문항의 지능과 측면을 고른다",
        "questions": questions,
    }


def a2_questionnaire(b: int) -> dict:
    questions = []
    for x in block_items(b):
        head = f"[{x['no']}번 · 의도: {NAME[x['intel']]} / {FACETS[x['facet']]}] 「{x['text']}」"
        questions += [
            {
                "id": f"{x['id']}_fit",
                "prompt": f"{head} 적합성 (1 = 적절하지 않다 ~ 4 = 매우 적절하다)",
                "type": "likert",
                "construct": "content_validity",
                "required": True,
                "minValue": 1,
                "maxValue": 4,
            },
            {
                "id": f"{x['id']}_clarity",
                "prompt": f"{head} 5~6학년 이해 난이도 (1 = 대부분 이해하지 못한다 ~ 4 = 모두 쉽게 이해한다)",
                "type": "likert",
                "construct": "readability",
                "required": True,
                "minValue": 1,
                "maxValue": 4,
            },
        ]
    questions.append({
        "id": f"a2_b{b}_revise",
        "prompt": "고쳐야 할 문항이 있다면 '번호: 수정안' 형식으로 한 줄에 하나씩 적어 주세요. "
                  "5~6학년이 어려워할 낱말도 함께 적어 주세요. 없으면 '없음'이라고 쓰세요.",
        "type": "free_text",
        "construct": "revision_suggestion",
        "required": True,
    })
    return {
        "schemaVersion": "1.0",
        "id": f"mi_a2_rate_b{b}_v1",
        "title": f"다중지능 체크리스트 문항 평가 (블록 {b}/4)",
        "description": "교사 페르소나가 문항의 내용 적합성과 5~6학년 이해 난이도를 평가한다",
        "questions": questions,
    }


# ---------------------------------------------------------------- B. 가상 학생

CHILD_INSTRUCTION = """\
# 나를 알아보는 체크리스트

## 답하는 방법

- 먼저 안내(context)를 읽으세요.
- 문장마다 나에게 얼마나 맞는지 1~5 중 하나를 고르세요.
- 정답은 없어요. 친구나 선생님이 어떻게 생각할지가 아니라, 내가 생각하는 나를 솔직하게 고르면 돼요.
- 마지막 질문들까지 모두 답하세요. 선택형은 선택지 id로, 서술형은 한국어로 답하세요.
"""

CHILD_CONTEXT = """\
# 안내

새 학기를 맞아 나를 알아보는 체크리스트를 해 봐요. 모두 192문장이에요.
문장을 읽고 나에게 얼마나 맞는지 골라요.

| 점수 | 뜻 |
|---|---|
| 1 | 전혀 아니에요 |
| 2 | 별로 아니에요 |
| 3 | 보통이에요 |
| 4 | 대체로 그래요 |
| 5 | 정말 그래요 |

96번까지 하면 절반 온 거예요. 힘내요!
"""

FATIGUE_OPTIONS = [
    {"id": "none", "label": "끝까지 지루하지 않았어요"},
    {"id": "q1_48", "label": "1~48번쯤부터 지루했어요"},
    {"id": "q49_96", "label": "49~96번쯤부터 지루했어요"},
    {"id": "q97_144", "label": "97~144번쯤부터 지루했어요"},
    {"id": "q145_192", "label": "145~192번쯤부터 지루했어요"},
]


def child_questionnaire() -> dict:
    questions = [
        {
            "id": x["id"],
            "prompt": f"{x['no']}. {x['text']} (1 = 전혀 아니에요, 5 = 정말 그래요)",
            "type": "likert",
            "construct": f"{x['intel']}_{x['facet']}",
            "required": True,
            "minValue": 1,
            "maxValue": 5,
        }
        for x in ITEMS
    ]
    questions += [
        {
            "id": "b_hard_words",
            "prompt": "뜻을 잘 모르겠던 낱말이나 문장이 있었나요? '번호: 낱말' 형식으로 적어 주세요. "
                      "없으면 '없음'이라고 쓰세요.",
            "type": "free_text",
            "construct": "comprehension",
            "required": True,
        },
        {
            "id": "b_fatigue",
            "prompt": "몇 번쯤부터 지루하거나 대충 고르고 싶었나요?",
            "type": "single_choice",
            "construct": "fatigue",
            "required": True,
            "options": FATIGUE_OPTIONS,
        },
        {
            "id": "b_length",
            "prompt": "문장 수가 너무 많았다. (1 = 전혀 아니에요, 5 = 정말 그래요)",
            "type": "likert",
            "construct": "length_burden",
            "required": True,
            "minValue": 1,
            "maxValue": 5,
        },
    ]
    return {
        "schemaVersion": "1.0",
        "id": "mi_b_child_v1",
        "title": "나를 알아보는 체크리스트 (후보 192문항)",
        "description": "강점 지능을 심어 둔 가상 5~6학년 학생이 192문항 전체에 응답한다",
        "questions": questions,
    }


# ---------------------------------------------------------------- 과제 폴더

TASK_TOML = """\
version = "1.0"
artifacts = [ "/app/output",]

[task]
name = "application/{name}"

[metadata]
difficulty = "medium"
type = "survey"
domain = "education"
tags = [ "multiple intelligences", "elementary", "item review", "career education",]

[verifier]
timeout_sec = 120.0

[agent]
timeout_sec = {agent_timeout}

[environment]
definition = "application/shared-survey-form"
build_timeout_sec = 600.0
cpus = 1
memory_mb = 2048
storage_mb = 10240
gpus = 0
"""


def _expert_strategy() -> dict:
    return {
        "schemaVersion": "1.0",
        "sources": [],
        "dimensionFilters": {
            "role_function": ["Teaching"],
            "years_experience": PANEL_EXPERIENCE,
        },
        "sampling": {
            "mode": "stratified",
            "fields": ["years_experience"],
            "allocation": "equalTotal",
            "sampleSize": PANEL_SIZE,
        },
    }


def write_task(matraix: Path, name: str, *, instruction: str, context: str, questionnaire: dict,
               readme: str, strategy: dict | None, agent_timeout: float) -> Path:
    import yaml  # MatrAIx의 uv 환경에 포함되어 있다

    task_dir = matraix / "application" / "tasks" / name
    if task_dir.exists():
        shutil.rmtree(task_dir)
    (task_dir / "input").mkdir(parents=True)
    shutil.copytree(matraix / "application" / "tasks" / REFERENCE_TASK / "tests", task_dir / "tests")
    (task_dir / "task.toml").write_text(TASK_TOML.format(name=name, agent_timeout=agent_timeout), encoding="utf-8")
    (task_dir / "instruction.md").write_text(instruction, encoding="utf-8")
    (task_dir / "input" / "context.md").write_text(context, encoding="utf-8")
    (task_dir / "input" / "questionnaire.yaml").write_text(
        yaml.safe_dump(questionnaire, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")
    (task_dir / "reporting.json").write_text('{"schemaVersion": "1.0", "contextRules": []}\n', encoding="utf-8")
    if strategy:
        (task_dir / "persona_strategy.json").write_text(
            json.dumps(strategy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (task_dir / "README.md").write_text(
        readme + "\n이 폴더는 matraix-mi-checklist/build_tasks.py가 생성한다. 직접 고치지 말고 mi_items.py를 고친다.\n",
        encoding="utf-8")
    return task_dir


def build_all(matraix: Path) -> list[Path]:
    out = []
    for b in range(1, N_BLOCKS + 1):
        n = len(block_items(b))
        out.append(write_task(
            matraix, f"{TASK_PREFIX}a1-classify-b{b}",
            instruction=EXPERT_INSTRUCTION.format(part=f"분류, 블록 {b}/4"),
            context=EXPERT_CONTEXT.format(guide=INTEL_GUIDE, task=A1_TASK.format(b=b, n=n)),
            questionnaire=a1_questionnaire(b),
            readme=f"# 다중지능 문항 분류 (블록 {b}/4)\n\n교사 페르소나가 의도한 지능을 모르는 상태에서 {n}문항의 지능과 측면을 고른다.\n",
            strategy=_expert_strategy(), agent_timeout=1800.0,
        ))
        out.append(write_task(
            matraix, f"{TASK_PREFIX}a2-rate-b{b}",
            instruction=EXPERT_INSTRUCTION.format(part=f"평가, 블록 {b}/4"),
            context=EXPERT_CONTEXT.format(guide=INTEL_GUIDE, task=A2_TASK.format(b=b, n=n)),
            questionnaire=a2_questionnaire(b),
            readme=f"# 다중지능 문항 평가 (블록 {b}/4)\n\n교사 페르소나가 {n}문항의 내용 적합성과 5~6학년 이해 난이도를 평가한다.\n",
            strategy=_expert_strategy(), agent_timeout=1800.0,
        ))
    out.append(write_task(
        matraix, f"{TASK_PREFIX}b-child",
        instruction=CHILD_INSTRUCTION, context=CHILD_CONTEXT, questionnaire=child_questionnaire(),
        readme="# 가상 학생 응답\n\n강점 지능을 심어 둔 가상 5~6학년(child_profiles.py)이 192문항 전체에 응답한다.\n"
               "페르소나는 Persona 1M이 아니라 persona/datasets/mi-child-profiles/의 직접 만든 프로필을 쓴다.\n",
        strategy=None, agent_timeout=3600.0,
    ))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--matraix", type=Path, required=True, help="MatrAIx-Persona-8B 저장소 경로")
    args = parser.parse_args()
    for path in build_all(args.matraix.resolve()):
        print(path)


if __name__ == "__main__":
    main()
