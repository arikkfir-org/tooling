#!/usr/bin/env python3
"""Composes a repository's docs with the other repositories' published layers, for the docs site's checks.

Usage: compose.py --repository NAME --checkout DIR --files FILE --layers FILE --site DIR --links FILE --problems FILE

--files is the repository's tracked files at the revision under test (git ls-files -z), --layers the URLs of every
object under the bucket's .layers/ (gcloud storage ls, one per line). The composed site goes to --site: this
repository's sources as they are and every other layer's files as empty placeholders, so links.lua can resolve links
against the whole URL space. The site paths whose links to check go to --links, one per line. Problems go to
--problems, one per line; the script itself fails only on bad input.

See https://github.com/arikkfir-org/docs/blob/main/hub/designs/docs-site-composition.md.
"""

import argparse
import os
import shutil
import sys

# The repository whose whole tree is its docs; every other repository publishes its docs/ directory.
DOCS_REPOSITORY = "docs"
LAYERS = ".layers/"


def hidden(path):
    return any(part.startswith(".") for part in path.split("/"))


def sources(repository, files):
    """Maps the repository's tracked files to site paths: {site path: file path}. Hidden paths are never published."""
    out = {}
    for path in files:
        if repository == DOCS_REPOSITORY:
            site = path
        elif path.startswith("docs/"):
            site = path[len("docs/"):]
        else:
            continue
        if site and not hidden(site):
            out[site] = path
    return out


def layers(urls, repository):
    """Parses gs://BUCKET/.layers/<repository>/<path> URLs into {repository: {paths}}, leaving out this repository's
    own layer, which its sources replace."""
    out = {}
    for url in urls:
        url = url.strip()
        if not url.startswith("gs://") or url.endswith("/"):
            continue
        _, _, name = url[len("gs://"):].partition("/")
        if not name.startswith(LAYERS):
            continue
        owner, _, path = name[len(LAYERS):].partition("/")
        if owner and path and owner != repository:
            out.setdefault(owner, set()).add(path)
    return out


def directories(path):
    """The directories a path lives in: a/b/c -> a, a/b."""
    parts = path.split("/")[:-1]
    return {"/".join(parts[:i]) for i in range(1, len(parts) + 1)}


def problems(sources, others):
    """Reserved names and collisions with other repositories' layers, one message each."""
    out = []
    for path in sorted(sources):
        if path.endswith(".md.html"):
            out.append(f"{path}: the name is reserved for the page the site renders from {path[:-len('.html')]}")
    for owner in sorted(others):
        theirs = others[owner]
        their_dirs = set().union(*map(directories, theirs))
        for path in sorted(sources):
            if path in theirs:
                out.append(f"{path}: {owner} already publishes this path; rename or move one of them")
            elif path in their_dirs:
                out.append(f"{path}: {owner} publishes a directory at this path")
            else:
                for directory in sorted(directories(path) & theirs):
                    out.append(f"{path}: {owner} publishes a file at {directory}, which this path needs as a directory")
    return out


def compose(checkout, sources, others, site):
    """Writes the composed site: this repository's sources copied, the other layers' files empty. Returns the site
    paths whose links to check: this repository's Markdown and HTML."""
    os.makedirs(site, exist_ok=True)
    for owner in sorted(others):
        for path in others[owner]:
            target = os.path.join(site, path)
            if os.path.isdir(target) or any(os.path.isfile(os.path.join(site, d)) for d in directories(path)):
                continue  # a collision, already reported
            os.makedirs(os.path.dirname(target), exist_ok=True)
            open(target, "a").close()
    links = []
    for path, file in sorted(sources.items()):
        target = os.path.join(site, path)
        if os.path.isdir(target) or any(os.path.isfile(os.path.join(site, d)) for d in directories(path)):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(checkout, file), target)
        if path.endswith((".md", ".html", ".htm")):
            links.append(path)
    return links


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--files", required=True)
    parser.add_argument("--layers", required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--links", required=True)
    parser.add_argument("--problems", required=True)
    args = parser.parse_args()

    with open(args.files, "rb") as f:
        files = [p.decode() for p in f.read().split(b"\0") if p]
    with open(args.layers) as f:
        others = layers(f, args.repository)
    mine = sources(args.repository, files)
    found = problems(mine, others)
    links = compose(args.checkout, mine, others, args.site)

    print(f"{args.repository}: {len(mine)} source(s); other layers: "
          + (", ".join(f"{owner} ({len(paths)})" for owner, paths in sorted(others.items())) or "none"))
    with open(args.links, "w") as f:
        f.writelines(f"{path}\n" for path in links)
    with open(args.problems, "a") as f:
        f.writelines(f"error: {problem}\n" for problem in found)
    for problem in found:
        print(f"error: {problem}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
