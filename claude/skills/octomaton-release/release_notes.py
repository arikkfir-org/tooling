#!/usr/bin/env python3
"""Drafts the notes and the version of the next Octomaton release, from the pull requests merged since the last one.

Reads GitHub's API only (no clone): the compare between the previous version's tag and the target, the pull request of
each merge commit, and its commits, whose Conventional Commits subjects decide the pull request's kind and the version
bump (a breaking change raises the major, a feature the minor, anything else the patch). Renders notes.md, beside this
script, with a section per kind: each pull request as its title, link and author.

Usage: release_notes.py [--repo OWNER/NAME] [--target REF] [--since TAG] [--version X.Y.Z] [--out FILE] [--json]

Authentication: GH_TOKEN or GITHUB_TOKEN when set; otherwise unauthenticated (the repository is public). Stdlib only.
"""

import argparse
import json
import os
import re
import string
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
DEFAULT_REPO = "arikkfir-org/octomaton"
IMAGES = "me-west1-docker.pkg.dev/arikkfir/octomaton"
INSTALL_PAGE = "docs/octomaton/installation.md"
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notes.md")

# Kinds in the order they are listed, and the order that decides a pull request's kind when its commits mix several.
KINDS = [
    ("breaking", "💥 Breaking changes"),
    ("feat", "✨ Features"),
    ("fix", "🐛 Bug fixes"),
    ("perf", "⚡ Performance"),
    ("revert", "⏪ Reverts"),
    ("refactor", "♻️ Refactoring"),
    ("docs", "📝 Documentation"),
    ("test", "🧪 Tests"),
    ("chore", "🔧 Build, CI and chores"),
]
KIND_OF_TYPE = {"feat": "feat", "fix": "fix", "perf": "perf", "revert": "revert", "refactor": "refactor",
                "docs": "docs", "test": "test", "build": "chore", "ci": "chore", "chore": "chore"}
RANK = {kind: i for i, (kind, _) in enumerate(KINDS)}
SUBJECT = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?(?P<bang>!)?: ")
MERGED_PR = re.compile(r" \(#(?P<number>\d+)\)$")
VERSION_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


