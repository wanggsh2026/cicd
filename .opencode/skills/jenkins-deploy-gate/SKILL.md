# Jenkins Deploy Gate

Use this skill when Jenkins needs to decide whether the current commit can be deployed to a target environment.

This skill implements a single Jenkins release/deploy gate. Jenkins remains the final blocking system; this OpenCode skill coordinates evidence collection and calls deterministic scripts to produce the gate decision.

## Goal

Answer one question:

```text
Can this exact commit be deployed to this exact environment?
```

Do not answer whether the code can be merged. Merge policy belongs to GitLab or an earlier CI gate.

## Required Inputs

Read deployment context from Jenkins environment variables and files prepared by the pipeline:

- `TARGET_ENV`: deployment environment, such as `dev`, `test`, `uat`, or `prod`
- `DEPLOY_COMMIT`: current commit being deployed; default may be `git rev-parse HEAD`
- `LAST_SUCCESS_DEPLOY_COMMIT`: baseline commit already deployed to the target environment
- `GATE_OUTPUT_DIR`: artifact output directory; default `gate-output`
- `GATE_CONFIG`: gate rule config; default `jenkins-deploy-gate-demo/gate-config.example.json`
- `OCR_LLM_URL`, `OCR_LLM_TOKEN`, `OCR_LLM_MODEL`: OpenCodeReview LLM configuration

Optional GitLab context:

- `GITLAB_TOKEN`
- `GITLAB_PROJECT_ID`
- `GITLAB_PROJECT_URL`
- `GITLAB_MR_IID`

## Deterministic Scripts

Prefer scripts for all operations that decide gate results. The agent may explain, summarize, or fill narrative fields, but the final PASS/BLOCKED decision must come from deterministic script output.

Current demo scripts:

```text
jenkins-deploy-gate-demo/scripts/run-deploy-gate.sh
jenkins-deploy-gate-demo/scripts/evaluate_gate.py
scripts/run_platform_offline_gate.py
scripts/resolve_deploy_base.py
scripts/gitlab_context.py
```

Future OpenCode-native scripts should live under:

```text
.opencode/scripts/
```

## Workflow

### Business Agent Platform Offline Mode

When running inside the business Agent platform without direct OCR CLI access, use this write path:

1. Read:

   ```text
   input/deploy-context.json
   input/changed-files.txt
   input/diff.patch
   ```

2. Review `input/diff.patch` with the platform model and produce an OCR-compatible JSON object:

   ```json
   {
     "comments": [
       {
         "severity": "high",
         "category": "test",
         "path": "path/to/file",
         "line": null,
         "content": "finding"
       }
     ]
   }
   ```

3. Do not manually create every artifact. Pass the JSON result to the deterministic writer:

   ```bash
   python3 scripts/run_platform_offline_gate.py --review-json-stdin <<'JSON'
   {"comments":[]}
   JSON
   ```

4. The script must write all artifacts under:

   ```text
   output/gate-output/
   ```

5. Treat `output/gate-output/gate-report.json` as the source of truth. The conversational answer must match this file.

1. Resolve deployment context.
   - Identify `deploy_commit`.
   - Identify `base_commit` from `LAST_SUCCESS_DEPLOY_COMMIT`.
   - Prefer the deployment base resolver when available. It may read manual parameters, environment VERSION files, artifact metadata, deployment platform records, or Jenkins last-successful-build metadata.
   - Record target environment, branch, Jenkins job, build number, and trigger user.
   - Write deployment base evidence to `gate-output/deploy-base.json`.
   - Write `gate-output/deploy-context.json`.

2. Resolve deployment diff.
   - Review the deployment delta, not only a single MR delta.
   - Use `base_commit..deploy_commit`.
   - Write changed files to `gate-output/changed-files.txt`.

3. Collect GitLab context when available.
   - Read MR title, description, labels, author, source branch, target branch, and requirement identifiers.
   - If GitLab context is missing, continue with commit-level context unless config requires GitLab context.
   - Write normalized GitLab context to `gate-output/gitlab-context.json`.

4. Run OpenCodeReview.
   - Use the `ocr` CLI.
   - Review the deployment diff.
   - Use JSON output for deterministic parsing.

   Example command:

   ```bash
   ocr review --from "$BASE_COMMIT" --to "$DEPLOY_COMMIT" --format json --audience agent
   ```

   Write:

   ```text
   gate-output/ocr-result.json
   gate-output/ocr-stderr.log
   ```

5. Evaluate gate decision.
   - Parse `ocr-result.json`.
   - Count findings by severity.
   - Match changed files against configured risk patterns.
   - Validate confirmation required fields.
   - Apply blocking rules from `gate-config`.
   - Write `gate-output/gate-report.json`.

6. Generate confirmation sheet.
   - Always generate the confirmation sheet, even when deployment is blocked.
   - Include deployment context, changed files, risk tags, regression strategy, OCR summary, and blocking reasons.
   - Current demo writes Markdown:

     ```text
     gate-output/ai-agent-confirmation.md
     ```

   - The demo also writes a Word document when the template is available:

     ```text
     gate-output/AI-agent-confirmation.docx
     ```

7. Return gate result.
   - Exit `0` only when gate status is `PASS`.
   - Exit non-zero when status is `BLOCKED` or when required evidence cannot be produced.

## Output Contract

The gate must produce these artifacts:

```text
gate-output/deploy-context.json
gate-output/deploy-base.json
gate-output/gitlab-context.json
gate-output/changed-files.txt
gate-output/ocr-result.json
gate-output/ocr-stderr.log
gate-output/gate-report.json
gate-output/ai-agent-confirmation.md
gate-output/AI-agent-confirmation.docx
```

`gate-report.json` is the source of truth for Jenkins blocking behavior. Jenkins should archive the whole `gate-output/` directory regardless of pass or fail.

## Blocking Rules

Default blocking reasons:

- Required environment variables are missing.
- `base_commit` is required but missing.
- OCR cannot run successfully and `require_ocr_success` is true.
- OCR reports any configured blocking severity, typically `critical` or `high`.
- OCR reports more `medium` findings than allowed by config.
- Confirmation required fields are missing.
- Required GitLab context is missing and `require_gitlab_context` is true.

Warnings do not block unless explicitly configured:

- GitLab MR context is missing.
- Risk-sensitive files changed.
- Medium or low findings exist but are below threshold.
- Requirement identifier is missing.

## Confirmation Sheet Fields

The confirmation sheet should cover:

- Basic information: target environment, build number, deployment commit, baseline commit, trigger user, date.
- Requirement implementation list: requirement id, brief description, implementation status, main changed files.
- Impact radius and regression strategy: affected modules, regression depth, impact reason.
- Contract and data impact: database, cache key, API input/output, error code or enum, config item.
- High-risk markers: boundary condition, concurrency, precision calculation, state transition, third-party callback, permission/auth.
- Admission checks: OCR result, blocking reasons, review status, test/coverage fields when available.

## Safety Notes

- Do not deploy or restart services from this skill. Deployment happens only after Jenkins sees a passing gate.
- Do not place business repositories or generated artifacts inside `.opencode/`.
- Do not embed tokens in repository files or Docker images. Jenkins credentials should provide secrets at runtime.
- Treat the current deployment commit as the primary anchor. GitLab MR information is supporting evidence, not the only source of truth.
