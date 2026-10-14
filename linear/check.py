#!/usr/bin/env python3
"""Check Linear: passes a pull request that Linear links to an issue, or that carries the label skip-linear, and fails
any other (CONTRIBUTING.md in arikkfir-org/docs, "Linear").

Octomaton runs it as organization pipeline linear, in every repository: on a pull request when it opens, gets a new
head commit, or has its description or labels changed, and on merge groups, which pass without asking anything: their
pull requests passed the check to enter the queue.

It reads the pull request from GitHub with the run's token, then asks Linear's GraphQL API, with a read-only key, for
the issues attached to the pull request's URL. Linear attaches a pull request to an issue however it was linked (branch
name, magic word, or by hand in Linear). Linear hears of a pull request when this run does, so the check asks again
every 10 seconds, for up to 2 minutes, before it fails. The verdict goes to the check's title and summary through the
task results check-title and check-summary. It never fetches the pull request's code.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

WAIVER = "skip-linear"
GITHUB_API = "https://api.github.com"
LINEAR_API = "https://api.linear.app/graphql"
QUERY = "query($url: String!) { attachmentsForURL(url: $url) { nodes { issue { identifier url } } } }"
WAIT_SECONDS = 120
POLL_SECONDS = 10
REQUEST_TIMEOUT = 20
# A merge queue entry's branch names its pull request: refs/heads/gh-readonly-queue/<base>/pr-<number>-<sha>.
MERGE_GROUP_PR = re.compile(r"/pr-([1-9][0-9]*)-[0-9a-f]+$")

HOW_TO_FIX = f"""Linear links no issue to this pull request. Either:

- Add `Closes ENG-123` to the description, or `Part of ENG-123` while more pull requests for the issue are still to \
come. Editing the description runs this check again.
- Label the pull request `{WAIVER}` if it needs no issue (a dependency bump, a typo).

Linked it from Linear instead? Re-run this check.
"""


class Unavailable(Exception):
    """A request that failed for a reason that may pass: a network error, a timeout or a 5xx."""


def call(url, headers, body=None):
    """Sends one request and returns its JSON answer. A refusal (a 4xx) raises RuntimeError, anything that may pass
    raises Unavailable."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"User-Agent": "arikkfir-org-linear-check", **headers})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        if e.code >= 500 or e.code == 429:
            raise Unavailable(f"{url} answered {e.code}") from e
        raise RuntimeError(f"{url} answered {e.code}: {e.read(500).decode(errors='replace')}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise Unavailable(f"{url}: {e}") from e


def pull_request(repository, number, token):
    """Reads pull request number of repository from GitHub, trying three times."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    for attempt in range(3):
        try:
            return call(f"{GITHUB_API}/repos/{repository}/pulls/{number}", headers)
        except Unavailable:
            if attempt == 2:
                raise
            time.sleep(POLL_SECONDS)


def linked_issues(url, key):
    """Returns the issues Linear attaches the pull request at url to, as (identifier, url) pairs, sorted."""
    answer = call(LINEAR_API, {"Authorization": key, "Content-Type": "application/json"},
                  {"query": QUERY, "variables": {"url": url}})
    if answer.get("errors"):
        raise RuntimeError(f"Linear answered: {json.dumps(answer['errors'])[:500]}")
    nodes = answer["data"]["attachmentsForURL"]["nodes"]
    return sorted({(n["issue"]["identifier"], n["issue"]["url"]) for n in nodes if n.get("issue")})


def verdict(repository, number, merge_group, token, key):
    """Decides the check: (passed, title, summary)."""
    if merge_group:
        match = MERGE_GROUP_PR.search(merge_group)
        entry = f"pull request #{match.group(1)}" if match else "its pull requests"
        return True, "Checked on the pull request", f"The merge queue checks nothing: {entry} passed this check to enter it."
    if not number:
        raise RuntimeError("no pull request number")
    pr = pull_request(repository, int(number), token)
    if any(label.get("name") == WAIVER for label in pr.get("labels", [])):
        return True, f"Waived with {WAIVER}", f"This pull request carries `{WAIVER}`: it needs no Linear issue."
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        try:
            issues = linked_issues(pr["html_url"], key)
        except Unavailable as e:
            issues, problem = [], e
        else:
            problem = None
        if issues:
            names = ", ".join(identifier for identifier, _ in issues)
            links = ", ".join(f"[{identifier}]({url})" for identifier, url in issues)
            return True, f"Linked to {names}", f"Linear links this pull request to {links}."
        if time.monotonic() >= deadline:
            if problem:
                return False, "Linear did not answer", f"Linear did not answer for two minutes ({problem}). Re-run this check."
            return False, "No Linear issue linked", HOW_TO_FIX
        time.sleep(POLL_SECONDS)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--title-file", required=True)
    parser.add_argument("--summary-file", required=True)
    args = parser.parse_args()
    try:
        with open(args.token_file) as f:
            token = f.read().strip()
        passed, title, summary = verdict(os.environ["REPOSITORY"], os.environ.get("NUMBER", ""),
                                         os.environ.get("MERGE_GROUP", ""), token, os.environ.get("LINEAR_API_KEY", ""))
    except Exception as e:  # whatever went wrong is the check's to say
        passed, title, summary = False, "Could not check", f"The check failed: {e}. Re-run this check."
    with open(args.title_file, "w") as f:
        f.write(title)
    with open(args.summary_file, "w") as f:
        f.write(summary)
    print(f"{title}\n\n{summary}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
