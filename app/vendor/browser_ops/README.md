# browser-ops

A modular browser operations toolkit for agents that share one Chromium
browser on a device. It gives an agent three things a raw DevTools
connection does not:

1. **A tab registry.** Every tab an agent opens or claims is registered to
   an owner (the lane or agent id) with a task description, an opened time,
   and a heartbeat. Any agent can list the whole browser and see which tab
   belongs to whom, what it is for, and whether it is still being used.
2. **A sweeper.** A small script that closes registered tabs whose owner
   marked them done or whose heartbeat went stale (default 45 minutes),
   and logs every close. It exists because agents crash, time out, and
   stand down mid-task, and their tabs otherwise pile up forever.
3. **Screen observation.** Agents can capture a PNG screenshot of one
   specific tab, or of the whole desktop, so they can SEE the state they
   are about to act on instead of guessing from a list of URLs.

The module is host-agnostic. It has no dependency on any particular agent
framework, memory system, or scheduler. An agent host (Tree today, Xod
later) ships the files to the device, points its agents at the tools in
`tools.json`, and gets JSON back for every call.

## Why it exists

On the Razer, many scheduled lanes drive one Chrome profile over CDP
(port 9222). Before this module, nothing recorded which tab belonged to
which lane. A lane that needed a tab opened a fresh one rather than risk
reusing a tab it could not identify; a lane that died left its tabs
behind; the next lane would not touch tabs it could not prove were its
own. The pile grew to around ten stale tabs burning CPU on the machine,
and one lane's end-of-session cleanup could close tabs another lane was
still using, because ownership was invisible to everyone.

The registry makes ownership explicit, the sweeper gives tabs a lifecycle,
and screenshots give agents eyes.

## Layout

```
browser-ops/
  README.md            this file
  LANES.md             rules for lanes and agents using the toolkit
  tools.json           agent tool schema (the callable surface)
  config.example.json  the one config file: device runtime + host remote
  bin/bops             reference host-side wrapper (SSH transport)
  src/browser_ops.py   device-side core and CLI (the whole engine)
  src/desktop_shot.ps1 desktop capture script for the interactive session
  screenshots/         screenshots the wrapper downloads land here
```

