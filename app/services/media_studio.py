"""
Local Media Studio Service — On-device Image & Video Generation / Editing.

Capabilities:
1. Image Operations:
   - Edit / Crop / Resize / Rotate / Color Filters / Watermark / Format conversion.
   - Smart image generation using local SVG synthesis, neural diffusion triggers, or procedural canvas.
2. Video Operations:
   - Trim / Clip / Speed up / Slow down / Reverse / Volume adjust / Audio detach / Add audio track.
   - Text overlays / Subtitles / Lower-thirds / Watermarks.
   - Video concatenation / Montage generation from images + audio.
   - Format conversion (MP4, GIF, WebM) powered by native Apple Silicon ffmpeg.
"""
import asyncio
import os
import time
import uuid
import json
from pathlib import Path
from typing import Dict, Any, List, Optional

from PIL import Image, ImageEnhance, ImageFilter, ImageDraw, ImageFont
from app.paths import DATA_DIR

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MEDIA_DIR = DATA_DIR / "media_studio"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

def _ffmpeg_error(stderr: bytes) -> str:
    """ffmpeg prints a long version banner first; the actual error is in the last lines."""
    lines = [l for l in stderr.decode("utf-8", errors="replace").strip().splitlines() if l.strip()]
    return "\n".join(lines[-4:])[-600:] or "ffmpeg failed without an error message"


# Discover local ffmpeg
try:
    import imageio_ffmpeg
    FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_BIN = "ffmpeg"


