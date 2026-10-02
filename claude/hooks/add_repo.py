#!/usr/bin/env python3
"""PreToolUse hook for the remote session's add_repo and register_repo_root: allows them without a prompt when the
repository belongs to arikkfir-org. Any other owner goes through the normal permission flow.

It only spares the prompt: the backend still decides whether the session may reach the repository.
"""

import json
import sys

ORGANIZATION = "arikkfir-org"
# The remote-session server's tools, under the names it has in each kind of session. A tool of the same name from any
# other server isn't trusted.
TOOLS = {
    f"mcp__{server}__{tool}"
    for server in ("claude-code-remote", "Claude_Code_Remote")
    for tool in ("add_repo", "register_repo_root")
}


def main():
    try:
        payload = json.load(sys.stdin)
        if payload.get("tool_name") not in TOOLS:
            return
        owner = str((payload.get("tool_input") or {}).get("owner") or "")
    except Exception:  # never break the session because of this hook
        return
    if owner.lower() == ORGANIZATION:  # GitHub owner names are case-insensitive
        json.dump({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": f"The repository belongs to {ORGANIZATION}.",
            }
        }, sys.stdout)


if __name__ == "__main__":
    main()
