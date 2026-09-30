#!/usr/bin/env python3
"""Posts the findings as one GitHub review by the token's user (arikkfir-reviewer), one thread per finding.

It holds a credential, so it trusts nothing on the shared volume but findings.json, read as data: the pull request,
its files and its threads are fetched again, and the findings are checked against them before anything is posted.

Usage: report.py --repository OWNER/NAME --number N --revision SHA --run PIPELINERUN --findings findings.json
                 --title-file FILE --summary-file FILE          (the token in GITHUB_TOKEN)
"""

import argparse
import os
import sys
import time

import findings
import github
import state

PRIORITIES = findings.PRIORITIES
CIRCLES = {"blocking": "🔴", "non-blocking": "🟡", "nit": "🔵"}
WHOLE = "About the pull request as a whole."
# Check run results travel in the TaskRun's termination message, which holds 4 KiB in all.
MAX_SUMMARY_BYTES = 2500

CONTEXT_QUERY = """
query Context($owner: String!, $name: String!, $number: Int!) {
  viewer { login }
  repository(owner: $owner, name: $name) { pullRequest(number: $number) { id headRefOid } }
}
"""
REVIEWS_QUERY = """
query Reviews($owner: String!, $name: String!, $number: Int!, $author: String!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      pending: reviews(states: [PENDING], author: $author, first: 10) { nodes { id } }
      recent: reviews(author: $author, last: 100) { nodes { body url } }
    }
  }
}
"""
DELETE_REVIEW = """
mutation DeleteReview($id: ID!) { deletePullRequestReview(input: {pullRequestReviewId: $id}) { clientMutationId } }
"""
ADD_REVIEW = """
mutation AddReview($pullRequest: ID!, $commit: GitObjectID!) {
  addPullRequestReview(input: {pullRequestId: $pullRequest, commitOID: $commit}) { pullRequestReview { id } }
}
"""
ADD_THREAD = """
mutation AddThread($input: AddPullRequestReviewThreadInput!) {
  addPullRequestReviewThread(input: $input) { thread { id } }
}
"""
ADD_REPLY = """
mutation AddReply($input: AddPullRequestReviewThreadReplyInput!) {
  addPullRequestReviewThreadReply(input: $input) { comment { id } }
}
"""
SUBMIT_REVIEW = """
mutation SubmitReview($input: SubmitPullRequestReviewInput!) {
  submitPullRequestReview(input: $input) { pullRequestReview { url } }
}
"""
RESOLVE = "mutation Resolve($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { id } } }"
UNRESOLVE = "mutation Unresolve($id: ID!) { unresolveReviewThread(input: {threadId: $id}) { thread { id } } }"


class Refused(Exception):
    """The review can't be posted; errors says why."""

    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


class Mutations:
    """Runs GraphQL mutations a second apart, as GitHub asks of clients that create content."""

    def __init__(self, client, sleep):
        self.client = client
        self.sleep = sleep
        self.started = False

    def __call__(self, query, variables):
        if self.started:
            self.sleep(1)
        self.started = True
        return self.client.graphql(query, variables)


def code_order(code):
    prefix, number = code.rsplit("-", 1)
    return prefix, int(number)


def nits(count):
    return f"{count} nit" if count == 1 else f"{count} nits"


def tally(findings):
    return {priority: sum(f["priority"] == priority for f in findings.values()) for priority in PRIORITIES}


def run_marker(run):
    return f"<!-- reviewer-run:{run} -->"


def comment(code, finding, note=None, marker=False):
    """A finding's comment: the first of its thread (with the marker state.py reads the code from), or a reply."""
    priority = finding["priority"]
    heading = (f"{CIRCLES[priority]} **{code}: {finding['title'].strip()}**\n"
               f"{priority.capitalize()} · {finding['severity']} severity · {finding['likelihood']} likelihood")
    text = "\n\n".join(part for part in (heading, note, finding["body"].strip()) if part)
    return f"<!-- reviewer:{code} -->\n{text}" if marker else text


