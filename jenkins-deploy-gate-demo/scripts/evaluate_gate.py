#!/usr/bin/env python3
import argparse
import fnmatch
import json
from datetime import datetime, timezone
from pathlib import Path


def load_json(path, default):
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return default
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_text(path):
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def read_lines(path):
    return [line.strip() for line in read_text(path).splitlines() if line.strip()]


def collect_comments(ocr_result):
    comments = ocr_result.get("comments", [])
    if isinstance(comments, list):
        return comments
    return []


def severity_counts(comments):
    counts = {}
    for item in comments:
        severity = str(item.get("severity", "unknown")).lower()
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def match_risks(files, patterns_by_risk):
    risks = {}
    for risk, patterns in patterns_by_risk.items():
        matched = []
        for file_path in files:
            normalized = file_path.replace("\\", "/")
            if any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns):
                matched.append(file_path)
        if matched:
            risks[risk] = matched
    return risks


def required_field_values(context, changed_files, risks):
    return {
        "deploy_commit": context.get("deploy_commit", ""),
        "base_commit": context.get("base_commit", ""),
        "target_env": context.get("target_env", ""),
        "deploy_diff_summary": f"{len(changed_files)} changed file(s)",
        "regression_strategy": regression_strategy(risks, changed_files),
    }


def regression_strategy(risks, changed_files):
    if not changed_files:
        return ""
    if {"database", "api_contract", "permission"} & set(risks):
        return "Full Regression"
    if risks:
        return "Selective Regression"
    return "Sanity Check Only"


def make_decision(config, context, ocr_exit_code, ocr_stderr, comments, changed_files, risks):
    blocking = []
    warnings = []
    counts = severity_counts(comments)

    if config.get("require_deploy_base", True) and not context.get("base_commit"):
        blocking.append("base_commit is required but empty")

    if config.get("require_gitlab_context", False):
        if not context.get("gitlab_project_id") and not context.get("gitlab_project_url"):
            blocking.append("GitLab context is required but missing")

    if config.get("require_ocr_success", True) and ocr_exit_code != 0:
        detail = ocr_stderr.strip().splitlines()
        suffix = f": {detail[0]}" if detail else ""
        blocking.append(f"OCR execution failed with exit code {ocr_exit_code}{suffix}")

    for severity in config.get("blocking_severities", ["critical", "high"]):
        count = counts.get(severity.lower(), 0)
        if count > 0:
            blocking.append(f"OCR found {count} {severity} finding(s)")

    max_medium = config.get("max_medium_findings")
    if isinstance(max_medium, int) and counts.get("medium", 0) > max_medium:
        blocking.append(f"OCR found {counts.get('medium', 0)} medium finding(s), limit is {max_medium}")

    fields = required_field_values(context, changed_files, risks)
    missing = [name for name in config.get("confirm_required_fields", []) if not fields.get(name)]
    if config.get("require_confirm_required_fields", True) and missing:
        blocking.append("confirmation required field(s) missing: " + ", ".join(missing))

    if not context.get("gitlab_mr_iid"):
        warnings.append("No GitLab MR IID was provided; confirmation sheet will use commit-level context")
    if risks:
        warnings.append("Risk-sensitive files changed: " + ", ".join(sorted(risks.keys())))

    return {
        "status": "PASS" if not blocking else "BLOCKED",
        "blocking_reasons": blocking,
        "warnings": warnings,
        "severity_counts": counts,
        "required_fields": fields,
    }


