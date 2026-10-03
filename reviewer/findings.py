#!/usr/bin/env python3
"""The findings.json schema, shared by the review task's checks and report.py (see prompt.md for the model's view).

Usage: findings.py check --state pr.json --findings findings.json [--errors FILE] [--strict]

Prints every error, one per line, and writes them to FILE (left empty when findings.json is valid). Exits 0, or 1
with --strict when there are errors.
"""

import argparse
import json
import os
import re
import stat
import sys

CODE = re.compile(r"[A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,3}")
PRIORITIES = ("blocking", "non-blocking", "nit")
SEVERITIES = ("low", "medium", "high", "urgent")
LIKELIHOODS = ("low", "medium", "high")
SIDES = ("RIGHT", "LEFT")
REQUIRED = ("title", "priority", "severity", "likelihood", "body")
LOCATION = ("path", "line", "startLine", "side")
MAX_SUMMARY = 1500
MAX_TITLE = 200
MAX_BODY = 4000
MAX_BYTES = 1024 * 1024


class DuplicateKey(ValueError):
    pass


def unique_keys(pairs):
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise DuplicateKey(key)
        seen.add(key)
    return dict(pairs)


def load(path, name="findings.json"):
    """Reads a JSON file as data: a regular file (never a symlink or a pipe) of at most 1 MiB, with no repeated keys.
    Returns (value, errors)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, [f"{name} is missing; write it in the working directory."]
    except OSError as error:
        return None, [f"{name} can't be read ({error.strerror}); write it as a plain file."]
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return None, [f"{name} is not a plain file; write it as one."]
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return None, [f"{name} is larger than {MAX_BYTES} bytes; shorten it."]
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique_keys), []
    except UnicodeDecodeError:
        return None, [f"{name} is not UTF-8 text; write it as UTF-8 JSON."]
    except DuplicateKey as error:
        return None, [f'{name} has the key "{error}" twice; give each finding its own code, and each key once.']
    except json.JSONDecodeError as error:
        return None, [f"{name} is not valid JSON ({error.msg} at line {error.lineno}, column {error.colno}); fix it."]
    except RecursionError:
        return None, [f"{name} is nested too deeply; write the shape the prompt shows."]


def validate(findings, files_commentable, existing_codes):
    """Checks findings.json against the schema and the diff. files_commentable maps every file of the diff to its
    commentable ranges per side; existing_codes are the codes of earlier threads. Returns a list of errors, each one
    sentence naming the code and the fix."""
    if not isinstance(findings, dict):
        return ['findings.json must be a JSON object with the keys "summary" and "findings".']
    errors = [f'findings.json has an unknown key {short(key)}; remove it (the keys are "summary" and "findings").'
              for key in findings if key not in ("summary", "findings")]
    errors += [f'findings.json has no "{key}"; add it.' for key in ("summary", "findings") if key not in findings]
    summary = findings.get("summary", "")
    if not isinstance(summary, str) or ("summary" in findings and not summary.strip()):
        errors.append('"summary" must be a non-empty string: what the change does and your overall take.')
    elif len(summary) > MAX_SUMMARY:
        errors.append(f'"summary" has {len(summary)} characters; shorten it to at most {MAX_SUMMARY}.')
    items = findings.get("findings", {})
    if not isinstance(items, dict):
        errors.append('"findings" must be an object keyed by code, such as {"IAM-3": {...}}; use {} for none.')
        items = {}
    for code, finding in items.items():
        errors += validate_finding(code, finding, files_commentable, code in existing_codes)
    return errors


def validate_finding(code, finding, files_commentable, existing):
    if not CODE.fullmatch(code):
        return [f"{short(code)} is not a valid code; use an upper-case prefix, a dash and a number, such as IAM-3."]
    if not isinstance(finding, dict):
        return [f"{code}: the finding must be an object with title, priority, severity, likelihood and body."]
    errors = [f"{code}: unknown field {short(key)}; remove it (the fields are {', '.join(REQUIRED + LOCATION)})."
              for key in finding if key not in REQUIRED + LOCATION]
    title = finding.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"{code}: title must be a non-empty string naming the problem in one line.")
    elif "\n" in title or "\r" in title:
        errors.append(f"{code}: title must be one line; move the details to body.")
    elif len(title) > MAX_TITLE:
        errors.append(f"{code}: title has {len(title)} characters; shorten it to at most {MAX_TITLE}.")
    for field, allowed in (("priority", PRIORITIES), ("severity", SEVERITIES), ("likelihood", LIKELIHOODS)):
        if finding.get(field) not in allowed:
            errors.append(f"{code}: {field} is {short(finding.get(field))}; use one of {', '.join(allowed)}.")
    body = finding.get("body")
    if not isinstance(body, str) or not body.strip():
        errors.append(f"{code}: body must be a non-empty string: what is wrong, why it matters and how to fix it.")
    elif len(body) > MAX_BODY:
        errors.append(f"{code}: body has {len(body)} characters; shorten it to at most {MAX_BODY}.")
    if not existing:
        errors += validate_location(code, finding, files_commentable)
    return errors


def validate_location(code, finding, files_commentable):
    """Where a new finding goes: lines of a file, a whole file (no line), or the pull request as a whole (no path)."""
    path, line, start, side = (finding.get(key) for key in LOCATION)
    if path is None:
        if line is not None or start is not None:
            return [f"{code}: line needs a path; add the file's path, or drop line and startLine for a finding about "
                    f"the whole pull request."]
        return []
    if not isinstance(path, str) or path not in files_commentable:
        return [f"{code}: path {short(path)} is not a file of this pull request; use a files[].filename from "
                f"pr.json, or omit path for a finding about the whole pull request."]
    if side is not None and side not in SIDES:
        return [f"{code}: side is {short(side)}; use RIGHT (the new code) or LEFT (the old code)."]
    if line is None:
        if start is not None:
            return [f"{code}: startLine needs line; add line, or drop startLine for a comment on the whole file."]
        return []
    if not is_line(line) or (start is not None and not is_line(start)):
        return [f"{code}: line and startLine must be positive whole numbers."]
    if start is not None and start > line:
        return [f"{code}: startLine {start} is after line {line}; startLine must be at most line."]
    side = side or "RIGHT"
    ranges = (files_commentable[path] or {}).get(side) or []
    listed = ", ".join(f"{low}-{high}" for low, high in ranges) or "none"
    first = line if start is None else start
    if any(low <= first and line <= high for low, high in ranges):
        return []
    if start is not None and all(any(low <= n <= high for low, high in ranges) for n in (start, line)):
        return [f"{code}: lines {start}-{line} of {path} span more than one commentable {side} range ({listed}); "
                f"keep startLine and line in one range."]
    where = f"line {line}" if start is None else f"lines {start}-{line}"
    return [f"{code}: {where} of {path} is outside the diff; commentable {side} ranges: {listed}. Pick a line in one "
            f"of them, or omit line for a file-level comment."]


def is_line(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def short(value):
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 60 else text[:57] + "..."


def check(state_path, findings_path, errors_path=None, strict=False):
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        commentable = {file["filename"]: file.get("commentable") for file in state["files"]}
        codes = set(state["codes"])
        findings, errors = load(findings_path)
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors = [f"{state_path} is unusable ({error}); it must be the pr.json the state step wrote."]
    else:
        errors = errors or validate(findings, commentable, codes)
    if errors_path:
        with open(errors_path, "w", encoding="utf-8") as f:
            f.writelines(f"{error}\n" for error in errors)
    for error in errors:
        print(error)
    if not errors:
        print(f"{findings_path} is valid.")
    return 1 if strict and errors else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Checks findings.json against pr.json.")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("check", help="check findings.json")
    command.add_argument("--state", required=True, help="pr.json, from the state step")
    command.add_argument("--findings", required=True, help="findings.json, from the model")
    command.add_argument("--errors", help="write the errors to this file, one per line")
    command.add_argument("--strict", action="store_true", help="exit 1 when there are errors")
    args = parser.parse_args(argv)
    return check(args.state, args.findings, args.errors, args.strict)


if __name__ == "__main__":
    sys.exit(main())
