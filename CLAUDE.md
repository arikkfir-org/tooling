# tooling

Org-wide developer tooling. Currently the Claude Code web bundle (`claude/`) and its installer (`setup/setup.sh`).

## Rules

- `claude/` is published to a public bucket. Never put secrets, tokens, internal hostnames or personal data in it.
  Allowed files: `claude/CLAUDE.md`, `claude/settings.json`, `claude/hooks/*.py`. Anything else needs a matching change
  to `scripts/verify.py`.
- Hooks must never break a session: catch everything, fail open, stay fast (< 1 s typical), stdlib-only Python 3.
- `claude/CLAUDE.md` is the user's personal response style. Change its substance only when asked.
- `setup/setup.sh` must stay idempotent and fail-soft (warn and exit 0 unless `ARIKKFIR_CLAUDE_STRICT=1`). Keep the
  `@BUNDLE_SHA256@` placeholder; `scripts/build.sh` pins it.
- Bundles are content-addressed and immutable. Upload bundles before `setup.sh`.
- CI is Switchboard + Tekton (`.switchboard.yaml`, `.tekton/bundle.yaml`), not GitHub Actions.

## Before finishing a change

```bash
python3 -m unittest discover -s tests
sh scripts/build.sh && python3 scripts/verify.py && bash tests/test_setup.sh
shellcheck setup/setup.sh scripts/build.sh tests/test_setup.sh
```

`scripts/build.sh` archives `HEAD`: commit before building, or the bundle won't contain your change.
Add a test to `tests/test_hooks.py` for every guard rule you add or change.
