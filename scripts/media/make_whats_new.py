#!/usr/bin/env python3
"""
The 2-minute film with what's new: the film as it was up to "And a lot more" (docs/media/xod-launch.mp4), then a title
card and a live recording, in a wide window, for each new thing (your Mac's apps, coding agents, any model), then the
closing cards again (updated). The scenes are the same as the vertical shorts (make_use_case_videos.py).

Same demo setup as the shorts (a demo data folder, never your own):
  XOD_DATA_DIR=<empty folder> python -m uvicorn app.main:app --port 8125
  python scripts/media/make_whats_new.py --url http://127.0.0.1:8125 --model-note "☁ Cloud model: Fireworks · GLM 5.3 Flash"

  --cut 103.0   where the old film's "And a lot more" card starts (everything before it is kept)
"""
import argparse
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from studio import Studio, ffmpeg, frames_to_mp4  # noqa: E402
import make_use_case_videos as uc  # noqa: E402

STAGE = (HERE / "stage.html").as_uri()
W, H, SCALE = 1120, 630, 1920 / 1120          # the app's window → 1920 × 1080 frames: big enough to read at 720p
PACE = 0.6                                     # each scene at about 60% of its length in the shorts: the film stays short
OUT_W, OUT_H = 1280, 720
CARDS = {"apps": 4.2, "agents": 4.2, "models": 4.2, "more": 4.4, "end": 6.0}


async def card(name: str, secs: float, out: Path) -> Path:
    async with Studio(width=1920, height=1080, scale=1, cursor=False, port=9531) as s:
        await s.goto("about:blank", wait=0.2)
        await s.start_recording()
        await s.cmd("Page.navigate", url=f"{STAGE}#{name}")
        if name == "end":
            await asyncio.sleep(1.4)
            await s.js("XodMascot.beHappy(1600)")
            await asyncio.sleep(secs - 1.4)
        else:
            await asyncio.sleep(secs)
        frames_to_mp4(await s.stop_recording(), out, size=None)
    return out


def finish(raw: Path, out: Path, badge: Path = None, note: Path = None) -> Path:
    """The recording at the film's size, with the "sped up" badge bottom-left and the model note bottom-right."""
    ins, fc, last = ["-i", str(raw)], f"[0:v]scale={OUT_W}:{OUT_H}:flags=lanczos[v]", "v"
    for i, (png, where) in enumerate(((badge, "left"), (note, "right")), 1):
        if not png:
            continue
        ins += ["-loop", "1", "-i", str(png)]
        idx = sum(1 for a in ins if a == "-i") - 1
        x = "24" if where == "left" else f"W-w-24"
        fc += f";[{idx}:v]scale=iw*0.62:-1[p{i}];[{last}][p{i}]overlay={x}:H-h-24:shortest=1[o{i}]"
        last = f"o{i}"
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", *ins, "-filter_complex", fc + f";[{last}]fps=30,format=yuv420p[out]",
                    "-map", "[out]", "-c:v", "libx264", "-preset", "slow", "-crf", "19", "-movflags", "+faststart", str(out)], check=True)
    return out


def join(clips, out: Path, fade=0.45) -> Path:
    ins, parts, durs = [], [], [uc.duration(c) for c in clips]
    for i, c in enumerate(clips):
        ins += ["-i", str(c)]
        parts.append(f"[{i}:v]scale={OUT_W}:{OUT_H},fps=30,format=yuv420p,setsar=1[v{i}]")
    last, offset = "v0", 0.0
    for i in range(1, len(clips)):
        offset += durs[i - 1] - fade
        parts.append(f"[{last}][v{i}]xfade=transition=fade:duration={fade}:offset={offset:.3f}[x{i}]")
        last = f"x{i}"
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", *ins, "-filter_complex", ";".join(parts), "-map", f"[{last}]",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                   check=True)
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8125")
    ap.add_argument("--film", default=str(HERE.parents[1] / "docs" / "media" / "xod-launch.mp4"))
    ap.add_argument("--out", default=str(HERE.parents[1] / "docs" / "media" / "xod-launch.mp4"))
    ap.add_argument("--work", default=str(Path.home() / ".xod-film-work"))
    ap.add_argument("--cut", type=float, default=103.0)
    ap.add_argument("--model-note", default="")
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    kit = uc.Kit(work)
    sc = uc.Scenes(a.url, work, a.model_note, size=(W, H, SCALE))

    old = work / "old-part.mp4"         # the film so far, up to "And a lot more" (re-encoded, so it joins cleanly)
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", a.film, "-t", f"{a.cut:.2f}", "-vf", f"scale={OUT_W}:{OUT_H},fps=30",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "19", "-pix_fmt", "yuv420p", str(old)], check=True)
    pieces = [old]
    note = await kit.badge(a.model_note) if a.model_note else None
    for name in ("apps", "agents", "models"):
        print(f"== {name}", flush=True)
        pieces.append(await card(name, CARDS[name], work / f"card-{name}.mp4"))
        for i, t in enumerate(await getattr(sc, name)(), 1):
            if not t.frames:
                continue
            if t.seconds:
                t.seconds *= PACE
            sp = t.play_speed()
            raw = work / f"{name}-take{i}.mp4"
            frames_to_mp4(t.frames, raw, size=(int(W * SCALE), int(H * SCALE)), speed=sp)
            badge = await kit.badge(f"⏩ sped up {round(sp)}×") if sp >= 1.8 else None
            pieces.append(finish(raw, work / f"{name}-piece{i}.mp4", badge, note if name != "models" else None))
            print(f"   take {i}: {t.raw():.0f}s → {uc.duration(raw):.1f}s ({sp:.1f}×)", flush=True)
    for name in ("more", "end"):
        pieces.append(await card(name, CARDS[name], work / f"card-{name}.mp4"))
    out = Path(a.out)
    tmp = work / "film.mp4"
    join(pieces, tmp)
    shutil.copy(tmp, out)
    print(f"film: {out} ({uc.duration(out):.1f}s)")


if __name__ == "__main__":
    asyncio.run(main())
