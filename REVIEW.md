# Critical Review — Proxmox VM Autoscale

**Reviewed at:** `v1.6.0` (commit `2fcfbba`), 5 September 2026
**Scope:** product strategy, architecture, correctness, security, reliability, test quality, engineering process, documentation
**Method:** full read of the 2,426 lines of production Python, the 2,668 lines of tests, the installer, the systemd unit, the CI and docs pipelines, the 18-page documentation site, and the git history (148 commits, 7 tags). Measurements were taken against the reviewed commit and are quoted inline.

This document records **problems only**. It deliberately proposes no solutions: the value of the exercise is an unflinching inventory, and mixing remedies into it makes the inventory easier to argue with and easier to feel good about.

Everything below is written about the project as it stands today, including the substantial portion of it written today.

---

## 1. Verdict

**This is a competent homelab script wearing the documentation, release notes and metric surface of an infrastructure product, and the gap between those two things is now the project's single largest risk.**

The engineering that landed in `v1.3.0`–`v1.6.0` genuinely removed a set of severe defects: limits that were ignored, metrics that read as zero when unreadable, host keys accepted blindly, commands that lied about succeeding. That work is real and it was verified. But it was almost entirely *corrective*. Not one of those releases touched the structural decisions that produced the defects in the first place, and the polish added around them — a documentation site larger than the codebase, four releases in ninety-nine minutes, a Prometheus endpoint — raises the perceived maturity of the project far faster than its actual maturity.

A reader arriving at the repository today will conclude this is a production-grade tool. The code does not support that conclusion. That mismatch is not cosmetic: it changes who deploys it, on what, and with what expectations.

### Scorecard

| Dimension | Score | One-line justification |
|---|:--:|---|
| Product strategy & market fit | **4/10** | The reaction envelope does not match the problem the README describes |
| Architecture & design | **3/10** | No configuration contract; one 95-line orchestration method; no hypervisor abstraction |
| Correctness & data integrity | **4/10** | Non-atomic billing writes with silent total loss on corruption |
| Security posture | **3/10** | Root on every hypervisor, single process, plaintext credentials, unvalidated shell interpolation |
| Reliability & operability | **4/10** | No version identity, no signal handling, no reload, installs unreleased code |
| Test quality *(not quantity)* | **4/10** | Risk-inverted coverage; the main loop, the constructor and SSH execution are untested |
| Engineering process | **3/10** | Four minor releases in 99 minutes; no lint, type or coverage gate |
| Documentation | **7/10** | Genuinely excellent and honest — and disproportionate, and compensating |
| Maintainability & bus factor | **3/10** | One person has touched the core in twelve months |
| **Overall** | **4/10** | Correct-ish today, structurally fragile, and presenting as more than it is |

---

## 2. Strategic and product criticism

### 2.1 The reaction envelope does not fit the stated problem

The project positions itself for VMs with "bursty, uncorrelated load" ([README.md](README.md), [docs/index.md](docs/index.md)). Work through the timing the design actually permits:

- A burst is only visible if it is still running when a poll happens. Default `check_interval` is 300 s.
- One step per resource per decision: **1 core, or 512 MB**.
- Steps are further gated by `scale_cooldown`, default 300 s.
- Effective interval between two changes to the same resource: `max(check_interval, scale_cooldown)`.

Therefore: a burst shorter than five minutes is invisible. Moving a guest from 2 to 8 cores takes **six cycles — thirty minutes at defaults**. By the time the capacity arrives, the burst that justified it is statistically over.

This is not a tuning problem. Lowering `check_interval` multiplies SSH load (see §3.4) and increases flapping; raising step size is not configurable at all. The tool is structurally too slow for burst absorption, which is the use case it advertises.

What it can genuinely do is **slow capacity drift and de-provisioning of over-allocated fleets** — mostly scale-*down*. That is a real and useful niche. It is also the direction with the highest blast radius (§4.3), and it is not the niche the marketing describes. The project is selling the exciting half of a capability whose safe half is the boring one.

### 2.2 The billing feature is a category error

`billing_tracker.py` (601 lines — a quarter of the entire codebase) exists to help hosting providers **invoice customers**. That is financial data. It is persisted as a single JSON file rewritten in full on every event, with no atomicity, no audit trail, no immutability, no reconciliation, no currency field, and naive local-time timestamps that will be wrong across a DST boundary.

