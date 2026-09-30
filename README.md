# tooling

Developer tooling for the `arikkfir-org` hub: a Claude Code bundle for Claude Code on the web, and the pull request
reviewer.

## Claude Code web bundle

A user-level configuration installed into `~/.claude` of every Claude Code on the web session:

| File | Purpose |
| --- | --- |
| [`claude/CLAUDE.md`](claude/CLAUDE.md) | User-level instructions: tone, terseness, answer-first responses |
| [`claude/settings.json`](claude/settings.json) | Registers the hooks |
| [`claude/hooks/guard.py`](claude/hooks/guard.py) | `PreToolUse` (Bash): denies force-pushes/deletions of `main`/`master` and recursive deletion of `/` or `~` |
| [`claude/hooks/format.py`](claude/hooks/format.py) | `PostToolUse` (Edit/Write): runs `gofmt` / `terraform fmt` on the written file and tells Claude when it changed |

### Using it

Set the environment's setup script (claude.ai/code → environment settings) to:

```bash
curl -fsSL https://storage.googleapis.com/arikkfir-claude/setup.sh | bash
```

`setup.sh` downloads the bundle it is pinned to, verifies its SHA-256, and installs it into
`${CLAUDE_CONFIG_DIR:-~/.claude}`: `CLAUDE.md` is replaced, hooks live in `hooks/arikkfir/` (replaced as a whole), and
`settings.json` is merged with any existing settings (bundle values win). Running it again changes nothing. If anything
fails it warns and exits 0, so a broken download never blocks a session; set `ARIKKFIR_CLAUDE_STRICT=1` to fail instead.

### Publishing

| Object | Cache | Notes |
| --- | --- | --- |
| `gs://arikkfir-claude/bundles/<sha256>.tar.gz` | immutable | content-addressed; never overwritten |
| `gs://arikkfir-claude/setup.sh` | `no-cache` | pinned to the newest bundle; uploaded after it |

Octomaton runs [`.tekton/bundle.yaml`](.tekton/bundle.yaml) as `ci` for pull requests and the merge queue, and as
`publish` for pushes to `main` ([`.octomaton.yaml`](.octomaton.yaml)). Both build and verify; only `publish` uploads.
Uploads use `gcloud storage rsync --checksums-only`, so unchanged objects are not rewritten.

### No secrets

The bundle is public. What guards it:

1. `scripts/build.sh` archives committed files under `claude/` only (`git archive`), so untracked files never ship.
2. `scripts/verify.py` allows only `CLAUDE.md`, `settings.json` and `hooks/*.py`, caps the size at 256 KiB, and rejects
   settings keys that exist to carry credentials (`env`, `apiKeyHelper`, …).
3. gitleaks scans the extracted bundle and the repository; any finding fails the pipeline.

## Pull request reviewer

Requesting a review from `arikkfir-reviewer` on a pull request runs an AI review: opencode with DeepSeek reads the pull
request and the hub's repositories, and `arikkfir-reviewer` posts one review, with a thread per finding. Details are in
the [design](https://github.com/arikkfir-org/docs/blob/main/hub/designs/pr-reviewer.md).

| File | Purpose |
| --- | --- |
| [`reviewer/pipelinerun.yaml`](reviewer/pipelinerun.yaml) | The PipelineRun: tasks `setup`, `review` and `report` |
| [`reviewer/prompt.md`](reviewer/prompt.md) | The reviewer's instructions, with the `findings.json` schema |
| [`reviewer/opencode.json`](reviewer/opencode.json) | opencode's configuration: the model, no sharing, every tool allowed |
| [`reviewer/state.py`](reviewer/state.py) | Writes `pr.json`: the pull request, its files, conversation, reviews and threads |
| [`reviewer/findings.py`](reviewer/findings.py) | Checks `findings.json` against the diff and the earlier findings |
| [`reviewer/report.py`](reviewer/report.py) | Posts the review as `arikkfir-reviewer`, through [`reviewer/github.py`](reviewer/github.py) |

Pipeline `review` is an organization pipeline, declared once in `arikkfir-org/.github`'s `.octomaton.yaml`, so every
repository has it. Octomaton reads `reviewer/pipelinerun.yaml` from this repository's default branch, and the scripts
and the prompt run from the default branch too: `setup` extracts `reviewer/` from it, and `report` clones its own copy.
So no pull request, here or elsewhere, changes its own review; a change here takes effect once merged.

## Development

```bash
python3 -m unittest discover -s tests   # hook and reviewer tests (format tests need gofmt)
sh scripts/build.sh                      # needs a commit: the bundle is built from HEAD
python3 scripts/verify.py
bash tests/test_setup.sh                 # end-to-end install test against a local HTTP server
shellcheck setup/setup.sh scripts/build.sh tests/test_setup.sh
```
