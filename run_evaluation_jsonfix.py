"""Evaluate with the original requests, repairing only invalid JSON escapes.

Usage: python run_evaluation_jsonfix.py --pdf_dir INPUT --out_jsonl NEW_RESULT ...
All evaluation arguments are forwarded to run_evaluation.py. Existing result
files are refused. Repairs and rejected responses are saved beside the output.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import uuid

import run_evaluation as original


AUDIT_DIR = None
FENCED_JSON = re.compile(r"```json\s*(.*?)```", re.DOTALL)


def repair_invalid_escapes(text):
    """Double only an invalid escape's backslash inside a JSON string.

    Legal escapes, including \\t and \\f, are left alone: they cannot safely
    be interpreted as LaTeX without changing the response's meaning.
    Offsets in the change list refer to the unmodified JSON candidate.
    """
    output, changes = [], []
    inside = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            inside = not inside
        if inside and char == "\\" and index + 1 < len(text):
            following = text[index + 1]
            if following not in '"\\/bfnrtu':
                output.append("\\\\")
                changes.append({"offset": index, "before": "\\", "after": "\\\\"})
                index += 1
                continue
            # Consume both characters so an escaped quote does not toggle the
            # string state and an escaped backslash is never repaired twice.
            output.append(text[index:index + 2])
            index += 2
            continue
        output.append(char)
        index += 1
    return "".join(output), changes


def write_audit(raw, candidate, repaired, changes, error, parsed_ok):
    if AUDIT_DIR is None:
        return
    directory = Path(AUDIT_DIR) / uuid.uuid4().hex
    directory.mkdir(parents=True, mode=0o700)
    values = {
        "response.txt": raw,
        "candidate.txt": candidate,
        "repaired.txt": repaired,
        "audit.json": json.dumps({
            "time": datetime.now(timezone.utc).isoformat(),
            "original_error": error,
            "changes": changes,
            "json_parse_ok": parsed_ok,
            "rubric_validation": "performed separately before accepting batch",
        }, ensure_ascii=False, indent=2) + "\n",
    }
    for name, value in values.items():
        path = directory / name
        with path.open("x", encoding="utf-8") as stream:
            os.chmod(path, 0o600)
            stream.write(value)


def parse_model_text(text):
    # Preserve every already-valid JSON response, including compact JSON.
    candidates = [text]
    match = FENCED_JSON.search(text)
    if match:
        candidates.append(match.group(1))
    for candidate in candidates:
        try:
            return json.loads(candidate), True
        except json.JSONDecodeError:
            pass

    candidate = match.group(1) if match else text
    repaired, changes = repair_invalid_escapes(candidate)
    try:
        parsed = json.loads(repaired)
    except json.JSONDecodeError as exc:
        write_audit(text, candidate, repaired, changes, str(exc), False)
        print(f"[warn] JSON repair rejected: {exc}; response saved for audit")
        return None, False
    write_audit(text, candidate, repaired, changes, "invalid JSON escape", True)
    print(f"[jsonfix] repaired {len(changes)} invalid escapes; validating rubric next")
    return parsed, True


def validate_batch_result(rubric_items, parsed_result):
    if not isinstance(parsed_result, dict):
        return False
    results = parsed_result.get("results")
    if not isinstance(results, list) or len(results) != len(rubric_items):
        return False
    if not all(isinstance(row, dict) for row in results):
        return False
    if not all(isinstance(row.get("rubric_item"), str) for row in results):
        return False
    if Counter(row["rubric_item"] for row in results) != Counter(rubric_items):
        return False
    return all(
        type(row.get("score")) is int and row["score"] in (-1, 0, 1)
        and isinstance(row.get("reason"), str)
        and isinstance(row.get("evidence"), str)
        for row in results
    )


def main():
    global AUDIT_DIR
    # Read only output-related arguments. The original CLI still owns every
    # model, prompt, batch, retry and concurrency setting.
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--out_jsonl")
    parser.add_argument("--log_file")
    known, _ = parser.parse_known_args()
    if "--help" in sys.argv or "-h" in sys.argv:
        original.main()
        return
    if not known.out_jsonl:
        raise SystemExit("Specify --out_jsonl with a NEW output path; old results are preserved.")
    output = Path(known.out_jsonl)
    if output.exists():
        raise SystemExit(f"Refusing to append to existing result: {output}")
    AUDIT_DIR = output.parent / (output.stem + ".repair_audit")
    if not known.log_file:
        sys.argv.extend(["--log_file", str(output.with_suffix(".log"))])
    original.parse_model_text = parse_model_text
    original.validate_batch_result = validate_batch_result
    original.main()


if __name__ == "__main__":
    main()