A feature that produces numbers someone will bill against carries a different quality bar than a feature that resizes a homelab VM. This one is built to the second standard and used for the first. §4.1 covers the mechanism; the point here is that the *decision to ship it at all* at this level of rigour is the more serious error, and no amount of subsequent bug-fixing changes that.

It also drags the project into a market — hosting billing — where it competes with actual billing systems, on a surface it cannot win, for users it has no way to support.

### 2.3 The commercial model is a mailto:

[README.md](README.md) offers "paid support, custom development and consulting" behind an email address. There is no SLA, no response commitment, no tiering, no pricing anchor, and no separation between the free artifact and the paid one. This is not a commercial model; it is a hope. It also creates a support expectation the project has no process to absorb — 302 stars and 23 forks of people who now believe there is a person behind this.

### 2.4 Value is bounded by constraints the project cannot fix

Honest framing of the ceiling, which the documentation does state but the positioning does not internalise:

- `cores` changes need a guest reboot; only `vcpus` is live. Live scale-up only works inside pre-provisioned core headroom.
- Memory reclaim depends on the balloon driver being present and functional in the guest.
- CPU hot-unplug is unreliable across guest operating systems and routinely refused.

So the "live scaling" headline is conditional on guest-side facts the tool neither controls nor verifies (§4.5). The product's actual reach is smaller than its name.

---

## 3. Architectural criticism

### 3.1 There is no configuration contract — and this is the root cause of nearly every defect fixed today

This is the most important finding in the document.

Configuration is a free-form `dict` from `yaml.safe_load`, validated by exactly one check in [autoscale.py](autoscale.py) `_load_config`: that four top-level keys are *present*. Nothing validates types, ranges, unknown keys, cross-field consistency or referential integrity.

Measured: **34 distinct configuration keys are read across 56 sites in 3 modules** (`autoscale.py` 42, `vm_manager.py` 7, `billing_tracker.py` 7), each with its own inline default.

Now look at what was fixed in the last four releases:

| Defect | Mechanism |
|---|---|
| `scaling_limits` ignored (v1.3.0) | Read under a key name that did not exist |
| Per-VM `thresholds` ignored (v1.5.0) | Documented key never read by anything |
| `ssh_port` `KeyError` (v1.5.0) | Direct index instead of a default, on a key the shipped example omitted |
| Per-VM `scaling_limits` absent (v1.6.0) | No mechanism to express it |
| `logging:` section inert | Silently overridden by a JSON file |

**Every one of these is the same defect.** A key that is written, documented, and not read produces no error at any layer — not at load, not at use, not in the log. The four fixes were symptomatic; the generator is untouched, and it will keep producing. Any key added tomorrow is one typo away from being silently inert, and the user's only feedback is a VM that does not behave as configured.

The `proxmox_host` → `proxmox_hosts[].name` link is the same hazard in referential form: a typo there means a VM is never processed, with no warning emitted anywhere. It remains unaddressed, and is documented as a known limitation rather than treated as a design defect.

### 3.2 `process_vm` is an orchestration god-method

[autoscale.py:225–320](autoscale.py) — roughly 95 lines handling, in one scope: SSH lifecycle, manager cache lookup, running-state gate, billing state recording, metrics emission, host-limit gate, usage retrieval, availability branching, per-resource threshold resolution, two nested try/except scaling blocks, error notification and connection teardown. Nesting reaches five levels.

Consequences visible in the code today:
- Metrics emission is interleaved with control flow rather than derived from it, so every new observation means editing the decision path.
- The two scaling blocks are near-identical copies differing only in the resource name.
- The method cannot be tested without patching the constructor and hand-assembling seven attributes (§6.2).

### 3.3 There is no abstraction over the hypervisor

Command strings are formatted inline in `vm_manager.py` (14 f-string interpolations of `self.vm_id` into shell commands) and parsed back with regular expressions in the same methods. There is no Proxmox client layer, no command builder, no response model.

The practical costs:
- The Proxmox CLI surface is a hard dependency spread across the module rather than isolated behind one seam.
- Every Proxmox behaviour change is a shotgun edit.
- The `pvesh` table-scrape defect fixed in v1.4.0 was possible precisely because parsing lives next to business logic instead of behind a boundary.
- There is no way to support a second hypervisor, a Proxmox API token, or a mock backend for integration testing — the last of which is why there is no integration test.

