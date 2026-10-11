#!/usr/bin/env python3
"""
Makes Xod's launch video and README clips from the real app (nothing mocked): title cards from
stage.html, then live recordings of a demo Xod answering, asking before it acts, saving a routine
and undoing a change. Waiting is sped up; everything else plays in real time.

Run it against a Xod on a demo data folder with a model loaded (LM Studio or the built-in engine):
  XOD_DATA_DIR=/tmp/xod-demo .venv/bin/python -m uvicorn app.main:app --port 8125
  .venv/bin/python scripts/media/make_launch_video.py --url http://127.0.0.1:8125 --out docs/media

  --only cards,home,research,approval,routine,undo   record some scenes again; the others are reused.
"""
import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from studio import Studio, ffmpeg, frames_to_mp4  # noqa: E402

HERE = Path(__file__).resolve().parent
STAGE = (HERE / "stage.html").as_uri()
QUESTION = "Compare the Sony WH-1000XM6 and Bose QuietComfort Ultra headphones: price, battery and noise cancelling."
W, H = 1600, 900


class Scenes:
    def __init__(self, url: str, work: Path):
        self.url, self.work = url.rstrip("/"), work

    def clip(self, name):
        return self.work / f"{name}.mp4"

    # ------------------------------------------------------------ title cards
    async def cards(self):
        timing = {"intro": 5.2, "private": 3.8, "research": 3.4, "asks": 3.6, "routines": 3.6, "undo": 3.6,
                  "more": 4.4, "end": 6.0}
        async with Studio(width=1920, height=1080, scale=1, cursor=False, port=9481) as s:
            for name, secs in timing.items():
                await s.goto("about:blank", wait=0.2)
                await s.start_recording()
                await s.cmd("Page.navigate", url=f"{STAGE}#{name}")
                if name == "intro":
                    await asyncio.sleep(2.8)
                    await s.js("XodMascot.beHappy(1500)")
                    await asyncio.sleep(secs - 2.8)
                elif name == "end":
                    await asyncio.sleep(1.4)
                    await s.js("XodMascot.beHappy(1600)")
                    await asyncio.sleep(secs - 1.4)
                else:
                    await asyncio.sleep(secs)
                frames_to_mp4(await s.stop_recording(), self.clip(f"card-{name}"), size=None)
                print("card", name)

    # ------------------------------------------------------------ the app
    async def fresh(self, s: Studio, path="/"):
        await s.goto(self.url + path, wait=3.5)
        # The first-visit hello would cover things; every clip starts from a calm home screen.
        await s.js("try { localStorage.setItem('xod.saidHello', '1') } catch (_) {}")

    async def home(self):
        """The moon notices you: eyes follow the cursor, a pet, a boop, a hello."""
        async with Studio(width=W, height=H, scale=2, port=9482) as s:
            await s.goto(self.url + "/", wait=0.5)
            await s.js("try { localStorage.removeItem('xod.saidHello') } catch (_) {}")   # show the first-visit hello
            await s.start_recording()
            await s.goto(self.url + "/", wait=3.0)
            mx, my = await s.center_of(".xod-mascot--hero")
            await s.move(mx + 330, my - 60, 1.1)
            await asyncio.sleep(0.5)
            await s.move(mx - 360, my + 160, 1.3)
            await asyncio.sleep(0.4)
            await s.move(mx + 6, my + 4, 0.8)
            for i in range(14):                           # a pet: quick back and forth
                await s.move(mx + (22 if i % 2 else -22), my + 6, 0.12)
            await asyncio.sleep(1.2)
            await s.click(at=(mx, my), move=0.2)
            await asyncio.sleep(1.0)
            await s.move(mx + 150, my + 70, 0.5)
            await asyncio.sleep(1.8)
            await s.move(mx + 120, my + 300, 0.9)
            await asyncio.sleep(0.6)
            frames = await s.stop_recording()
            # The film zooms in on the moon and the cards; the README GIF is a close-up of the moon.
            frames_to_mp4(frames, self.clip("app-home"), crop=(mx - 480, my - 120, 960, 540), scale=2)
            frames_to_mp4(frames, self.work / "moon-closeup.mp4", size=(960, 540), crop=(mx - 300, my - 90, 600, 337.5), scale=2)

    async def research(self):
        """A real question, a real answer with sources. The wait is sped up 8x."""
        async with Studio(width=W, height=H, scale=1.5, port=9483) as s:
            await self.fresh(s)
            await s.move(W / 2, H - 200, 0.1)
            await s.start_recording()
            await s.click("#chatInput", move=0.9)
            await s.type(QUESTION, cps=24)
            await asyncio.sleep(0.5)
            await s.key()
            await asyncio.sleep(2.5)
            frames_to_mp4(await s.stop_recording(), self.clip("app-research-1"))

            # Thinking, searching, reading: fast-forward until words start to arrive.
            await s.move(W - 260, 180, 0.6)
            await s.start_recording()
            t0 = time.time()
            while time.time() - t0 < 600:
                n = await s.js("(() => { const p = [...document.querySelectorAll('.prose-custom')].pop(); return p ? p.innerText.length : 0 })()")
                if n and n > 40:
                    break
                await asyncio.sleep(0.5)
            frames_to_mp4(await s.stop_recording(), self.clip("app-research-2"), speed=8)
            print(f"research: first words after {time.time() - t0:.0f}s")

            # The answer arriving, at 2x, with the chat following along.
            await s.start_recording()
            while await s.js("typeof isStreaming !== 'undefined' && isStreaming"):
                await asyncio.sleep(0.5)
                if time.time() - t0 > 900:
                    break
            await asyncio.sleep(1.5)
            frames_to_mp4(await s.stop_recording(), self.clip("app-research-3"), speed=2)

            # Scroll through the answer to its sources, at real speed.
            await s.start_recording()
            box = await s.center_of("#chatMessages")
            await s.move(box[0], box[1], 0.6)
            await s.js("""(async () => { const box = document.querySelector('#chatMessages');
                const ans = [...box.querySelectorAll('.prose-custom')].pop();
                const top = ans.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 120;
                box.scrollTo({top, behavior: 'instant'});
                await new Promise(r => setTimeout(r, 900));
                const end = box.scrollHeight;
                for (let y = box.scrollTop; y < end; y += 6) { box.scrollTop = y; await new Promise(r => setTimeout(r, 16)); }
            })()""")
            await asyncio.sleep(1.5)
            frames_to_mp4(await s.stop_recording(), self.clip("app-research-4"))
            sid = await s.js("currentSessionId")
            (self.work / "research-session.txt").write_text(str(sid or ""))

    async def approval(self):
        """A background task gets to the Send button and waits for a person."""
        mock = subprocess.Popen([sys.executable, str(HERE.parents[1] / "tests" / "browser_missions" / "mock_jobs.py")],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(2)
            body = json.dumps({"request": "Go to http://localhost:8799/messages and send the Acme recruiter this message: "
                                            "\"Hi Dana, thanks for the call today. I'm excited about the role and happy to share more examples.\""}).encode()
            req = urllib.request.Request(self.url + "/api/tasks", data=body, headers={"Content-Type": "application/json"})
            task = json.loads(urllib.request.urlopen(req).read())
            tid = task.get("id") or (task.get("task") or {}).get("id")
            print("approval: task", tid)
            t0 = time.time()
            while time.time() - t0 < 900:
                lst = json.loads(urllib.request.urlopen(f"{self.url}/api/tasks").read())
                lst = lst.get("tasks", lst) if isinstance(lst, dict) else lst
                st = next((t.get("status") for t in lst if t.get("id") == tid), None)
                if st == "waiting":
                    break
                time.sleep(3)
            print(f"approval: waiting after {time.time() - t0:.0f}s")
            async with Studio(width=W, height=H, scale=1.5, port=9484) as s:
                await self.fresh(s)
                await s.move(W / 2, H / 2, 0.1)
                await s.start_recording()
                await asyncio.sleep(0.6)
                await s.js("openTasksPanel()")
                await asyncio.sleep(2.2)
                btn = await s.js("""(() => { const b = [...document.querySelectorAll('button')].find(b => /approve|send it|yes/i.test(b.innerText) && b.offsetParent);
                    if (!b) return null; const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()""")
                if btn:
                    await s.move(btn[0], btn[1], 1.0)
                    await asyncio.sleep(0.8)
                    await s.click(at=btn, move=0.1)
                await asyncio.sleep(6)
                frames_to_mp4(await s.stop_recording(), self.clip("app-approval"))
        finally:
            mock.terminate()

    async def routine(self):
        """Turn the answer into a routine that runs every Monday."""
        sid = (self.work / "research-session.txt").read_text().strip() if (self.work / "research-session.txt").exists() else ""
        async with Studio(width=W, height=H, scale=1.5, port=9485) as s:
            await self.fresh(s)
            if sid:
                await s.js(f"selectSession({json.dumps(sid)})")
                await asyncio.sleep(2)
            await s.start_recording()
            await asyncio.sleep(0.5)
            btn = await s.js("""(() => { const b = [...document.querySelectorAll('button')].filter(b => /save as routine/i.test(b.innerText || b.title || '') ).pop();
                if (!b) return null; b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()""")
            if btn:
                await asyncio.sleep(0.6)
                await s.click(at=btn, move=1.0)
                await asyncio.sleep(2.5)
            await s.js("openRoutinesPanel()")
            await asyncio.sleep(3.5)
            frames_to_mp4(await s.stop_recording(), self.clip("app-routine"))

    async def undo(self):
        """The activity log, and one click to put a change back."""
        async with Studio(width=W, height=H, scale=1.5, port=9486) as s:
            await self.fresh(s)
            await s.start_recording()
            await asyncio.sleep(0.5)
            await s.js("openActivityPanel()")
            await asyncio.sleep(2.0)
            btn = await s.js("""(() => { const b = [...document.querySelectorAll('#activityPanel button, .activity-panel button, button')].find(b => /^\\s*undo\\s*$/i.test(b.innerText) && b.offsetParent);
                if (!b) return null; const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()""")
            if btn:
                await s.move(btn[0], btn[1], 1.1)
                await asyncio.sleep(0.6)
                await s.click(at=btn, move=0.1)
            await asyncio.sleep(3)
            frames_to_mp4(await s.stop_recording(), self.clip("app-undo"))


def join(clips, out: Path, fade=0.45):
    """Crossfades the clips together into one 1080p video."""
    ins, parts = [], []
    durs = []
    for c in clips:
        ins += ["-i", str(c)]
        d = float(subprocess.run([ffmpeg(), "-i", str(c)], capture_output=True, text=True).stderr.split("Duration: ")[1].split(",")[0].split(":")[-1])
        durs.append(d)
    for i in range(len(clips)):
        parts.append(f"[{i}:v]scale=1920:1080:flags=lanczos,fps=30,format=yuv420p,setsar=1[v{i}]")
    last, offset = "v0", 0.0
    for i in range(1, len(clips)):
        offset += durs[i - 1] - fade
        parts.append(f"[{last}][v{i}]xfade=transition=fade:duration={fade}:offset={offset:.3f}[x{i}]")
        last = f"x{i}"
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", *ins, "-filter_complex", ";".join(parts), "-map", f"[{last}]",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                   check=True)
    return out


def gif(src: Path, out: Path, start=0.0, dur=None, width=880, fps=18):
    """A small looping GIF for the README (palette-optimized)."""
    pal = out.with_suffix(".palette.png")
    t = ["-ss", str(start)] + (["-t", str(dur)] if dur else [])
    vf = f"fps={fps},scale={width}:-1:flags=lanczos"
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", *t, "-i", str(src), "-vf", f"{vf},palettegen=stats_mode=diff", str(pal)], check=True)
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", *t, "-i", str(src), "-i", str(pal), "-lavfi",
                    f"{vf}[x];[x][1:v]paletteuse=dither=sierra2_4a:diff_mode=rectangle", "-loop", "0", str(out)], check=True)
    pal.unlink(missing_ok=True)
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8125")
    ap.add_argument("--out", default=str(HERE.parents[1] / "docs" / "media"))
    ap.add_argument("--work", default="/tmp/xod-launch-video")
    ap.add_argument("--only", default="cards,home,research,approval,routine,undo")
    a = ap.parse_args()
    work, out = Path(a.work), Path(a.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    sc = Scenes(a.url, work)
    for name in a.only.split(","):
        print("==", name)
        await getattr(sc, name)()

    c = sc.clip
    order = [c("card-intro"), c("card-private"), c("app-home"), c("card-research"),
             c("app-research-1"), c("app-research-2"), c("app-research-3"), c("app-research-4"),
             c("card-asks"), c("app-approval"), c("card-routines"), c("app-routine"),
             c("card-undo"), c("app-undo"), c("card-more"), c("card-end")]
    have = [p for p in order if p.exists()]
    if len(have) == len(order):
        join(have, out / "xod-launch.mp4")
        print("video:", out / "xod-launch.mp4")
    else:
        print("missing clips:", [p.name for p in order if not p.exists()])
    if (work / "moon-closeup.mp4").exists():
        gif(work / "moon-closeup.mp4", out / "moon.gif", start=1.5, width=600, fps=15)
    if c("app-research-3").exists():
        shutil.copy(c("app-research-3"), work / "answer-streaming.mp4")


if __name__ == "__main__":
    asyncio.run(main())
