#!/usr/bin/env bash
# 다중지능 체크리스트 192문항 후보를 MatrAIx 공식 Survey 파이프라인으로 검토하고 최종 128문항을 고른다.
#
# 필요: ANTHROPIC_API_KEY(또는 MATRAIX_ANTHROPIC_API_KEY) 환경 변수, huggingface.co 접속(Persona 1M 다운로드), uv
#
#   ./run_all.sh                      # 전체: A1 4블록 + A2 4블록 (블록당 교사 12명) + B 가상 학생 96명
#   PANEL=4 CHILDREN=16 ./run_all.sh  # 시험 실행
#   PARTS="B" ./run_all.sh            # 일부만 (A1, A2, B 중에서)
#
# 환경 변수
#   MATRAIX_DIR     MatrAIx-Persona-8B 클론 위치 (없으면 자동 클론)
#   MODEL           페르소나 모델 (기본: 파이프라인 기본값 anthropic/claude-sonnet-4-6)
#   PANEL           블록당 교사 페르소나 수 (기본 12, 4의 배수 권장: 경력 4개 구간)
#   CHILDREN        가상 학생 수 (기본 96 = 지능별 12명, 최대 96)
#   MAX_COST_USD    job당 비용 상한 (기본 5)
#   CONCURRENCY     동시 실행 trial 수 (기본 4)
#   SEED            표집 시드 (기본 42)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MATRAIX_DIR="${MATRAIX_DIR:-$HERE/.matraix}"
MODEL="${MODEL:-anthropic/claude-sonnet-4-6}"
PANEL="${PANEL:-12}"
CHILDREN="${CHILDREN:-96}"
MAX_COST_USD="${MAX_COST_USD:-5}"
CONCURRENCY="${CONCURRENCY:-4}"
SEED="${SEED:-42}"
PARTS="${PARTS:-A1 A2 B}"
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

# 2. Persona 1M 공개 데이터 (최초 1회, 교사 페르소나 표집용)
if [ ! -d "$DATASET/release/data" ]; then
  uv run hf download MatrAIx2026/MatrAIx_Persona_1M_Public_Release \
    --repo-type dataset --local-dir "$DATASET/release"
fi

# 3. 과제 9개 + 가상 학생 페르소나 96명
uv run python "$HERE/build_tasks.py" --matraix "$MATRAIX_DIR"
uv run python "$HERE/child_profiles.py" --out "$CHILD_POOL"

job_dirs=()
run_job() {  # run_job <task 폴더> <job 이름>
  export MATRIX_SURVEY_TASK_PATH="$1"
  uv run matraix run -c "$RECIPES/$2.yaml" --max-cost-usd "$MAX_COST_USD"
  job_dirs+=("$MATRAIX_DIR/jobs/$2")
}

# 4. A. 가상 전문가 패널: 블록마다 교사 페르소나 표집 → 실행 (A2는 A1과 다른 패널)
for part in A1 A2; do
  [[ " $PARTS " == *" $part "* ]] || continue
  for b in 1 2 3 4; do
    if [ "$part" = A1 ]; then task="application/tasks/mi-a1-classify-b$b"; seed="$SEED"
    else task="application/tasks/mi-a2-rate-b$b"; seed=$((SEED + 1)); fi
    job="mi-$(echo "$part" | tr 'A' 'a')-b$b-n$PANEL-s$seed"
    echo "=== $part 블록 $b: $task → jobs/$job"
    uv run python application/scripts/generate_application_job.py \
      --task "$task" --execution-mode auto --dataset "$DATASET" \
      --sample-size "$PANEL" --seed "$seed" --model-name "$MODEL" \
      --n-concurrent-trials "$CONCURRENCY" --name "$job" --job-name "$job"
    run_job "$task" "$job"
  done
done

# 5. B. 가상 학생: 공식 스크립트로 1명짜리 job을 만들어 실행 설정을 얻은 뒤, 페르소나만 아동 프로필로 바꾼다
if [[ " $PARTS " == *" B "* ]]; then
  task=application/tasks/mi-b-child
  job="mi-b-child-n$CHILDREN"
  uv run python application/scripts/generate_application_job.py \
    --task "$task" --execution-mode auto --dataset "$DATASET" --no-strategy \
    --sample-size 1 --seed "$SEED" --model-name "$MODEL" \
    --n-concurrent-trials "$CONCURRENCY" --name "$job-template" --job-name "$job-template"
  uv run python "$HERE/make_child_job.py" --template "$RECIPES/$job-template.yaml" \
    --personas "$CHILD_POOL" --job-name "$job" --out "$RECIPES/$job.yaml" --limit "$CHILDREN"
  echo "=== B: $task → jobs/$job"
  run_job "$task" "$job"
fi

# 6. 문항 통계 → 최종 128문항
uv run python "$HERE/report.py" --jobs "${job_dirs[@]}" --out "$HERE/report"
echo "완료: $HERE/report/report.html, $HERE/report/final_checklist.html"