def write_confirmation(path, context, changed_files, risks, decision, comments):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    top_comments = comments[:20]
    lines = [
        "# AI agent提测确认单",
        "",
        f"- 准入结论: {decision['status']}",
        f"- 生成时间: {now}",
        f"- 目标环境: {context.get('target_env', '')}",
        f"- 部署分支: {context.get('deploy_branch', '')}",
        f"- 基准 Commit: {context.get('base_commit', '')}",
        f"- 部署 Commit: {context.get('deploy_commit', '')}",
        f"- Jenkins Job: {context.get('repo', '')}",
        f"- Build Number: {context.get('build_number', '')}",
        f"- GitLab MR: {context.get('gitlab_mr_iid', '') or 'N/A'}",
        "",
        "## 1. 基础信息",
        "",
        "| 字段 | 值 |",
        "| --- | --- |",
        f"| 提测版本 | {context.get('build_number', '')}-{context.get('deploy_commit', '')[:12]} |",
        f"| 研发负责人 | {context.get('trigger_user', '') or '待补充'} |",
        f"| 提测日期 | {now[:10]} |",
        "",
        "## 2. 需求实现清单",
        "",
        "| 需求编号 | 需求点简述 | 实现状态 | 主要代码文件 |",
        "| --- | --- | --- | --- |",
        f"| 待补充 | Jenkins部署差量: {len(changed_files)} 个文件 | 待确认 | {', '.join(changed_files[:8]) or 'N/A'} |",
        "",
        "## 3. 需求影响半径与回归策略",
        "",
        "| 可能涉及的需求/模块 | 回归深度 | 影响原因 |",
        "| --- | --- | --- |",
    ]
    if risks:
        for risk, files in risks.items():
            lines.append(f"| {risk} | {decision['required_fields']['regression_strategy']} | 命中文件: {', '.join(files[:6])} |")
    else:
        lines.append(f"| 常规代码变更 | {decision['required_fields']['regression_strategy'] or '待补充'} | 未命中特定风险文件模式 |")

    lines.extend([
        "",
        "## 4. 高风险变更标记",
        "",
        "| 风险类型 | 是否命中 | 说明 |",
        "| --- | --- | --- |",
    ])
    for risk in ["database", "api_contract", "permission", "config", "async_or_concurrency"]:
        files = risks.get(risk, [])
        lines.append(f"| {risk} | {'是' if files else '否'} | {', '.join(files[:6]) if files else ''} |")

    lines.extend([
        "",
        "## 5. 准入检查",
        "",
        "| 检查项 | 结果 |",
        "| --- | --- |",
        f"| OCR执行 | {'通过' if not any('OCR execution failed' in r for r in decision['blocking_reasons']) else '失败'} |",
        f"| OCR严重问题 | {json.dumps(decision['severity_counts'], ensure_ascii=False)} |",
        f"| 部署差量 | {len(changed_files)} 个文件 |",
        f"| 回归范围 | {decision['required_fields']['regression_strategy'] or '待补充'} |",
        f"| 最终结论 | {decision['status']} |",
        "",
        "## 6. 阻断原因",
        "",
    ])
    if decision["blocking_reasons"]:
        lines.extend([f"- {reason}" for reason in decision["blocking_reasons"]])
    else:
        lines.append("- 无")

    lines.extend([
        "",
        "## 7. OCR问题摘要",
        "",
    ])
    if top_comments:
        for idx, item in enumerate(top_comments, 1):
            lines.append(
                f"{idx}. [{item.get('severity', 'unknown')}/{item.get('category', 'unknown')}] "
                f"{item.get('path', '')}: {item.get('content', '')}"
            )
    else:
        lines.append("- 无")

    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--changed-files", required=True)
    parser.add_argument("--ocr-result", required=True)
    parser.add_argument("--ocr-stderr", required=True)
    parser.add_argument("--ocr-exit-code", required=True, type=int)
    parser.add_argument("--report", required=True)
    parser.add_argument("--confirmation", required=True)
    args = parser.parse_args()

    config = load_json(args.config, {})
    context = load_json(args.context, {})
    ocr_result = load_json(args.ocr_result, {})
    ocr_stderr = read_text(args.ocr_stderr)
    changed_files = read_lines(args.changed_files)
    comments = collect_comments(ocr_result)
    risks = match_risks(changed_files, config.get("risk_file_patterns", {}))

    decision = make_decision(config, context, args.ocr_exit_code, ocr_stderr, comments, changed_files, risks)
    report = {
        "decision": decision,
        "context": context,
        "changed_files": changed_files,
        "risks": risks,
        "ocr_exit_code": args.ocr_exit_code,
        "ocr_stderr": ocr_stderr,
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_confirmation(args.confirmation, context, changed_files, risks, decision, comments)

    if decision["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
