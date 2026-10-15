"""
Media Model Manager Service
Provides on-device management, catalog query, discovery, 1-click download,
and active model selection for local Image and Video generation models.
"""

import os
import json
import time
import shutil
import asyncio
import logging
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional
from app.paths import DATA_DIR

try:
    from huggingface_hub import HfApi, snapshot_download
except ImportError:
    HfApi = None
    snapshot_download = None

logger = logging.getLogger("media_model_manager")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = DATA_DIR / "models"
CONFIG_FILE = DATA_DIR / "media_models_config.json"

# Ensure models dir exists
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Curated High-Efficiency Models for Apple Silicon & Local Computers
# Models Xod's media engine can actually run on a 16 GB Apple silicon Mac. Each one has an
# install recipe: which files to fetch (fp16 only, no duplicate formats) and, for models that
# need it, a xod_model.json marker describing how to run them (see media_runtime.list_models).
_FP16_FILES = ["model_index.json", "*/*.json", "*/*.txt", "*/*.model", "*/*.fp16.safetensors"]
_EPIC_FILES = ["model_index.json", "*/*.json", "*/*.txt", "text_encoder/model.safetensors", "unet/diffusion_pytorch_model.safetensors", "vae/diffusion_pytorch_model.safetensors"]

CURATED_MEDIA_MODELS: List[Dict[str, Any]] = [
    # --- TEXT-TO-IMAGE ---
    {
        "id": "flux-schnell-4bit",
        "name": "FLUX.1 schnell, 4-bit (Apple silicon)",
        "author": "Black Forest Labs (4-bit MLX conversion)",
        "type": "text-to-image",
        "format": "MLX 4-bit (mflux)",
        "size_est": "~9.6 GB download · ~5 GB memory",
        "resolution": "512 px (up to 768)",
        "speed": "2 steps, ~25 s at 512 px",
        "description": "The best image quality Xod can run here. Streams through memory in chunks, so it fits next to everything else on 16 GB. No LoRAs yet.",
        "recommended": True,
        "is_gated": False,
        "tags": ["text-to-image", "flux", "mlx", "4-bit"],
        "install": {"folder": "flux-schnell-4bit-mflux",
                    "downloads": [{"repo": "dhairyashil/FLUX.1-schnell-mflux-4bit"}],
                    "marker": {"id": "flux-schnell-4bit", "kind": "image", "engine": "mflux", "variant": "schnell",
                               "label": "FLUX.1 schnell (4-bit)", "need_gb": 5.0, "default_steps": 2, "max_side": 768,
                               "source": "dhairyashil/FLUX.1-schnell-mflux-4bit"}},
    },
    {
        "id": "stabilityai/sd-turbo",
        "name": "SD-Turbo (1 step)",
        "author": "Stability AI",
        "type": "text-to-image",
        "format": "Diffusers fp16",
        "size_est": "~2.4 GB download · ~5.7 GB memory",
        "resolution": "512 x 512",
        "speed": "1 step, ~2 s",
        "description": "Fastest. Good for drafts and quick ideas. Works with SD 2.1 LoRAs.",
        "recommended": True,
        "is_gated": False,
        "tags": ["text-to-image", "1-step", "turbo", "fast"],
        "install": {"folder": "stabilityai---sd-turbo",
                    "downloads": [{"repo": "stabilityai/sd-turbo", "allow": _FP16_FILES}]},
    },
    {
        "id": "emilianJR/epiCRealism",
        "name": "epiCRealism (SD 1.5)",
        "author": "emilianJR",
        "type": "text-to-image",
        "format": "Diffusers",
        "size_est": "~4.3 GB download · ~4.9 GB memory",
        "resolution": "512 x 512",
        "speed": "20-25 steps",
        "description": "Photorealistic SD 1.5 model; works with the huge library of SD 1.5 LoRAs. Also the base for AnimateDiff video.",
        "recommended": False,
        "is_gated": False,
        "tags": ["text-to-image", "sd15", "photoreal", "lora"],
        "install": {"folder": "emilianJR---epiCRealism",
                    "downloads": [{"repo": "emilianJR/epiCRealism", "allow": _EPIC_FILES}]},
    },
    {
        "id": "stabilityai/sdxl-turbo",
        "name": "SDXL-Turbo (1 step, larger)",
        "author": "Stability AI",
        "type": "text-to-image",
        "format": "Diffusers fp16",
        "size_est": "~7 GB download · ~9 GB memory",
        "resolution": "512 x 512",
        "speed": "1 step",
        "description": "Sharper than SD-Turbo, still one step. Works with SDXL LoRAs.",
        "recommended": False,
        "is_gated": False,
        "tags": ["text-to-image", "sdxl", "turbo"],
        "install": {"folder": "stabilityai---sdxl-turbo",
                    "downloads": [{"repo": "stabilityai/sdxl-turbo", "allow": _FP16_FILES}]},
    },

    # --- TEXT-TO-VIDEO ---
    {
        "id": "animatediff-lightning",
        "name": "AnimateDiff-Lightning (text to video)",
        "author": "ByteDance (+ epiCRealism base)",
        "type": "text-to-video",
        "format": "Diffusers motion adapter + SD 1.5",
        "size_est": "~4.9 GB download (incl. epiCRealism) · ~7.5 GB memory",
        "resolution": "512 x 512, 1-4 s, smoothed to 24 fps",
        "speed": "4 steps",
        "description": "Real generated motion from a text prompt. Works with SD 1.5 LoRAs.",
        "recommended": True,
        "is_gated": False,
        "tags": ["text-to-video", "animatediff", "lightning"],
        "install": {"folder": "animatediff-lightning",
                    "downloads": [{"repo": "ByteDance/AnimateDiff-Lightning", "allow": ["animatediff_lightning_4step_diffusers.safetensors"]},
                                  {"repo": "emilianJR/epiCRealism", "allow": _EPIC_FILES, "folder": "emilianJR---epiCRealism"}],
                    "marker": {"id": "animatediff-lightning", "kind": "video", "engine": "animatediff",
                               "label": "AnimateDiff-Lightning (4-step)", "base": "emilianJR---epiCRealism",
                               "adapter": "animatediff_lightning_4step_diffusers.safetensors", "need_gb": 7.5,
                               "default_steps": 4, "source": "ByteDance/AnimateDiff-Lightning + emilianJR/epiCRealism"}},
    },
]