### 3.4 The work-per-cycle is measured, and it is bad

Instrumented against the reviewed commit with a recording SSH double:

```
scale_cpu('up')  -> 5 SSH commands:  3x `qm config 101`, 1x `qm status`, 1x `qm set -vcpus`
scale_ram('up')  -> 5 SSH commands:  3x `qm config 101`, 1x `qm status`, 1x `qm set -balloon`
```

**The identical `qm config <vmid>` is executed three times inside a single scaling decision**, because `_get_current_cores`, `_get_current_vcpus`, `_check_hotplug_enabled`, `_check_numa_enabled`, `_get_current_ram` and `_get_balloon_value` each run it independently with no caching. The `qm status` is also redundant — `process_vm` established the guest was running seconds earlier.

Add the per-VM gate work and a full cycle for one VM scaling both resources is **~13 SSH round-trips, of which roughly 7 fetch data already in hand**.

Compounding this:
- A new `SSHClient` is constructed, connected and torn down **per VM**, not per host. N VMs on one node means N full SSH handshakes per cycle.
- `HostResourceChecker` is instantiated **per VM** ([autoscale.py:251](autoscale.py)), so the node status query runs once per VM. Twenty VMs on a node = twenty identical `pvesh get /nodes/.../status` calls per cycle, and twenty pairs of log lines.

### 3.5 The concurrency model caps the fleet size, silently

Hosts and VMs are processed strictly sequentially in `run()`; every SSH command blocks. Failure paths are slow by construction: connection retry is five attempts with exponential backoff (~31 s), and command retry adds more.

So one unreachable node stalls every VM behind it in the cycle. There is no parallelism, no timeout on the cycle as a whole, and no back-pressure. When a cycle exceeds `check_interval` the loop simply runs continuously — with no metric, log line or warning distinguishing "polling every 5 minutes" from "saturated and running flat out". The `vm_autoscale_cycle_duration_seconds` gauge added in v1.6.0 exposes the number but nothing interprets it.

### 3.6 State that matters lives only in memory

Cooldown timers and the manager cache are process-local. A restart clears them, so the first cycle after any restart can scale every eligible VM at once. `Restart=always` in the unit means a crash loop is also a *rate-limit-bypass* loop. This is documented as a limitation; it is more accurately a design consequence of having no durable state store for a service whose correctness depends on remembering when it last acted.

---

## 4. Correctness and data integrity

### 4.1 Billing persistence can lose everything, silently

[billing_tracker.py](billing_tracker.py) `_save_data` opens the state file with mode `'w'` — which truncates immediately — and writes the full JSON in place. There is no temp-file-and-rename, no fsync, no backup, no write-ahead.

[billing_tracker.py](billing_tracker.py) `_load_data` wraps the whole read in `try/except Exception`, logs a **warning**, and continues with empty in-memory state.

Chain the two:

1. The process is killed mid-write (see §4.2 — this is the *normal* stop path, not an edge case).
2. The file is left truncated or invalid.
3. On restart, load fails, logs one warning at a level nobody alerts on, and the tracker starts empty.
4. The next scaling action calls `_save_data`, which **overwrites the damaged file with the empty state**.

The billing history is now permanently gone, and the only trace is a single `WARNING` line. For a feature whose output is an invoice, this is the most serious defect in the codebase — more serious than anything fixed in v1.3.0–v1.6.0, none of which touched it.

Secondary: `_save_data` is called on every spec change and every state change, rewriting the entire file each time, and nothing ever prunes it. Cost per event grows linearly with total history, forever.

### 4.2 There is no signal handling, so the normal stop path is a hard kill

Measured: no `signal`, `SIGTERM` or `atexit` handling anywhere in the codebase.

`run()` catches `KeyboardInterrupt`, which is `SIGINT` only. `systemctl stop` sends `SIGTERM`. Python's default `SIGTERM` disposition terminates the process immediately, wherever it is — including inside `_save_data`, inside an SSH exchange, or between a `qm set` and the log line claiming it happened.

[vm_autoscale.service](vm_autoscale.service) sets no `TimeoutStopSec`, `KillSignal` or `ExecStop`. Every stop, restart and upgrade of this service is an abrupt kill. §4.1 is what that costs.

### 4.3 Scale-down is the dangerous direction and is treated identically to scale-up

