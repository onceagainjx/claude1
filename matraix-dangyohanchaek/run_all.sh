#!/usr/bin/env bash
# 「당교한책」 6개 여정을 MatrAIx 공식 Survey 파이프라인으로 일괄 실행하고 보고서를 만든다.
# 페르소나: 한국어 모국어 현직 교사(학생·은퇴·프리랜서 제외), 경력 5구간 × 구간당 남 30% : 여 70% 목표, 학교급=초등학교,
# 근무 지역=서울·경기 반반 (build_pool.py). 모든 여정에 같은 페르소나를 쓴다.
#
# 필요: ANTHROPIC_API_KEY(또는 MATRAIX_ANTHROPIC_API_KEY) 환경 변수, huggingface.co 접속(Persona 1M 다운로드), uv
#
#   ./run_all.sh                     # 6개 여정 × 50명 (경력 5개 구간 × 10명: 남 3, 여 7. 3~5년만 남 6, 여 4)
#   SAMPLE_SIZE=10 ./run_all.sh      # 시험 실행 (구간당 2명: 남 1, 여 1이라 30:70은 맞지 않음)
#   JOURNEYS="2 5" ./run_all.sh      # 일부 여정만
#
# 환경 변수
#   MATRAIX_DIR     MatrAIx-Persona-8B 클론 위치 (없으면 자동 클론)
#   MODEL           페르소나 모델 (기본: 파이프라인 기본값 anthropic/claude-sonnet-4-6)
#   SAMPLE_SIZE     여정당 페르소나 수 (기본 50, 5의 배수 권장. 구간당 올림(SAMPLE_SIZE/5)명)
#   MAX_COST_USD    여정당 비용 상한 (기본 5)
#   CONCURRENCY     동시 실행 trial 수 (기본 4)
#   SEED            표집 시드 (기본 42, 같은 시드 = 같은 페르소나)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MATRAIX_DIR="${MATRAIX_DIR:-$HERE/.matraix}"
MODEL="${MODEL:-anthropic/claude-sonnet-4-6}"
SAMPLE_SIZE="${SAMPLE_SIZE:-50}"
MAX_COST_USD="${MAX_COST_USD:-5}"
CONCURRENCY="${CONCURRENCY:-4}"
SEED="${SEED:-42}"
JOURNEYS="${JOURNEYS:-1 2 3 4 5 6}"
DATASET=persona/datasets/matraix-persona-1m
# 경력 구간 수 (build_tasks.py의 EXPERIENCE_BINS). 구간당 인원은 build_pool.py가 성별로 나눠 뽑는다.
N_BINS=5
PER_BIN=$(( (SAMPLE_SIZE + N_BINS - 1) / N_BINS ))

# Claude Code 클라우드 환경에서는 ANTHROPIC_API_KEY 대신 MATRAIX_ANTHROPIC_API_KEY로 넣을 수 있다.
export ANTHROPIC_API_KEY="${MATRAIX_ANTHROPIC_API_KEY:-${ANTHROPIC_API_KEY:-}}"
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY 또는 MATRAIX_ANTHROPIC_API_KEY 환경 변수를 설정하세요}"

# 1. 공식 저장소와 의존성
if [ ! -d "$MATRAIX_DIR/.git" ]; then
  git clone --depth 1 https://github.com/MatrAIx-ai/MatrAIx-Persona-8B.git "$MATRAIX_DIR"
fi
cd "$MATRAIX_DIR"
uv sync

# 2. Persona 1M 공개 데이터 (최초 1회)
if [ ! -d "$DATASET/release/data" ]; then
  uv run hf download MatrAIx2026/MatrAIx_Persona_1M_Public_Release \
    --repo-type dataset --local-dir "$DATASET/release"
fi

# 3. 질문 세트 → 공식 Survey 과제 6개
uv run python "$HERE/build_tasks.py" --matraix "$MATRAIX_DIR"

# 4. 페르소나 묶음 (서울·경기 초등교사, 남 30% : 여 70%)
POOL=$(uv run python "$HERE/build_pool.py" --matraix "$MATRAIX_DIR" --per-bin "$PER_BIN" --seed "$SEED" | tail -1)
mapfile -t persona_ids < "$POOL/persona_ids.txt"
echo "=== 페르소나 ${#persona_ids[@]}명: $POOL"

# 5. 여정별 일괄 실행
job_dirs=()
for n in $JOURNEYS; do
  task=$(ls -d application/tasks/dangyohanchaek-j"$n"-* | head -1)
  job="dk-j${n}-kr-elem-pc${PER_BIN}-s${SEED}"
  echo "=== 여정 $n: $task → jobs/$job"
  uv run python application/scripts/generate_application_job.py \
    --task "$task" \
    --execution-mode auto \
    --dataset "$POOL" \
    --persona-ids "${persona_ids[@]}" \
    --no-strategy \
    --seed "$SEED" \
    --model-name "$MODEL" \
    --n-concurrent-trials "$CONCURRENCY" \
    --name "$job" --job-name "$job"
  export MATRIX_SURVEY_TASK_PATH="$task"
  uv run matraix run -c "configs/jobs/application-task-job-recipe/$job.yaml" --max-cost-usd "$MAX_COST_USD"
  uv run matraix results "jobs/$job" --group-by years_experience --format json,csv -o "$HERE/results/$job" || true
  job_dirs+=("$MATRAIX_DIR/jobs/$job")
done

# 6. 경력 집단 비교 보고서
uv run python "$HERE/report.py" --jobs "${job_dirs[@]}" --out "$HERE/report"
echo "완료: $HERE/report/report.html"
