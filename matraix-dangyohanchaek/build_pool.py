"""MatrAIx Persona 1M에서 '서울·경기 초등교사' 조건에 맞춘 페르소나 묶음을 만든다.

    uv run python build_pool.py --matraix /path/to/MatrAIx-Persona-8B --per-bin 10 --seed 42

1. 공식 1M 표집기(sample_production_1m)로 한국어가 모국어인 현직 교사 후보를 경력·성별 칸마다 모두
   가져와, 프리랜서(학원·과외 강사로 보임)를 빼고 칸마다 무작위로 고른다. 경력 구간마다 남 30% : 여 70%를
   목표로 하되, 한쪽 성별 후보가 모자란 칸은 다른 성별로 채워 구간 인원을 맞춘다.
2. 스키마에 없는 속성(overlay)을 붙인다. 학교급은 모두 '초등학교', 근무 지역은 경력·성별 칸마다
   서울과 경기를 번갈아 나눈다. 한국식 이름(성별에 맞춤)으로 display_name도 바꾼다.
3. <matraix>/persona/datasets/<이름>/ 에 페르소나 YAML, manifest.json(overlay_dimensions 포함),
   persona_ids.txt를 쓰고, 마지막 줄에 그 경로(저장소 기준 상대 경로)를 출력한다.

붙인 속성은 페르소나 설명의 'Other attributes'에 한 줄로 들어갈 뿐, 원래 페르소나 이야기는 바뀌지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from build_tasks import EXPERIENCE_BINS

# 한국 교사에 가까운 페르소나만 남기는 스키마 속성 (국가 속성은 스키마에 없음).
FILTERS = {
    "role_function": ["Teaching"],
    "region": ["East Asia"],
    "lang_korean": ["Native"],
    "life_stage": ["Early career", "Parent of young kids", "Mid-life", "Career change",
                   "Empty nester"],  # 학생(Student), 은퇴(Retirement) 제외
}
# 표집기 필터는 '포함' 목록만 받으므로, 빼야 할 값은 가져온 뒤 거른다 (값이 없는 페르소나는 남긴다).
EXCLUDE = {"company_size": ["Solo / freelance"]}
MALE_SHARE = 0.3

OVERLAYS = [
    {"id": "school_level", "label": "학교급", "values": ["초등학교"]},
    {"id": "work_region", "label": "근무 지역", "values": ["서울", "경기"]},
]

SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오", "서", "신", "권", "황"]
GIVEN = {
    "Man": ["민준", "서준", "도윤", "예준", "시우", "하준", "주원", "지호", "준서", "현우",
            "성민", "재훈", "동현", "상우", "영수", "정호", "승현", "태윤", "건우", "우진"],
    "Woman": ["서연", "지우", "서윤", "하은", "민서", "지민", "수빈", "예은", "지현", "은영",
              "미경", "수진", "혜진", "윤서", "소영", "다은", "유진", "현정", "가영", "나연"],
}


def split_by_gender(per_bin: int) -> dict[str, int]:
    men = round(per_bin * MALE_SHARE)
    return {"Man": men, "Woman": per_bin - men}


def allocate(per_bin: int, available: dict[str, int]) -> dict[str, int]:
    """목표 성별 인원, 한쪽 후보가 모자라면 다른 쪽으로 채운다."""
    want = split_by_gender(per_bin)
    take = {g: min(n, available.get(g, 0)) for g, n in want.items()}
    short = per_bin - sum(take.values())
    for g in sorted(take, key=lambda g: -(available.get(g, 0) - take[g])):
        extra = min(short, available.get(g, 0) - take[g])
        take[g] += extra
        short -= extra
    if short:
        raise SystemExit(f"후보가 {sum(available.values())}명뿐이라 구간당 {per_bin}명을 채울 수 없습니다.")
    return take


def korean_name(persona_id: str, gender: str, used: set[str]) -> str:
    h = int(hashlib.sha1(persona_id.encode()).hexdigest(), 16)
    for k in range(len(SURNAMES) * len(GIVEN[gender])):
        name = SURNAMES[(h + k) % len(SURNAMES)] + GIVEN[gender][(h // 7 + k) % len(GIVEN[gender])]
        if name not in used:
            used.add(name)
            return name
    raise ValueError("이름 목록이 부족합니다")


def excluded(row: dict) -> bool:
    return any(str(row["dimensions"].get(k)) in vals for k, vals in EXCLUDE.items())


def sample(matraix: Path, per_bin: int, seed: int) -> list[dict]:
    """공식 표집기로 칸(경력 × 성별)마다 후보를 모두 가져와 거른 뒤 칸마다 고른다."""
    sys.path[:0] = [str(matraix), str(matraix / "application" / "playground")]
    from backend.service.persona_1m_pool import MAX_SAMPLE_SIZE, sample_production_1m

    cap = MAX_SAMPLE_SIZE // len(EXPERIENCE_BINS)  # 칸당 상한 = 사실상 전부
    candidates: dict[tuple[str, str], list[dict]] = {}
    for gender in ("Man", "Woman"):
        result = sample_production_1m(
            repo_root=matraix,
            sample_size=cap * len(EXPERIENCE_BINS),
            seed=seed,
            dimension_filters={**FILTERS, "gender_identity": [gender], "years_experience": EXPERIENCE_BINS},
            stratify_fields=["years_experience"],
            sample_size_per_value_group=cap,
            allocation="perCell",
        )
        for p in result["personas"]:
            row = yaml.safe_load((matraix / p["path"]).read_text(encoding="utf-8"))
            if not excluded(row):
                candidates.setdefault((row["dimensions"]["years_experience"], gender), []).append(row)

    rng = random.Random(seed)
    rows = []
    for years in EXPERIENCE_BINS:
        available = {g: len(candidates.get((years, g), [])) for g in ("Man", "Woman")}
        take = allocate(per_bin, available)
        if take != split_by_gender(per_bin):
            print(f"주의: {years}년 구간은 후보가 남 {available['Man']}명, 여 {available['Woman']}명이라 "
                  f"남 {take['Man']}명, 여 {take['Woman']}명으로 뽑습니다.", file=sys.stderr)
        for g, n in take.items():
            pool = sorted(candidates.get((years, g), []), key=lambda r: r["persona_id"])
            rows.extend(rng.sample(pool, n))
    return rows


def stamp(rows: list[dict], seed: int) -> None:
    """학교급·근무 지역·한국식 이름을 붙인다 (제자리 수정)."""
    rng = random.Random(seed)
    cells: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        d = row["dimensions"]
        cells.setdefault((d["years_experience"], d["gender_identity"]), []).append(row)
    regions = OVERLAYS[1]["values"]
    offset = 0  # 칸마다 시작 지역을 바꿔 홀수 칸이 한쪽으로 쏠리지 않게 한다
    used: set[str] = set()
    for key in sorted(cells):
        members = sorted(cells[key], key=lambda r: r["persona_id"])
        rng.shuffle(members)
        for i, row in enumerate(members):
            row["dimensions"]["school_level"] = OVERLAYS[0]["values"][0]
            row["dimensions"]["work_region"] = regions[(i + offset) % len(regions)]
            row["display_name"] = korean_name(row["persona_id"], key[1], used)
        offset += len(members)


def check(rows: list[dict], per_bin: int) -> None:
    for years in EXPERIENCE_BINS:
        got = [r for r in rows if r["dimensions"]["years_experience"] == years]
        if len(got) != per_bin or any(excluded(r) for r in got):
            raise SystemExit(f"{years}년 구간: {per_bin}명이 필요한데 {len(got)}명이 뽑혔습니다.")


def write_pool(matraix: Path, rows: list[dict], per_bin: int, seed: int) -> str:
    rel = f"persona/datasets/dangyohanchaek-seoul-gyeonggi-elem-pc{per_bin}-s{seed}"
    out = matraix / rel
    out.mkdir(parents=True, exist_ok=True)
    keep = {f"persona_{r['persona_id']}.yaml" for r in rows}
    for old in out.glob("persona_*.yaml"):  # 조건을 바꿔 다시 만들 때 남은 이전 페르소나
        if old.name not in keep:
            old.unlink()
    entries = []
    for row in rows:
        path = f"{rel}/persona_{row['persona_id']}.yaml"
        (matraix / path).write_text(yaml.safe_dump(row, allow_unicode=True, sort_keys=False), encoding="utf-8")
        d = row["dimensions"]
        entries.append({
            "persona_id": row["persona_id"],
            "display_name": row["display_name"],
            "path": path,
            "source": row.get("source"),
            "dimensions": {k: d[k] for k in ("years_experience", "gender_identity", "school_level", "work_region")},
        })
    manifest = {
        "kind": "dangyohanchaek-pool",
        "parent_pool": "persona/datasets/matraix-persona-1m",
        "count": len(rows),
        "seed": seed,
        "schema_version": "1.0",
        "dimension_filters": FILTERS,
        "excluded": EXCLUDE,
        "male_share": MALE_SHARE,
        "overlay_dimensions": OVERLAYS,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "personas": entries,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "persona_ids.txt").write_text("".join(r["persona_id"] + "\n" for r in rows), encoding="utf-8")
    return rel


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matraix", type=Path, required=True)
    ap.add_argument("--per-bin", type=int, default=10, help="경력 구간당 인원 (남 30%%, 여 70%%로 나눔)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    matraix = args.matraix.resolve()

    split = split_by_gender(args.per_bin)
    if split["Man"] / args.per_bin != MALE_SHARE:
        print(f"주의: 구간당 {args.per_bin}명으로는 남 {MALE_SHARE:.0%}를 정확히 맞출 수 없어 "
              f"남 {split['Man']}명, 여 {split['Woman']}명을 목표로 뽑습니다.", file=sys.stderr)
    rows = sample(matraix, args.per_bin, args.seed)
    check(rows, args.per_bin)
    stamp(rows, args.seed)
    print(write_pool(matraix, rows, args.per_bin, args.seed))


if __name__ == "__main__":
    main()
