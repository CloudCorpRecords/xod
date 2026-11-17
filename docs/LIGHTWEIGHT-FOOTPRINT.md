# Lightweight resource measurement

## Scope and proposed budget

This is an idle measurement, not a performance optimization or an inference
benchmark. The approval relay and Partner watchdog remain part of lightweight
mode. No service was removed to make the measurements smaller. Optional
inference and desktop dependencies must be counted separately.

Proposed acceptance budget, not a previously established product guarantee:

| Component | Sustained idle resident memory ceiling |
| --- | ---: |
| Backend, including its non-model child processes | 256 MiB |
| Native Partner process and children | 64 MiB |
| Desktop shell, including renderers | 192 MiB |
| Combined always-on application | 512 MiB |

For the combined always-on application, propose mean CPU <= 1% of one logical
core and per-sample p95 <= 5% of one core over a ten-minute native idle run.
Propose startup readiness <= 10 seconds and sampled startup/settling RSS peak
<= 768 MiB. A short burst over the idle CPU ceiling is not a failure of the
mean target, but must remain visible in the maximum and p95.

Treat an advertised 8 GB machine conservatively as 8,000,000,000 bytes
(7,629.4 MiB), rather than silently assuming 8 GiB. Reserve 5,600 MiB for the
OS, Muse, browser and other applications, plus 512 MiB for Xod's always-on
layer. That leaves approximately 1,517.4 MiB, including all inference
working memory and safety margin. This does NOT establish that a particular
local model fits. macOS unified GPU memory, model weights, KV cache,
compression and swap all need native measurements. Cloud/BYOK inference
does not eliminate browser, tool or local buffering costs.

## Reproducible protocol

Collector: `tests/runtime/measure_lightweight.py`, using existing psutil.

1. Record revision and uncommitted changes with `git rev-parse HEAD` and
   `git status --short`. Record machine/OS, physical and logical CPU count,
   visible memory and cgroup limits. The collector includes platform details
   in each JSON file. Container-visible host memory is not the machine budget.
2. Use a quiet, plugged-in machine with no tasks, model calls, browser
   navigation, active approval cards or unrelated benchmark work. Record
   whether Muse is open, approval mode, permissions, shell/headless mode,
   Partner version, trust and watchdog availability. Do not change a real
   user's approval mode just to run the collector.
3. For Linux, launch a fresh isolated dry-run backend. The collector sets
   temporary HOME, XDG configuration/data and Xod data paths. It passes only
   interpreter/library/SSL/locale environment variables, not API keys or
   integration credentials. It ignores the checkout's developer env file,
   uses the null keyring, enables the approval preference in isolated data,
   selects lightweight mode and dry run, and disables speech and update
   checks. There are no saved tasks, accounts, routines or models. The normal
   preview remains untouched. Core startup code is real, not a fixture.
4. Sample every second from launch. Poll `/api/mode` only until it first
   responds; record that elapsed readiness time. Allow 30 seconds after
   readiness for startup activity to settle, then sample idle for 120 seconds.
   This is a fixed settling allowance, not proof that memory has converged.
   Record startup/settling peaks, not just the final sample.
5. Repeat from fresh isolated state. Compare repeated runs only under the
   same settings, machine, interval and idle duration. Do not use the earlier
   five-second, preview-active measurements as a before/after baseline.
6. On a native desktop use read-only attachment, a 60-second settling
   allowance and at least 600 seconds of sampling, three runs. Supply the
   actual Partner, Electron shell and loaded model-server root PIDs where
   present. The collector never starts, restarts or stops attached processes.
   Native startup timing requires a separately timed real desktop launch;
   attachment reports readiness as null, not zero.
7. Review raw JSON and local startup logs. Record permission failures,
   unexpected child processes, warnings, restarts and unrelated activity.
   A missing Partner, shell, model, or native watcher is UNKNOWN, not 0 MiB.

Linux collection commands:

```sh
python tests/runtime/measure_lightweight.py \
  --settle 30 --duration 120 --interval 1 --label linux-lightweight-1 \
  --output generated-artifacts/footprint/run-1.json
python tests/runtime/measure_lightweight.py \
  --settle 30 --duration 120 --interval 1 --label linux-lightweight-2 \
  --output generated-artifacts/footprint/run-2.json
```

Example native attachment, replacing each PID and port with the actual value:

