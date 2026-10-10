"""
Runs `mlx_lm.lora` with memory caps, which its command line doesn't expose.

MLX keeps freed GPU buffers in a cache for reuse; during LoRA training that cache can grow
to several times what the model needs and starve a 16 GB Mac. Capping it trades a little
speed for a predictable footprint.

    XOD_MLX_CACHE_GB=0.5 XOD_MLX_MEMORY_GB=6 python -m app.services.mlx_train <mlx_lm.lora args>
"""
import os
import sys

import mlx.core as mx

mx.set_cache_limit(int(float(os.environ.get("XOD_MLX_CACHE_GB", "0.5")) * 1e9))
if os.environ.get("XOD_MLX_MEMORY_GB"):
    mx.set_memory_limit(int(float(os.environ["XOD_MLX_MEMORY_GB"]) * 1e9))

from mlx_lm import lora  # noqa: E402  (limits must be set first)

sys.argv = ["mlx_lm.lora"] + sys.argv[1:]
lora.main()
