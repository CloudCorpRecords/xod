"""Creates a small random LoRA for sd-turbo (data/loras/xod_test_lora.safetensors) to test LoRA hot-swapping
without downloading anything. It changes images noticeably but isn't a meaningful style."""
import json, sys, torch
from pathlib import Path
from diffusers import StableDiffusionPipeline
from peft import LoraConfig, get_peft_model_state_dict
from diffusers.utils import convert_state_dict_to_diffusers

ROOT = Path(__file__).resolve().parents[2]
import sys as _sys; _sys.path.insert(0, str(ROOT)); from app.paths import DATA_DIR as _DATA  # noqa: E402  (outside the repo)
pipe = StableDiffusionPipeline.from_pretrained(str(_DATA / "models/stabilityai---sd-turbo"), torch_dtype=torch.float32,
                                               variant="fp16", local_files_only=True, safety_checker=None)
pipe.unet.add_adapter(LoraConfig(r=4, lora_alpha=4, init_lora_weights="gaussian",
                                 target_modules=["to_k", "to_q", "to_v", "to_out.0"]))
torch.manual_seed(0)
with torch.no_grad():
    for name, p in pipe.unet.named_parameters():
        if "lora_B" in name:
            p.normal_(0, 0.08)  # non-zero so the LoRA visibly changes the output
state = convert_state_dict_to_diffusers(get_peft_model_state_dict(pipe.unet))
out = _DATA / "loras"
StableDiffusionPipeline.save_lora_weights(str(out), unet_lora_layers=state, weight_name="xod_test_lora.safetensors")
(out / "xod_test_lora.json").write_text(json.dumps({"base": "stabilityai/sd-turbo",
    "description": "Random test LoRA (changes images; not a real style). Safe to delete."}))
print("saved", out / "xod_test_lora.safetensors", round((out / "xod_test_lora.safetensors").stat().st_size / 1e6, 1), "MB")
