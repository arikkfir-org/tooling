# tooling

Developer tooling for the `arikkfir-org` hub: a Claude Code bundle for Claude Code on the web, the pull request
reviewer, and the organization pipelines every repository runs.

## Claude Code web bundle

A user-level configuration installed into `~/.claude` of every Claude Code on the web session:

| File | Purpose |
| --- | --- |
| [`claude/CLAUDE.md`](claude/CLAUDE.md) | User-level instructions: tone, terseness, answer-first responses |
| [`claude/settings.json`](claude/settings.json) | Registers the hooks; runs the repositories' checks, local git and read-only GitHub tools without a prompt, and asks before `rm -r`, a remote branch deletion or a switch that discards changes or resets a branch |
| [`claude/hooks/guard.py`](claude/hooks/guard.py) | `PreToolUse` (Bash): denies force-pushes/deletions of `main`/`master` and recursive deletion of `/` or `~`; asks before deleting any other remote branch and before a `git switch` that discards changes or resets a branch, in any spelling |
| [`claude/hooks/commit_message.py`](claude/hooks/commit_message.py) | `PreToolUse` (Bash): in `arikkfir-org` repositories, denies a `git commit` whose message breaks the commit rules (`CONTRIBUTING.md` in `docs`), and says what to fix |
| [`claude/hooks/format.py`](claude/hooks/format.py) | `PostToolUse` (Edit/Write): runs `gofmt` / `terraform fmt` on the written file and tells Claude when it changed |
| [`claude/hooks/add_repo.py`](claude/hooks/add_repo.py) | `PreToolUse` (`add_repo`, `register_repo_root`): attaches `arikkfir-org`'s public repositories without a prompt; the internal `fin`, one not listed yet and other owners' get the normal one |
| [`claude/hooks/git_hooks.py`](claude/hooks/git_hooks.py) | `SessionStart`, and `PostToolUse` after `register_repo_root`: points each `arikkfir-org` repository that commits hooks in `.githooks/` at them (`core.hooksPath`), so they run in cloud sessions too; any other repository's hooks stay off |
| [`claude/hooks/pull_request.py`](claude/hooks/pull_request.py) | `PostToolUse` (`create_pull_request`): after opening an `arikkfir-org` pull request, reminds the session to request `arikkfir-reviewer` and to link the Linear issue and design |
| [`claude/hooks/dockerd.py`](claude/hooks/dockerd.py) | `SessionStart`: in cloud sessions, starts the Docker daemon in the background, pulling from Docker Hub through `mirror.gcr.io` |
| [`claude/hooks/bundle.py`](claude/hooks/bundle.py) | `SessionStart` (async): in cloud sessions, installs the published bundle when it isn't the one installed, so sessions started from a cached environment or resumed after idling catch up |

### Using it

Set the environment's setup script (claude.ai/code → environment settings) to:

```bash
curl -fsSL https://storage.googleapis.com/arikkfir-claude/setup.sh | bash
```

`setup.sh` downloads the bundle it is pinned to, verifies its SHA-256, and installs it into
`${CLAUDE_CONFIG_DIR:-~/.claude}`: `CLAUDE.md` is replaced, hooks live in `hooks/arikkfir/` (replaced as a whole), and
`settings.json` is merged with any existing settings (bundle values win). Running it again changes nothing. If anything
fails it warns and exits 0, so a broken download never blocks a session; set `ARIKKFIR_CLAUDE_STRICT=1` to fail instead.

The environment runs its setup script once and starts later sessions from a snapshot of the result for about seven
days, and a resumed session skips it too. So `setup.sh` records the bundle it installed in `hooks/arikkfir/.bundle`, and
at every session start, resume and compaction `bundle.py` runs the published `setup.sh` in the background when it is
pinned to another bundle (log: `/tmp/arikkfir-claude.log`). Installs take turns under a lock and swap the hooks in
whole. Hooks and settings apply at once; Claude Code reads `CLAUDE.md` again at the next compaction or resume.

### Publishing

| Object | Cache | Notes |
| --- | --- | --- |
| `gs://arikkfir-claude/bundles/<sha256>.tar.gz` | immutable | content-addressed; never overwritten |
| `gs://arikkfir-claude/setup.sh` | `no-cache` | pinned to the newest bundle; uploaded after it |

Octomaton runs [`.tekton/ci.yaml`](.tekton/ci.yaml) as `ci` for pull requests and the merge queue, and
[`.tekton/publish.yaml`](.tekton/publish.yaml) as `publish` for pushes to `main` ([`.octomaton.yaml`](.octomaton.yaml)).
Both build and verify the bundle; only `publish` uploads, as ServiceAccount `ci-tooling-publish`, which only `main` may
use. `ci` runs as Tekton's default ServiceAccount and needs no Google Cloud access.
Uploads use `gcloud storage rsync --checksums-only`, so unchanged objects are not rewritten.

### No secrets

The bundle is public. What guards it:

1. `scripts/build.sh` archives committed files under `claude/` only (`git archive`), so untracked files never ship.
2. `scripts/verify.py` allows only `CLAUDE.md`, `settings.json` and `hooks/*.py`, caps the size at 256 KiB, and rejects
   settings keys that exist to carry credentials (`env`, `apiKeyHelper`, …).
