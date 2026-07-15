#!/usr/bin/env python3
import argparse
import html
import json
import shutil
import tempfile
import zipfile
from pathlib import Path


def load_json(path):
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return {}
    with p.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def text_node(value):
    value = "" if value is None else str(value)
    return html.escape(value, quote=False)


def paragraph(text, style=None):
    style_xml = ""
    if style:
        style_xml = f'<w:pPr><w:pStyle w:val="{html.escape(style)}"/></w:pPr>'
    return (
        "<w:p>"
        f"{style_xml}"
        "<w:r>"
        f"<w:t>{text_node(text)}</w:t>"
        "</w:r>"
        "</w:p>"
    )


def bullet(text):
    return paragraph("- " + text)


def build_summary_xml(report):
    decision = report.get("decision", {})
    context = report.get("context", {})
    risks = report.get("risks", {})
    changed_files = report.get("changed_files", [])

    lines = [
        paragraph("AI Gate 自动摘要", "2"),
        bullet(f"准入结论: {decision.get('status', 'UNKNOWN')}"),
        bullet(f"目标环境: {context.get('target_env', '')}"),
        bullet(f"Jenkins Job: {context.get('repo', '')}"),
        bullet(f"Build Number: {context.get('build_number', '')}"),
        bullet(f"部署分支: {context.get('deploy_branch', '')}"),
        bullet(f"基准 Commit: {context.get('base_commit', '')}"),
        bullet(f"部署 Commit: {context.get('deploy_commit', '')}"),
        bullet(f"变更文件数: {len(changed_files)}"),
        bullet("OCR 严重程度统计: " + json.dumps(decision.get("severity_counts", {}), ensure_ascii=False)),
        bullet("回归策略: " + str(decision.get("required_fields", {}).get("regression_strategy", ""))),
    ]

    if risks:
        lines.append(paragraph("风险文件命中", "3"))
        for risk, files in risks.items():
            lines.append(bullet(f"{risk}: {', '.join(files[:8])}"))

    blocking = decision.get("blocking_reasons", [])
    lines.append(paragraph("阻断原因", "3"))
    if blocking:
        for reason in blocking:
            lines.append(bullet(reason))
    else:
        lines.append(bullet("无"))

    warnings = decision.get("warnings", [])
    if warnings:
        lines.append(paragraph("提示信息", "3"))
        for warning in warnings:
            lines.append(bullet(warning))

    lines.append(paragraph("调试产物"))
    lines.append(bullet("完整结构化结果见 gate-output/gate-report.json"))
    lines.append(bullet("Markdown 草稿见 gate-output/ai-agent-confirmation.md"))
    return "".join(lines)


def append_to_document_xml(document_xml, insert_xml):
    marker = "<w:sectPr"
    idx = document_xml.rfind(marker)
    if idx >= 0:
        return document_xml[:idx] + insert_xml + document_xml[idx:]

    marker = "</w:body>"
    idx = document_xml.rfind(marker)
    if idx >= 0:
        return document_xml[:idx] + insert_xml + document_xml[idx:]

    raise ValueError("word/document.xml does not contain a writable body marker")


def write_docx(template_path, output_path, report):
    template = Path(template_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    if not template.exists():
        raise FileNotFoundError(f"template not found: {template}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        with zipfile.ZipFile(template, "r") as zin:
            zin.extractall(tmp_dir)

        document_path = tmp_dir / "word" / "document.xml"
        raw = document_path.read_text(encoding="utf-8", errors="replace")
        updated = append_to_document_xml(raw, build_summary_xml(report))
        document_path.write_text(updated, encoding="utf-8")

        if output.exists():
            output.unlink()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zout:
            for path in tmp_dir.rglob("*"):
                if path.is_file():
                    zout.write(path, path.relative_to(tmp_dir).as_posix())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--markdown", default="")
    args = parser.parse_args()

    report = load_json(args.report)
    write_docx(args.template, args.output, report)

    if args.markdown:
        md_path = Path(args.markdown)
        if md_path.exists():
            target_md = Path(args.output).with_suffix(".md")
            if md_path.resolve() != target_md.resolve() and str(md_path).lower() != str(target_md).lower():
                shutil.copyfile(md_path, target_md)


if __name__ == "__main__":
    main()