def new_thread(code, finding, files):
    """The action starting a new code's thread: on lines, on a whole file, or, without a path, on the pull request as
    a whole, anchored on its first changed file (into the review's body when it changes none)."""
    path = finding.get("path")
    if path is None:
        if not files:
            return {"action": "body", "code": code, "text": comment(code, finding, WHOLE)}
        return {"action": "thread", "code": code, "path": min(files), "subjectType": "FILE",
                "body": comment(code, finding, WHOLE, marker=True)}
    action = {"action": "thread", "code": code, "path": path, "subjectType": "FILE",
              "body": comment(code, finding, marker=True)}
    line, start = finding.get("line"), finding.get("startLine")
    if line is not None:
        side = finding.get("side") or "RIGHT"
        where = f"{path}:{line}" if start in (None, line) else f"{path}:{start}-{line}"
        action.update(subjectType="LINE", line=line, side=side,
                      fallback=comment(code, finding, f"Location: {where}", marker=True))
        if start not in (None, line):
            action.update(startLine=start, startSide=side)
    return action


def plan(findings, threads, viewer, revision, files):
    """What the review does, in order. findings maps codes to valid findings, threads are the pull request's review
    threads as GraphQL returns them, and files names the changed files. Each action is one of:

      unresolve, reply, thread, body   before the review is submitted; replies and threads are part of it
      keep                             nothing: the author resolved a nit, meaning it won't be fixed, and that stands
      resolve                          after the review is submitted
    """
    own = {}
    for thread in sorted(threads, key=state.age):
        code = state.thread_code(thread, viewer)
        if code:
            own.setdefault(code, []).append(thread)
    actions = []
    for code in sorted(findings, key=code_order):
        finding = findings[code]
        if code not in own:
            actions.append(new_thread(code, finding, files))
            continue
        thread = own[code][0]
        if thread["isResolved"]:
            resolver = state.login(thread.get("resolvedBy")) or ""
            if finding["priority"] == "nit" and resolver.lower() != viewer.lower():
                actions.append({"action": "keep", "code": code, "thread": thread["id"]})
                continue
            actions.append({"action": "unresolve", "code": code, "thread": thread["id"]})
        actions.append({"action": "reply", "code": code, "thread": thread["id"], "body": comment(code, finding)})
    dropped = [(code, thread) for code in sorted(own, key=code_order) if code not in findings
               for thread in own[code] if not thread["isResolved"]]
    actions += [{"action": "reply", "code": code, "thread": thread["id"],
                 "body": f"**{code}**: no longer found at {revision[:7]}."} for code, thread in dropped]
    actions += [{"action": "resolve", "code": code, "thread": thread["id"]} for code, thread in dropped]
    return actions


def verdict(findings):
    """The review's event and the check's title, derived from the findings' priorities alone."""
    counts = tally(findings)
    if counts["blocking"] or counts["non-blocking"]:
        parts = (f"{counts['blocking']} blocking", f"{counts['non-blocking']} non-blocking", nits(counts["nit"]))
        return "REQUEST_CHANGES", "Changes requested: " + ", ".join(p for p, n in zip(parts, counts.values()) if n)
    if counts["nit"]:
        return "APPROVE", f"Approved with {nits(counts['nit'])}"
    return "APPROVE", "Approved"


def review_body(summary, findings, resolved, extra, run):
    counts = tally(findings)
    line = (f"🔴 {counts['blocking']} blocking · 🟡 {counts['non-blocking']} non-blocking · "
            f"🔵 {nits(counts['nit'])} · {resolved} resolved")
    return "\n\n".join([summary.strip(), *extra, line, run_marker(run)])


def check_summary(summary, url):
    link = f"\n\n[The review on GitHub]({url})"
    text = summary.strip()
    budget = MAX_SUMMARY_BYTES - len(link.encode())
    if len(text.encode()) > budget:
        text = text.encode()[:budget - 3].decode("utf-8", "ignore") + "…"
    return text + link


def add_thread(mutate, review_id, action):
    """Starts a thread in the pending review. A line anchor GitHub rejects becomes a comment on the whole file."""
    fields = {key: action[key] for key in ("path", "line", "side", "startLine", "startSide", "subjectType")
              if key in action}
    try:
        added = mutate(ADD_THREAD, {"input": {"pullRequestReviewId": review_id, "body": action["body"], **fields}})
        thread = added["addPullRequestReviewThread"]["thread"]
    except github.GitHubError as error:
        if action["subjectType"] != "LINE":
            raise
        print(f"{action['code']}: GitHub refused the line anchor ({error}).")
        thread = None
    if thread is None and action["subjectType"] == "LINE":
        print(f"{action['code']}: commenting on the whole of {action['path']} instead.")
        added = mutate(ADD_THREAD, {"input": {"pullRequestReviewId": review_id, "path": action["path"],
                                              "subjectType": "FILE", "body": action["fallback"]}})
        thread = added["addPullRequestReviewThread"]["thread"]
    if thread is None:
        raise github.GitHubError(f"GitHub didn't create the thread of {action['code']}.")


