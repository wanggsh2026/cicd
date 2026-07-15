# OpenCode Workspace

This directory contains the OpenCode agent assets for the Jenkins deploy gate.

- `skills/` contains task instructions and reusable procedures.
- `agents/` contains agent role definitions.
- `commands/` contains user-facing slash command entrypoints.
- `scripts/` contains deterministic helper scripts used by skills or commands.

Business repositories are mounted or checked out outside `.opencode`; gate outputs should be written to `gate-output/` or another Jenkins artifact directory.