Deployed on the Razer at `C:\Users\cloud\browser-ops\`:

```
browser_ops.py   the engine, run with the device's python
desktop_shot.ps1 run by the desktop capture scheduled task
config.json      the device_runtime block of the config, per device
registry.json    the live tab registry (created on first write)
sweep.log        one line per sweeper close or prune
screenshots\     tab and desktop PNGs, device side
```

## How it works

### Device side

`browser_ops.py` runs on the device, next to the browser. It talks to the
browser over the local CDP HTTP surface (`/json/list`, `/json/new`,
`/json/close/<id>`) and keeps the registry in one JSON file, guarded by a
lockfile so concurrent lanes cannot corrupt it. Every subcommand prints
exactly one JSON object and exits 0 on success, 1 on error.

```
python browser_ops.py status
python browser_ops.py open      --owner <lane> --task <text> --url <url>
python browser_ops.py register  --owner <lane> --task <text> --target <id>
python browser_ops.py heartbeat --owner <lane> [--target <id>]
python browser_ops.py done      --owner <lane> [--target <id>]
python browser_ops.py list
python browser_ops.py close-mine --owner <lane>
python browser_ops.py sweep
python browser_ops.py shot-tab --target <id>
python browser_ops.py shot-desktop
```

`list` merges the registry with live browser state: registered tabs get
their live URL and title read from the browser at call time, and
unregistered page tabs are listed too, so an agent always sees the whole
browser, not just its own corner of it.

### Host side

`bin/bops` is the reference wrapper for a host that is not on the device.
It forwards each subcommand to the device over SSH (connection settings
live in the `remote` block of the config), prints the device's JSON, and
for screenshot commands also downloads the PNG and adds a `local_path`
field so the calling agent can open the image.

```
bops list
bops open --owner x-reaction-replies --task "X replies" --url https://x.com/home
bops shot-tab --target <id>     # result includes local_path to the PNG
bops shot-desktop
bops sweep
```

A host with its own transport (an Xod node agent, an MCP bridge, anything)
can skip the wrapper entirely. The contract is the device-side CLI and its
JSON in and out; `tools.json` describes that same surface as agent tools.

### Tab screenshots

`shot-tab` captures a tab by target id without focusing or disturbing it.
Implementation note, learned the hard way on Chrome 156: calling
`Page.captureScreenshot` on a background tab's own DevTools socket hangs.
The working route is the browser-level socket: `Target.attachToTarget`
with `flatten: true`, then `Page.captureScreenshot` inside that session.
That is what the engine does.

### Desktop screenshots

A remote shell on Windows lands in session 0, where screen capture fails
("The handle is invalid" from CopyFromScreen). So `shot-desktop` does not
capture anything itself: it triggers a scheduled task,
`BrowserOpsDesktopShot`, whose action runs `desktop_shot.ps1` in the
interactive desktop session (LogonType Interactive), then waits for the
fresh PNG the script writes and reports its path and size. Install the
task once per device:

```powershell
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\Users\cloud\browser-ops\desktop_shot.ps1'
$p = New-ScheduledTaskPrincipal -UserId '<device user>' -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'BrowserOpsDesktopShot' -Action $a -Principal $p -Force
```

The capture covers the full virtual screen (all monitors).

## Safety rules (enforced by the engine, not by convention)

- The sweeper and `close-mine` only ever close **registered** tabs, and
  `close-mine` only the caller's own.
- The sweeper never closes a tab whose owner heartbeat is fresh.
- Nothing ever leaves the browser with zero page tabs: if a close would
  empty the browser, a blank tab is opened first.
- Tabs that vanish on their own are pruned from the registry and logged,
  never treated as an error.
- Every sweeper close and prune is written to `sweep.log` with the time,
  tab id, owner, and reason (`done` or `stale`).

## Running the sweeper on a schedule

The engine ships the sweeper as an on-demand command; scheduling belongs
to the host. Recommended: run it every 15 minutes.

```
~/workspace/browser-ops/bin/bops sweep
```

With a 45 minute stale window, a 15 minute cadence closes dead tabs
promptly without racing lanes that are merely between heartbeats. Lanes
working longer than a few minutes should heartbeat at least every
15 minutes (see LANES.md).

## Adding this to Xod later

The plan: when Xod runs an agent on a device that has no native
observation or tab-ownership facilities of its own, Xod should hand the
agent these tools automatically. The module is built for exactly that:

1. **Provisioning is file copy plus one task.** Ship `browser_ops.py`,
   `desktop_shot.ps1`, and a per-device `config.json` (the
   `device_runtime` block: CDP port, registry and screenshot paths, stale
   window, capture task name) to the device, then register the
   `BrowserOpsDesktopShot` scheduled task (Windows) so desktop capture
   works. No installer, no service, no host-specific state.
2. **The tool surface is data, not code.** `tools.json` lists the tools
   (tabs_register, tabs_heartbeat, tabs_mark_done, tabs_list,
   tabs_close_mine, tabs_sweep_now, screen_capture_tab,
   screen_capture_desktop) with input schemas. Xod can register them
   verbatim in its tool registry and route calls to the device CLI, the
   same way the reference wrapper does over SSH, or over whatever
   transport the node already has.
3. **Agents need no host knowledge.** An agent given these tools can share
   a browser safely on any device: register what it opens, heartbeat while
   it works, screenshot before it acts, and let the sweeper clean up after
   agents that never come back.
4. **Per-device differences stay in config.** A different browser port, a
   different profile, a Linux device with a different capture route: all
   of it is config or a small device-side variant of the capture step. The
   registry, sweeper, and tool contract do not change.

## Proof (2026-11-04, Razer, Chrome 156, port 9222)

Exercised live while an X engagement lane was working in the same
browser; the module touched only its own tabs:

- Two test tabs (example.com, example.org) opened and registered to a
  proof owner; `list` showed both with live titles, alongside the lane's
  unregistered tabs.
- `shot-tab` on the example.com tab returned a 180,456 byte PNG showing
  the actual Example Domain page, verified by viewing the image.
- The example.org tab was marked done; one `sweep` closed exactly that
  tab (reason `done`, written to `sweep.log`) and kept everything else.
  All six pre-existing unregistered tabs were verified still open after
  the sweep.
- `shot-desktop` returned a real 877,033 byte capture of both monitors,
  showing the Chrome window with the lane mid-session and the Xod app on
  the second screen.
- Cleanup removed the proof registry entries; the registry was left
  empty. During the proof window the X lane ended its session and closed
  its own working tabs (its pre-existing habit, and the very problem this
  module solves once lanes register their tabs); browser-ops itself
  closed exactly one tab all morning, the done test tab.
- Not live-proven: the last-tab guard (open a blank tab before closing
  the final tab). It is implemented in `close_with_guard`; the proof
  never reached a state where it could fire without endangering the live
  lane's last tab, so it was left untested rather than forced.
