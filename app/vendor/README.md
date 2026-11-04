# Vendored code

## mflux 0.2.1 (MIT License, https://github.com/filipstrand/mflux)
Runs FLUX.1 image models natively on Apple silicon with MLX, including 4-bit quantized weights.
Used by the media worker for `data/models/flux-schnell-4bit-mflux`.

Vendored instead of pip-installed because current mflux releases need Python 3.10+, and Xod's
virtualenv is Python 3.9. The only change from upstream: `from __future__ import annotations`
added at the top of each module (so `str | None` annotations work on 3.9), and the
`save.py` CLI removed.

Second change: `attention()` in `models/transformer/joint_attention.py` and
`single_block_attention.py` uses `mx.fast.scaled_dot_product_attention` (fused) instead of
building the full attention-score matrix in float32. Same math; the unfused version needed
~3.7 GB extra at 512 px, which pushed a 16 GB Mac into swap.

## browser-ops 1.0.0 (CloudCorpRecords/browser-ops, PR #1, commit 6d6cadf)
A CDP tab registry with ownership and heartbeats, a sweeper, per-tab and desktop screen capture, its CLI
(`src/browser_ops.py`), the reference SSH wrapper (`bin/bops`), and `tools.json`, the agent tool surface.
Xod hands these tools to its agent when this device has no screen tools of its own, and for a device it
reaches over SSH (services/browser_ops.py, the adapter).

Vendored from the PR head (6d6cadffc1e6c3383cf65097627d608dcc6f2e82) of `feat/browser-ops-module`. The code
(`src/browser_ops.py`, `src/desktop_shot.ps1`, `bin/bops`), `tools.json`, `LANES.md` and `.gitignore` are
byte-identical. Two documentation-only edits keep personal details out of this public repo: README.md says
"The plan:" where it named a person, and config.example.json uses `<device-user>` and `<device-tailnet-ip>`
placeholders for a real username and tailnet address. Xod never reads config.example.json, so behavior is
unchanged. Xod never imports the engine. It runs the module's own CLI and wrapper as
programs, and puts everything Xod-specific in the adapter. Its one config file is
`<Xod data folder>/browser-ops/config.json`, in the module's own format (`config.example.json`), kept
apart from Xod's settings. To update, copy the new files over these unchanged and re-run
`tests/smoke/run_browser_ops_test.py`.
