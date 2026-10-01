#!/usr/bin/env python3
"""Writes pr.json, the pull request's state for the review task: the pull request, its files with the lines GitHub
accepts comments on, its conversation, and every review with the threads it started. Only organization members'
comments, reviews and thread comments go in: anyone can comment on a public repository's pull request, and the model
must never read an outsider's words. report.py reuses the helpers.

Usage: state.py --repository OWNER/NAME --number N --revision SHA --base-ref BRANCH --reviewer LOGIN
                --token-file FILE --output pr.json
"""

import argparse
import json
import re

import findings
import github

MARKER = re.compile(rf"<!-- reviewer:({findings.CODE.pattern}) -->")
HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
HEADING = re.compile(r"\S+ \*\*[^*:]+: (.+)\*\*")
# GitHub's author associations of the organization's members. COLLABORATOR, CONTRIBUTOR, NONE and the rest are outsiders.
MEMBERS = {"OWNER", "MEMBER"}

THREADS_QUERY = """
query Threads($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line startLine diffSide subjectType
          resolvedBy { login }
          comments(first: 100) {
            nodes { author { login } authorAssociation body createdAt url pullRequestReview { databaseId } }
          }
        }
      }
    }
  }
}
"""


def commentable_ranges(patch):
    """The lines of a file's patch that GitHub accepts review comments on, as merged [start, end] ranges per side:
    RIGHT covers added and context lines of the new file, LEFT deleted and context lines of the old file."""
    lines = {"RIGHT": [], "LEFT": []}
    old = new = old_left = new_left = 0
    for text in (patch or "").splitlines():
        header = HUNK.match(text)
        if header:
            old, old_left, new, new_left = (int(n) if n is not None else 1 for n in header.groups())
        elif text.startswith("\\") or (old_left <= 0 and new_left <= 0):
            continue
        elif text.startswith("+"):
            lines["RIGHT"].append(new)
            new, new_left = new + 1, new_left - 1
        elif text.startswith("-"):
            lines["LEFT"].append(old)
            old, old_left = old + 1, old_left - 1
        else:  # context; an empty line is context whose leading space was lost
            lines["RIGHT"].append(new)
            lines["LEFT"].append(old)
            new, new_left, old, old_left = new + 1, new_left - 1, old + 1, old_left - 1
    return {side: merge(numbers) for side, numbers in lines.items()}


def merge(numbers):
    ranges = []
    for number in numbers:
        if ranges and number <= ranges[-1][1] + 1:
            ranges[-1][1] = max(ranges[-1][1], number)
        else:
            ranges.append([number, number])
    return ranges


def login(actor):
    return (actor or {}).get("login")


def first_comment(thread):
    comments = (thread.get("comments") or {}).get("nodes") or []
    return comments[0] if comments else {}


def age(thread):
    """Sorts threads oldest first."""
    return first_comment(thread).get("createdAt") or "", thread.get("id") or ""


def thread_code(thread, reviewer):
    """The code of a thread the reviewer started, from the marker opening its first comment; otherwise None."""
    first = first_comment(thread)
    if (login(first.get("author")) or "").lower() != reviewer.lower():
        return None
    match = MARKER.match(first.get("body") or "")
    return match[1] if match else None


def title(body):
    """A finding's title, from the heading line after the marker ("🔴 **IAM-3: <title>**")."""
    lines = [line.strip() for line in (body or "").splitlines()[1:] if line.strip()]
    if not lines:
        return ""
    match = HEADING.fullmatch(lines[0])
    return match[1] if match else lines[0]


def fetch_threads(client, owner, name, number):
    """Every review thread of the pull request, as GraphQL returns it (each with its first 100 comments)."""
    threads, after = [], None
    while True:
        data = client.graphql(THREADS_QUERY, {"owner": owner, "name": name, "number": number, "after": after})
        page = data["repository"]["pullRequest"]["reviewThreads"]
        threads += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return threads
        after = page["pageInfo"]["endCursor"]