The decision path applies symmetric logic to two asymmetric operations. Reclaiming memory from a guest that is using it drives it to swap or to the OOM killer; removing a vCPU may be refused or may destabilise a pinned workload. Adding resources, by contrast, fails safe.

There is no asymmetry anywhere in the model: no separate cooldown, no confirmation, no guest-side check before reclaiming, no hysteresis beyond the shared dead band, no "never shrink below observed working set". The only guard is `min_ram_mb`, a static number the operator has to guess correctly for every workload in the fleet — and it is global unless overridden per VM.

### 4.4 Timestamps are naive local time

`datetime.now()` throughout `billing_tracker.py`. No timezone, no UTC. A billing period spanning a DST transition is off by an hour, and the error is silent and unfalsifiable after the fact.

### 4.5 The service still cannot tell whether the guest obeyed

v1.6.0 correctly made a non-zero `qm set` exit status raise. But QEMU accepting a command is not the guest honouring it. A missing balloon driver, or a kernel refusing a CPU hot-unplug, produces exit status 0 and no change inside the VM. The service will log success, notify success, record a billing spec change that did not happen, and start the cooldown.

This is the last place where the service can say "done" without it being true, and it is the one that most affects billing accuracy.

---

## 5. Security criticism

### 5.1 The blast radius is the entire estate; the engineering rigour is a script

This service holds credentials for `root` on every configured hypervisor, in plaintext, in one file, in one process, on one host, with no privilege separation and no audit trail. Compromise of that single process is compromise of every VM in the fleet.

The threat model at [docs/security/index.md](docs/security/index.md) states this clearly and honestly. Stating it does not reduce it. Between v1.3.0 and v1.6.0 the *documentation* of the security posture improved dramatically; the *posture* changed in exactly one respect (host key verification). Root is still required, credentials are still plaintext, the process is still unconfined, and there is still no least-privilege path — no Proxmox API token support, no forced-command wrapper, no capability scoping.

There is a real risk that the quality of the security documentation is now read as evidence of security.

### 5.2 Unvalidated identifiers are interpolated into shell commands

`vm_id` is taken from configuration and formatted into shell command strings in **14 places** in [vm_manager.py](vm_manager.py), with no validation anywhere — not an `int()` coercion, not a regex, not a whitelist.

The usual defence is that the config is administrator-controlled, and for remote exploitation that holds. It does not make the property acceptable in a service running as root: it means a copy-paste error in YAML becomes an arbitrary command executed as root on a hypervisor, and it means the config file's integrity is load-bearing for command safety while being protected only by file permissions.

### 5.3 The billing webhook is an unauthenticated arbitrary-execution surface

[billing_tracker.py](billing_tracker.py) `run_webhook` executes `webhook_script` via `subprocess.run` and POSTs to `webhook_url` via `requests`. There is no signature, no authentication header, no TLS pinning, no allowlist, no verification that the script path is not writable by others. Anyone who can edit the config, or write to the referenced script path, gets root execution on the next billing period boundary.

### 5.4 The metrics endpoint is unauthenticated by construction

Added in v1.6.0. Correctly disabled by default and correctly bound to localhost — but there is no authentication, no TLS and no allowlist available at all, so the only safe deployment is the loopback default. The moment anyone changes `bind` to reach a remote Prometheus, the series naming every node, every VMID and their live utilisation are exposed to anything that can route to the port. The configuration makes that a one-line change with no guard rail beyond a warning in prose.

### 5.5 Notification channels leak infrastructure detail and cannot be throttled

Messages carry node names, VMIDs and utilisation figures, to a Gotify server and an SMTP relay. There is no rate limiting or deduplication anywhere in [autoscale.py](autoscale.py): an unreachable node emits one priority-9 notification **per VM, per cycle** — twenty VMs on a five-minute interval is 240 notifications an hour, indefinitely. This is both an operational failure mode and a slow leak of estate topology into whatever inbox those land in.

---

## 6. Testing criticism

201 tests, 2,668 lines, 80% overall coverage, sub-three-second runtime. Those numbers look excellent and they are the most misleading numbers in the project.

### 6.1 Coverage is inverted against risk

