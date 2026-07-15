#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


SHA_PATTERN = re.compile(r"\b[0-9a-fA-F]{7,40}\b")


DEFAULT_JSON_KEYS = [
    "last_success_deploy_commit",
    "last_successful_deploy_commit",
    "base_commit",
    "deploy_base_commit",
    "deployed_commit",
    "deploy_commit",
    "commit",
    "git_commit",
    "gitCommit",
    "revision",
    "sha",
]


def first_non_empty(*values):
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def is_sha(value):
    return bool(value and SHA_PATTERN.fullmatch(str(value).strip()))


def normalize_commit(value):
    if not value:
        return ""
    text = str(value).strip()
    if is_sha(text):
        return text
    match = SHA_PATTERN.search(text)
    return match.group(0) if match else ""


def run_git(args, cwd):
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd or None,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def resolve_commitish(value, workspace):
    commit = normalize_commit(value)
    if commit:
        return commit
    if not value:
        return ""
    resolved = run_git(["rev-parse", str(value).strip()], workspace)
    return normalize_commit(resolved)


def load_text(path):
    if not path:
        return ""
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return ""
    return p.read_text(encoding="utf-8-sig", errors="replace")


def find_in_json(data, keys=None):
    keys = set(keys or DEFAULT_JSON_KEYS)
    found = []

    def walk(value, path=""):
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                if key in keys:
                    commit = normalize_commit(child)
                    if commit:
                        found.append((child_path, commit))
                walk(child, child_path)
        elif isinstance(value, list):
            for idx, child in enumerate(value):
                walk(child, f"{path}[{idx}]")

    walk(data)
    return found[0] if found else ("", "")


def parse_commit_from_file(path, keys=None):
    text = load_text(path)
    if not text:
        return "", ""

    try:
        data = json.loads(text)
        field, commit = find_in_json(data, keys)
        if commit:
            return commit, f"json:{field}"
    except json.JSONDecodeError:
        pass

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped or ":" in stripped:
            sep = "=" if "=" in stripped else ":"
            key, value = stripped.split(sep, 1)
            if key.strip() in set(keys or DEFAULT_JSON_KEYS):
                commit = normalize_commit(value)
                if commit:
                    return commit, f"kv:{key.strip()}"

    commit = normalize_commit(text)
    if commit:
        return commit, "text"
    return "", ""


def http_get_json(url, token="", token_header="", username="", password="", timeout=20):
    headers = {"Accept": "application/json"}
    if token:
        headers[token_header or "PRIVATE-TOKEN"] = token
    if username and password:
        raw = f"{username}:{password}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HTTP request failed for {url}: {exc}") from exc


def resolve_from_manual(args, workspace):
    value = first_non_empty(args.manual_commit, os.getenv("LAST_SUCCESS_DEPLOY_COMMIT"))
    commit = resolve_commitish(value, workspace)
    if commit:
        return {
            "source": "manual",
            "commit": commit,
            "detail": "LAST_SUCCESS_DEPLOY_COMMIT or --manual-commit",
        }
    return {}


def resolve_from_version_file(args):
    path = first_non_empty(args.version_file, os.getenv("DEPLOY_VERSION_FILE"), os.getenv("VERSION_FILE"))
    commit, detail = parse_commit_from_file(path, args.commit_key) if path else ("", "")
    if commit:
        return {"source": "version_file", "commit": commit, "detail": f"{path}#{detail}"}
    return {}


def resolve_from_metadata_file(args):
    path = first_non_empty(args.metadata_file, os.getenv("DEPLOY_METADATA_FILE"), os.getenv("ARTIFACT_METADATA_FILE"))
    commit, detail = parse_commit_from_file(path, args.commit_key) if path else ("", "")
    if commit:
        return {"source": "metadata_file", "commit": commit, "detail": f"{path}#{detail}"}
    return {}


def resolve_from_deploy_record_api(args):
    url = first_non_empty(args.deploy_record_url, os.getenv("DEPLOY_RECORD_URL"))
    if not url:
        return {}
    data = http_get_json(
        url,
        token=first_non_empty(args.deploy_record_token, os.getenv("DEPLOY_RECORD_TOKEN")),
        token_header=first_non_empty(args.deploy_record_token_header, os.getenv("DEPLOY_RECORD_TOKEN_HEADER"), "Authorization"),
        timeout=args.timeout,
    )
    field, commit = find_in_json(data, args.commit_key)
    if commit:
        return {"source": "deploy_record_api", "commit": commit, "detail": f"{url}#{field}"}
    return {}


def jenkins_last_success_url(args):
    explicit = first_non_empty(args.jenkins_api_url, os.getenv("JENKINS_LAST_SUCCESS_API_URL"))
    if explicit:
        return explicit
    job_url = first_non_empty(args.jenkins_job_url, os.getenv("JOB_URL"))
    if not job_url:
        return ""
    return job_url.rstrip("/") + "/lastSuccessfulBuild/api/json"