```sh
python tests/runtime/measure_lightweight.py \
  --pid BACKEND_PID --partner-pid PARTNER_PID --shell-pid ELECTRON_ROOT_PID \
  --port BACKEND_PORT --settle 60 --duration 600 --interval 1 \
  --label mac-lightweight-idle \
  --output generated-artifacts/footprint/mac-idle.json
# Add --model-pid MODEL_SERVER_PID only if a model is already loaded.
# Omit absent roots; do not substitute a fake Partner or load a model.
```

### Metric definitions and overhead

- RSS is resident memory, not virtual address space. Summed process RSS can
  double-count shared pages, so it is a conservative allocation indicator,
  not unique physical memory. No USS/PSS, GPU, compressed-memory, system-wide
  swap, energy or thermal claim is made.
- CPU is the delta of user+system seconds divided by actual elapsed wall time,
  multiplied by 100. 100% means one fully occupied logical core, not the
  entire machine. The first sample has no CPU delta.
- The backend group includes recursively observed descendants. Explicit
  Partner/model/shell roots take precedence to avoid counting a process
  twice. The inventory records PID, parent, name and first/last observed
  time; it deliberately excludes environment variables and command arguments.
- Independently launched model servers and detached processes are not
  automatically attributed. Supply their PIDs explicitly. Processes that
  live entirely between samples, or exit before the next observation, can
  have missed CPU/memory. One-second peaks are lower bounds on true peaks.
- Collection runs in a separate process excluded from the application
  totals. Its process inspection and the shared Replit host still introduce
  overhead and scheduling variance. No API polling occurs during idle;
  final service-status reads happen after sampling.
- Raw samples and logs go under ignored `generated-artifacts/`, excluded
  from Git and deployment. Logs from a native installation can contain
  personal data; review locally before sharing them.

## Native availability

`run_mode.OPTIONAL_LOOPS` does not skip `muse_app` or `partner_watchdog`.
However, `muse_app.start()` returns on Linux because no desktop backend exists.
`partner_client.start_watchdog()` intentionally refuses a non-installed
development process, so it cannot restart a person's installed Partner.
Neither guard is overridden for measurement. Linux numbers therefore do not
include actual Accessibility scans, physical approval actions, the native
Partner process, reciprocal installed watchdog activity, or Electron.

## Measured results

Measured November 16, 2026 in America/Los_Angeles (November 17 UTC).
Product revision: `a0f79adf13e11fb6c935ee3f739796bd0203b9d2`.
The working tree contained only this measurement tooling, report and ignore
changes; the application's inference, approval and scheduling code was not
modified. The regular web preview remained running outside the measured tree.

### Platform and enabled services

- Replit Linux 6.18.63 x86_64, glibc 2.40, Python 3.12.12, psutil 7.2.2.
- KVM virtual machine; reported CPU model Intel Xeon Platinum 8581C at
  2.30 GHz; 4 visible logical CPUs (psutil also reports 4 physical).
- Visible memory: 7,965.1 MiB. Cgroup memory ceiling: 8,589,934,592 bytes
  (8 GiB); CPU quota: `400000 100000` (4 CPU-equivalents). These figures
  do not establish performance on a real 8 GB Mac or a low-end processor.
- Real lightweight startup: task queue, worker watchdog, doctor,
  self-maintenance, missions, routines, Agent Mail, MCP hub, phone bridge,
  browser idle loop and computer-health startup remained in place, subject
  to each service's existing no-config/platform guards. Fresh isolated data
  meant no actual jobs, paired accounts or browser session.
- One backend Python process plus one computer-control MCP Python child
  were observed in each run; the logs report five computer-control tools.
  No tool actions or model calls were requested. The child is included in
  the backend numbers, not silently omitted.
- Approval preference was **on / always**, dry run true. Linux reported
  desktop backend null and watcher_running false. Partner startup logged
  that the reciprocal watchdog stays off outside the installed app. This
  is retained product behavior, not an active native relay measurement.
- The eight standard lightweight optional loops were skipped:
  soul_life, recall_startup_index, autoconnect, xod_network, memory_sync,
  handoff_dispatch, specialists and inbox_warm. Pending restart was false.
- Both logs warned that no chat model was configured and that a macOS
  memory-pressure sysctl is unavailable. No secure keyring was emulated,
  model downloaded, inference invoked, or installed watchdog guard bypassed.

### Two matched runs