| Module | Coverage | Operational criticality |
|---|:--:|---|
| `metrics.py` | **100%** | None — an optional read-only endpoint |
| `host_resource_checker.py` | **100%** | Low — one read query |
| `vm_manager.py` | 90% | High |
| `billing_tracker.py` | 87% | Financial |
| **`autoscale.py`** | **63%** | **Highest — the orchestrator** |
| **`ssh_utils.py`** | **63%** | **Highest — the only path to the hypervisor** |

The two modules that touch the outside world and can damage a fleet are the two least tested. The module that cannot damage anything is perfectly tested, because it was written last and written for testability.

Specifically uncovered at the reviewed commit:

- **`autoscale.py:596–625` — the entire `run()` main loop.** Ordering, sleep behaviour, billing scheduling, error recovery, the `Restart=always` interaction: none of it is exercised.
- **`autoscale.py:629–637` — `main()`**, the actual entrypoint.
- **`autoscale.py:162–189` — `VMAutoscaler.__init__`.** Every test bypasses it by patching. The method that wires the entire object graph is never run by the suite.
- **`autoscale.py:64–86, 90–126` — Gotify and SMTP delivery.** Routing is tested; delivery is not.
- **`ssh_utils.py:200–225` — `execute_command` in its entirety**, including the retry-and-reconnect loop. This is the single function through which every hypervisor mutation passes.

### 6.2 The suite tests the implementation, not the contract

Measured: **191 calls to private members across 277 assertions** — 0.69 private accesses per assertion. Tests reach into `_thresholds_for`, `_scaling_limit`, `_run`, `_apply_host_key_policy`, `_get_current_cores`, `_mark_scaled`, `_set_ram`, `_scale_cpu_up` and more.

The cost is not theoretical. Twice today, adding a single attribute to `VMAutoscaler` (`dry_run`, then `metrics`) broke ten tests across two files — not because behaviour changed, but because every test hand-constructs the object by patching `__init__` and setting seven attributes by hand. A suite that breaks when the constructor gains a field is a suite that will be *edited to pass* rather than consulted, and each such edit erodes it further.

### 6.3 There is no integration test, and no seam to build one on

Every test mocks SSH. Nothing has ever run against a real Proxmox node in CI or in the repository's history. This matters more than usual here because the highest-severity defects in the project's history were all **format and protocol assumptions** about `pvesh` and `qm` output — precisely the class of defect that only integration testing catches, and precisely the class that mocks reproduce faithfully and therefore validate falsely. The v1.4.0 table-scrape defect survived 138 passing tests.

§3.3 explains why such a test cannot easily be written: there is no boundary to substitute.

### 6.4 The regression tests are correct but narrow

The tests added in v1.3.0–v1.6.0 are good — several were verified to fail against the previous commit, which is more than most projects do. But they are all *example-based tests of specific past bugs*. There is no property testing, no fuzzing of the configuration surface, no state-machine testing of the cooldown/scaling interaction, and no adversarial testing of the parsers. The suite proves the known bugs are gone. It does not explore.

---

## 7. Engineering process criticism

### 7.1 Four minor releases in ninety-nine minutes

Measured from the tags:

```
v1.3.0  20:48
v1.4.0  21:39   (+51 min)
v1.5.0  22:08   (+29 min)
v1.6.0  22:27   (+19 min)
```

Preceded by four months of no releases at all.

Each of those four carries behaviour changes and an upgrade note. No operator can consume that. A user who upgrades once lands on an arbitrary point in the sequence; a user who watches releases receives four notifications in an evening and learns to mute them. The upgrade notes — which are careful and well written — are read by nobody, because nobody upgrades four times in an evening.

This is a release *stream*, not a release *process*. It optimises for the author's sense of progress at the cost of the consumer's ability to track state.

### 7.2 The recommended install path ignores the releases entirely

[install.sh:49](install.sh) runs `git clone` against the repository with no `--branch`, so it clones the **default branch**. The command the README puts first therefore installs **whatever is on `main` at that moment**, not a release.

So: seven tags exist, four of them cut today with detailed upgrade notes, and the documented happy path bypasses all of them. Release engineering that nothing consumes is theatre. It also means every user is a continuous-deployment target for unreviewed `main`, and no two installs are reliably the same artifact.

### 7.3 The running software cannot identify itself

Measured: there is no `__version__`, no version constant, no version file anywhere in the Python sources. The service logs `"Starting VM Autoscaler"` with no version. The `vm_autoscale_build_info` metric added in v1.6.0 carries a `dry_run` label and **no version label** — a `build_info` series without a version is close to useless, and that is a flaw in work done today.

