#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[deploy-gate] %s\n' "$*"
}

fail() {
  printf '[deploy-gate] ERROR: %s\n' "$*" >&2
  exit 2
}

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    fail "required command not found: $name"
  fi
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app_root="$(cd "$script_dir/.." && pwd)"

workspace="${GATE_WORKSPACE:-${WORKSPACE:-}}"
if [[ -z "$workspace" ]]; then
  if [[ -d /workspace ]]; then
    workspace=/workspace
  else
    workspace="$(pwd)"
  fi
fi

if [[ ! -d "$workspace" ]]; then
  fail "workspace does not exist: $workspace"
fi

export WORKSPACE="$workspace"
export GATE_WORKSPACE="$workspace"
export GATE_OUTPUT_DIR="${GATE_OUTPUT_DIR:-gate-output}"
export GATE_CONFIG="${GATE_CONFIG:-$app_root/jenkins-deploy-gate-demo/gate-config.example.json}"

check_required_env() {
  local missing=()

  [[ -n "${TARGET_ENV:-}" ]] || missing+=("TARGET_ENV")
  [[ -n "${OCR_LLM_URL:-}" ]] || missing+=("OCR_LLM_URL")
  [[ -n "${OCR_LLM_TOKEN:-}" ]] || missing+=("OCR_LLM_TOKEN")
  [[ -n "${OCR_LLM_MODEL:-}" ]] || missing+=("OCR_LLM_MODEL")

  if (( ${#missing[@]} > 0 )); then
    fail "missing required environment variable(s): ${missing[*]}"
  fi

  if [[ -z "${LAST_SUCCESS_DEPLOY_COMMIT:-}" ]]; then
    log "LAST_SUCCESS_DEPLOY_COMMIT is empty; demo script will fall back to HEAD~1 when possible"
  fi
}

configure_ocr() {
  if ! command -v ocr >/dev/null 2>&1; then
    log "ocr command not found; gate script will report OCR execution failure"
    return 0
  fi

  log "configuring ocr LLM endpoint"
  ocr config set llm.url "$OCR_LLM_URL" >/dev/null
  ocr config set llm.auth_token "$OCR_LLM_TOKEN" >/dev/null
  ocr config set llm.model "$OCR_LLM_MODEL" >/dev/null

  if [[ -n "${OCR_USE_ANTHROPIC:-}" ]]; then
    ocr config set llm.use_anthropic "$OCR_USE_ANTHROPIC" >/dev/null
  fi
  if [[ -n "${OCR_LLM_AUTH_HEADER:-}" ]]; then
    ocr config set llm.auth_header "$OCR_LLM_AUTH_HEADER" >/dev/null
  fi
  if [[ -n "${OCR_LLM_EXTRA_HEADERS:-}" ]]; then
    ocr config set llm.extra_headers "$OCR_LLM_EXTRA_HEADERS" >/dev/null
  fi
  if [[ -n "${OCR_LLM_EXTRA_BODY:-}" ]]; then
    ocr config set llm.extra_body "$OCR_LLM_EXTRA_BODY" >/dev/null
  fi
}

prepare_workspace() {
  log "using app root: $app_root"
  log "using workspace: $workspace"
  mkdir -p "$workspace/$GATE_OUTPUT_DIR"

  if [[ -d "$app_root/.opencode" ]]; then
    export OPENCODE_APP_ROOT="${OPENCODE_APP_ROOT:-$app_root}"
  fi
}

run_opencode_gate() {
  if [[ -z "${OPENCODE_GATE_COMMAND:-}" ]]; then
    return 127
  fi
  require_command opencode
  log "running OpenCode gate command: $OPENCODE_GATE_COMMAND"
  (
    cd "$workspace"
    bash -lc "$OPENCODE_GATE_COMMAND"
  )
}

run_shell_gate() {
  local gate_script="$app_root/jenkins-deploy-gate-demo/scripts/run-deploy-gate.sh"
  if [[ ! -f "$gate_script" ]]; then
    fail "gate script not found: $gate_script"
  fi
  log "running shell gate fallback"
  (
    cd "$workspace"
    bash "$gate_script"
  )
}

run_gate() {
  local rc=0

  if [[ "${GATE_MODE:-auto}" == "opencode" ]]; then
    run_opencode_gate
    return $?
  fi

  if [[ "${GATE_MODE:-auto}" == "shell" ]]; then
    run_shell_gate
    return $?
  fi

  if command -v opencode >/dev/null 2>&1 && [[ -n "${OPENCODE_GATE_COMMAND:-}" ]]; then
    run_opencode_gate
    return $?
  fi

  run_shell_gate
  rc=$?
  return $rc
}

main() {
  require_command git
  require_command bash
  require_command python3

  check_required_env
  configure_ocr
  prepare_workspace

  local rc=0
  set +e
  if (( $# > 0 )); then
    log "running custom command: $*"
    "$@"
    rc=$?
  else
    run_gate
    rc=$?
  fi
  set -e

  if [[ -d "$workspace/$GATE_OUTPUT_DIR" ]]; then
    log "gate artifacts:"
    find "$workspace/$GATE_OUTPUT_DIR" -maxdepth 2 -type f -print | sed 's/^/[deploy-gate]   /'
  fi

  if (( rc == 0 )); then
    log "gate passed"
  else
    log "gate blocked or failed with exit code $rc"
  fi
  exit "$rc"
}

main "$@"