Each run had 30 seconds of settling after readiness, followed by 121
one-second observations spanning approximately 120.000 seconds of idle.
The startup and settling observations are excluded from the idle summary.

| Metric, backend plus observed child | Run 1 | Run 2 | Proposed target |
| --- | ---: | ---: | --- |
| Time to first successful mode response | 2.037 s | 2.023 s | <= 10 s |
| Startup/settling sampled RSS peak | 201.77 MiB | 202.18 MiB | <= 768 MiB combined |
| Idle mean RSS | 202.79 MiB | 203.16 MiB | <= 256 MiB backend |
| Idle median RSS | 203.44 MiB | 203.70 MiB | Descriptive |
| Idle RSS p95 | 203.59 MiB | 203.84 MiB | Descriptive |
| Idle RSS maximum | 204.01 MiB | 203.87 MiB | <= 256 MiB backend |
| Idle RSS standard deviation | 0.78 MiB | 0.67 MiB | Descriptive |
| Idle mean CPU, percent of one core | 0.281% | 0.289% | <= 1% combined |
| Idle CPU p95, percent of one core | 1.000% | 1.000% | <= 5% combined |
| Idle maximum CPU, percent of one core | 4.009% | 5.000% | Descriptive, not p95 |
| Idle CPU standard deviation, percentage points | 0.564 | 0.622 | Descriptive |

Run starts were `2026-11-17T01:44:19Z` and `2026-11-17T01:46:52Z`.
Observed process trees were backend 11739 -> child 11776 and backend 11859
-> child 11896. No additional descendants were observed at sample times.
These PIDs are evidence from these runs, not reusable collection inputs.

The two mean RSS values differ by 0.37 MiB (about 0.18%); mean CPU differs
by 0.008 percentage points. With only two short runs these are descriptive
differences, not confidence intervals or a statistically established baseline.
An earlier two-second collector pilot had approximately 9 seconds to readiness;
it was not a matched sustained run and is not included in this table. File
caches and shared-host scheduling were not controlled, so no cold-start
guarantee is implied by the two faster formal starts.

### Budget assessment and remaining risks

- **Backend-only Linux observation: within the proposed limits.** Maximum
  observed backend-tree RSS was 204.01 MiB, leaving approximately 51.99 MiB
  below its 256 MiB allocation. Its mean and p95 CPU were below the proposed
  combined limits, but native components would consume additional CPU.
- **Whole always-on application: UNKNOWN.** Partner's 64 MiB, Electron's
  192 MiB and their CPU cannot be marked as passing or zero. Consequently
  the combined 512 MiB budget and complete 8 GB machine budget are unverified.
- **Specific measured pressure:** the backend plus idle MCP child already
  uses about 203 MiB, approximately 79% of the backend allocation, even with
  no loaded model. Per-module or per-child allocation was not profiled, so
  this does not establish that a particular import or MCP tool is responsible.
- RSS rose 2.24 MiB during Run 1 idle and 1.69 MiB during Run 2 idle.
  A 30-second settling period did not produce a perfectly flat RSS trace.
  This is not proof of a leak or of eventual stability; a longer native
  soak is needed before claiming a bounded long-term footprint.
- This two-minute window does not cover every multi-minute/hourly service
  cadence. In particular the 300-second computer-health cycle is not fully
  represented. Native thirty-second Accessibility backstops and fifteen-second
  installed Partner probes were unavailable, not measured idle wins.
- No loaded model or live Muse/Partner process was present. Model memory,
  token latency, sustained approvals, wakeups, battery life, weak-hardware
  responsiveness and memory pressure remain UNKNOWN. No performance win
  over an earlier revision is claimed and no core behavior was disabled.

### Evidence and collector checks

Raw process inventory, samples, platform and service-state evidence:
`generated-artifacts/footprint/run-1.json` and `run-2.json`, with corresponding
`.log` files. These generated files remain outside Git/deployment. Re-run
the documented commands to reproduce the observations on another machine.

`python tests/runtime/test_measure_lightweight.py` covers eight deterministic
collector contracts: empty data stays UNKNOWN, nearest-rank p95, descriptive
variance, exited-process handling, recursive root inclusion, isolated null
keyring/environment behavior, no inference in the launcher, and CPU/RSS
accounting including the first-sample exclusion and attach-readiness limit.
These are collector tests, not synthetic resource numbers substituted for
the real process measurements above.
