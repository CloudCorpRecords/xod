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
