---
title: Contributing
description: How to contribute to Proxmox VM Autoscale — development setup, testing, code style, and what makes a change easy to review.
---

# Contributing

Bug reports, fixes and features are all welcome. This page covers the practical parts; [`CONTRIBUTING.md`](https://github.com/fabriziosalmi/proxmox-vm-autoscale/blob/main/CONTRIBUTING.md) in the repository is the canonical version.

## Before you start

For anything beyond a bug fix, open an issue first. It is much less frustrating than finding out after the work is done that the direction was wrong.

Worth reading first: [architecture](/reference/architecture) for how the pieces fit, and [known limitations](/reference/limitations) for what is already known — several of those are good first contributions.

## Development setup

```bash
git clone https://github.com/YOUR_USERNAME/proxmox-vm-autoscale.git
cd proxmox-vm-autoscale

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pytest
```

You do not need a Proxmox host to work on most of this — the test suite mocks SSH throughout.

## Running the tests

```bash
pytest -q                                    # everything
pytest tests/test_vm_hotplug.py -v           # one file
pytest -k cooldown -v                        # by name
```

170 tests, under a second. CI runs them on Python 3.10 through 3.12, plus `shellcheck -S warning install.sh`.

## What a good change looks like

**Tests that fail before the fix.** The most useful thing you can include is a test that demonstrates the bug against `main`. It proves the problem is real and stops it coming back.

```bash
git stash                                    # set your fix aside
pytest tests/test_your_new_test.py           # should fail
git stash pop
pytest tests/test_your_new_test.py           # should pass
```

**No unrelated changes.** Reformatting a file you happened to open makes the actual change impossible to see.

**Documentation updated in the same PR** when behaviour changes. The docs live in `docs/`; every page has an "Edit this page" link.

**A changelog entry** under `## [Unreleased]` in `CHANGELOG.md` for anything user-visible.

## Code style

Match what is already there. Specifically:

- 4-space indentation, no tabs
- `snake_case` for functions and variables, `PascalCase` for classes
- Docstrings on public methods, saying what the method does and what it returns
- Type hints where the surrounding code uses them — `autoscale.py` and `billing_tracker.py` are annotated, `vm_manager.py` and `ssh_utils.py` largely are not; follow the file
- Comments explain *why*, not *what*. The code already says what it does

There is no linter in CI and no formatter config, so consistency is by hand.

## Commit messages

Conventional-commit prefixes, imperative mood:

```
fix: enforce scaling_limits from config.yaml
feat: add per-VM threshold overrides
docs: document the balloon fallback path
test: cover NUMA detection with no numa line
chore: bump paramiko
```

Explain the reasoning in the body when a one-line subject cannot carry it. A reader six months from now needs to know why, not just what.

## Pull requests

1. Branch from `main` — `fix/...` or `feat/...`
2. Make the change, add tests, update docs
3. `pytest -q` and `shellcheck -S warning install.sh` clean
4. Push and open the PR

In the description: what changed, why, how you verified it, and any behaviour change existing installations will notice.

## Areas that need work

Pulled from [known limitations](/reference/limitations), roughly by value:

| Area | What is needed |
|---|---|
| **Dry-run mode** | Log what would happen without issuing commands. The most requested missing safety net |
| **Command result checking** | `qm set` failures are logged as successes because `execute_command` does not raise on a non-zero exit |
| **Metrics endpoint** | Prometheus exposition for cycle count, decisions, failures |
| **Per-VM scaling limits** | `scaling_limits` is global; thresholds are now per-VM but limits are not |
| **Encrypted SSH keys** | A passphrase option, or ssh-agent support |
| **Configurable step sizes** | Fixed at 1 core and 512 MB |

## Reporting bugs

[Open an issue](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/new/choose) with:

- What you expected and what happened
- Steps to reproduce
- Proxmox version (`pveversion`), Python version, OS
- Relevant log excerpts
- Your config **with every credential removed**

Security issues go through [responsible disclosure](/security/disclosure) instead — never a public issue.

## Code of conduct

Participation is covered by the [Code of Conduct](https://github.com/fabriziosalmi/proxmox-vm-autoscale/blob/main/CODE_OF_CONDUCT.md).

## Licence

Contributions are licensed under the [MIT Licence](https://github.com/fabriziosalmi/proxmox-vm-autoscale/blob/main/LICENSE), same as the project.
