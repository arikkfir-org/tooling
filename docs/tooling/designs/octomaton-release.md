# Octomaton release skill

**Goal**: a session asked to release Octomaton drafts the GitHub release the
[releases design](../../octomaton/designs/releases.md) describes, the same way every time: tag `v<version>`, title
`<version>`, notes generated from the pull requests merged since the previous version, and a person publishes it.
[ENG-181](https://linear.app/arikkfir/issue/ENG-181).

## Where it lives

The skill is `claude/skills/octomaton-release/` in the Claude Code bundle ([Phase 5](phase-5-tooling.md)), so every
Claude Code on the web session has it under `~/.claude/skills/octomaton-release/`:

| File | Role |
| --- | --- |
| `SKILL.md` | The procedure: generate, show the person, create a draft with `gh release create --draft`, say what publishing does. Never publish |
| `release_notes.py` | Stdlib Python. Reads GitHub's API (no clone): the compare from the previous `vX.Y.Z` tag to the target, each merge commit's pull request and its commits; decides each pull request's kind and the version; renders the notes |
| `notes.md` | The notes' template (`string.Template`): the images at the tag, a section per kind, the full-changelog link, how to install and upgrade |

The bundle ships a skill as one directory: `verify.py` allows `claude/skills/<name>/SKILL.md` with `.md` and `.py`
files beside it, `setup.sh` replaces `skills/<name>/` as a whole and records the installed names in
`hooks/arikkfir/.skills`, removing a skill a later bundle drops. The bundle is public, so a skill links public pages
only: the design above by its GitHub URL, never the docs site.

## Version and notes

| Input | Rule |
| --- | --- |
| Previous version | The newest `vX.Y.Z` tag, or `--since` |
| Pull requests | Those merged into the target (`main` by default) since the previous tag: the compare's merge commits, each resolved to its pull request through GitHub's API, at most 250 commits |
| A pull request's kind | From its commits' Conventional Commits subjects: `type!:` or a `BREAKING CHANGE:` footer is breaking; `feat`, `fix`, `perf`, `revert`, `refactor`, `docs`, `test` are themselves; `build`, `ci`, `chore` and anything without a conventional subject are chores. Mixed commits take the highest kind in that order |
| Version | A breaking change raises the major (the minor before 1.0), a feature the minor, anything else the patch; the first release is `1.0.0`; `--version` wins. A version whose tag exists is refused |
| Notes | A section per kind with its emoji, each pull request as title, link and `@author`; the full changelog between the tags; the installation guide at the tag |

`--out FILE` writes the notes to a file and `--json` prints the version, tag, previous tag, target commit and pull
requests to stdout, for the session to pass to `gh`; with `--json` and no `--out`, the notes go to stderr, so stdout
stays parseable.

## Why a draft, and why a skill

Publishing a release creates the tag, and the tag runs Octomaton's `release` pipeline, which publishes the images
`v<version>`: a step that cannot be undone, so it stays the person's. A draft release has no tag and no effect, which
is also what makes the skill safe to try. The skill lives in the bundle rather than in `octomaton` because releasing
is a session procedure, not part of the product: `octomaton` keeps the pipeline and the design, `tooling` keeps how a
session drives them, and a session needs no clone of `octomaton` to release it.

## Failure modes

- GitHub's API refuses the unauthenticated script (rate limit): set `GH_TOKEN` or `GITHUB_TOKEN`, or wait.
- The target has no merge commits since the previous tag: the script stops with "nothing to release".
- A pull request's commits carry no conventional subject: it lands under chores and bumps the patch; the person
  corrects the version with `--version`.
- `gh` is not signed in as the person: the session hands over the notes file and the command instead of posting.
- The draft is published at a commit not on `main`: the reviewed `release` pipeline refuses it and the release stays
  without images; the person deletes it and drafts again at a commit of `main`.

## Decisions

| Decision | Why | Rejected |
| --- | --- | --- |
| Notes generated from Conventional Commits, per pull request | The commit rules already carry the kind of every change; grouping by pull request gives one line, one link and one author per change | Hand-written notes; one line per commit |
| Draft only; `gh` as the person | Publishing is irreversible and must carry the person's identity; a raw `curl` through the session's proxy would author it as `claude[bot]` | The session publishing; the GitHub connector |
| Kinds and the version from the script, not the model | The same inputs give the same version and the same notes in every session | Asking the model to summarise the changes |
| Public GitHub links in the skill | The bundle is public and the docs site is behind the hub's sign-in | Links to the docs site |

## Rollout

1. octomaton#50 (the tag-triggered build and the releases design), delivery#81 (the identity's `octomaton.dev/tags`).
2. This pull request: the skill, the bundle's support for skills, this page. Live once merged and a session installs
   the new bundle.
3. The person drafts and publishes the first version, `1.0.0`, with the skill.