Combine with §7.2: the software installs from a moving branch, cannot state which commit it is, and exposes a build-info metric that does not say either. Answering "what is actually running on that node?" requires going to the node and running `git log`.

### 7.4 CI enforces almost nothing

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs `pytest` on three Python versions and `shellcheck` on the installer. Measured: **no linter, no formatter, no type checker, no coverage gate, no security scanner, no dependency audit** in the pipeline.

Consequences already visible: type hints cover **37 of 104 functions (36%)**, and the distribution is arbitrary — `vm_manager.py` has **0 of 31** annotated and `ssh_utils.py` **0 of 9**, while `autoscale.py` has 16 of 23. The two least-annotated modules are again the two most dangerous ones. Nothing in the pipeline notices, and nothing prevents the ratio from drifting further.

`SECURITY.md` recommends `pip-audit` to contributors. CI does not run it.

### 7.5 The project is not packaged

No `pyproject.toml`, no `setup.py`, no `setup.cfg`. Not installable with pip, not packageable as a `.deb`, no declared entry point, no dependency resolution beyond a three-line `requirements.txt` of lower bounds with no upper bounds and no lockfile.

Installation is `git clone` into **`/usr/local/bin/vm_autoscale/`** — a directory of Python sources, config and state inside a directory reserved for executables. Configuration lives there too rather than under `/etc`. This breaks FHS expectations, breaks configuration-management tooling that treats `/usr/local/bin` as binaries, and is why the installer has to `rm -rf` the directory on every upgrade.

### 7.6 The bus factor is one, and the project's surface just tripled

Measured: over the last twelve months, the core modules (`autoscale.py`, `vm_manager.py`, `ssh_utils.py`) were touched by **one person**. External contributions exist (9 commits from 4 contributors) but are historical and peripheral.

Meanwhile the project today acquired: a Node/VitePress documentation toolchain (**180 npm packages, a 2,681-line lockfile**), a Prometheus metrics module, a billing scheduler, a host-key policy layer, and a dry-run mode. Every one of those is new surface that one person maintains and no one else has reviewed.

The npm toolchain deserves separate mention: contributing a documentation fix to a 2,400-line Python project now requires Node, an `npm ci` of 180 packages, and knowledge of VitePress. That is a contribution barrier introduced for the benefit of the maintainer's output, paid by everyone else.

### 7.7 Review depth did not scale with change volume

Two of the four releases today received an automated review; two did not, and none received human review. The changes shipped in that window include a new network listener, a change to SSH trust policy, and modifications to financial calculations. The velocity was high, the verification was self-administered, and the reviewer and the author were the same agent.

That is worth stating plainly rather than leaving as an inference: **a significant fraction of the current codebase has never been read by a second party.**

---

## 8. Documentation criticism

The documentation is the best-executed part of this project. It is also disproportionate, and in one important way it is doing the wrong job.

### 8.1 It is larger than the product

Measured: **3,476 lines of documentation site + 1,012 lines of root markdown = 4,488 lines, against 2,426 lines of production Python.** A ratio of 1.85:1.

Documentation this thorough for a codebase this small is not a virtue by default. It signals either that the software needs this much explanation — which is an indictment of the software — or that effort went where it was most enjoyable rather than where it was most needed. Both readings are partly true here. A tool whose entire behaviour is "compare two numbers, add one core" requiring an 18-page site to operate safely is telling you something about the tool.

### 8.2 It documents around defects instead of the defects being fixed

The [known limitations](docs/reference/limitations.md) page is admirably honest — and it is also a list of things that were written down instead of repaired. Several entries are pure design debt presented as immutable characteristics: the `proxmox_host` typo that silently skips a VM, `host_limits` blocking scale-*down* on a saturated node, cooldown state lost on restart, `ssh_password` silently overriding `ssh_key`.

Writing a limitation down converts a bug into a feature of the documentation. It is genuinely better than hiding it. It is not the same as fixing it, and a page of fifteen carefully-worded constraints can read to a maintainer as "handled".

### 8.3 It carries drift risk it has already demonstrated

