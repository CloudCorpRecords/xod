"""
FLUX.1 (4-bit, MLX) that fits on a 16 GB Mac next to everything else.

Three memory tricks, all measured on an M1 Pro / 16 GB:
- Shifts: the text encoders (T5 + CLIP, ~3 GB) load, turn the prompt into embeddings, and leave
  before the image model runs. The last prompt's embeddings are cached, so re-rolling a seed
  skips them entirely.
- Streaming: the image transformer is 6.7 GB at 4-bit. When there isn't room to keep it
  resident, its 57 blocks are streamed through memory in four chunks (~1.7 GB each) for every
  step: load a chunk, run it, drop it. Same math and same image; peak goes from ~8.5 GB to ~4 GB,
  at the cost of re-reading the weights each step (from the OS file cache, usually).
- bfloat16 activations and a small MLX cache: the float32 default peaked at 10.8 GB and pushed
  the Mac into swap.

Runs inside the media worker process (app/services/media_worker.py).
"""
import gc
import sys
import time
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"
if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

import mlx.core as mx  # noqa: E402
from mlx import nn  # noqa: E402
from mlx.utils import tree_flatten, tree_unflatten  # noqa: E402

from mflux.config.config import Config  # noqa: E402
from mflux.config.model_config import ModelConfig  # noqa: E402
from mflux.config.runtime_config import RuntimeConfig  # noqa: E402
from mflux.flux.flux import Flux1  # noqa: E402
from mflux.models.text_encoder.clip_encoder.clip_encoder import CLIPEncoder  # noqa: E402
from mflux.models.text_encoder.t5_encoder.t5_encoder import T5Encoder  # noqa: E402
from mflux.models.transformer.transformer import Transformer  # noqa: E402
from mflux.models.vae.vae import VAE  # noqa: E402
from mflux.post_processing.image_util import ImageUtil  # noqa: E402
from mflux.tokenizer.clip_tokenizer import TokenizerCLIP  # noqa: E402
from mflux.tokenizer.t5_tokenizer import TokenizerT5  # noqa: E402
from mflux.tokenizer.tokenizer_handler import TokenizerHandler  # noqa: E402
from mflux.weights.weight_handler import WeightHandler  # noqa: E402

RESIDENT_GB = 6.9 + 1.7   # whole transformer + denoising working memory at 512 px
DECODE_GB = 4.0           # the VAE decode briefly needs ~3.7 GB at 512 px
MARGIN_GB = 1.5           # never plan to go below this much free memory
# Chunks of transformer blocks streamed through memory, ~1.5-1.9 GB each.
CHUNKS = [("transformer_blocks", range(0, 10)), ("transformer_blocks", range(10, 19)),
          ("single_transformer_blocks", range(0, 19)), ("single_transformer_blocks", range(19, 38))]

mx.set_cache_limit(256 * 1024 * 1024)  # freed buffers go straight back to macOS


def _available_gb() -> float:
    import psutil
    return psutil.virtual_memory().available / 1e9


def _free():
    gc.collect()
    mx.clear_cache()


def _quantize_like(module, saved_keys, bits):
    # Quantize exactly the layers the saved file has scales for. Newer mflux versions also
    # quantize embeddings, so a fixed "Linear only" rule doesn't match their 4-bit files.
    nn.quantize(module, group_size=64, bits=int(bits),
                class_predicate=lambda path, m: hasattr(m, "to_quantized") and f"{path}.scales" in saved_keys)