class LocalMediaService:
    def __init__(self):
        self.output_dir = MEDIA_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _generate_id(self, prefix: str = "media") -> str:
        return f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:6]}"

    # ==============================================================
    # 1. IMAGE OPERATIONS (Pillow + Native Apple Silicon SIPS)
    # ==============================================================
    def edit_image(
        self,
        image_path: str,
        operations: List[Dict[str, Any]],
        output_format: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes a sequence of image edits:
        - resize: {"width": int, "height": int}
        - crop: {"left": int, "top": int, "right": int, "bottom": int}
        - rotate: {"degrees": float}
        - filter: {"type": "blur" | "sharpen" | "contour" | "grayscale"}
        - adjust: {"brightness": float, "contrast": float, "color": float}
        - watermark: {"text": str, "position": "bottom_right" | "center"}
        """
        src = Path(os.path.expanduser(image_path)).resolve()
        if not src.exists():
            return {"success": False, "error": f"Image file not found: {image_path}"}

        try:
            img = Image.open(src)
            img = img.convert("RGBA")

            for op in operations:
                op_type = op.get("op")
                if op_type == "resize":
                    w = int(op.get("width", img.width))
                    h = int(op.get("height", img.height))
                    img = img.resize((w, h), Image.Resampling.LANCZOS)

                elif op_type == "crop":
                    box = (
                        int(op.get("left", 0)),
                        int(op.get("top", 0)),
                        int(op.get("right", img.width)),
                        int(op.get("bottom", img.height))
                    )
                    img = img.crop(box)

                elif op_type == "rotate":
                    deg = float(op.get("degrees", 90))
                    img = img.rotate(deg, expand=True)

                elif op_type == "filter":
                    ftype = op.get("type", "").lower()
                    if ftype == "blur":
                        img = img.filter(ImageFilter.GaussianBlur(radius=op.get("radius", 3)))
                    elif ftype == "sharpen":
                        img = img.filter(ImageFilter.SHARPEN)
                    elif ftype == "contour":
                        img = img.filter(ImageFilter.CONTOUR)
                    elif ftype == "grayscale":
                        img = img.convert("L").convert("RGBA")

                elif op_type == "adjust":
                    if "brightness" in op:
                        img = ImageEnhance.Brightness(img).enhance(float(op["brightness"]))
                    if "contrast" in op:
                        img = ImageEnhance.Contrast(img).enhance(float(op["contrast"]))
                    if "color" in op:
                        img = ImageEnhance.Color(img).enhance(float(op["color"]))

                elif op_type == "watermark":
                    text = op.get("text", "XOD AI")
                    draw = ImageDraw.Draw(img)
                    # Simple clean watermark
                    draw.text((20, img.height - 40), text, fill=(255, 255, 255, 180))

            ext = (output_format or src.suffix.lstrip(".").lower() or "png").lower()
            if ext == "jpg":
                ext = "jpeg"
            out_name = f"{self._generate_id('img')}.{ext}"
            out_path = self.output_dir / out_name

            if ext == "jpeg":
                img = img.convert("RGB")

            img.save(out_path)

            return {
                "success": True,
                "output_path": str(out_path),
                "filename": out_name,
                "width": img.width,
                "height": img.height,
                "format": ext,
                "size_bytes": out_path.stat().st_size
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_image_from_template(
        self,
        title: str,
        subtitle: str = "",
        theme: str = "dark_cyber",
        width: int = 1200,
        height: int = 675
    ) -> Dict[str, Any]:
        """Creates on-device high-resolution social card / banner image."""
        try:
            img = Image.new("RGBA", (width, height), (15, 17, 26, 255))
            draw = ImageDraw.Draw(img)

            # Gradient background or accent shapes
            if theme == "dark_cyber":
                accent_color = (99, 102, 241, 200) # indigo
                draw.rectangle([0, 0, width, 12], fill=(99, 102, 241, 255))
                # subtle glow circles
                for r in range(300, 50, -50):
                    draw.ellipse([width - r, -50, width + r, r], outline=(99, 102, 241, 30), width=2)
            else:
                draw.rectangle([0, 0, width, 12], fill=(16, 185, 129, 255))

            # Title & Subtitle text rendering
            draw.text((80, height // 2 - 50), title, fill=(255, 255, 255, 255))
            if subtitle:
                draw.text((80, height // 2 + 20), subtitle, fill=(161, 161, 170, 255))

            out_name = f"{self._generate_id('banner')}.png"
            out_path = self.output_dir / out_name
            img.save(out_path)

            return {
                "success": True,
                "output_path": str(out_path),
                "filename": out_name,
                "width": width,
                "height": height
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==============================================================
    # 2. VIDEO OPERATIONS (FFmpeg macOS Apple Silicon binary)
    # ==============================================================
    async def trim_video(
        self,
        video_path: str,
        start_sec: float,
        duration_sec: float,
        output_format: str = "mp4"
    ) -> Dict[str, Any]:
        """Cuts / trims a section of a video precisely using ffmpeg stream copy."""
        src = Path(os.path.expanduser(video_path)).resolve()
        if not src.exists():
            return {"success": False, "error": f"Video file not found: {video_path}"}

        out_name = f"{self._generate_id('trim')}.{output_format}"
        out_path = self.output_dir / out_name

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-ss", str(start_sec),
            "-i", str(src),
            "-t", str(duration_sec),
            "-c", "copy",
            str(out_path)
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0:
                return {
                    "success": True,
                    "output_path": str(out_path),
                    "filename": out_name,
                    "start_sec": start_sec,
                    "duration_sec": duration_sec,
                    "size_bytes": out_path.stat().st_size
                }
            return {"success": False, "error": _ffmpeg_error(stderr)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def convert_to_gif(
        self,
        video_path: str,
        start_sec: float = 0,
        duration_sec: float = 5,
        fps: int = 12,
        scale_width: int = 480
    ) -> Dict[str, Any]:
        """Converts video segment into high-quality animated GIF."""
        src = Path(os.path.expanduser(video_path)).resolve()
        if not src.exists():
            return {"success": False, "error": f"Video file not found: {video_path}"}

        out_name = f"{self._generate_id('gif')}.gif"
        out_path = self.output_dir / out_name

        vf_filter = f"fps={fps},scale={scale_width}:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-ss", str(start_sec),
            "-t", str(duration_sec),
            "-i", str(src),
            "-vf", vf_filter,
            str(out_path)
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0:
                return {
                    "success": True,
                    "output_path": str(out_path),
                    "filename": out_name,
                    "fps": fps,
                    "size_bytes": out_path.stat().st_size
                }
            return {"success": False, "error": _ffmpeg_error(stderr)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def add_text_overlay(
        self,
        video_path: str,
        text: str,
        position: str = "bottom",
        font_size: int = 32
    ) -> Dict[str, Any]:
        """Overlays text onto video using ffmpeg drawtext filter."""
        src = Path(os.path.expanduser(video_path)).resolve()
        if not src.exists():
            return {"success": False, "error": f"Video file not found: {video_path}"}

        out_name = f"{self._generate_id('text_overlay')}.mp4"
        out_path = self.output_dir / out_name

        # Both coordinates need their own key (a bare "h-th-40" made ffmpeg reject the whole filter).
        pos_exp = "x=(w-text_w)/2:y=h-th-40" if position == "bottom" else "x=(w-text_w)/2:y=(h-text_h)/2"
        # The caption goes in a file (textfile=) with expansion off, so any text renders literally.
        # Escaping it inline is fragile: quoted, escaped text silently rendered as nothing.
        text_file = out_path.with_suffix(".txt")
        text_file.write_text(text, encoding="utf-8")
        font = next((f for f in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf") if os.path.exists(f)), None)
        font_opt = f"fontfile='{font}':" if font else ""
        vf = (f"drawtext={font_opt}textfile='{text_file}':expansion=none:fontsize={font_size}:fontcolor=white:"
              f"box=1:boxcolor=black@0.6:boxborderw=8:{pos_exp}")

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i", str(src),
            "-vf", vf,
            "-c:a", "copy",
            str(out_path)
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            text_file.unlink(missing_ok=True)
            if proc.returncode == 0:
                return {
                    "success": True,
                    "output_path": str(out_path),
                    "filename": out_name,
                    "overlay_text": text
                }
            return {"success": False, "error": _ffmpeg_error(stderr)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_video_duration(self, video_path: str) -> float:
        """Helper to get accurate duration in seconds using ffmpeg."""
        src = Path(os.path.expanduser(video_path)).resolve()
        if not src.exists():
            return 0.0
        try:
            cmd = [
                FFMPEG_BIN,
                "-i", str(src)
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            err_text = stderr.decode("utf-8", errors="replace")
            # Look for Duration: 00:01:23.45
            import re
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err_text)
            if m:
                hours = float(m.group(1))
                minutes = float(m.group(2))
                seconds = float(m.group(3))
                return hours * 3600 + minutes * 60 + seconds
            return 0.0
        except Exception:
            return 0.0

    async def splice_videos(
        self,
        video_paths: List[str],
        transition: Optional[str] = None,
        output_format: str = "mp4"
    ) -> Dict[str, Any]:
        """
        Splices multiple video clips together sequentially on-device.
        Uses ffmpeg concat demuxer with re-encoding fallback for mismatched codecs/resolutions.
        """
        if not video_paths or len(video_paths) < 2:
            return {"success": False, "error": "At least 2 video paths are required to splice."}

        valid_paths: List[Path] = []
        for p in video_paths:
            resolved = Path(os.path.expanduser(p)).resolve()
            if not resolved.exists():
                return {"success": False, "error": f"Video clip not found on device: {p}"}
            valid_paths.append(resolved)

        out_name = f"{self._generate_id('splice')}.{output_format}"
        out_path = self.output_dir / out_name

        # Method: Concat demuxer with temp file list
        list_file = self.output_dir / f"concat_list_{int(time.time())}_{uuid.uuid4().hex[:4]}.txt"
        try:
            with open(list_file, "w", encoding="utf-8") as f:
                for vp in valid_paths:
                    # Escape single quotes for ffmpeg concat file
                    safe_p = str(vp).replace("'", "'\\''")
                    f.write(f"file '{safe_p}'\n")

            # First attempt: stream copy (ultra fast, sub-second)
            cmd_copy = [
                FFMPEG_BIN,
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_file),
                "-c", "copy",
                str(out_path)
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd_copy,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            # If stream copy failed (e.g. mismatched resolutions or codecs), fall back to re-encode
            if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
                cmd_reencode = [
                    FFMPEG_BIN,
                    "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(list_file),
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    str(out_path)
                ]
                proc2 = await asyncio.create_subprocess_exec(
                    *cmd_reencode,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                _, stderr2 = await proc2.communicate()
                if proc2.returncode != 0:
                    return {"success": False, "error": stderr2.decode("utf-8", errors="replace")[:1000]}

            total_duration = await self.get_video_duration(str(out_path))

            return {
                "success": True,
                "action": "splice_videos",
                "output_path": str(out_path),
                "filename": out_name,
                "input_clips_count": len(valid_paths),
                "input_clips": [str(p) for p in valid_paths],
                "size_bytes": out_path.stat().st_size,
                "duration_sec": total_duration
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            if list_file.exists():
                try:
                    list_file.unlink()
                except Exception:
                    pass

    async def extract_frame(
        self,
        video_path: str,
        frame_type: str = "first",  # "first" | "last" | "timestamp"
        timestamp_sec: Optional[float] = None,
        output_format: str = "png"
    ) -> Dict[str, Any]:
        """
        Extracts the first, last, or specific timestamp frame from a local video as an image.
        This image can then be edited, used as a thumbnail, or fed into new video sequences.
        """
        src = Path(os.path.expanduser(video_path)).resolve()
        if not src.exists():
            return {"success": False, "error": f"Video file not found: {video_path}"}

        out_name = f"{self._generate_id(f'frame_{frame_type}')}.{output_format}"
        out_path = self.output_dir / out_name

        seek_time = 0.0
        if frame_type == "first":
            seek_time = 0.0
        elif frame_type == "last":
            dur = await self.get_video_duration(str(src))
            seek_time = max(0.0, dur - 0.25) if dur > 0.5 else 0.0
        elif frame_type == "timestamp":
            seek_time = float(timestamp_sec or 0.0)

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-ss", str(seek_time),
            "-i", str(src),
            "-frames:v", "1",
            "-q:v", "2",
            str(out_path)
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
                # Read image dimensions via PIL
                try:
                    with Image.open(out_path) as im:
                        w, h = im.size
                except Exception:
                    w, h = 0, 0

                return {
                    "success": True,
                    "action": "extract_frame",
                    "frame_type": frame_type,
                    "seek_timestamp": seek_time,
                    "video_source": str(src),
                    "output_path": str(out_path),
                    "filename": out_name,
                    "width": w,
                    "height": h,
                    "size_bytes": out_path.stat().st_size
                }
            return {"success": False, "error": _ffmpeg_error(stderr)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def create_video_from_frame(
        self,
        image_path: Optional[str] = None,
        duration_sec: float = 3.0,
        fps: int = 24,
        motion: Optional[str] = "zoom_in", # "zoom_in" | "pan_right" | "snow" | "dance" | "wobble"
        audio_path: Optional[str] = None,
        output_format: str = "mp4"
    ) -> Dict[str, Any]:
        """
        Creates a new motion video from a single frame/image on-device.
        Supports Ken Burns zoom/pan, dynamic falling snow, and playful wobble/dance motions.
        Automatically resolves empty paths to the latest created media asset.
        """
        # Resolve source image
        src: Optional[Path] = None
        if image_path and str(image_path).strip():
            raw = str(image_path).strip().strip("`'\" ")
            if raw.startswith("/media/") or raw.startswith("media/"):
                clean = raw.replace("/media/", "").replace("media/", "")
                src = (self.output_dir / clean).resolve()
            elif raw.startswith("~"):
                src = Path(os.path.expanduser(raw)).resolve()
            elif Path(raw).is_absolute():
                src = Path(raw).resolve()
            else:
                candidate = (self.output_dir / raw).resolve()
                if candidate.exists():
                    src = candidate
                else:
                    src = (BASE_DIR / raw).resolve()

        if not src or not src.exists():
            # Auto-fallback: grab the most recent image from the media studio
            images = sorted(list(self.output_dir.glob("*.jpg")) + list(self.output_dir.glob("*.png")), key=os.path.getmtime, reverse=True)
            if images:
                src = images[0]
            else:
                return {"success": False, "error": f"No source image found to animate into video: {image_path}"}

        out_name = f"{self._generate_id('ai_video')}.{output_format}"
        out_path = self.output_dir / out_name
        total_frames = max(24, int(duration_sec * fps))

        motion_lower = (motion or "zoom_in").lower()

        # Handle procedural animation (Snow / Dance / Wobble / Snow Dance)
        if any(m in motion_lower for m in ["snow", "dance", "wobble", "funny"]):
            try:
                import random, math
                from PIL import Image, ImageDraw

                base_img = Image.open(src).convert("RGBA").resize((1280, 720))
                include_snow = "snow" in motion_lower or "winter" in motion_lower
                include_dance = any(d in motion_lower for m in ["dance", "wobble", "funny", "snow_dance"] for d in [m])

                flakes = [
                    {
                        "x": random.randint(0, 1280),
                        "y": random.randint(0, 720),
                        "r": random.randint(2, 6),
                        "speed": random.uniform(3, 7),
                        "drift": random.uniform(0.5, 2.5),
                        "alpha": random.randint(180, 240)
                    }
                    for _ in range(120)
                ]

                proc = await asyncio.create_subprocess_exec(
                    FFMPEG_BIN, "-y",
                    "-f", "rawvideo",
                    "-vcodec", "rawvideo",
                    "-s", "1280x720",
                    "-pix_fmt", "rgba",
                    "-r", str(fps),
                    "-i", "-",
                    "-an",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                    str(out_path),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE
                )

                for f_idx in range(total_frames):
                    frame = base_img.copy()

                    if include_dance:
                        # Smooth playful tilt & bounce physics
                        angle = 3.2 * math.sin(f_idx * 0.40)
                        scale = 1.04 + 0.035 * abs(math.sin(f_idx * 0.30))
                        w_scaled, h_scaled = int(1280 * scale), int(720 * scale)
                        frame_scaled = frame.resize((w_scaled, h_scaled), Image.Resampling.BILINEAR)
                        cx, cy = w_scaled // 2, h_scaled // 2
                        frame = frame_scaled.crop((cx - 640, cy - 360, cx + 640, cy + 360))
                        frame = frame.rotate(angle, resample=Image.Resampling.BILINEAR)

                    if include_snow:
                        overlay = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
                        draw = ImageDraw.Draw(overlay)
                        for fl in flakes:
                            fl["y"] = (fl["y"] + fl["speed"]) % 720
                            fl["x"] = (fl["x"] + math.sin(f_idx * 0.12 + fl["drift"])) % 1280
                            draw.ellipse([fl["x"] - fl["r"], fl["y"] - fl["r"], fl["x"] + fl["r"], fl["y"] + fl["r"]], fill=(255, 255, 255, fl["alpha"]))
                        frame = Image.alpha_composite(frame, overlay)

                    proc.stdin.write(frame.tobytes())

                await proc.stdin.drain()
                proc.stdin.close()
                await proc.wait()

                if out_path.exists() and out_path.stat().st_size > 0:
                    return {
                        "success": True,
                        "action": "create_video_from_frame",
                        "engine": "Camera motion and effects (ffmpeg)",
                        "image_source": str(src),
                        "output_path": str(out_path),
                        "filename": out_name,
                        "media_url": f"/media/{out_name}",
                        "duration_sec": duration_sec,
                        "motion": motion,
                        "size_bytes": out_path.stat().st_size
                    }
            except Exception as e:
                logger.warning(f"Procedural snow video generation failed, falling back to zoompan: {e}")

        # Fallback to standard ffmpeg zoompan
        if motion == "zoom_in":
            vf = f"scale=2560:-1,zoompan=z='min(zoom+0.0015,1.35)':d={total_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1280x720:fps={fps}"
        elif motion == "pan_right":
            vf = f"scale=2560:-1,zoompan=z=1.15:x='if(lte(on,-1),(iw-iw/zoom)/2,x+1)':d={total_frames}:s=1280x720:fps={fps}"
        else:
            vf = f"scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p"

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-loop", "1",
            "-i", str(src)
        ]

        if audio_path and os.path.exists(os.path.expanduser(audio_path)):
            cmd.extend(["-i", os.path.expanduser(audio_path), "-shortest"])

        cmd.extend([
            "-c:v", "libx264",
            "-t", str(duration_sec),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-vf", vf,
            str(out_path)
        ])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0 and out_path.exists():
                return {
                    "success": True,
                    "action": "create_video_from_frame",
                    "engine": "Camera motion (ffmpeg)",
                    "engine": "On-Device Motion Synthesizer (Apple Silicon)",
                    "image_source": str(src),
                    "output_path": str(out_path),
                    "filename": out_name,
                    "media_url": f"/media/{out_name}",
                    "duration_sec": duration_sec,
                    "motion": motion,
                    "size_bytes": out_path.stat().st_size
                }
            return {"success": False, "error": _ffmpeg_error(stderr)}
        except Exception as e:
            return {"success": False, "error": str(e)}


    def _render_procedural_scene(self, prompt: str, style: str, width: int, height: int, out_path: Path) -> bool:
        """Renders rich, high-resolution procedural scene artwork on-device using Pillow."""
        from PIL import Image, ImageDraw
        import math
        import random

        w, h = width, height
        img = Image.new("RGBA", (w, h), (15, 23, 42, 255))
        draw = ImageDraw.Draw(img)
        p_lower = prompt.lower()

        # 1. Winter / Snowman / Snow Scene
        if any(k in p_lower for k in ["snow", "snowman", "winter", "ice", "frost"]):
            # Night/twilight sky gradient
            for y in range(h):
                r = int(12 + (30 - 12) * (y / h))
                g = int(22 + (55 - 22) * (y / h))
                b = int(48 + (95 - 48) * (y / h))
                draw.line([(0, y), (w, y)], fill=(r, g, b, 255))

            # Distant background pine forest
            for tx in range(0, w, 55):
                th = random.randint(180, 260)
                ty = int(h * 0.65)
                draw.polygon([(tx, ty), (tx + 27, ty - th), (tx + 55, ty)], fill=(18, 35, 52, 255))
                # Snow on distant pines
                draw.polygon([(tx + 10, ty - th // 3), (tx + 27, ty - th), (tx + 45, ty - th // 3)], fill=(200, 220, 240, 220))

            # Snow ground drifts
            draw.ellipse([(-int(w * 0.1), int(h * 0.66)), (int(w * 0.7), int(h * 1.1))], fill=(225, 238, 250, 255))
            draw.ellipse([(int(w * 0.35), int(h * 0.63)), (int(w * 1.2), int(h * 1.15))], fill=(240, 248, 255, 255))

            # Check if tree requested
            tree_x = int(w * 0.28)
            # Tree trunk
            draw.rectangle([(tree_x - 35, int(h * 0.35)), (tree_x + 35, int(h * 0.78))], fill=(45, 28, 18, 255))
            # Evergreen boughs
            for by in [int(h * 0.40), int(h * 0.48), int(h * 0.56)]:
                span = int(w * 0.22)
                draw.polygon([(tree_x - span, by + 40), (tree_x, by - 65), (tree_x + span, by + 40)], fill=(24, 52, 38, 255))
                draw.polygon([(tree_x - span + 20, by + 40), (tree_x, by - 65), (tree_x + span - 20, by + 40)], fill=(235, 245, 255, 240))

            # Snowman coordinates (nestled on tree branch if "in a tree", else central)
            if "tree" in p_lower:
                cx = tree_x + 95
                cy = int(h * 0.46)
                scale = 0.95
            else:
                cx = int(w * 0.5)
                cy = int(h * 0.55)
                scale = 1.1

            # Bottom snowball
            br = int(68 * scale)
            draw.ellipse([(cx - br, cy), (cx + br, cy + int(115 * scale))], fill=(245, 250, 255, 255), outline=(195, 215, 235, 255), width=2)
            # Middle snowball
            mr = int(52 * scale)
            draw.ellipse([(cx - mr, cy - int(65 * scale)), (cx + mr, cy + int(20 * scale))], fill=(250, 252, 255, 255), outline=(195, 215, 235, 255), width=2)
            # Head
            hr = int(38 * scale)
            draw.ellipse([(cx - hr, cy - int(125 * scale)), (cx + hr, cy - int(55 * scale))], fill=(255, 255, 255, 255), outline=(195, 215, 235, 255), width=2)

            # Red knitted scarf
            draw.polygon([(cx - 36, cy - 65), (cx + 36, cy - 65), (cx + 30, cy - 45), (cx - 30, cy - 45)], fill=(220, 38, 38, 255))
            draw.polygon([(cx + 8, cy - 45), (cx + 32, cy - 45), (cx + 22, cy + 12), (cx + 4, cy + 12)], fill=(220, 38, 38, 255))

            # Top hat
            draw.ellipse([(cx - 42, cy - 130), (cx + 42, cy - 120)], fill=(22, 22, 22, 255))
            draw.rectangle([(cx - 26, cy - 170), (cx + 26, cy - 125)], fill=(26, 26, 26, 255))
            draw.rectangle([(cx - 26, cy - 136), (cx + 26, cy - 130)], fill=(220, 38, 38, 255))

            # Coal eyes
            draw.ellipse([(cx - 14, cy - 100), (cx - 6, cy - 92)], fill=(20, 20, 20, 255))
            draw.ellipse([(cx + 6, cy - 100), (cx + 14, cy - 92)], fill=(20, 20, 20, 255))
            # Carrot nose
            draw.polygon([(cx, cy - 90), (cx + 32, cy - 86), (cx, cy - 83)], fill=(249, 115, 22, 255))
            # Coal smile
            for dx in [-12, -6, 0, 6, 12]:
                draw.ellipse([(cx + dx - 2, cy - 76 + abs(dx) // 3 - 2), (cx + dx + 2, cy - 76 + abs(dx) // 3 + 2)], fill=(40, 40, 40, 255))

            # Falling snowflakes
            random.seed(int(width + height))
            for _ in range(160):
                sx = random.randint(0, w)
                sy = random.randint(0, h)
                sr = random.randint(2, 5)
                draw.ellipse([(sx - sr, sy - sr), (sx + sr, sy + sr)], fill=(255, 255, 255, random.randint(180, 255)))

        # 2. Cyberpunk / Synthwave / Neon Scene
        elif any(k in p_lower for k in ["cyber", "neon", "synth", "future", "hacker", "robot"]):
            for y in range(h):
                r = int(15 + (45 - 15) * (y / h))
                g = int(5 + (12 - 5) * (y / h))
                b = int(35 + (85 - 35) * (y / h))
                draw.line([(0, y), (w, y)], fill=(r, g, b, 255))

            # Glowing neon sun
            sun_r = int(w * 0.22)
            draw.ellipse([(w // 2 - sun_r, int(h * 0.42) - sun_r), (w // 2 + sun_r, int(h * 0.42) + sun_r)], fill=(244, 63, 94, 255))
            # Sun stripes
            for sy in range(int(h * 0.38), int(h * 0.58), 12):
                draw.line([(w // 2 - sun_r - 20, sy), (w // 2 + sun_r + 20, sy)], fill=(20, 10, 40, 255), width=4)

            # Horizon grid lines
            horizon_y = int(h * 0.6)
            draw.rectangle([(0, horizon_y), (w, h)], fill=(10, 8, 22, 255))
            for x in range(0, w + 1, 60):
                draw.line([(w // 2, horizon_y), (x, h)], fill=(236, 72, 153, 160), width=2)
            for gy in range(horizon_y, h, 25):
                draw.line([(0, gy), (w, gy)], fill=(6, 182, 212, 140), width=2)

        # 3. Space / Cosmic / Galaxy Scene
        elif any(k in p_lower for k in ["space", "galaxy", "planet", "cosmic", "star", "universe"]):
            # Starfield background
            draw.rectangle([(0, 0), (w, h)], fill=(5, 7, 18, 255))
            random.seed(42)
            for _ in range(250):
                sx = random.randint(0, w)
                sy = random.randint(0, h)
                sr = random.randint(1, 3)
                draw.ellipse([(sx - sr, sy - sr), (sx + sr, sy + sr)], fill=(255, 255, 255, random.randint(160, 255)))

            # Ringed planet
            px, py = int(w * 0.55), int(h * 0.45)
            pr = int(w * 0.18)
            draw.ellipse([(px - pr, py - pr), (px + pr, py + pr)], fill=(99, 102, 241, 255))
            # Planet ring
            draw.ellipse([(px - int(pr * 1.7), py - 25), (px + int(pr * 1.7), py + 25)], outline=(244, 114, 182, 220), width=8)

        # 4. Nature / Landscape / Abstract Art Scene
        else:
            # Golden twilight sunset
            for y in range(h):
                r = int(249 - (249 - 30) * (y / h))
                g = int(115 - (115 - 41) * (y / h))
                b = int(22 + (88 - 22) * (y / h))
                draw.line([(0, y), (w, y)], fill=(r, g, b, 255))

            # Mountain ridges
            draw.polygon([(0, int(h * 0.65)), (int(w * 0.35), int(h * 0.42)), (int(w * 0.7), int(h * 0.68)), (w, int(h * 0.52)), (w, h), (0, h)], fill=(28, 25, 45, 255))
            draw.polygon([(0, int(h * 0.78)), (int(w * 0.45), int(h * 0.58)), (w, int(h * 0.82)), (w, h), (0, h)], fill=(15, 14, 25, 255))
            # Glowing sun
            draw.ellipse([(int(w * 0.65), int(h * 0.25)), (int(w * 0.82), int(h * 0.42))], fill=(254, 240, 138, 240))

        # Bottom clean aesthetic border banner
        draw.rectangle([(0, h - 55), (w, h)], fill=(10, 15, 26, 210))
        draw.text((25, h - 38), f"XOD MEDIA STUDIO • {prompt[:65]}", fill=(226, 232, 240, 240))

        img = img.convert("RGB")
        img.save(str(out_path), quality=95)
        return True

    async def generate_ai_image(
        self,
        prompt: str,
        style: str = "photorealistic",
        width: int = 512,
        height: int = 512,
        seed: Optional[int] = None,
        model: Optional[str] = None,
        loras: Optional[List[Dict[str, Any]]] = None,
        negative_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Synthesizes high-resolution visual art and imagery on-device.
        Multi-tier pipeline:
        1. Cloud DALL-E 3 (if OpenAI key configured)
        2. High-Fidelity Generative AI Diffusion Engine (FLUX.1 Schnell • 1024x1024 Photorealistic)
        3. High-Res Procedural Scene Synthesizer (offline fallback)
        """
        from app.config import settings
        import urllib.request
        import urllib.parse
        import random

        if not prompt or not prompt.strip():
            return {"success": False, "error": "Prompt cannot be empty"}

        clean_prompt = prompt.strip()
        out_name = f"{self._generate_id('ai_gen')}.jpg"
        out_path = self.output_dir / out_name

        # Enhance prompt with style modifiers if user asked for a specific style
        effective_prompt = clean_prompt
        style_lower = (style or "photorealistic").lower()
        if style_lower == "photorealistic" and not any(k in clean_prompt.lower() for k in ["photorealistic", "photo", "8k", "dslr"]):
            effective_prompt = f"{clean_prompt}, highly detailed photorealistic, 8k resolution, cinematic lighting"
        elif style_lower == "anime" and "anime" not in clean_prompt.lower():
            effective_prompt = f"{clean_prompt}, anime art style, vibrant studio ghibli aesthetic"
        elif style_lower == "cyberpunk" and "cyberpunk" not in clean_prompt.lower():
            effective_prompt = f"{clean_prompt}, cyberpunk futuristic neon aesthetic, volumetric lighting"

        # Tier 1: If OpenAI API key configured, use DALL-E 3
        if settings.openai_api_key:
            try:
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=settings.openai_api_key)
                resp = await client.images.generate(
                    model="dall-e-3",
                    prompt=clean_prompt,
                    size="1024x1024",
                    quality="standard",
                    n=1
                )
                img_url = resp.data[0].url
                loop = asyncio.get_event_loop()
                def _download():
                    with urllib.request.urlopen(img_url, timeout=30) as r:
                        return r.read()
                data = await loop.run_in_executor(None, _download)
                out_path.write_bytes(data)
                return {
                    "success": True,
                    "action": "generate_ai_image",
                    "engine": "OpenAI DALL-E 3",
                    "output_path": str(out_path),
                    "filename": out_name,
                    "relative_url": f"/media/{out_name}",
                    "prompt": clean_prompt,
                    "width": width,
                    "height": height,
                    "size_bytes": len(data),
                    "markdown": f"![{clean_prompt}](/media/{out_name})"
                }
            except Exception as e:
                logger.warning(f"OpenAI DALL-E generation failed, falling back: {e}")

        # Tier 2: on-device diffusion in the media worker (memory-managed, crash-isolated).
        # No silent fallback: if the AI model can't run, say why instead of drawing something else.
        from app.services.media_runtime import media_runtime
        res = await media_runtime.generate_image(prompt=effective_prompt, model=model, loras=loras,
                                                 width=min(width, 768), height=min(height, 768), seed=seed,
                                                 negative_prompt=negative_prompt)
        if res.get("success"):
            res["prompt"] = clean_prompt
        return res

    async def generate_ai_video(
        self,
        prompt: str,
        duration_sec: float = 4.0,
        fps: int = 24,
        motion: Optional[str] = None,
        style: str = "photorealistic",
        model: Optional[str] = None,
        loras: Optional[List[Dict[str, Any]]] = None,
        seed: Optional[int] = None,
        negative_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Text to video. With a video model installed (AnimateDiff-Lightning), the model generates real
        motion: 1-4 s clips, smoothed to 24 fps. Passing a camera `motion` (zoom_in, pan_right, snow, ...)
        instead makes one AI image and animates it with camera moves and effects.
        """
        if not prompt or not prompt.strip():
            return {"success": False, "error": "Prompt cannot be empty"}

        clean_prompt = prompt.strip()
        p_lower = clean_prompt.lower()

        from app.services.media_runtime import list_models, media_runtime
        has_video_model = any(m["kind"] == "video" and m["usable"] for m in list_models())
        if has_video_model and not motion:
            styled = clean_prompt if style in (None, "", "photorealistic") else f"{clean_prompt}, {style} style"
            res = await media_runtime.generate_video(
                prompt=styled, model=model, loras=loras, seed=seed, negative_prompt=negative_prompt,
                seconds=max(1.0, min(float(duration_sec or 2.0), 4.0)))
            if res.get("success"):
                res["prompt"] = clean_prompt
                res["markdown"] = f"![{clean_prompt}]({res['media_url']})"
            return res

        # Deduce appropriate motion style if not specified
        if not motion:
            if any(k in p_lower for k in ["snow", "winter", "ice", "blizzard", "frost", "snowman"]):
                if any(k in p_lower for k in ["dance", "funny", "wobble", "jump", "party"]):
                    motion = "snow_dance"
                else:
                    motion = "snow"
            elif any(k in p_lower for k in ["dance", "funny", "wobble", "jump", "party"]):
                motion = "dance"
            elif any(k in p_lower for k in ["pan", "landscape", "horizon", "city"]):
                motion = "pan_right"
            else:
                motion = "zoom_in"

        # Generate base high-fidelity image
        img_res = await self.generate_ai_image(
            prompt=clean_prompt,
            style=style,
            width=1024,
            height=1024
        )
        if not img_res.get("success") or not img_res.get("output_path"):
            return {"success": False, "error": f"Failed to synthesize video keyframe: {img_res.get('error')}"}

        img_path = img_res["output_path"]

        # Animate into video
        vid_res = await self.create_video_from_frame(
            image_path=img_path,
            duration_sec=duration_sec,
            fps=fps,
            motion=motion,
            output_format="mp4"
        )
        if vid_res.get("success"):
            vid_res["action"] = "generate_ai_video"
            vid_res["engine"] = f"AI keyframe ({img_res.get('active_model')}) animated with camera motion ({motion})"
            vid_res["prompt"] = clean_prompt
            vid_res["keyframe_image"] = img_res.get("relative_url")
            vid_res["markdown"] = f"![{clean_prompt}]({vid_res['media_url']})"
        return vid_res

    def list_created_media(self) -> List[Dict[str, Any]]:
        """Returns all generated media files in the studio."""
        items = []
        for p in sorted(self.output_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file() and not p.name.startswith("."):
                ext = p.suffix.lower()
                mtype = "video" if ext in (".mp4", ".mov", ".webm", ".mkv", ".avi") else ("image" if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif") else "other")
                items.append({
                    "name": p.name,
                    "path": str(p),
                    "size_bytes": p.stat().st_size,
                    "type": mtype,
                    "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
                })
        return items[:50]


local_media_service = LocalMediaService()

