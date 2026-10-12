"""
The companion at work in a chat (powers.js): a scripted agent run, recorded for the README.

Point it at a Xod running on a demo data folder (never your own):
  XOD_DATA_DIR=/tmp/xod-demo .venv/bin/python -m uvicorn app.main:app --port 8125
  .venv/bin/python scripts/media/make_powers_video.py --url http://127.0.0.1:8125 --look cat
"""
import argparse
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from studio import Studio, frames_to_mp4  # noqa: E402
from make_launch_video import gif  # noqa: E402

W, H = 1280, 800


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8125")
    ap.add_argument("--look", default="cat")
    ap.add_argument("--out", default=str(HERE.parents[1] / "docs" / "media" / "powers.mp4"))
    ap.add_argument("--gif", default="")
    a = ap.parse_args()
    async with Studio(W, H, scale=2, cursor=False) as s:
        await s.goto(a.url, wait=3.5)
        await s.js("document.querySelectorAll('.fixed.inset-0').forEach(e => { if (e.textContent.includes('Pick Xod')) e.remove(); }); true")
        await s.js(f"XodMascot.setCharacter('{a.look}', {{ save: false, greet: false }}); true")
        await s.js((HERE / "chat_demo.js").read_text())
        await asyncio.sleep(1)
        await s.start_recording()
        await s.js("XodChatDemo.run({ approval: true }).then(() => true)")
        await asyncio.sleep(2.6)
        await s.js("document.querySelector('[data-mcp=\"approve\"]:not(:disabled)')?.click(); true")
        await asyncio.sleep(3.2)
        frames = await s.stop_recording()
    out = Path(a.out)
    frames_to_mp4(frames, out, fps=30, size=(W, H), scale=2)
    print(out, out.stat().st_size // 1024, "KB")
    if a.gif:
        crop = work = out.with_name(out.stem + "-chat.mp4")
        frames_to_mp4(frames, crop, fps=30, size=(960, 600), crop=(300, 40, 960, 600), scale=2)
        gif(crop, Path(a.gif), width=640, fps=12)
        work.unlink(missing_ok=True)
        print(a.gif, Path(a.gif).stat().st_size // 1024, "KB")


if __name__ == "__main__":
    asyncio.run(main())