3. gitleaks scans the extracted bundle and the repository; any finding fails the pipeline.

## Pull request reviewer

Requesting a review from `arikkfir-reviewer` on a pull request runs an AI review: opencode with DeepSeek reviews the
pull request in its repository's checkout, cloning any other repository it needs, and `arikkfir-reviewer` posts one
review, with a thread per finding. Details are in the
[design](https://github.com/arikkfir-org/docs/blob/main/hub/designs/pr-reviewer.md).

| File | Purpose |
| --- | --- |
| [`reviewer/pipelinerun.yaml`](reviewer/pipelinerun.yaml) | The PipelineRun: tasks `review` and `report` |
| [`reviewer/prompt.md`](reviewer/prompt.md) | The reviewer's instructions, with the `findings.json` schema |
| [`reviewer/opencode.json`](reviewer/opencode.json) | opencode's configuration: the model, no sharing, every tool allowed but subagents |
| [`reviewer/state.py`](reviewer/state.py) | Writes `pr.json`: the pull request, its files, and the conversation, reviews and threads of people with write access only |
| [`reviewer/findings.py`](reviewer/findings.py) | Checks `findings.json` against the diff and the earlier findings |
| [`reviewer/report.py`](reviewer/report.py) | Posts the review as `arikkfir-reviewer`, through [`reviewer/github.py`](reviewer/github.py) |
| [`reviewer/github_proxy.py`](reviewer/github_proxy.py) | The review task's `github` sidecar: serves GitHub's API (reads only) and git fetches to the model on `127.0.0.1:8080`, adding the run's token, which the model never sees |

Pipeline `review` is an organization pipeline, declared once in this repository's `.octomaton.yaml` (below), so every
repository has it. Octomaton reads `reviewer/pipelinerun.yaml` from this repository's default branch, and the scripts
and the prompt run from the default branch too: `review`'s `clone` step extracts `reviewer/` from it, and `report`
clones its own copy. So no pull request, here or elsewhere, changes its own review; a change here takes effect once
merged.

Every step, the model's included, runs in the reviewer image, `me-west1-docker.pkg.dev/arikkfir/images/reviewer`
(opencode plus bash, python3, git, jq, yq, curl, wget and GNU userland), which `arikkfir-org/octomaton` builds from
`images/reviewer` and publishes from its `main`. `reviewer/pipelinerun.yaml` runs its `main` tag and pulls it for every
pod, so a change there reaches the next review. Octomaton mints the run's token for every repository of the organization, reading code and pull
requests only. In `review`, only the `clone` and `state` steps, which run before the model, and the `github` sidecar
mount it, so the model reads other repositories' code and pull requests (an internal one's too) through the sidecar,
never with the token.

## Docs site

`docs.dev.kfirs.com` is one URL space composed from every repository: the `docs` repository's whole tree and every other
repository's `docs/` directory, each published to its own layer of `gs://arikkfir-docs/.layers/<repository>/`. Details
are in the [design](https://github.com/arikkfir-org/docs/blob/main/hub/designs/docs-site-composition.md).

| File | Role |
| --- | --- |
| [`docs-site/check.yaml`](docs-site/check.yaml) | Pipeline `docs` (check `Docs`), as `docs-reader`: composes the change's docs with the other layers and checks them |
| [`docs-site/publish.yaml`](docs-site/publish.yaml) | Pipeline `docs-publish`, as `docs-publisher` (`main` only): mirrors the repository's layer, then runs the same checks |
| [`docs-site/compose.py`](docs-site/compose.py) | Maps the repository's files to site paths, reports reserved names and collisions, and writes the composed site |
| [`docs-site/links.lua`](docs-site/links.lua) | Checks that relative links resolve in the composed site (`pandoc lua`) |

Both are organization pipelines; their runs take `docs-site/` from this repository's default branch, never from the
change under test.

## Organization pipelines

This is the hub's organization repository: Octomaton's `OCTOMATON_ORGANIZATION_REPOSITORY` names it. The pipelines
under `organization.pipelines` in [`.octomaton.yaml`](.octomaton.yaml) run in every repository of `arikkfir-org`, this
one included. Octomaton reads them from `main`, so a pull request can't change them, and no repository can replace one.
Their runs belong to the repository they run for: its namespace, checks and token.

| Pipeline | Check | Runs when |
| --- | --- | --- |
| `review` | `AI Review` | a review is requested from `arikkfir-reviewer` |
| `docs` | `Docs` | pull requests and merge groups |
| `docs-publish` | `docs-publish` | pushes to `main` |

## Development

```bash
python3 -m unittest discover -s tests   # hook and reviewer tests (format tests need gofmt)
sh scripts/build.sh                      # needs a commit: the bundle is built from HEAD
python3 scripts/verify.py
bash tests/test_setup.sh                 # end-to-end install test against a local HTTP server
sh tests/test_links.sh                   # docs-site/links.lua against a composed site (needs pandoc)
shellcheck setup/setup.sh scripts/build.sh tests/test_setup.sh
```
