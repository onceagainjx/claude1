"""가상 5~6학년 학생 프로필 (B: 가상 학생 응답용).

MatrAIx Persona 1M에는 아동 페르소나가 없으므로, 강점·약점 지능을 미리 정해 둔 프로필을 직접 만든다.
프로필에는 지능 이름이나 문항 문장을 쓰지 않고 생활 모습만 적는다. 그래야 "문항이 생활 모습에서
강점 지능을 찾아내는가"를 볼 수 있다.

    python3 child_profiles.py --out <matraix>/persona/datasets/mi-child-profiles

- 8개 지능 × 12명 = 96명. 각 아이는 가장 강한 지능 1개 + 그다음 강점 2개 + 약점 2개를 갖는다.
- 정답(심어 둔 강점·약점)은 child_profiles.json에 저장되어 report.py가 읽는다.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from mi_items import INTEL_IDS

PER_PRIMARY = 12
SEED = 2026
ID_PREFIX = "mi-child-"

NAMES = ["지우", "서윤", "하준", "도윤", "서연", "민준", "하은", "시우", "지호", "수아", "예준", "지아",
         "은우", "유나", "주원", "채원", "건우", "다은", "현우", "소율", "우진", "윤서", "선우", "지민"]

# 생활 모습 묘사 (지능 이름이나 문항 문장과 겹치지 않게 쓴다)
STRONG = {
    "linguistic": ["학급 문집에 실린 글이 두 편이나 된다", "새 책이 오면 그날 밤에 다 읽어 버린다",
                   "국어 시간 발표 때 말이 술술 나와 선생님이 놀라곤 한다"],
    "logical": ["수학 익힘책을 먼저 끝내고 심화 문제를 찾아 푼다", "과학 실험 보고서를 꼼꼼하게 쓴다",
                "보드게임을 하면 규칙을 제일 먼저 이해하고 친구들에게 알려 준다"],
    "spatial": ["공책 귀퉁이마다 캐릭터 그림이 그려져 있다", "레고로 큰 성을 설명서 없이 만든 적이 있다",
                "가족 여행 때 지도 앱 없이도 길을 기억한다"],
    "bodily": ["점심시간마다 운동장에서 축구를 한다", "태권도 품새 대회에 나간 적이 있다",
               "만들기 시간에 손이 빠르고 작품이 튼튼하다"],
    "musical": ["피아노를 4년째 배우고 있다", "한 번 들은 노래를 금방 흥얼거린다",
                "음악 시간에 리코더 합주에서 앞장선다"],
    "interpersonal": ["반에서 친구들 사이 다툼이 생기면 중간에서 말린다", "전학 온 친구에게 먼저 말을 건다",
                      "모둠장이 자주 되고 친구들이 의견을 물으러 온다"],
    "intrapersonal": ["매일 밤 짧게 일기를 쓴다", "속상한 일이 있으면 혼자 생각한 뒤 스스로 마음을 정리한다",
                      "방학 계획표를 직접 만들고 대부분 지킨다"],
    "naturalist": ["집에서 거북이와 장수풍뎅이를 키운다", "등굣길에 핀 꽃 이름을 거의 다 안다",
                   "주말마다 가족과 숲 체험이나 갯벌 체험을 간다"],
}
WEAK = {
    "linguistic": ["책 읽기는 숙제일 때만 한다", "글쓰기 시간이 오면 첫 문장을 못 써서 한참 앉아 있다"],
    "logical": ["수학 문제가 길어지면 읽다가 포기한다", "계산 실수가 잦아 수학 시간이 부담스럽다"],
    "spatial": ["그림 그리기 시간이 제일 어렵다", "처음 가는 곳에서는 자주 길을 헤맨다"],
    "bodily": ["체육 시간에 공이 오면 피하고 싶다", "줄넘기 급수 시험에서 자주 떨어진다"],
    "musical": ["노래 부를 때 음이 자꾸 엇나간다", "음악 시간은 그저 그런 시간이다"],
    "interpersonal": ["모둠 활동보다 혼자 하는 과제가 편하다", "새 학년에 친구를 사귀는 데 시간이 오래 걸린다"],
    "intrapersonal": ["내 기분이 왜 그런지 잘 모를 때가 많다", "계획을 세워도 금방 잊어버린다"],
    "naturalist": ["곤충을 보면 무서워서 피한다", "식물 키우기 숙제에서 화분을 시들게 한 적이 있다"],
}
STYLES = [
    ("modest", "자기 자랑을 쑥스러워해서 '잘한다'는 문장에는 점수를 조금 낮게 주는 편이다"),
    ("plain", "솔직하게 있는 그대로 답하는 편이다"),
    ("generous", "대체로 기분이 좋고 긍정적이라 점수를 조금 후하게 주는 편이다"),
]


def build_profiles(seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    profiles = []
    n = 0
    for primary in INTEL_IDS:
        for k in range(PER_PRIMARY):
            n += 1
            others = [i for i in INTEL_IDS if i != primary]
            rng.shuffle(others)
            secondary, weak = others[:2], others[2:4]
            grade = 5 if k % 2 == 0 else 6
            gender = "여학생" if (k // 2) % 2 == 0 else "남학생"
            style_id, style_text = STYLES[k % 3]
            name = NAMES[(n - 1) % len(NAMES)]
            strong_lines = (rng.sample(STRONG[primary], 3)
                            + [rng.choice(STRONG[s]) for s in secondary])
            weak_lines = [rng.choice(WEAK[w]) for w in weak]
            profiles.append({
                "persona_id": f"{ID_PREFIX}{n:03d}",
                "name": name, "grade": grade, "gender": gender,
                "primary": primary, "secondary": secondary, "weak": weak,
                "style": style_id, "style_text": style_text,
                "strong_lines": strong_lines, "weak_lines": weak_lines,
            })
    return profiles


def system_prompt(p: dict) -> str:
    strong = "\n".join(f"- {s}" for s in p["strong_lines"])
    weak = "\n".join(f"- {s}" for s in p["weak_lines"])
    return (
        f"너는 한국 초등학교 {p['grade']}학년 {p['gender']} {p['name']}이다. 나이는 {p['grade'] + 6}살이다.\n\n"
        f"너에 대해:\n{strong}\n\n"
        f"너에게 어려운 것:\n{weak}\n\n"
        "위에 적히지 않은 일은 반 친구들과 비슷한 보통 수준이다.\n"
        f"설문에 답할 때 너는 {p['style_text']}.\n\n"
        f"초등학교 {p['grade']}학년이 아는 낱말과 생각 수준으로 읽고 답한다. 어른처럼 분석하지 말고, "
        "문장을 읽고 떠오르는 대로 솔직하게 답한다. 뜻을 모르는 낱말이 있으면 아는 척하지 말고 "
        "모르는 대로 짐작해서 답한 뒤, 마지막 질문에 그 낱말을 적는다."
    )


def write_personas(out_dir: Path, profiles: list[dict]) -> None:
    import yaml

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"persona_{ID_PREFIX}*.yaml"):
        old.unlink()
    for p in profiles:
        doc = {
            "persona_id": p["persona_id"],
            "version": "1.0",
            "display_name": f"{p['name']} (초등 {p['grade']}학년)",
            "summary": f"초등학교 {p['grade']}학년 {p['gender']}",
            "system_prompt": system_prompt(p),
        }
        (out_dir / f"persona_{p['persona_id']}.yaml").write_text(
            yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="페르소나 YAML을 쓸 폴더 (MatrAIx 저장소 안)")
    args = parser.parse_args()
    profiles = build_profiles()
    truth = Path(__file__).with_name("child_profiles.json")
    truth.write_text(json.dumps(profiles, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{truth}: {len(profiles)}명")
    if args.out:
        write_personas(args.out, profiles)
        print(f"{args.out}: 페르소나 YAML {len(profiles)}개")


if __name__ == "__main__":
    main()
