#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_ROOT="$(cd "$DEMO_DIR/.." && pwd)"

ROOT_DIR="${GATE_WORKSPACE:-${WORKSPACE:-$(pwd)}}"
cd "$ROOT_DIR"

OUTPUT_DIR="${GATE_OUTPUT_DIR:-gate-output}"
CONFIG_PATH="${GATE_CONFIG:-$DEMO_DIR/gate-config.example.json}"
EVALUATOR_PATH="${GATE_EVALUATOR:-$SCRIPT_DIR/evaluate_gate.py}"
BASE_RESOLVER="${GATE_BASE_RESOLVER:-$APP_ROOT/scripts/resolve_deploy_base.py}"
GITLAB_CONTEXT_SCRIPT="${GATE_GITLAB_CONTEXT_SCRIPT:-$APP_ROOT/scripts/gitlab_context.py}"
DOCX_GENERATOR="${GATE_DOCX_GENERATOR:-$APP_ROOT/scripts/generate_confirmation_docx.py}"
DOCX_TEMPLATE="${GATE_CONFIRM_TEMPLATE:-$APP_ROOT/templates/AI-agent-confirmation.docx}"
mkdir -p "$OUTPUT_DIR"

DEPLOY_COMMIT="${DEPLOY_COMMIT:-$(git rev-parse HEAD)}"
TARGET_ENV="${TARGET_ENV:-test}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)}"
BASE_COMMIT=""

BASE_RESULT="$OUTPUT_DIR/deploy-base.json"
if [[ -f "$BASE_RESOLVER" ]]; then
  BASE_ARGS=(
    --output "$BASE_RESULT"
    --workspace "$ROOT_DIR"
    --deploy-commit "$DEPLOY_COMMIT"
  )
  if [[ -n "${LAST_SUCCESS_DEPLOY_COMMIT:-}" ]]; then
    BASE_ARGS+=(--manual-commit "$LAST_SUCCESS_DEPLOY_COMMIT")
  fi
  if [[ "${GATE_RESOLVE_BASE_GIT_FALLBACK:-true}" != "false" ]]; then
    BASE_ARGS+=(--git-fallback)
  fi

  set +e
  BASE_COMMIT="$(python3 "$BASE_RESOLVER" "${BASE_ARGS[@]}" | tail -n 1)"
  BASE_RESOLVE_STATUS=$?
  set -e
  if [[ "$BASE_RESOLVE_STATUS" -ne 0 ]]; then
    echo "deployment base resolver failed with exit code $BASE_RESOLVE_STATUS" >&2
    BASE_COMMIT=""
  fi
else
  echo "deployment base resolver skipped: script not found: $BASE_RESOLVER" >&2
fi

if [[ -z "$BASE_COMMIT" && -n "${LAST_SUCCESS_DEPLOY_COMMIT:-}" ]]; then
  BASE_COMMIT="$LAST_SUCCESS_DEPLOY_COMMIT"
fi

if [[ -z "$BASE_COMMIT" && "${GATE_RESOLVE_BASE_GIT_FALLBACK:-true}" != "false" ]]; then
  if git rev-parse "${DEPLOY_COMMIT}~1" >/dev/null 2>&1; then
    BASE_COMMIT="$(git rev-parse "${DEPLOY_COMMIT}~1")"
  fi
fi

if [[ -n "$BASE_COMMIT" ]]; then
  git diff --name-only "$BASE_COMMIT" "$DEPLOY_COMMIT" > "$OUTPUT_DIR/changed-files.txt"
else
  git show --name-only --format='' "$DEPLOY_COMMIT" > "$OUTPUT_DIR/changed-files.txt" || true
fi

cat > "$OUTPUT_DIR/deploy-context.json" <<JSON
{
  "repo": "${JOB_NAME:-local}",
  "build_number": "${BUILD_NUMBER:-local}",
  "target_env": "${TARGET_ENV}",
  "deploy_branch": "${DEPLOY_BRANCH}",
  "deploy_commit": "${DEPLOY_COMMIT}",
  "base_commit": "${BASE_COMMIT}",
  "gitlab_project_id": "${GITLAB_PROJECT_ID:-}",
  "gitlab_mr_iid": "${GITLAB_MR_IID:-}",
  "gitlab_project_url": "${GITLAB_PROJECT_URL:-}",
  "trigger_user": "${BUILD_USER_ID:-}"
}
JSON

GITLAB_CONTEXT="$OUTPUT_DIR/gitlab-context.json"
if [[ -f "$GITLAB_CONTEXT_SCRIPT" ]]; then
  set +e
  python3 "$GITLAB_CONTEXT_SCRIPT" \
    --context "$OUTPUT_DIR/deploy-context.json" \
    --output "$GITLAB_CONTEXT" \
    --update-context "$OUTPUT_DIR/deploy-context.json"
  GITLAB_CONTEXT_STATUS=$?
  set -e
  if [[ "$GITLAB_CONTEXT_STATUS" -ne 0 ]]; then
    echo "GitLab context collection failed with exit code $GITLAB_CONTEXT_STATUS" >&2
  fi
else
  echo "GitLab context skipped: script not found: $GITLAB_CONTEXT_SCRIPT" >&2
fi

OCR_STATUS=0
OCR_STDERR="$OUTPUT_DIR/ocr-stderr.log"
OCR_RESULT="$OUTPUT_DIR/ocr-result.json"

if ! command -v ocr >/dev/null 2>&1; then
  OCR_STATUS=127
  echo "ocr command not found in gate image or Jenkins agent" > "$OCR_STDERR"
elif [[ -z "$BASE_COMMIT" ]]; then
  OCR_STATUS=2
  echo "base commit is empty; cannot run deployment diff review" > "$OCR_STDERR"
else
  set +e
  ocr review \
    --from "$BASE_COMMIT" \
    --to "$DEPLOY_COMMIT" \
    --format json \
    --audience agent \
    > "$OCR_RESULT" 2> "$OCR_STDERR"
  OCR_STATUS=$?
  set -e
fi

REPORT_PATH="$OUTPUT_DIR/gate-report.json"
CONFIRMATION_MD="$OUTPUT_DIR/ai-agent-confirmation.md"
CONFIRMATION_DOCX="${GATE_CONFIRM_DOCX:-$OUTPUT_DIR/AI-agent-confirmation.docx}"

set +e
python3 "$EVALUATOR_PATH" \
  --config "$CONFIG_PATH" \
  --context "$OUTPUT_DIR/deploy-context.json" \
  --changed-files "$OUTPUT_DIR/changed-files.txt" \
  --ocr-result "$OCR_RESULT" \
  --ocr-stderr "$OCR_STDERR" \
  --ocr-exit-code "$OCR_STATUS" \
  --report "$REPORT_PATH" \
  --confirmation "$CONFIRMATION_MD"
EVAL_STATUS=$?
set -e

if [[ -f "$DOCX_GENERATOR" && -f "$DOCX_TEMPLATE" && -f "$REPORT_PATH" ]]; then
  python3 "$DOCX_GENERATOR" \
    --template "$DOCX_TEMPLATE" \
    --report "$REPORT_PATH" \
    --markdown "$CONFIRMATION_MD" \
    --output "$CONFIRMATION_DOCX"
else
  echo "docx confirmation skipped: generator/template/report not found" >&2
fi

exit "$EVAL_STATUS"