class MediaModelManager:
    """
    Manages local image & video model storage, discovery from Hugging Face,
    download background tasks, active model assignment, and Finder integration.
    """

    def __init__(self):
        self.config = self._load_config()
        self._download_jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _load_config(self) -> Dict[str, Any]:
        """Loads configuration or creates default."""
        default_config = {
            "active_image_model": "",   # empty = the media engine uses the first usable model
            "active_video_model": "",
            "device_accelerator": "mps",
            "downloaded_models": []
        }
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    default_config.update(data)
            except Exception as e:
                logger.warning(f"Error loading media_models_config.json: {e}")
        return default_config

    def _save_config(self):
        """Persists configuration to disk."""
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to write media_models_config.json: {e}")

    def get_hf_token(self) -> Optional[str]:
        """The Hugging Face token, from the macOS Keychain (see secret_store.py)."""
        try:
            from app.config import settings
            tok = getattr(settings, "huggingface_token", "")
            return tok.strip() if (tok and str(tok).strip()) else None
        except Exception:
            return None

    def set_hf_token(self, token: str) -> Dict[str, Any]:
        """Saves the Hugging Face token to the Keychain and checks it."""
        clean_tok = token.strip()
        from app.config import update_env_variable
        update_env_variable("huggingface_token", clean_tok)
        return self.test_hf_token(clean_tok)

    def test_hf_token(self, token: Optional[str] = None) -> Dict[str, Any]:
        """Validates the token against the Hugging Face API."""
        tok = token or self.get_hf_token()
        if not tok:
            return {"valid": False, "message": "No token entered."}
        if not HfApi:
            return {"valid": True, "message": "Token saved."}
        try:
            api = HfApi(token=tok)
            user_info = api.whoami()
            name = user_info.get("name") or user_info.get("fullname") or "User"
            return {"valid": True, "username": name, "message": f"Authenticated with Hugging Face as @{name}"}
        except Exception as e:
            return {"valid": False, "message": f"Token verification failed: {str(e)}"}

    def get_status(self) -> Dict[str, Any]:
        """Returns overall media models status, accelerator info, and active selections."""
        downloaded = self.list_downloaded_models()
        active_img = self.config.get("active_image_model", "")
        active_vid = self.config.get("active_video_model", "")
        hf_tok = self.get_hf_token()
        hf_masked = f"{hf_tok[:4]}...{hf_tok[-4:]}" if (hf_tok and len(hf_tok) > 8) else ""

        return {
            "active_image_model": active_img,
            "active_video_model": active_vid,
            "device_accelerator": "Apple Silicon MPS (Metal Performance Shaders)",
            "models_dir": str(MODELS_DIR),
            "total_downloaded": len(downloaded),
            "downloaded_models": downloaded,
            "has_hf_token": bool(hf_tok),
            "hf_token_masked": hf_masked
        }

    def list_curated_models(self) -> List[Dict[str, Any]]:
        """Returns the curated catalog with downloaded flags."""
        downloaded_ids = {m["id"] for m in self.list_downloaded_models()}
        active_img = self.config.get("active_image_model")
        active_vid = self.config.get("active_video_model")

        curated = []
        for m in CURATED_MEDIA_MODELS:
            item = dict(m)
            item["is_downloaded"] = item["id"] in downloaded_ids
            item["is_active"] = (item["id"] == active_img) or (item["id"] == active_vid)
            curated.append(item)
        return curated

    def list_downloaded_models(self) -> List[Dict[str, Any]]:
        """Installed models, straight from the media engine's own check: whether each can run, and why not."""
        from app.services.media_runtime import list_models
        curated = {c["id"]: c for c in CURATED_MEDIA_MODELS}
        results: List[Dict[str, Any]] = []
        for m in list_models():
            c = curated.get(m["id"], {})
            kind = m.get("kind")
            results.append({
                "id": m["id"],
                "name": c.get("name") or m.get("label") or m["id"],
                "author": c.get("author") or (m["id"].split("/")[0] if "/" in m["id"] else "Local"),
                "type": c.get("type") or {"image": "text-to-image", "video": "text-to-video"}.get(kind, "unknown"),
                "format": c.get("format") or m.get("pipeline") or "unknown",
                "size_formatted": f"{m['size_gb']} GB",
                "size_bytes": int(m["size_gb"] * 1e9),
                "path": m["dir"],
                "is_builtin": False,
                "usable": m["usable"],
                "need_gb": m.get("need_gb"),
                "is_active": m["id"] in (self.config.get("active_image_model"), self.config.get("active_video_model")),
                "description": c.get("description") if m["usable"] else f"Can't run here: {m.get('reason')}",
            })
        return results

    def search_huggingface_models(
        self,
        query: str = "",
        model_type: Optional[str] = None,
        limit: int = 15
    ) -> List[Dict[str, Any]]:
        """
        Queries Hugging Face Hub live for text-to-image or text-to-video models.
        """
        if not HfApi:
            return self.list_curated_models()

        api = HfApi()
        filter_tag = "text-to-video" if model_type == "video" else ("text-to-image" if model_type == "image" else None)
        
        try:
            raw_models = list(api.list_models(
                search=query if query else None,
                filter=filter_tag,
                sort="downloads",
                direction=-1,
                limit=limit
            ))
        except Exception as e:
            logger.warning(f"Failed to query Hugging Face Hub: {e}")
            # Fallback to curated
            return [m for m in self.list_curated_models() if not query or query.lower() in m["name"].lower() or query.lower() in m["id"].lower()]

        downloaded_ids = {m["id"] for m in self.list_downloaded_models()}
        active_img = self.config.get("active_image_model")
        active_vid = self.config.get("active_video_model")

        items = []
        for rm in raw_models:
            p_tag = getattr(rm, "pipeline_tag", None) or filter_tag or "text-to-image"
            items.append({
                "id": rm.id,
                "name": rm.id.split("/")[-1] if "/" in rm.id else rm.id,
                "author": rm.id.split("/")[0] if "/" in rm.id else "Hugging Face",
                "type": p_tag,
                "downloads": getattr(rm, "downloads", 0) or 0,
                "likes": getattr(rm, "likes", 0) or 0,
                "is_downloaded": rm.id in downloaded_ids,
                "is_active": (rm.id == active_img) or (rm.id == active_vid),
                "tags": getattr(rm, "tags", [])[:5]
            })

        return items

    def set_active_model(self, model_id: str, model_type: str) -> Dict[str, Any]:
        """Sets the active model for image or video generation."""
        if model_type in ("image", "text-to-image"):
            self.config["active_image_model"] = model_id
        elif model_type in ("video", "text-to-video", "image-to-video"):
            self.config["active_video_model"] = model_id
        else:
            # Auto-detect from ID
            if "video" in model_id.lower() or "animate" in model_id.lower() or "wan" in model_id.lower():
                self.config["active_video_model"] = model_id
            else:
                self.config["active_image_model"] = model_id

        self._save_config()
        return {
            "success": True,
            "active_image_model": self.config["active_image_model"],
            "active_video_model": self.config["active_video_model"],
            "message": f"Successfully activated {model_id} for {model_type}."
        }

    def start_download_job(self, model_id: str, model_type: str = "image") -> Dict[str, Any]:
        """Downloads a model in the background, following its install recipe when it has one."""
        if not snapshot_download:
            return {"success": False, "error": "huggingface_hub package is not installed."}

        from app.services import media_packages
        media_packages.start_install()   # the Mac app's first media model also needs the libraries
        sanitized_name = model_id.replace("/", "---")
        recipe = next((c.get("install") for c in CURATED_MEDIA_MODELS if c["id"] == model_id), None)
        if recipe is None:
            recipe = {"folder": sanitized_name, "downloads": [{"repo": model_id}]}
        target_dir = MODELS_DIR / recipe["folder"]

        token = self.get_hf_token()
        job_id = f"dl_{int(time.time())}_{sanitized_name[:16]}"
        with self._lock:
            self._download_jobs[job_id] = {
                "job_id": job_id,
                "model_id": model_id,
                "model_type": model_type,
                "status": "downloading",
                "progress_pct": 0,
                "start_time": time.time(),
                "error": None
            }

        def _worker():
            try:
                logger.info(f"Starting download of {model_id} to {target_dir}...")
                for dl in recipe["downloads"]:
                    snapshot_download(
                        repo_id=dl["repo"],
                        local_dir=str(MODELS_DIR / dl.get("folder", recipe["folder"])),
                        token=token or None,
                        allow_patterns=dl.get("allow"),
                        ignore_patterns=["*.msgpack", "*.h5", "*.ot", "*.bin", "*.ckpt", "*.onnx*", "safety_checker/*"],
                        max_workers=4
                    )
                if recipe.get("marker"):
                    (target_dir / "xod_model.json").write_text(json.dumps(recipe["marker"], indent=1))
                with self._lock:
                    self._download_jobs[job_id]["status"] = "completed"
                    self._download_jobs[job_id]["progress_pct"] = 100
                logger.info(f"Download of {model_id} completed successfully!")
            except Exception as e:
                logger.error(f"Download of {model_id} failed: {e}")
                err_str = str(e)
                is_gated = ("401" in err_str) or ("gated" in err_str.lower()) or ("restricted" in err_str.lower()) or ("unauthorized" in err_str.lower())

                if is_gated:
                    clean_msg = (
                        f"🔒 Access to '{model_id}' is restricted on Hugging Face.\n\n"
                        f"1. Please open https://huggingface.co/{model_id} in your browser and click 'Agree and access repository'.\n"
                        f"2. Add your free Hugging Face User Access Token (hf_...) in Settings.\n\n"
                        f"Tip: every model in Xod's list is open and needs no token."
                    )
                else:
                    clean_msg = err_str

                # Clean up incomplete files so we don't leave broken folders
                try:
                    if target_dir.exists():
                        has_large_weights = any(f.stat().st_size > 50_000_000 for f in target_dir.rglob("*") if f.is_file())
                        if not has_large_weights:
                            shutil.rmtree(target_dir, ignore_errors=True)
                except Exception:
                    pass

                with self._lock:
                    self._download_jobs[job_id]["status"] = "failed"
                    self._download_jobs[job_id]["error"] = clean_msg
                    self._download_jobs[job_id]["is_gated"] = is_gated

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        return {
            "success": True,
            "job_id": job_id,
            "model_id": model_id,
            "status": "downloading",
            "message": f"Started background download for {model_id}."
        }

    def get_download_status(self, job_id: str) -> Dict[str, Any]:
        """Returns the status and progress of an active or completed download job."""
        with self._lock:
            job = self._download_jobs.get(job_id)
        if not job:
            return {"success": False, "error": "Job not found"}
        return {"success": True, **job}

    @staticmethod
    def _model_dir(model_id: str) -> Optional[Path]:
        from app.services.media_runtime import list_models
        m = next((m for m in list_models() if m["id"] == model_id), None)
        if m:
            return Path(m["dir"])
        d = MODELS_DIR / model_id.replace("/", "---")
        return d if d.exists() else None

    def delete_model(self, model_id: str) -> Dict[str, Any]:
        """Moves a model folder to the Trash (recoverable until the Trash is emptied)."""
        target_dir = self._model_dir(model_id)
        if not target_dir:
            return {"success": False, "error": f"Model {model_id} isn't on disk."}
        try:
            import sys
            if sys.platform == "win32":
                from app.services.system_control_win import recycle
                recycle(str(target_dir))
            else:
                trash = Path.home() / ".Trash"
                dest = trash / target_dir.name
                if dest.exists():
                    dest = trash / f"{target_dir.name} {time.strftime('%H.%M.%S')}"
                shutil.move(str(target_dir), str(dest))
            for kind in ("image", "video"):
                if self.config.get(f"active_{kind}_model") == model_id:
                    self.config[f"active_{kind}_model"] = ""  # the engine falls back to the first usable model
            self._save_config()
            bin_name = "Recycle Bin" if sys.platform == "win32" else "Trash"
            return {"success": True, "message": f"Moved {model_id} to the {bin_name}. Empty the {bin_name} to free the disk space."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def reveal_in_finder(self, model_id: str) -> Dict[str, Any]:
        """Opens the model directory in Finder (File Explorer on Windows)."""
        target_dir = self._model_dir(model_id) or MODELS_DIR
        try:
            import subprocess
            import sys
            if sys.platform == "win32":
                os.startfile(str(target_dir.resolve()))
            else:
                subprocess.run(["open", str(target_dir.resolve())], check=False)
            return {"success": True, "target": str(target_dir.resolve())}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def _format_bytes(size: int) -> str:
        """Converts raw byte count to readable string."""
        if size == 0:
            return "0 B"
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"


# Singleton instance
media_model_manager = MediaModelManager()
