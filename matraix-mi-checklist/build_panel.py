"""A(가상 전문가) 패널: 서울·경기 초등교사 페르소나 24명을 뽑아 A1·A2 패널 12명씩으로 나눈다.

    uv run python build_panel.py --matraix /path/to/MatrAIx-Persona-8B --per-bin 6 --seed 42

표집 조건은 「당교한책」 연구의 matraix-dangyohanchaek/build_pool.py를 그대로 쓴다
(한국어 모국어 현직 교사, 학생·은퇴·프리랜서·대학 강사 제외, 남 30% : 여 70% 목표, 학교급=초등학교,
근무 지역=서울·경기 반반). 다만 문항을 판단할 경력이 있어야 하므로 경력 0~2년 구간은 뺀다.

출력: <matraix>/persona/datasets/mi-panel-seoul-gyeonggi-elem-pc<per-bin>-s<seed>/
    persona_*.yaml, manifest.json, panel_a1.txt, panel_a2.txt
마지막 줄에 그 경로(저장소 기준 상대 경로)를 출력한다.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import yaml

PANEL_BINS = ["3-5", "6-10", "11-20", "20+"]


def _load_build_pool() -> types.ModuleType:
    """「당교한책」의 build_pool.py를 경로로 불러온다. 그 파일은 자기 폴더의 build_tasks에서 경력 구간만
    가져오는데, 이 폴더에도 build_tasks.py가 있어 이름이 겹치므로 구간 값만 담은 대역을 잠시 끼운다."""
    path = Path(__file__).resolve().parent.parent / "matraix-dangyohanchaek" / "build_pool.py"
    saved = sys.modules.get("build_tasks")
    sys.modules["build_tasks"] = types.SimpleNamespace(EXPERIENCE_BINS=PANEL_BINS)
    try:
        spec = importlib.util.spec_from_file_location("dangyohanchaek_build_pool", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is None:
            sys.modules.pop("build_tasks", None)
        else:
            sys.modules["build_tasks"] = saved
    return module


build_pool = _load_build_pool()


def split_panels(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """경력 × 성별 칸마다 번갈아 나눠, 두 패널의 경력·성별 구성을 맞춘다."""
    a1, a2 = [], []
    cells: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        d = r["dimensions"]
        cells.setdefault((d["years_experience"], d["gender_identity"]), []).append(r)
    turn = 0
    for key in sorted(cells):
        for r in sorted(cells[key], key=lambda r: r["persona_id"]):
            (a1 if turn % 2 == 0 else a2).append(r)
            turn += 1
    return a1, a2


def write_panel(matraix: Path, rows: list[dict], per_bin: int, seed: int) -> str:
    rel = f"persona/datasets/mi-panel-seoul-gyeonggi-elem-pc{per_bin}-s{seed}"
    out = matraix / rel
    out.mkdir(parents=True, exist_ok=True)
    keep = {f"persona_{r['persona_id']}.yaml" for r in rows}
    for old in out.glob("persona_*.yaml"):
        if old.name not in keep:
            old.unlink()
    entries = []
    for row in rows:
        path = f"{rel}/persona_{row['persona_id']}.yaml"
        (matraix / path).write_text(yaml.safe_dump(row, allow_unicode=True, sort_keys=False), encoding="utf-8")
        d = row["dimensions"]
        entries.append({
            "persona_id": row["persona_id"], "display_name": row["display_name"], "path": path,
            "source": row.get("source"),
            "dimensions": {k: d[k] for k in ("years_experience", "gender_identity", "school_level", "work_region")},
        })
    manifest = {
        "kind": "mi-checklist-expert-panel",
        "parent_pool": "persona/datasets/matraix-persona-1m",
        "count": len(rows), "seed": seed, "schema_version": "1.0",
        "dimension_filters": {**build_pool.FILTERS, "years_experience": PANEL_BINS},
        "excluded": build_pool.EXCLUDE, "keep_only": build_pool.KEEP_ONLY, "male_share": build_pool.MALE_SHARE,
        "overlay_dimensions": build_pool.OVERLAYS,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "personas": entries,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    a1, a2 = split_panels(rows)
    for name, panel in (("panel_a1.txt", a1), ("panel_a2.txt", a2)):
        (out / name).write_text("".join(r["persona_id"] + "\n" for r in panel), encoding="utf-8")
    return rel


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matraix", type=Path, required=True)
    ap.add_argument("--per-bin", type=int, default=6, help="경력 구간당 인원 (A1·A2에 반씩)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    matraix = args.matraix.resolve()

    rows = build_pool.sample(matraix, args.per_bin, args.seed)
    build_pool.check(rows, args.per_bin)
    build_pool.stamp(rows, args.seed)
    print(write_panel(matraix, rows, args.per_bin, args.seed))


if __name__ == "__main__":
    main()