def extract_jenkins_commit(data, keys=None):
    field, commit = find_in_json(data, keys)
    if commit:
        return field, commit

    actions = data.get("actions") if isinstance(data, dict) else []
    if isinstance(actions, list):
        for idx, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            revision = action.get("lastBuiltRevision")
            if isinstance(revision, dict):
                commit = normalize_commit(revision.get("SHA1"))
                if commit:
                    return f"actions[{idx}].lastBuiltRevision.SHA1", commit
            for param in action.get("parameters") or []:
                if not isinstance(param, dict):
                    continue
                name = param.get("name")
                if name in set(keys or DEFAULT_JSON_KEYS):
                    commit = normalize_commit(param.get("value"))
                    if commit:
                        return f"actions[{idx}].parameters.{name}", commit
    return "", ""


def resolve_from_jenkins(args):
    url = jenkins_last_success_url(args)
    if not url:
        return {}
    data = http_get_json(
        url,
        username=first_non_empty(args.jenkins_user, os.getenv("JENKINS_USER")),
        password=first_non_empty(args.jenkins_token, os.getenv("JENKINS_TOKEN")),
        timeout=args.timeout,
    )
    field, commit = extract_jenkins_commit(data, args.commit_key)
    if commit:
        return {"source": "jenkins_last_success", "commit": commit, "detail": f"{url}#{field}"}
    return {}


def resolve_from_git_fallback(args, workspace, deploy_commit):
    if not args.git_fallback:
        return {}
    if not deploy_commit:
        deploy_commit = run_git(["rev-parse", "HEAD"], workspace)
    if not deploy_commit:
        return {}
    parent = run_git(["rev-parse", f"{deploy_commit}~1"], workspace)
    commit = normalize_commit(parent)
    if commit:
        return {
            "source": "git_parent_fallback",
            "commit": commit,
            "detail": f"{deploy_commit}~1",
            "warning": "fallback only; not a real environment deployment baseline",
        }
    return {}


RESOLVERS = {
    "manual": resolve_from_manual,
    "version_file": resolve_from_version_file,
    "metadata_file": resolve_from_metadata_file,
    "deploy_record_api": resolve_from_deploy_record_api,
    "jenkins": resolve_from_jenkins,
    "git_fallback": resolve_from_git_fallback,
}


def resolve(args):
    workspace = first_non_empty(args.workspace, os.getenv("GATE_WORKSPACE"), os.getenv("WORKSPACE"), os.getcwd())
    deploy_commit = first_non_empty(args.deploy_commit, os.getenv("DEPLOY_COMMIT"))
    if not deploy_commit:
        deploy_commit = run_git(["rev-parse", "HEAD"], workspace)

    attempts = []
    order = args.source or ["manual", "version_file", "metadata_file", "deploy_record_api", "jenkins", "git_fallback"]
    for source in order:
        source = source.strip()
        if not source:
            continue
        resolver = RESOLVERS.get(source)
        if not resolver:
            attempts.append({"source": source, "status": "unknown_source"})
            continue
        try:
            if source in ("manual", "git_fallback"):
                item = resolver(args, workspace) if source == "manual" else resolver(args, workspace, deploy_commit)
            else:
                item = resolver(args)
            if item.get("commit"):
                item.update({"status": "resolved"})
                return {
                    "status": "resolved",
                    "base_commit": item["commit"],
                    "source": item["source"],
                    "detail": item.get("detail", ""),
                    "warning": item.get("warning", ""),
                    "deploy_commit": deploy_commit,
                    "attempts": attempts + [item],
                }
            attempts.append({"source": source, "status": "not_found"})
        except Exception as exc:
            attempts.append({"source": source, "status": "failed", "error": str(exc)})

    return {
        "status": "unresolved",
        "base_commit": "",
        "source": "",
        "detail": "",
        "warning": "",
        "deploy_commit": deploy_commit,
        "attempts": attempts,
    }


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Resolve the deployment baseline commit for Jenkins deploy gate.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workspace", default="")
    parser.add_argument("--deploy-commit", default="")
    parser.add_argument("--manual-commit", default="")
    parser.add_argument("--version-file", default="")
    parser.add_argument("--metadata-file", default="")
    parser.add_argument("--deploy-record-url", default="")
    parser.add_argument("--deploy-record-token", default="")
    parser.add_argument("--deploy-record-token-header", default="")
    parser.add_argument("--jenkins-api-url", default="")
    parser.add_argument("--jenkins-job-url", default="")
    parser.add_argument("--jenkins-user", default="")
    parser.add_argument("--jenkins-token", default="")
    parser.add_argument("--commit-key", action="append", default=[])
    parser.add_argument("--source", action="append", choices=sorted(RESOLVERS), default=[])
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--git-fallback", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    if not args.commit_key:
        args.commit_key = DEFAULT_JSON_KEYS

    result = resolve(args)
    write_json(args.output, result)

    if result.get("base_commit"):
        print(result["base_commit"])

    if args.strict and result.get("status") != "resolved":
        print("deployment baseline commit could not be resolved", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