class GitHub:
    def __init__(self, repo):
        self.repo = repo
        self.headers = {"Accept": "application/vnd.github+json", "User-Agent": "octomaton-release"}
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def get(self, path, **query):
        url = f"{API}/repos/{self.repo}/{path}"
        if query:
            url += "?" + "&".join(f"{k}={v}" for k, v in query.items())
        request = urllib.request.Request(url, headers=self.headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            sys.exit(f"error: GET {url} answered {error.code}: {error.read().decode(errors='replace')[:300]}")

    def paged(self, path, **query):
        page = 1
        while True:
            items = self.get(path, per_page=100, page=page, **query)
            yield from items
            if len(items) < 100:
                return
            page += 1


def version_tags(gh):
    """Version tags, newest first, as (version tuple, tag name)."""
    tags = []
    for tag in gh.paged("tags"):
        match = VERSION_TAG.match(tag["name"])
        if match:
            tags.append((tuple(int(n) for n in match.groups()), tag["name"]))
    return sorted(tags, reverse=True)


def commits_since(gh, since, target):
    """The first-parent commits of target after since (every commit of target when since is None), oldest first."""
    if since is None:
        commits = list(gh.paged("commits", sha=target))
        commits.reverse()
        return commits
    compare = gh.get(f"compare/{since}...{target}", per_page=250)
    commits = compare["commits"]
    if compare.get("total_commits", len(commits)) > len(commits):
        sys.exit(f"error: more than {len(commits)} commits since {since}; release more often, or pass --since")
    return commits


def kind_of(messages):
    """The kind of a pull request, from the subjects and footers of its commits."""
    kinds = set()
    for message in messages:
        subject, _, body = message.partition("\n")
        if subject.startswith("Merge "):
            continue
        if re.search(r"^BREAKING[ -]CHANGE:", body, re.MULTILINE):
            kinds.add("breaking")
        match = SUBJECT.match(subject)
        if match:
            kinds.add("breaking" if match.group("bang") else KIND_OF_TYPE.get(match.group("type"), "chore"))
    return min(kinds, key=RANK.get) if kinds else "chore"


def pull_requests(gh, commits):
    """The pull requests the merge commits among commits merged, oldest first, each with its kind."""
    prs = []
    for commit in commits:
        match = MERGED_PR.search(commit["commit"]["message"].split("\n", 1)[0])
        if not match:
            continue
        number = int(match.group("number"))
        pr = gh.get(f"pulls/{number}")
        messages = [c["commit"]["message"] for c in gh.paged(f"pulls/{number}/commits")]
        author = pr["user"]["login"].removesuffix("[bot]")
        prs.append({"number": number, "title": title_of(pr["title"]), "url": pr["html_url"], "author": author,
                    "kind": kind_of(messages)})
    return prs


def title_of(title):
    """A pull request's title without a Conventional Commits prefix some early ones carried, capitalised."""
    title = SUBJECT.sub("", title.strip())
    return title[:1].upper() + title[1:]


def next_version(previous, prs):
    """The version the pull requests' kinds call for: major for a breaking change (minor before 1.0), minor for a
    feature, patch otherwise; 1.0.0 for a first release."""
    if previous is None:
        return (1, 0, 0)
    major, minor, patch = previous
    kinds = {pr["kind"] for pr in prs}
    if "breaking" in kinds and major >= 1:
        return (major + 1, 0, 0)
    if "breaking" in kinds or "feat" in kinds:
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def render(repo, version, previous_tag, prs):
    tag = f"v{version}"
    sections = []
    for kind, heading in KINDS:
        lines = [f"- {pr['title']} ([#{pr['number']}]({pr['url']})) @{pr['author']}" for pr in prs if pr["kind"] == kind]
        if lines:
            sections.append(f"## {heading}\n\n" + "\n".join(lines))
    if previous_tag:
        changelog = f"**Full changelog**: [{previous_tag}...{tag}](https://github.com/{repo}/compare/{previous_tag}...{tag})"
    else:
        changelog = f"**Full changelog**: [every commit to {tag}](https://github.com/{repo}/commits/{tag})"
    with open(TEMPLATE, encoding="utf-8") as f:
        template = string.Template(f.read())
    return template.substitute(
        version=version, tag=tag, repo=repo, changelog=changelog, sections="\n\n".join(sections) or "No pull requests.",
        install_url=f"https://github.com/{repo}/blob/{tag}/{INSTALL_PAGE}", images=IMAGES,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"OWNER/NAME (default {DEFAULT_REPO})")
    parser.add_argument("--target", default="main", help="the branch or commit to release (default main)")
    parser.add_argument("--since", help="the previous version's tag (default: the newest vX.Y.Z tag)")
    parser.add_argument("--version", help="the version to release, X.Y.Z (default: computed from the changes)")
    parser.add_argument("--out", help="write the notes here (default: stdout, or stderr with --json)")
    parser.add_argument("--json", action="store_true", help="print the version, tag, target and pull requests as JSON")
    args = parser.parse_args()

    gh = GitHub(args.repo)
    tags = version_tags(gh)
    if args.since:
        match = VERSION_TAG.match(args.since)
        if not match:
            sys.exit(f"error: --since must be a vX.Y.Z tag, got {args.since}")
        previous, previous_tag = tuple(int(n) for n in match.groups()), args.since
    elif tags:
        previous, previous_tag = tags[0]
    else:
        previous, previous_tag = None, None

    commits = commits_since(gh, previous_tag, args.target)
    if not commits:
        sys.exit(f"error: nothing to release: {args.target} is {previous_tag}")
    prs = pull_requests(gh, commits)
    if args.version:
        if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
            sys.exit(f"error: --version must be X.Y.Z, got {args.version}")
        version = args.version
    else:
        version = ".".join(str(n) for n in next_version(previous, prs))
    if any(name == f"v{version}" for _, name in tags):
        sys.exit(f"error: v{version} is already released")

    notes = render(args.repo, version, previous_tag, prs)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(notes)
    elif args.json:
        # stdout carries the JSON alone, so a caller can parse it; the notes go to stderr.
        print(notes, end="", file=sys.stderr)
    else:
        print(notes, end="")
    summary = {"version": version, "tag": f"v{version}", "previous": previous_tag, "target": commits[-1]["sha"],
               "pullRequests": prs}
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"\nversion {version} (previous: {previous_tag or 'none'}), target {commits[-1]['sha'][:7]}, "
              f"{len(prs)} pull requests", file=sys.stderr)


if __name__ == "__main__":
    main()
