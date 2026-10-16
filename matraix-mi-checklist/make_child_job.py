"""가상 학생 96명용 MatrAIx job YAML을 만든다.

공식 generate_application_job.py는 Persona 1M 같은 표집 풀에서 페르소나를 뽑는다. 직접 만든 아동 프로필은
그 풀에 없으므로, 공식 스크립트로 1명짜리 job을 만든 뒤(실행 환경·에이전트 설정을 그대로 얻기 위해)
그 job의 페르소나 목록만 아동 프로필로 바꾼다.

    python3 make_child_job.py --template configs/jobs/application-task-job-recipe/<1명 job>.yaml \\
        --personas persona/datasets/mi-child-profiles --job-name mi-b-child-n96 \\
        --out configs/jobs/application-task-job-recipe/mi-b-child-n96.yaml

경로는 MatrAIx 저장소 루트 기준이다 (matraix run을 그곳에서 실행하므로).
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from child_profiles import ID_PREFIX


def make_job(template: dict, persona_paths: list[str], job_name: str) -> dict:
    if not template.get("agents"):
        raise ValueError("template job has no agents")
    job = copy.deepcopy(template)
    base = template["agents"][0]
    agents = []
    for path in persona_paths:
        agent = copy.deepcopy(base)
        agent["kwargs"] = {**(agent.get("kwargs") or {}), "persona_path": path}
        agents.append(agent)
    job["agents"] = agents
    job["job_name"] = job_name
    return job


def main() -> None:
    import yaml

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--personas", type=Path, required=True, help="아동 페르소나 YAML 폴더 (저장소 루트 기준)")
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0, help="앞에서 N명만 (시험 실행)")
    args = parser.parse_args()

    paths = sorted(str(p) for p in args.personas.glob(f"persona_{ID_PREFIX}*.yaml"))
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        raise SystemExit(f"{args.personas}에 아동 페르소나가 없습니다. child_profiles.py --out 을 먼저 실행하세요.")

    template = yaml.safe_load(args.template.read_text(encoding="utf-8"))
    job = make_job(template, paths, args.job_name)
    args.out.write_text(
        f"# make_child_job.py가 {args.template.name}을 바탕으로 생성: 가상 학생 {len(paths)}명\n"
        + yaml.safe_dump(job, allow_unicode=True, sort_keys=False, width=1000),
        encoding="utf-8",
    )

    meta_in = args.template.with_suffix(".meta.json")
    if meta_in.is_file():
        meta = json.loads(meta_in.read_text(encoding="utf-8"))
        meta.update({
            "job_slug": args.job_name,
            "sample_size": len(paths),
            "selected_persona_ids": [Path(p).stem.removeprefix("persona_") for p in paths],
            "matched_pool_size": len(paths),
        })
        args.out.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                                                      encoding="utf-8")
    print(f"{args.out}: {len(paths)}명")


if __name__ == "__main__":
    main()