class FluxRunner:
    def __init__(self, model_dir: str, variant: str = "schnell"):
        self.root = Path(model_dir)
        self.cfg = ModelConfig.from_alias(variant)
        tok = TokenizerHandler(self.cfg.model_name, self.cfg.max_sequence_length, str(self.root))
        self.t5_tok = TokenizerT5(tok.t5, max_length=self.cfg.max_sequence_length)
        self.clip_tok = TokenizerCLIP(tok.clip)
        self.transformer = None
        self.streaming = False
        self.vae = self._load(VAE(), WeightHandler.load_vae)
        self._embeds = None   # (prompt, prompt_embeds, pooled_embeds)

    def _load(self, module, loader):
        weights, bits = loader(root_path=self.root)
        if bits is not None:
            _quantize_like(module, {k for k, _ in tree_flatten(weights)}, bits)
        module.update(weights)
        mx.eval(module.parameters())
        return module

    # ------------------------------------------------------------------ transformer
    def _lazy_transformer_weights(self):
        """Every transformer weight as a lazy array: nothing is read from disk until it's used."""
        flat, bits = {}, None
        for f in sorted((self.root / "transformer").glob("*.safetensors")):
            arrays, meta = mx.load(str(f), return_metadata=True)
            flat.update(arrays)
            bits = meta.get("quantization_level", bits)
        return flat, bits

    def _prepare_transformer(self):
        """Resident if there's room for all of it; otherwise only the small parts, blocks streamed."""
        want_stream = _available_gb() < RESIDENT_GB + MARGIN_GB
        if self.transformer is not None and want_stream == self.streaming:
            return
        self.drop_transformer()
        flat, bits = self._lazy_transformer_weights()
        tr = Transformer(self.cfg)
        if bits is not None:
            _quantize_like(tr, set(flat), bits)
        if want_stream:
            small = [(k, v) for k, v in flat.items() if not k.startswith(("transformer_blocks.", "single_transformer_blocks."))]
            tr.update(tree_unflatten(small))
            mx.eval([v for _, v in small])
        else:
            tr.update(tree_unflatten(list(flat.items())))
            mx.eval(tr.parameters())
        self.transformer, self.streaming = tr, want_stream

    def drop_transformer(self):
        self.transformer = None
        _free()

    def _set_chunk(self, name, idx, flat):
        blocks = getattr(self.transformer, name)
        for i in idx:
            prefix = f"{name}.{i}."
            blocks[i].update(tree_unflatten([(k[len(prefix):], v) for k, v in flat.items() if k.startswith(prefix)]))

    def _predict_streaming(self, t, prompt_embeds, pooled, hidden_states, config):
        """Transformer.predict with the blocks streamed through memory chunk by chunk."""
        tr = self.transformer
        time_step = mx.broadcast_to(config.sigmas[t] * config.num_train_steps, (1,)).astype(config.precision)
        hidden_states = tr.x_embedder(hidden_states)
        guidance = mx.broadcast_to(config.guidance * config.num_train_steps, (1,)).astype(config.precision)
        text_embeddings = tr.time_text_embed.forward(time_step, pooled, guidance)
        encoder_hidden_states = tr.context_embedder(prompt_embeds)
        ids = mx.concatenate((Transformer._prepare_text_ids(seq_len=prompt_embeds.shape[1]),
                              Transformer._prepare_latent_image_ids(config.height, config.width)), axis=1)
        rope = tr.pos_embed.forward(ids)
        text_len = encoder_hidden_states.shape[1]
        joined = False
        for name, idx in CHUNKS:
            self._set_chunk(name, idx, self._lazy_transformer_weights()[0])   # load this chunk
            blocks = getattr(tr, name)
            if name == "single_transformer_blocks" and not joined:
                hidden_states = mx.concatenate([encoder_hidden_states, hidden_states], axis=1)
                joined = True
            for i in idx:
                if name == "transformer_blocks":
                    encoder_hidden_states, hidden_states = blocks[i].forward(
                        hidden_states=hidden_states, encoder_hidden_states=encoder_hidden_states,
                        text_embeddings=text_embeddings, rotary_embeddings=rope)
                else:
                    hidden_states = blocks[i].forward(hidden_states=hidden_states, text_embeddings=text_embeddings,
                                                      rotary_embeddings=rope)
            mx.eval(hidden_states, encoder_hidden_states)
            self._set_chunk(name, idx, self._lazy_transformer_weights()[0])   # drop it (unread lazy arrays)
            _free()
        hidden_states = hidden_states[:, text_len:, ...]
        hidden_states = tr.norm_out.forward(hidden_states, text_embeddings)
        return tr.proj_out(hidden_states)

    # ------------------------------------------------------------------ text
    def _encode(self, prompt: str):
        if self._embeds and self._embeds[0] == prompt:
            return self._embeds[1], self._embeds[2]
        if self.transformer is not None and not self.streaming:
            self.drop_transformer()  # text encoders and the full transformer don't fit together
        t5 = self._load(T5Encoder(), WeightHandler.load_t5_encoder)
        clip = self._load(CLIPEncoder(), WeightHandler.load_clip_encoder)
        prompt_embeds = t5.forward(self.t5_tok.tokenize(prompt))
        pooled = clip.forward(self.clip_tok.tokenize(prompt))
        prompt_embeds, pooled = prompt_embeds.astype(mx.bfloat16), pooled.astype(mx.bfloat16)
        mx.eval(prompt_embeds, pooled)
        del t5, clip
        _free()
        self._embeds = (prompt, prompt_embeds, pooled)
        return prompt_embeds, pooled

    # ------------------------------------------------------------------ public
    def generate(self, prompt: str, seed: int, steps: int = 2, width: int = 512, height: int = 512):
        t = time.time()
        prompt_embeds, pooled = self._encode(prompt)
        self._prepare_transformer()
        config = RuntimeConfig(Config(num_inference_steps=steps, width=width, height=height), self.cfg)
        latents = mx.random.normal(shape=[1, (config.height // 16) * (config.width // 16), 64],
                                   key=mx.random.key(seed)).astype(mx.bfloat16)
        for i in range(config.num_inference_steps):
            if self.streaming:
                noise = self._predict_streaming(i, prompt_embeds, pooled, latents, config)
            else:
                noise = self.transformer.predict(t=i, prompt_embeds=prompt_embeds, pooled_prompt_embeds=pooled,
                                                 hidden_states=latents, config=config)
            latents += noise * (config.sigmas[i + 1] - config.sigmas[i])
            mx.eval(latents)
        if not self.streaming and _available_gb() < DECODE_GB * (config.width * config.height) / (512 * 512) + MARGIN_GB:
            self.drop_transformer()  # reloads in ~2 s next time; better than swapping
        decoded = self.vae.decode(Flux1._unpack_latents(latents, config.height, config.width))
        image = ImageUtil._numpy_to_pil(ImageUtil._to_numpy(ImageUtil._denormalize(decoded)))
        _free()
        return image, round(time.time() - t, 1)
