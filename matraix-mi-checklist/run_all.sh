#!/usr/bin/env bash
# 다중지능 체크리스트 192문항 후보를 MatrAIx 공식 Survey 파이프라인으로 검토하고 최종 128문항을 고른다.
#
# 필요: ANTHROPIC_API_KEY(또는 MATRAIX_ANTHROPIC_API_KEY) 환경 변수, huggingface.co 접속(Persona 1M 다운로드), uv
#
#   ./run_all.sh                         # 전체: A1 4블록 + A2 4블록 (블록당 교사 12명) + B 가상 학생 96명
#   PANEL_LIMIT=2 CHILDREN=4 RUN_TAG=smoke PARTS="A1 B" BLOCKS=1 ./run_all.sh   # 시험 실행
#
# 환경 변수
#   MATRAIX_DIR     MatrAIx-Persona-8B 클론 위치 (없으면 자동 클론)
#   MODEL           페르소나 모델 (기본: 파이프라인 기본값 anthropic/claude-sonnet-4-6)
#   PER_BIN         교사 패널 경력 구간당 인원 (기본 6 → 4구간 × 6 = 24명, A1·A2에 12명씩)
#   PANEL_LIMIT     블록마다 패널 앞에서 N명만 (시험 실행용, 0 = 전부)
#   CHILDREN        가상 학생 수 (기본 96 = 지능별 12명, 최대 96)
#   PARTS / BLOCKS  일부만 실행 (기본 "A1 A2 B" / "1 2 3 4")
#   MAX_COST_USD    A job당 비용 상한 (기본 5) / MAX_COST_B: B job 비용 상한 (기본 25)
#   CONCURRENCY     동시 실행 trial 수 (기본 4)
#   SEED            표집 시드 (기본 42)
#   RUN_TAG         job 이름 끝 꼬리표 (없으면 같은 이름의 이전 job을 이어서 실행)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MATRAIX_DIR="${MATRAIX_DIR:-$HERE/.matraix}"
MODEL="${MODEL:-anthropic/claude-sonnet-4-6}"
PER_BIN="${PER_BIN:-6}"
PANEL_LIMIT="${PANEL_LIMIT:-0}"
CHILDREN="${CHILDREN:-96}"
PARTS="${PARTS:-A1 A2 B}"
BLOCKS="${BLOCKS:-1 2 3 4}"
MAX_COST_USD="${MAX_COST_USD:-5}"
MAX_COST_B="${MAX_COST_B:-25}"
CONCURRENCY="${CONCURRENCY:-4}"
SEED="${SEED:-42}"
RUN_TAG="${RUN_TAG:-}"
TAG="${RUN_TAG:+-$RUN_TAG}"
DATASET=persona/datasets/matraix-persona-1m
CHILD_POOL=persona/datasets/mi-child-profiles
RECIPES=configs/jobs/application-task-job-recipe

export ANTHROPIC_API_KEY="${MATRAIX_ANTHROPIC_API_KEY:-${ANTHROPIC_API_KEY:-}}"
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY 또는 MATRAIX_ANTHROPIC_API_KEY 환경 변수를 설정하세요}"

# 1. 공식 저장소와 의존성
if [ ! -d "$MATRAIX_DIR/.git" ]; then
  git clone --depth 1 https://github.com/MatrAIx-ai/MatrAIx-Persona-8B.git "$MATRAIX_DIR"
fi
cd "$MATRAIX_DIR"
uv sync

# 2. Persona 1M 공개 데이터 (최초 1회, 교사 패널 표집용)
if [ ! -d "$DATASET/release/data" ]; then
  uv run hf download MatrAIx2026/MatrAIx_Persona_1M_Public_Release \
    --repo-type dataset --local-dir "$DATASET/release"
fi

# 3. 과제 9개, 교사 패널 24명(서울·경기 초등교사), 가상 학생 96명
uv run python "$HERE/build_tasks.py" --matraix "$MATRAIX_DIR"
PANEL=$(uv run python "$HERE/build_panel.py" --matraix "$MATRAIX_DIR" --per-bin "$PER_BIN" --seed "$SEED" | tail -1)
uv run python "$HERE/child_profiles.py" --out "$CHILD_POOL"
echo "=== 교사 패널: $PANEL"

job_dirs=()
run_job() {  # run_job <task 폴더> <job 이름> <비용 상한>
  export MATRIX_SURVEY_TASK_PATH="$1"
  uv run matraix run -c "$RECIPES/$2.yaml" --max-cost-usd "$3"
  uv run matraix results "jobs/$2" --format json,csv -o "$HERE/results/$2" || true
  job_dirs+=("$MATRAIX_DIR/jobs/$2")
}

panel_ids() {  # panel_ids <a1|a2>
  if [ "$PANEL_LIMIT" -gt 0 ]; then head -n "$PANEL_LIMIT" "$PANEL/panel_$1.txt"; else cat "$PANEL/panel_$1.txt"; fi
}

# 4. A. 가상 전문가: A1은 분류 패널, A2는 평가 패널 (서로 다른 12명)
for part in A1 A2; do
  [[ " $PARTS " == *" $part "* ]] || continue
  lower=$(echo "$part" | tr 'A' 'a')
  mapfile -t ids < <(panel_ids "$lower")
  for b in $BLOCKS; do
    if [ "$part" = A1 ]; then task="application/tasks/mi-a1-classify-b$b"; else task="application/tasks/mi-a2-rate-b$b"; fi
    job="mi-$lower-b$b-kr-elem-n${#ids[@]}-s$SEED$TAG"
    echo "=== $part 블록 $b: $task → jobs/$job"
    uv run python application/scripts/generate_application_job.py \
      --task "$task" --execution-mode auto --dataset "$PANEL" --persona-ids "${ids[@]}" --no-strategy \
      --seed "$SEED" --model-name "$MODEL" \
      --n-concurrent-trials "$CONCURRENCY" --name "$job" --job-name "$job"
    run_job "$task" "$job" "$MAX_COST_USD"
  done
done

# 5. B. 가상 학생: 공식 스크립트로 1명짜리 job을 만들어 실행 설정을 얻은 뒤, 페르소나만 아동 프로필로 바꾼다
if [[ " $PARTS " == *" B "* ]]; then
  task=application/tasks/mi-b-child
  job="mi-b-child-n$CHILDREN-s$SEED$TAG"
  first=$(head -n 1 "$PANEL/panel_a1.txt")
  uv run python application/scripts/generate_application_job.py \
    --task "$task" --execution-mode auto --dataset "$PANEL" --persona-ids "$first" --no-strategy \
    --seed "$SEED" --model-name "$MODEL" \
    --n-concurrent-trials "$CONCURRENCY" --name "$job-template" --job-name "$job-template"
  uv run python "$HERE/make_child_job.py" --template "$RECIPES/$job-template.yaml" \
    --personas "$CHILD_POOL" --job-name "$job" --out "$RECIPES/$job.yaml" --limit "$CHILDREN"
  echo "=== B: $task → jobs/$job"
  run_job "$task" "$job" "$MAX_COST_B"
fi

# 6. 문항 통계 → 최종 128문항
uv run python "$HERE/report.py" --jobs "${job_dirs[@]}" --out "$HERE/report${TAG}"
echo "완료: $HERE/report${TAG}/report.html, $HERE/report${TAG}/final_checklist.html"
