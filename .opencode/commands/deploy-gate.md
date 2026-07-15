# Deploy Gate

Run the Jenkins deploy gate for the current workspace.

Expected inputs are provided by Jenkins environment variables, including:

- `TARGET_ENV`
- `DEPLOY_COMMIT`
- `LAST_SUCCESS_DEPLOY_COMMIT`
- `OCR_LLM_URL`
- `OCR_LLM_TOKEN`
- `OCR_LLM_MODEL`
- `GITLAB_TOKEN`

The command should produce:

- `gate-output/deploy-context.json`
- `gate-output/changed-files.txt`
- `gate-output/ocr-result.json`
- `gate-output/gate-report.json`
- `gate-output/ai-agent-confirmation.md`

For the initial demo, call:

```bash
bash jenkins-deploy-gate-demo/scripts/run-deploy-gate.sh
```

For business Agent platform offline debugging, do not manually edit artifact files one by one. Generate an OCR-compatible JSON result from `input/diff.patch`, then pass it to the platform writer:

```bash
python3 scripts/run_platform_offline_gate.py --review-json-stdin <<'JSON'
{"comments":[]}
JSON
```

This command writes `output/gate-output/ocr-result.json`, runs deterministic evaluation, and generates the confirmation artifacts.
