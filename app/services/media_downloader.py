import os
import re
import sys
import asyncio
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger("media_downloader")

# yt-dlp uses the ffmpeg that ships inside the imageio-ffmpeg package (nothing downloaded at runtime).
try:
    import imageio_ffmpeg
    FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()
except Exception as e:
    FFMPEG_BIN = None
    logger.warning(f"ffmpeg not available: {e}")

try:
    import yt_dlp
except ImportError:
    yt_dlp = None
    logger.warning("yt-dlp is not installed")


class MediaDownloaderService:
    """
    Autonomous media downloading service for YouTube and web video/audio streams.
    Supports audio extraction (MP3 at 192kbps) and full video downloads (MP4),
    with auto-resolution to macOS ~/Downloads folder.
    """

    def __init__(self):
        self.default_downloads_dir = os.path.expanduser("~/Downloads")
        if not os.path.exists(self.default_downloads_dir):
            try:
                os.makedirs(self.default_downloads_dir, exist_ok=True)
            except Exception:
                self.default_downloads_dir = os.path.abspath("./downloads")
                os.makedirs(self.default_downloads_dir, exist_ok=True)

    def _format_duration(self, seconds: Optional[int]) -> str:
        if not seconds:
            return "Unknown"
        mins, secs = divmod(int(seconds), 60)
        hours, mins = divmod(mins, 60)
        if hours > 0:
            return f"{hours}:{mins:02d}:{secs:02d}"
        return f"{mins}:{secs:02d}"

    def _sanitize_target(self, url_or_query: str) -> str:
        target = url_or_query.strip()
        # If it's a URL, keep it
        if target.startswith("http://") or target.startswith("https://"):
            return target
        # If it's a raw video ID (e.g. dQw4w9WgXcQ)
        if re.match(r"^[a-zA-Z0-9_-]{11}$", target):
            return f"https://www.youtube.com/watch?v={target}"
        # Otherwise treat as a YouTube search
        return f"ytsearch1:{target}"

    def _download_sync(
        self,
        target: str,
        format_type: str = "auto",
        output_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        if not yt_dlp:
            return {
                "success": False,
                "error": "yt-dlp library is not installed in the environment."
            }

        resolved_output_dir = output_dir or self.default_downloads_dir
        os.makedirs(resolved_output_dir, exist_ok=True)

        # Detect format intent
        is_audio = False
        target_lower = target.lower()
        if format_type == "audio":
            is_audio = True
        elif format_type == "video":
            is_audio = False
        else:  # auto
            audio_keywords = ["song", "music", "audio", "track", "mp3", "listen", "album", "soundtrack"]
            if any(k in target_lower for k in audio_keywords):
                is_audio = True
            else:
                is_audio = False

        # Output template: saves clean filename to destination
        outtmpl = os.path.join(resolved_output_dir, "%(title).150s.%(ext)s")

        ydl_opts: Dict[str, Any] = {
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            # Use android & ios player clients to bypass YouTube 403 SABR throttling
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "ios"]
                }
            },
            # Overwrite if exists to guarantee fresh file
            "overwrites": True,
        }
        if FFMPEG_BIN:
            ydl_opts["ffmpeg_location"] = FFMPEG_BIN

        if is_audio:
            ydl_opts["format"] = "ba/b"
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        else:
            ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            ydl_opts["merge_output_format"] = "mp4"

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target, download=True)

                if "entries" in info and info["entries"]:
                    info = info["entries"][0]

                title = info.get("title") or "downloaded_media"
                uploader = info.get("uploader") or info.get("channel") or "Unknown Artist"
                duration_sec = info.get("duration")
                duration_str = self._format_duration(duration_sec)
                webpage_url = info.get("webpage_url") or target
                thumbnail = info.get("thumbnail")

                # Resolve downloaded file path on disk
                target_ext = "mp3" if is_audio else "mp4"
                expected_base = ydl.prepare_filename(info)
                expected_stem = os.path.splitext(expected_base)[0]
                expected_path = f"{expected_stem}.{target_ext}"

                actual_path = None
                if os.path.exists(expected_path):
                    actual_path = expected_path
                elif os.path.exists(expected_base):
                    actual_path = expected_base
                else:
                    # Look in the output directory for recently modified file matching title stem
                    safe_stem = os.path.basename(expected_stem)[:30]
                    for f in os.listdir(resolved_output_dir):
                        if safe_stem in f and f.endswith(f".{target_ext}"):
                            actual_path = os.path.join(resolved_output_dir, f)
                            break

                file_size_bytes = os.path.getsize(actual_path) if actual_path and os.path.exists(actual_path) else 0
                file_size_mb = round(file_size_bytes / (1024 * 1024), 2)

                return {
                    "success": True,
                    "media_type": "audio" if is_audio else "video",
                    "format": target_ext,
                    "title": title,
                    "uploader": uploader,
                    "duration": duration_str,
                    "duration_seconds": duration_sec,
                    "file_path": actual_path or expected_path,
                    "file_name": os.path.basename(actual_path or expected_path),
                    "file_size_mb": file_size_mb,
                    "thumbnail": thumbnail,
                    "webpage_url": webpage_url,
                    "message": f"Successfully downloaded {'song' if is_audio else 'video'} '{title}' ({file_size_mb} MB) to {actual_path or expected_path}"
                }

        except Exception as e:
            logger.error(f"Download error for '{target}': {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "target": target,
                "message": f"Failed to download media: {e}"
            }

    async def download(
        self,
        url_or_query: str,
        format_type: str = "auto",
        output_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Asynchronously downloads a YouTube video or song to disk.
        """
        target = self._sanitize_target(url_or_query)
        return await asyncio.to_thread(
            self._download_sync,
            target=target,
            format_type=format_type,
            output_dir=output_dir
        )

    def _get_info_sync(self, target: str) -> Dict[str, Any]:
        if not yt_dlp:
            return {"success": False, "error": "yt-dlp is not installed"}

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "ios"]
                }
            }
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target, download=False)
                if "entries" in info and info["entries"]:
                    info = info["entries"][0]
                return {
                    "success": True,
                    "title": info.get("title"),
                    "uploader": info.get("uploader"),
                    "duration": self._format_duration(info.get("duration")),
                    "duration_seconds": info.get("duration"),
                    "view_count": info.get("view_count"),
                    "thumbnail": info.get("thumbnail"),
                    "webpage_url": info.get("webpage_url") or target
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_info(self, url_or_query: str) -> Dict[str, Any]:
        """Inspects media metadata without downloading."""
        target = self._sanitize_target(url_or_query)
        return await asyncio.to_thread(self._get_info_sync, target)


media_downloader_service = MediaDownloaderService()
