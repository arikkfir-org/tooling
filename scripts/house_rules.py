#!/usr/bin/env python3
"""Copies the house rules into the bundle's CLAUDE.md.

claude/CLAUDE.md is the response style, then the house rules: CONTRIBUTING.md of arikkfir-org/docs, whole, under the
heading "# House rules". This replaces everything from that heading on with the given CONTRIBUTING.md. Its relative
links name pages of the docs site, so they become the site's URLs, as the house rules link a page from outside the site;
links to its own headings stay as they are, since the headings come along.

Usage: house_rules.py CONTRIBUTING.md   ("-" reads it from standard input)
"""

import os
import posixpath
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "claude", "CLAUDE.md")
SITE = "https://docs.dev.kfirs.com"
TITLE = "# Contributing"
HEADING = "# House rules"
NOTE = ("<!-- CONTRIBUTING.md of arikkfir-org/docs, copied by scripts/house_rules.py of arikkfir-org/tooling: change it"
        " in docs, then run the script again. -->")
LINK = re.compile(r"\]\(([^)\s]+)\)")
CODE = re.compile(r"(`+[^`]*`+)")


def site_url(target):
    if target.startswith("#") or "://" in target or target.startswith(("//", "mailto:")):
        return target
    path = posixpath.normpath(target.lstrip("/"))
    if path == ".." or path.startswith("../"):
        sys.exit(f"error: link {target} leaves the docs site")
    return f"{SITE}/{path}"


def rewrite(line):
    # Only text outside code spans holds links.
    parts = CODE.split(line)
    for i in range(0, len(parts), 2):
        parts[i] = LINK.sub(lambda m: f"]({site_url(m.group(1))})", parts[i])
    return "".join(parts)


def house_rules(contributing):
    lines = contributing.rstrip("\n").split("\n")
    if lines[0] != TITLE:
        sys.exit(f"error: CONTRIBUTING.md starts with {lines[0]!r}, not {TITLE!r}")
    out, fenced = [HEADING, "", NOTE], False
    for line in lines[1:]:
        if line.lstrip().startswith("```"):
            fenced = not fenced
        out.append(line if fenced else rewrite(line))
    return "\n".join(out) + "\n"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    if sys.argv[1] == "-":
        contributing = sys.stdin.read()
    else:
        with open(sys.argv[1], encoding="utf-8") as f:
            contributing = f.read()
    with open(TARGET, encoding="utf-8") as f:
        current = f.read()
    style = current.split(f"\n{HEADING}\n", 1)[0].rstrip("\n")
    updated = f"{style}\n\n{house_rules(contributing)}"
    if updated == current:
        print("claude/CLAUDE.md already holds these house rules")
        return
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(updated)
    print("claude/CLAUDE.md now holds these house rules")


if __name__ == "__main__":
    main()