The documentation restates behaviour that lives in code — test counts, threshold semantics, command strings, metric names, key defaults — across 18 pages plus a README plus `ARCHITECTURE.md` plus `SECURITY.md`. During this session alone, documentation had to be corrected in **13 files** in a single change, and stale claims (test counts, `ssh_port` requiredness, RSA-only keys, the systemd unit's provenance) were found repeatedly *after* the code changed.

Only one mechanism prevents drift: the docs changelog page includes `CHANGELOG.md` rather than restating it. Everything else is manual, and nothing in CI checks any documented claim against the code. The one automated check that exists ([.github/workflows/docs.yml](.github/workflows/docs.yml)) verifies that files are present and that the site is served — not that anything it says is true.

### 8.4 The social preview still shows the old identity

Cosmetic but public: the repository's Open Graph image, referenced from [docs/.vitepress/config.mts](docs/.vitepress/config.mts), is the GitHub-hosted social preview which still carries the previous logo. Every link shared to Slack, LinkedIn or X shows a mark the project no longer uses.

---

## 9. Risk register

Ordered by expected cost, not by how easy they are to talk about.

| # | Risk | Likelihood | Impact | Where |
|:--:|---|:--:|:--:|---|
| 1 | Billing history silently and permanently destroyed by a normal `systemctl stop` | **High** | **Severe** | §4.1, §4.2 |
| 2 | Compromise or misconfiguration of the single process yields root on the whole estate | Low | **Catastrophic** | §5.1 |
| 3 | Memory reclaimed from a guest that needs it → OOM in a production VM | Medium | High | §4.3 |
| 4 | A config key is written, documented and silently never read | **High** | Medium–High | §3.1 |
| 5 | A `qm set` succeeds, the guest ignores it, and billing records the change anyway | Medium | High | §4.5 |
| 6 | Nobody can determine which version is deployed during an incident | **High** | Medium | §7.2, §7.3 |
| 7 | Notification storm from one unreachable node buries real alerts | Medium | Medium | §5.5 |
| 8 | A regression lands in the untested main loop or SSH execution path | Medium | High | §6.1 |
| 9 | Sole maintainer becomes unavailable; the surface tripled today | Low | **Severe** | §7.6 |
| 10 | Metrics endpoint rebound off localhost, exposing estate topology | Low | Medium | §5.4 |

---

## 10. What is genuinely good

A review that finds nothing to defend is not a review, it is a posture.

- **The defect work in v1.3.0–v1.6.0 is real and was verified.** Regression tests were demonstrated to fail against the previous commit rather than merely asserted to. The `qm set`-exit-status defect was proved by running the same probe against both trees. That is a higher standard of evidence than most projects apply.
- **The "absent is not zero" principle is applied consistently** — in the scaling decision, in the metrics registry, and in the log vocabulary. That is genuine design coherence, and it is the correct instinct about the class of bug that has hurt this project most.
- **The documentation's honesty is unusual.** Publishing a fifteen-item known-limitations page, a threat model that names root access as the central exposure, and release notes that say "this used to be silently wrong" is a real editorial choice most maintainers do not make.
- **Failure paths are conservative in the right direction.** Unreadable metrics skip rather than guess; a mismatched host key is fatal rather than retried; a bind failure degrades rather than crashes.
- **The problem itself is real and under-served.** Proxmox has no native vertical autoscaling. There is a genuine gap here, and 302 stars are evidence that people are looking for something in this shape.

---

## 11. The uncomfortable summary

The last four releases fixed the bugs that were *findable by reading the code*. What remains is the set of problems that are only visible from a distance:

- The architecture has **no configuration contract**, and that single absence generated most of the defects that were fixed today. It is untouched, and it will generate more.
- The **billing subsystem can destroy its own data on a routine service stop**, and this is more severe than anything corrected in the last four releases.
- The **test suite's 201 tests and 80% coverage are inverted against risk**: the main loop, the constructor and the sole SSH execution function are untested, while the optional metrics endpoint is at 100%.
- The project **installs from a moving branch and cannot state its own version**, which makes its careful release notes unreachable and its incident response guesswork.
- The **documentation is 1.85× the size of the software** and is, in several places, doing the work that fixes should have done.

None of that makes the tool useless. It makes it a tool whose presentation has moved considerably further than its engineering, in a single day, with one pair of eyes. The most dangerous outcome is not any individual defect on this list — it is that the repository now looks finished.

*If one thing on this document is acted on, the ranking in §9 is the honest order. If nothing is, §3.1 is the one that will keep costing.*
