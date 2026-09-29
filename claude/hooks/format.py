#!/usr/bin/env python3
"""PostToolUse hook for Edit/MultiEdit/Write: formats the written file with its language's canonical formatter,
when that formatter is installed, and tells Claude when the file changed or the formatter rejected it.

  *.go             gofmt -w
  *.tf, *.tfvars   terraform fmt
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys

FORMATTERS = {
    ".go": ["gofmt", "-w"],
    ".tf": ["terraform", "fmt"],
    ".tfvars": ["terraform", "fmt"],
}


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def tell_claude(message):
    json.dump({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": message}}, sys.stdout)


def main():
    try:
        payload = json.load(sys.stdin)
        path = (payload.get("tool_input") or {}).get("file_path")
        if not path:
            return
        if not os.path.isabs(path):
            path = os.path.join(payload.get("cwd") or os.getcwd(), path)
        command = FORMATTERS.get(os.path.splitext(path)[1])
        if not command or not os.path.isfile(path) or shutil.which(command[0]) is None:
            return
        before = digest(path)
        result = subprocess.run(command + [path], capture_output=True, text=True, timeout=50, check=False)
    except Exception:  # never break the session because of this hook
        return
    if result.returncode != 0:
        tell_claude(f"{command[0]} could not format {path}:\n{(result.stderr or result.stdout).strip()[:2000]}")
    elif digest(path) != before:
        tell_claude(f"{command[0]} reformatted {path}; re-read it before editing it again.")


if __name__ == "__main__":
    main()
