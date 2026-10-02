#!/usr/bin/env python3
"""PreToolUse hook for the remote session's add_repo and register_repo_root: allows them without a prompt when the
repository is one of arikkfir-org's public repositories. Any other repository goes through the normal permission flow:
the internal fin, one added later, and every other owner's.

It only spares the prompt: the backend still decides whether the session may reach the repository.
"""

import json
import sys

ORGANIZATION = "arikkfir-org"
# The organization's public repositories (hub/reference.md in arikkfir-org/docs). The internal fin, and any repository
# added later, still ask.
REPOSITORIES = {".github", "delivery", "docs", "infra", "octomaton", "tooling"}
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
        tool_input = payload.get("tool_input") or {}
        owner = str(tool_input.get("owner") or "")
        repo = str(tool_input.get("repo") or "")
    except Exception:  # never break the session because of this hook
        return
    # GitHub owner and repository names are case-insensitive
    if owner.lower() == ORGANIZATION and repo.lower() in REPOSITORIES:
        json.dump({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": f"{ORGANIZATION}/{repo} is one of the organization's public repositories.",
            }
        }, sys.stdout)


if __name__ == "__main__":
    main()
