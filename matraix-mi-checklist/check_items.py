"""0단계 기계적 점검 + 사람이 읽는 문항표(items.md) 생성. LLM을 쓰지 않는다.

    python3 check_items.py            # items.md 갱신, 경고 요약 출력

경고는 '확인해 볼 문항' 표시일 뿐이다. 최종 판단은 교사가 한다.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from mi_items import FACETS, INTEL_IDS, ITEMS, NAME, NOTES, ORIGINAL, SHORT

MAX_LEN = 40

# 5~6학년에게 낯설 수 있는 낱말 (필요하면 추가)
HARD_WORDS = [
    "어휘", "계발", "좌절", "운율", "사자성어", "논리", "조직", "계열", "신체 언어", "민감",
    "전개도", "조리", "섬세", "원리", "가락", "화음", "음정", "직육면체", "어림", "패턴",
]

CHECKS = {
    "long": f"{MAX_LEN}자 초과",
    "compare": "'~보다' 비교 표현",
    "or": "'~거나'로 서로 다른 두 행동을 묶음",
    "others": "남의 평가에 기댐",
    "hard": "낯선 낱말",
}


def check(text: str) -> dict[str, str]:
    body = text.removeprefix("나는 ").removeprefix("나에게는 ")
    found: dict[str, str] = {}
    if len(text) > MAX_LEN:
        found["long"] = f"{len(text)}자"
    m = re.search(r"(\S+)보다\s", text)
    if m:
        found["compare"] = m.group(0).strip()
    ors = re.findall(r"\S+거나", body)
    if ors:
        found["or"] = ", ".join(o.strip() for o in ors)
    if re.search(r"이야기를 (주로 )?듣|칭찬을 받|말을 듣는다", text):
        found["others"] = "남의 말"
    hard = [w for w in HARD_WORDS if w in text]
    if hard:
        found["hard"] = ", ".join(hard)
    return found


def build_markdown() -> str:
    lines = [
        "# 다중지능 체크리스트 후보 문항 192개",
        "",
        "8개 지능 × 24문항 (흥미 12 + 능력 12). 최종 검사지는 여기서 지능마다 흥미 8 + 능력 8을 골라 "
        "128문항으로 만든다.",
        "",
        "- **번호**: 후보 번호. 현행 답안지처럼 8개 지능이 번갈아 나온다 "
        "(1열 언어, 2열 논리, 3열 공간, 4열 신체, 5열 음악, 6열 대인, 7열 자성, 8열 자연).",
        "- **출처**: `기존 n` = 현행 n번 문항을 고친 것, `신규` = 새로 쓴 문항.",
        "- **점검**: 0단계 기계적 점검에서 표시된 항목. 고쳐야 한다는 뜻이 아니라 한 번 더 볼 문항이라는 뜻이다.",
        "",
    ]
    counts = Counter()
    for intel in INTEL_IDS:
        rows = [x for x in ITEMS if x["intel"] == intel]
        lines += [f"## {NAME[intel]} ({SHORT[intel]})", ""]
        for facet in FACETS:
            lines += [f"### {FACETS[facet]}", "", "| 번호 | 문항 | 출처 | 점검 |", "|---|---|---|---|"]
            for x in (r for r in rows if r["facet"] == facet):
                flags = check(x["text"])
                counts.update(flags.keys())
                src = f"기존 {x['orig_no']}" if x["orig_no"] else "신규"
                flag = "; ".join(f"{CHECKS[k]}({v})" for k, v in flags.items())
                lines.append(f"| {x['no']} | {x['text']} | {src} | {flag} |")
            lines.append("")
    lines += [
        "## 기존 80문항 수정 내역",
        "",
        "| 원 번호 | 원문 | 수정 | 지능·측면 | 수정 사유 |",
        "|---|---|---|---|---|",
    ]
    for x in sorted((r for r in ITEMS if r["orig_no"]), key=lambda r: r["orig_no"]):
        why = ", ".join(NOTES[n] for n in x["notes"])
        lines.append(f"| {x['orig_no']} | {ORIGINAL[x['orig_no']]} | {x['text']} | "
                     f"{SHORT[x['intel']]}·{FACETS[x['facet']][:2]} | {why} |")
    lines += ["", "## 0단계 점검 요약", "", "| 점검 | 문항 수 |", "|---|---|"]
    lines += [f"| {CHECKS[k]} | {counts.get(k, 0)} |" for k in CHECKS]
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    out = Path(__file__).with_name("items.md")
    out.write_text(build_markdown(), encoding="utf-8")
    flagged = [x for x in ITEMS if check(x["text"])]
    print(f"{out} 작성: 192문항 중 {len(flagged)}문항에 점검 표시")


if __name__ == "__main__":
    main()
