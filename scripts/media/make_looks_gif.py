"""
The six looks side by side, doing their habits: docs/media/looks.gif for the README.

Point it at a Xod running on a demo data folder (never your own):
  XOD_DATA_DIR=/tmp/xod-demo .venv/bin/python -m uvicorn app.main:app --port 8125
  .venv/bin/python scripts/media/make_looks_gif.py --url http://127.0.0.1:8125
"""
import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from studio import Studio, frames_to_mp4  # noqa: E402
from make_launch_video import gif  # noqa: E402

W, H = 1200, 520

STAGE = """
(() => {
  document.querySelectorAll('.fixed.inset-0').forEach(e => { if (e.textContent.includes('Pick Xod')) e.remove(); });
  const st = document.createElement('div');
  st.id = 'looksStage';
  Object.assign(st.style, {position: 'fixed', inset: '0', zIndex: 2147483000, background: '#0f1222',
    display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', placeItems: 'center', padding: '0 24px'});
  for (const id of ['moon', 'cat', 'ghost', 'robot', 'owl', 'sprout']) {
    const cell = document.createElement('div');
    Object.assign(cell.style, {display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '18px'});
    const m = document.createElement('span');
    m.className = 'xod-mascot';
    m.dataset.character = id;
    Object.assign(m.style, {width: '150px', height: '150px'});
    const name = document.createElement('span');
    name.className = 'xc-name';
    name.textContent = XodMascot.characters().find(c => c.id === id).name;
    Object.assign(name.style, {fontSize: '22px'});
    cell.append(m, name);
    st.appendChild(cell);
  }
  document.body.appendChild(st);
  return true;
})()
"""


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8125")
    ap.add_argument("--out", default=str(HERE.parents[1] / "docs" / "media" / "looks.gif"))
    a = ap.parse_args()
    async with Studio(W, H, scale=1, cursor=False) as s:
        await s.goto(a.url, wait=3)
        await s.js(STAGE)
        await asyncio.sleep(1)
        await s.start_recording()
        steps = [
            ("XodMascot.fidget('look')", 1.8),
            ("XodMascot.fidget('special')", 2.8),
            ("XodMascot.fidget('hop')", 1.1),
            ("XodMascot.demo('thinking', 2200)", 2.3),
            ("XodMascot.demo('happy', 1500)", 1.7),
            ("XodMascot.fidget('wink')", 1.1),
            ("XodMascot.demo('sleepy', 2600)", 2.8),
            ("XodMascot.demo('idle', 50); XodMascot.demo('happy', 1200)", 1.6),
        ]
        for expr, wait in steps:
            await s.js(expr)
            await asyncio.sleep(wait)
        frames = await s.stop_recording()
    work = Path(tempfile.mkdtemp(prefix="xod-looks-"))
    mp4 = frames_to_mp4(frames, work / "looks.mp4", fps=30, size=(W, 300), crop=(0, 120, W, 300))
    gif(mp4, Path(a.out), start=0.2, width=720, fps=12)
    print(a.out, Path(a.out).stat().st_size // 1024, "KB")


if __name__ == "__main__":
    asyncio.run(main())