def group_threads(reviews, threads, reviewer):
    """Nests each thread under the review of its first comment, and lists the reviewer's codes. The oldest thread of
    a code describes it. Returns (reviews, codes); a thread whose review isn't listed gets a placeholder review."""
    grouped = [{**review, "threads": []} for review in reviews]
    by_id = {review["id"]: review for review in grouped}
    codes = {}
    for thread in sorted(threads, key=age):
        first = first_comment(thread)
        review_id = (first.get("pullRequestReview") or {}).get("databaseId")
        if review_id not in by_id:
            by_id[review_id] = {"id": review_id, "threads": []}
            grouped.append(by_id[review_id])
        code = thread_code(thread, reviewer)
        by_id[review_id]["threads"].append({
            "id": thread["id"],
            "code": code,
            "isResolved": thread["isResolved"],
            "isOutdated": thread["isOutdated"],
            "resolvedBy": login(thread.get("resolvedBy")),
            "path": thread.get("path"),
            "line": thread.get("line"),
            "startLine": thread.get("startLine"),
            "diffSide": thread.get("diffSide"),
            "subjectType": thread.get("subjectType"),
            "comments": [
                {"author": login(c.get("author")), "body": c.get("body"), "createdAt": c.get("createdAt"),
                 "url": c.get("url")}
                for c in (thread.get("comments") or {}).get("nodes") or []
            ],
        })
        if code and code not in codes:
            codes[code] = {"isResolved": thread["isResolved"], "title": title(first.get("body"))}
    return grouped, codes


def member(association):
    """Whether a comment's or review's author association is an organization member's."""
    return (association or "").upper() in MEMBERS


def members_only(threads):
    """The threads with only their members' comments, without the threads none of whose comments is a member's."""
    kept = []
    for thread in threads:
        comments = [c for c in (thread.get("comments") or {}).get("nodes") or [] if member(c.get("authorAssociation"))]
        if comments:
            kept.append({**thread, "comments": {**thread["comments"], "nodes": comments}})
    return kept


def collect(client, repository, number, revision, base_ref, reviewer):
    """The pull request's state, in pr.json's shape."""
    owner, name = repository.split("/", 1)
    base = f"/repos/{owner}/{name}"
    files = client.paginate(f"{base}/pulls/{number}/files")
    for file in files:
        file["commentable"] = commentable_ranges(file.get("patch"))
    reviews = [r for r in client.paginate(f"{base}/pulls/{number}/reviews") if member(r.get("author_association"))]
    reviews, codes = group_threads(reviews, members_only(fetch_threads(client, owner, name, number)), reviewer)
    return {
        "repository": repository,
        "number": number,
        "revision": revision,
        "baseRef": base_ref,
        "reviewer": reviewer,
        "pr": client.rest("GET", f"{base}/pulls/{number}"),
        "files": files,
        "comments": [c for c in client.paginate(f"{base}/issues/{number}/comments")
                     if member(c.get("author_association"))],
        "reviews": reviews,
        "codes": codes,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Writes pr.json, the pull request's state.")
    parser.add_argument("--repository", required=True, help="OWNER/NAME")
    parser.add_argument("--number", required=True, type=int)
    parser.add_argument("--revision", required=True, help="the head commit under review")
    parser.add_argument("--base-ref", required=True, help="the branch the pull request merges into")
    parser.add_argument("--reviewer", required=True, help="the reviewer's GitHub login")
    parser.add_argument("--token-file", required=True, help="a file holding a GitHub token")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    with open(args.token_file, encoding="utf-8") as f:
        client = github.Client(f.read().strip())
    state = collect(client, args.repository, args.number, args.revision, args.base_ref, args.reviewer)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
        f.write("\n")
    threads = sum(len(review["threads"]) for review in state["reviews"])
    print(f"Wrote {args.output}: {len(state['files'])} files, {len(state['comments'])} comments, "
          f"{len(state['reviews'])} reviews, {threads} threads, {len(state['codes'])} codes.")


if __name__ == "__main__":
    main()
