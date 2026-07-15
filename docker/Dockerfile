FROM node:22-bookworm-slim

ARG OCR_PACKAGE_VERSION=latest
ARG OPENCODE_PACKAGE_VERSION=latest

ENV APP_ROOT=/app \
    GATE_MODE=shell \
    GATE_OUTPUT_DIR=gate-output \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      curl \
      git \
      python3 \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g \
      "@alibaba-group/open-code-review@${OCR_PACKAGE_VERSION}" \
      "opencode-ai@${OPENCODE_PACKAGE_VERSION}" \
    && npm cache clean --force

WORKDIR /app

COPY .opencode/ .opencode/
COPY docker/ docker/
COPY jenkins-deploy-gate-demo/ jenkins-deploy-gate-demo/
COPY scripts/ scripts/
COPY templates/ templates/
COPY opencode.json opencode.json

RUN chmod +x docker/entrypoint.sh \
    && chmod +x jenkins-deploy-gate-demo/scripts/run-deploy-gate.sh \
    && python3 -m py_compile \
      scripts/generate_confirmation_docx.py \
      scripts/gitlab_context.py \
      scripts/resolve_deploy_base.py \
      scripts/run_platform_offline_gate.py \
      jenkins-deploy-gate-demo/scripts/evaluate_gate.py

WORKDIR /workspace

ENTRYPOINT ["/app/docker/entrypoint.sh"]
