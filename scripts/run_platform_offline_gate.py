#!/usr/bin/env python3
import argparse
import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path


def load_json(path):
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return {}
    with p.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def copy_required(src, dst):
    src_path = Path(src)
    if not src_path.exists():
        raise FileNotFoundError(f"required input not found: {src_path}")
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_path, dst_path)


def normalize_review_result(raw):
    if isinstance(raw, list):
        return {"comments": raw}
    if not isinstance(raw, dict):
        raise ValueError("review result must be a JSON object or a comments array")
    comments = raw.get("comments", [])
    if comments is None:
        comments = []
    if not isinstance(comments, list):
        raise ValueError("review result field 'comments' must be an array")
    raw["comments"] = comments
    return raw


def read_review_result(args):
    sources = [
        bool(args.review_json),
        bool(args.review_json_file),
        bool(args.review_json_base64),
        args.review_json_stdin,
    ]
    if sum(1 for item in sources if item) != 1:
        raise ValueError(
            "provide exactly one review source: --review-json, --review-json-file, "
            "--review-json-base64, or --review-json-stdin"
        )

    if args.review_json:
        text = args.review_json
    elif args.review_json_file:
        text = Path(args.review_json_file).read_text(encoding="utf-8-sig")
    elif args.review_json_base64:
        text = base64.b64decode(args.review_json_base64).decode("utf-8")
    else:
        text = sys.stdin.read()

    return normalize_review_result(json.loads(text))


def run_command(cmd, cwd):
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Run the platform offline deploy gate using a model-generated OCR-compatible review result."
    )
    parser.add_argument("--app-root", default="")
    parser.add_argument("--input-dir", default="input")
    parser.add_argument("--output-dir", default="output/gate-output")
    parser.add_argument("--config", default="jenkins-deploy-gate-demo/gate-config.example.json")
    parser.add_argument("--template", default="templates/AI-agent-confirmation.docx")
    parser.add_argument("--review-json", default="")
    parser.add_argument("--review-json-file", default="")
    parser.add_argument("--review-json-base64", default="")
    parser.add_argument("--review-json-stdin", action="store_true")
    parser.add_argument("--fail-on-blocked", action="store_true")
    args = parser.parse_args()

    script_path = Path(__file__).resolve()
    app_root = Path(args.app_root).resolve() if args.app_root else script_path.parent.parent
    input_dir = (app_root / args.input_dir).resolve()
    output_dir = (app_root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    deploy_context = output_dir / "deploy-context.json"
    changed_files = output_dir / "changed-files.txt"
    ocr_result = output_dir / "ocr-result.json"
    ocr_stderr = output_dir / "ocr-stderr.log"
    gate_report = output_dir / "gate-report.json"
    confirmation_md = output_dir / "ai-agent-confirmation.md"
    confirmation_docx = output_dir / "AI-agent-confirmation.docx"
    run_manifest = output_dir / "platform-offline-run.json"

    copy_required(input_dir / "deploy-context.json", deploy_context)
    copy_required(input_dir / "changed-files.txt", changed_files)
    review_result = read_review_result(args)
    write_json(ocr_result, review_result)
    ocr_stderr.write_text("", encoding="utf-8")

    evaluator = app_root / "jenkins-deploy-gate-demo" / "scripts" / "evaluate_gate.py"
    config = (app_root / args.config).resolve()
    evaluate_cmd = [
        sys.executable,
        str(evaluator),
        "--config",
        str(config),
        "--context",
        str(deploy_context),
        "--changed-files",
        str(changed_files),
        "--ocr-result",
        str(ocr_result),
        "--ocr-stderr",
        str(ocr_stderr),
        "--ocr-exit-code",
        "0",
        "--report",
        str(gate_report),
        "--confirmation",
        str(confirmation_md),
    ]
    eval_proc = run_command(evaluate_cmd, app_root)

    docx_status = "skipped"
    docx_stdout = ""
    docx_stderr = ""
    docx_rc = 0
    generator = app_root / "scripts" / "generate_confirmation_docx.py"
    template = (app_root / args.template).resolve()
    if generator.exists() and template.exists() and gate_report.exists():
        docx_cmd = [
            sys.executable,
            str(generator),
            "--template",
            str(template),
            "--report",
            str(gate_report),
            "--output",
            str(confirmation_docx),
        ]
        docx_proc = run_command(docx_cmd, app_root)
        docx_status = "ok" if docx_proc.returncode == 0 else "failed"
        docx_stdout = docx_proc.stdout
        docx_stderr = docx_proc.stderr
        docx_rc = docx_proc.returncode

    report = load_json(gate_report)
    decision = report.get("decision", {})
    gate_status = decision.get("status", "UNKNOWN")
    manifest = {
        "status": "ok",
        "gate_status": gate_status,
        "evaluator_exit_code": eval_proc.returncode,
        "docx_status": docx_status,
        "docx_exit_code": docx_rc,
        "artifacts": {
            "deploy_context": str(deploy_context),
            "changed_files": str(changed_files),
            "ocr_result": str(ocr_result),
            "gate_report": str(gate_report),
            "confirmation_md": str(confirmation_md),
            "confirmation_docx": str(confirmation_docx),
        },
        "evaluator_stdout": eval_proc.stdout,
        "evaluator_stderr": eval_proc.stderr,
        "docx_stdout": docx_stdout,
        "docx_stderr": docx_stderr,
    }
    write_json(run_manifest, manifest)

    print(f"Gate Result: {gate_status}")
    for reason in decision.get("blocking_reasons", []):
        print(f"- {reason}")
    print(f"Artifacts: {output_dir}")

    if docx_rc != 0:
        return docx_rc
    if args.fail_on_blocked and eval_proc.returncode != 0:
        return eval_proc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