def post(client, repository, number, revision, run, document, sleep=None):
    """Posts the review of findings.json (document) and returns the check run's (title, summary). Raises Refused
    when the findings don't hold against the pull request as GitHub has it now."""
    owner, name = repository.split("/", 1)
    variables = {"owner": owner, "name": name, "number": number}
    context = client.graphql(CONTEXT_QUERY, variables)
    viewer, pull = context["viewer"]["login"], context["repository"]["pullRequest"]
    files = {file["filename"]: state.commentable_ranges(file.get("patch"))
             for file in client.paginate(f"/repos/{owner}/{name}/pulls/{number}/files")}
    threads = state.fetch_threads(client, owner, name, number)
    errors = findings.validate(document, files, {state.thread_code(t, viewer) for t in threads} - {None})
    if errors:
        raise Refused(errors)

    actions = plan(document["findings"], threads, viewer, revision, list(files))
    kept = {action["code"] for action in actions if action["action"] == "keep"}
    shown = {code: f for code, f in document["findings"].items() if code not in kept}
    event, title = verdict(shown)
    reviews = client.graphql(REVIEWS_QUERY, {**variables, "author": viewer})["repository"]["pullRequest"]
    posted = [review["url"] for review in reviews["recent"]["nodes"]
              if review["body"].rstrip().endswith(run_marker(run))]
    mutate = Mutations(client, sleep or time.sleep)
    if posted:
        url = posted[-1]
        print(f"This run already posted {url}; only resolving threads.")
    else:
        if pull["headRefOid"] != revision:
            raise Refused([f"The pull request moved from {revision[:7]} to {pull['headRefOid'][:7]} during the "
                           f"review; request a new review."])
        for review in reviews["pending"]["nodes"]:
            mutate(DELETE_REVIEW, {"id": review["id"]})
        review = mutate(ADD_REVIEW, {"pullRequest": pull["id"], "commit": revision})
        review_id = review["addPullRequestReview"]["pullRequestReview"]["id"]
        extra = []
        for action in actions:
            if action["action"] == "unresolve":
                mutate(UNRESOLVE, {"id": action["thread"]})
            elif action["action"] == "reply":
                mutate(ADD_REPLY, {"input": {"pullRequestReviewId": review_id,
                                             "pullRequestReviewThreadId": action["thread"], "body": action["body"]}})
            elif action["action"] == "thread":
                add_thread(mutate, review_id, action)
            elif action["action"] == "body":
                extra.append(action["text"])
        resolved = sum(action["action"] == "resolve" for action in actions)
        body = review_body(document["summary"], shown, resolved, extra, run)
        submitted = mutate(SUBMIT_REVIEW, {"input": {"pullRequestReviewId": review_id, "event": event, "body": body}})
        url = submitted["submitPullRequestReview"]["pullRequestReview"]["url"]
        print(f"Posted {url}: {title}.")
    for action in actions:
        if action["action"] == "resolve":
            mutate(RESOLVE, {"id": action["thread"]})
    return title, check_summary(document["summary"], url)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Posts findings.json as one GitHub review.")
    parser.add_argument("--repository", required=True, help="OWNER/NAME")
    parser.add_argument("--number", required=True, type=int)
    parser.add_argument("--revision", required=True, help="the head commit under review")
    parser.add_argument("--run", required=True, help="the PipelineRun's name, which marks its review")
    parser.add_argument("--findings", required=True)
    parser.add_argument("--title-file", required=True, help="where to write the check run's title")
    parser.add_argument("--summary-file", required=True, help="where to write the check run's summary")
    args = parser.parse_args(argv)
    document, errors = findings.load(args.findings)
    token = os.environ.get("GITHUB_TOKEN")
    try:
        if errors:
            raise Refused(errors)
        if not token:
            raise Refused(["GITHUB_TOKEN is not set."])
        title, summary = post(github.Client(token), args.repository, args.number, args.revision, args.run, document)
    except Refused as error:
        print("\n".join(error.errors), file=sys.stderr)
        return 1
    except github.GitHubError as error:
        print(error, file=sys.stderr)
        return 1
    with open(args.title_file, "w", encoding="utf-8") as f:
        f.write(title)
    with open(args.summary_file, "w", encoding="utf-8") as f:
        f.write(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
