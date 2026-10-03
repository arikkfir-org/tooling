#!/usr/bin/env python3
"""Verifies dist/ before publication.

The bundle is public, so it may only contain the expected kinds of files, and its settings may not carry keys that
exist to hold credentials. Secret scanning (gitleaks) runs separately over the extracted content.

Usage: verify.py [EXTRACT_DIR]   extracts the bundle into EXTRACT_DIR (default dist/content) for further scanning
"""

import fnmatch
import hashlib
import json
import os
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
MAX_BYTES = 256 * 1024
ALLOWED_FILES = ["claude/CLAUDE.md", "claude/settings.json", "claude/mcp.json", "claude/hooks/*.py"]
ALLOWED_DIRS = {"claude", "claude/hooks"}
FORBIDDEN_SETTINGS = {"env", "apiKeyHelper", "awsAuthRefresh", "awsCredentialExport", "otelHeadersHelper"}
# An MCP server's credentials: request headers, a command printing them, a stdio server's environment, an OAuth client.
FORBIDDEN_MCP = {"headers", "headersHelper", "env", "oauth"}


def keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from keys(item)


def main():
    extract_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DIST, "content")
    errors = []

    bundles = sorted(os.listdir(os.path.join(DIST, "bundles")))
    if len(bundles) != 1 or not bundles[0].endswith(".tar.gz"):
        sys.exit(f"error: expected exactly one bundle in dist/bundles, found {bundles}")
    path = os.path.join(DIST, "bundles", bundles[0])
    sha = bundles[0][: -len(".tar.gz")]
    with open(path, "rb") as f:
        data = f.read()
    if hashlib.sha256(data).hexdigest() != sha:
        errors.append("bundle name does not match its checksum")
    if len(data) > MAX_BYTES:
        errors.append(f"bundle is {len(data)} bytes; the limit is {MAX_BYTES}")
    with open(os.path.join(DIST, "setup.sh")) as f:
        if f'bundle_sha256="{sha}"\n' not in f.read():
            errors.append(f"dist/setup.sh is not pinned to {sha}")

    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            name = member.name.rstrip("/")
            if member.isdir() and name in ALLOWED_DIRS:
                continue
            if member.isfile() and any(fnmatch.fnmatchcase(name, pattern) for pattern in ALLOWED_FILES):
                continue
            errors.append(f"unexpected bundle entry {member.name} (type {member.type!r})")
        if not errors:
            archive.extractall(extract_dir, filter="data")

    if not errors:
        with open(os.path.join(extract_dir, "claude", "settings.json")) as f:
            found = FORBIDDEN_SETTINGS & set(keys(json.load(f)))
        if found:
            errors.append(f"settings.json must not contain credential-bearing keys: {', '.join(sorted(found))}")
        with open(os.path.join(extract_dir, "claude", "mcp.json")) as f:
            found = FORBIDDEN_MCP & set(keys(json.load(f)))
        if found:
            errors.append(f"mcp.json must not contain credential-bearing keys: {', '.join(sorted(found))}")

    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        sys.exit(1)
    print(f"Bundle {sha} verified; content extracted to {extract_dir}")


if __name__ == "__main__":
    main()
