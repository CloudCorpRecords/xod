#!/usr/bin/env python3
"""
Five vertical (9:16) videos, about a minute each, of things Xod is good at, for TikTok, Reels and Shorts:

  private    free, private AI on your own computer: it finds your model, knows your hardware, answers
  research   a question answered from the pages it read, with sources, then saved as a weekly routine
  tidy       a messy Downloads folder sorted in seconds, and one click to put it all back
  chores     a reply on a job board in its own browser, stopping for your OK before Send
  build      an app from one sentence: written, run, checked for errors, working

Everything is recorded from the real app (nothing mocked) on a demo copy of Xod with its own data folder:
waits are sped up and say so on screen, everything else plays in real time. Each video: a hook card, the
app in the middle with captions above it and the brand bar below, and a closing card with the download link.

  1. A demo Xod (never your own data):    XOD_DATA_DIR=<empty folder> python -m uvicorn app.main:app --port 8125
     For "tidy" also give it a demo home, so "my Downloads" is a demo folder: on Windows USERPROFILE=C:\\XodDemo and
     USERNAME=XodDemo, on a Mac HOME=/tmp/xod-demo-home, and fill its Downloads first:
       python scripts/media/make_use_case_videos.py --messy-downloads C:\\XodDemo\\Downloads
     Only for that Xod: Chrome won't start with a home like that, so don't set it where this script runs.
     "chores" starts the mock job board itself (tests/browser_missions/mock_jobs.py).
  2. python scripts/media/make_use_case_videos.py --only private,research --out docs/media/shorts

Needs Chrome and a model the demo Xod can use (it was made with Ollama and qwen2.5:14b on an RTX 3080 Ti).
"""
import argparse
import asyncio
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from studio import Studio, ffmpeg, frames_to_mp4  # noqa: E402

VERTICAL = (HERE / "vertical.html").as_uri()
OUT_W, OUT_H = 1080, 1920
APP_W, APP_H, APP_SCALE = 640, 800, 1.6875         # the app's window (CSS px) → 1080 × 1350 frames, 4:5; big enough to read on a phone
APP_BOX = (24, 340, 1032, 1290)                    # where the app sits in the frame (vertical.html: --app-*)
BADGE_AT = (52, 1540)                              # the "sped up" pill, over the app's bottom-left corner
FADE = 0.35

HOOKS = {
    "private": ("Free AI that runs on|*your own* computer.", "No account. No cloud.|No monthly bill."),
    "research": ("Stop opening|*20 tabs*.", "Xod reads the web for you,|and shows its sources."),
    "tidy": ("Your Downloads folder|is a *mess*.", "Xod sorts it in seconds.|And can undo it in one click."),
    "chores": ("Hand off|the *boring stuff*.", "Xod does it in its own browser,|and asks before it acts."),
    "build": ("Describe an app.|*Get an app.*", "Xod writes it, runs it and fixes it,|on your computer."),
}


# ---------------------------------------------------------------- stills and cards from vertical.html

class Kit:
    """Renders the frame, the corner mask, captions and badges (PNGs) and the animated cards (MP4s)."""
    def __init__(self, work: Path):
        self.work = work
        self.n = 0

    async def _png(self, scene: str, out: Path, clip=None, transparent=False):
        async with Studio(width=OUT_W, height=OUT_H, scale=1, cursor=False, port=9501) as s:
            if transparent:
                await s.cmd("Emulation.setDefaultBackgroundColorOverride", color={"r": 0, "g": 0, "b": 0, "a": 0})
            await s.goto(f"{VERTICAL}#{scene}", wait=1.6)
            if clip == "badge":
                b = await s.js("(() => { const r = document.getElementById('badgeT').getBoundingClientRect(); return [r.width, r.height]; })()")
                clip = {"x": 0, "y": 0, "width": int(b[0]) + 2, "height": int(b[1]) + 2}
            await s.shot(out, clip=clip)
        return out

    async def frame(self) -> Path:
        out = self.work / "frame.png"
        if not out.exists():
            await self._png("frame", out)
        return out

    async def mask(self) -> Path:
        out = self.work / "mask.png"
        if not out.exists():
            x, y, w, h = APP_BOX
            await self._png("mask", out, clip={"x": x, "y": y, "width": w, "height": h})
        return out

    async def caption(self, text: str) -> Path:
        self.n += 1
        out = self.work / f"cap-{self.n:02d}.png"
        await self._png("caption?t=" + urllib.parse.quote(text), out, clip={"x": 0, "y": 0, "width": OUT_W, "height": 330}, transparent=True)
        return out

    async def badge(self, text: str) -> Path:
        out = self.work / f"badge-{abs(hash(text)) % 10 ** 8}.png"
        if not out.exists():
            await self._png("badge?t=" + urllib.parse.quote(text), out, clip="badge", transparent=True)
        return out

    async def card(self, scene: str, out: Path, seconds: float) -> Path:
        async with Studio(width=OUT_W, height=OUT_H, scale=1, cursor=False, port=9502) as s:
            await s.goto("about:blank", wait=0.2)
            await s.start_recording()
            await s.cmd("Page.navigate", url=f"{VERTICAL}#{scene}")
            await asyncio.sleep(1.3)
            await s.js("window.XodMascot && XodMascot.beHappy && XodMascot.beHappy(1500); true")
            await asyncio.sleep(seconds - 1.3)
            frames_to_mp4(await s.stop_recording(), out, size=(OUT_W, OUT_H))
        return out


# ---------------------------------------------------------------- putting it together (ffmpeg)

def duration(p: Path) -> float:
    err = subprocess.run([ffmpeg(), "-i", str(p)], capture_output=True, text=True).stderr
    h, m, s = err.split("Duration: ")[1].split(",")[0].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def compose(app: Path, frame: Path, mask: Path, caption: Path, out: Path, badge: Optional[Path] = None) -> Path:
    """The app clip with rounded corners in the frame, the caption above it, and a badge if it's sped up."""
    x, y, w, h = APP_BOX
    ins = ["-loop", "1", "-i", str(frame), "-i", str(app), "-loop", "1", "-i", str(mask), "-loop", "1", "-i", str(caption)]
    fc = (f"[1:v]scale={w}:{h}:flags=lanczos,format=rgba[a];[2:v]scale={w}:{h},format=gray[m];[a][m]alphamerge[am];"
          f"[0:v][am]overlay={x}:{y}[b];[b][3:v]overlay=0:0[c]")
    last = "c"
    if badge:
        ins += ["-loop", "1", "-i", str(badge)]
        fc += f";[c][4:v]overlay={BADGE_AT[0]}:{BADGE_AT[1]}[d]"
        last = "d"
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", *ins, "-filter_complex", fc + f";[{last}]fps=30,format=yuv420p[v]",
                    "-map", "[v]", "-t", f"{duration(app):.3f}", "-c:v", "libx264", "-preset", "slow", "-crf", "19",
                    "-movflags", "+faststart", str(out)], check=True)
    return out


def join(clips: List[Path], out: Path, fade=FADE) -> Path:
    """Crossfades the 1080 × 1920 pieces together."""
    ins, parts, durs = [], [], [duration(c) for c in clips]
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


# ---------------------------------------------------------------- recording the app

class Take:
    """One piece of app recording and how it's shown: its caption, and how fast it plays."""
    def __init__(self, frames, caption: str, seconds: Optional[float] = None, speed: float = 1.0):
        self.frames, self.caption, self.seconds, self.speed = frames, caption, seconds, speed

    def raw(self) -> float:
        return (self.frames[-1][0] - self.frames[0][0] + 0.5) if self.frames else 0

    def play_speed(self) -> float:
        """At most `seconds` long on screen: faster if it has to be, never slower than real time."""
        if self.seconds and self.raw() > self.seconds:
            return max(self.speed, self.raw() / self.seconds)
        return self.speed


class Scenes:
    def __init__(self, url: str, work: Path):
        self.url, self.work = url.rstrip("/"), work

    def api(self, path, body=None):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
        return json.loads(urllib.request.urlopen(req, timeout=60).read())

    async def open_app(self, s: Studio, wizard=False):
        # A new demo opens its setup screen a moment after the page loads (over everything): mark setup done first.
        self.api("/api/setup/complete", {})
        await s.goto(self.url + "/", wait=0.5)
        await s.js("try { localStorage.setItem('xod.saidHello', '1') } catch (_) {} true")
        await s.goto(self.url + "/", wait=3.5)
        await s.js("document.getElementById('sidebarSessions')?.classList.contains('hidden') || toggleSidebar(); true")
        if not wizard:
            await s.js("document.getElementById('setupWizard')?.remove(); true")
        await asyncio.sleep(0.6)

    async def ask(self, s: Studio, text: str, cps=22):
        await s.click("#chatInput", move=0.8)
        await s.type(text, cps=cps)
        await asyncio.sleep(0.5)
        await s.key()

    async def until(self, s: Studio, expr: str, limit=900, every=0.5) -> float:
        t0 = time.time()
        while time.time() - t0 < limit:
            if await s.js(expr):
                break
            await asyncio.sleep(every)
        return time.time() - t0

    async def in_preview(self, s: Studio, expr: str):
        """Runs expr in the app preview's frame; a returned [x, y] is turned into page coordinates."""
        def frames(tree):
            yield tree["frame"]
            for child in tree.get("childFrames", []):
                yield from frames(child)
        tree = (await s.cmd("Page.getFrameTree"))["frameTree"]
        fr = next((f for f in frames(tree) if "/preview/" in f.get("url", "")), None)
        if not fr:
            print("   preview frame not found; frames:", [f.get("url", "")[:80] for f in frames(tree)])
            return None
        ctx = (await s.cmd("Page.createIsolatedWorld", frameId=fr["id"], worldName="studio"))["executionContextId"]
        r = await s.cmd("Runtime.evaluate", expression=expr, contextId=ctx, returnByValue=True)
        val = (r.get("result") or {}).get("value")
        if isinstance(val, list) and len(val) == 2:
            box = await s.js("(() => { const f = document.querySelector('iframe[src*=\"/preview/\"]'); const r = f.getBoundingClientRect(); return [r.left, r.top]; })()")
            return [box[0] + val[0], box[1] + val[1]]
        return val

    ANSWER_STARTED ="(() => { const p = [...document.querySelectorAll('.prose-custom')].pop(); return p && p.innerText.trim().length > 30 })()"
    DONE = "!(typeof isStreaming !== 'undefined' && isStreaming)"

    async def scroll_answer(self, s: Studio, to="end", speed_px=7):
        await s.js(f"""(async () => {{ const box = document.querySelector('#chatMessages'); if (!box) return true;
            const end = {'box.scrollHeight' if to == 'end' else to};
            for (let y = box.scrollTop; y < end; y += {speed_px}) {{ box.scrollTop = y; await new Promise(r => setTimeout(r, 16)); }}
            return true; }})()""")

    # ------------------------------------------------------------ the five videos

    async def private(self) -> List[Take]:
        """A new install connects itself, knows the hardware, answers on the computer."""
        takes = []
        self.api("/api/setup/complete", {})        # its setup screen is opened on purpose below, not by itself
        async with Studio(width=APP_W, height=APP_H, scale=APP_SCALE, port=9511) as s:
            await s.goto(self.url + "/", wait=0.3)
            await s.js("try { localStorage.setItem('xod.saidHello', '1') } catch (_) {} true")
            await s.start_recording()
            await s.goto(self.url + "/", wait=2.0)
            await s.js("document.getElementById('setupWizard')?.remove(); document.getElementById('sidebarSessions')?.classList.contains('hidden') || toggleSidebar(); true")
            # The real "Connected to Ollama" notice, now rather than in 5 s, and held long enough to read.
            await s.js("watchAutoconnect().then(() => { clearTimeout(showToast._t); showToast._t = setTimeout(() => document.getElementById('xodToast')?.classList.add('hidden'), 6500); }); true")
            await asyncio.sleep(1.0)
            await s.move(205, 27, 1.2)                                       # towards "Ready · Ollama" in the header
            await asyncio.sleep(4.5)
            takes.append(Take(await s.stop_recording(), "Open it. It finds the AI|on your PC *by itself*.", seconds=8))

            # The setup screen takes a few seconds to look around the computer: open it first, record once it shows.
            await s.js("openSetupWizard(); true")
            await self.until(s, "!!document.querySelector('#setupWizard [data-setup=pick], #setupWizard [data-setup=ol-pull]')", 30)
            await asyncio.sleep(0.5)
            await s.start_recording()
            await asyncio.sleep(1.0)
            chips = await s.js("""(() => { const c = [...document.querySelectorAll('#setupWizard span')].find(e => /GB/.test(e.textContent) && e.offsetParent);
                if (!c) return null; const r = c.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()""")
            await s.move(*(chips or (APP_W / 2, 200)), 0.8)          # the GPU and memory it found
            await asyncio.sleep(3.0)
            await s.js("""(async () => { const box = document.querySelector('#setupWizard > div'); if (!box) return true;
                const h = [...box.querySelectorAll('h3, p')].find(e => /Get a model/.test(e.textContent));
                const end = h ? h.offsetTop - 40 : 520;
                for (let y = 0; y < end; y += 6) { box.scrollTop = y; await new Promise(r => setTimeout(r, 16)); } return true; })()""")
            await asyncio.sleep(3.2)
            takes.append(Take(await s.stop_recording(), "It reads your hardware|and picks models that *fit*.", seconds=11))

            # Want a smarter one? The recommended model starts downloading for real; stopped after a few seconds
            # (Ollama keeps the part it has; downloading again picks up there).
            await s.start_recording()
            pull = await s.js("""(() => { const b = [...document.querySelectorAll('#setupWizard [data-setup=ol-pull]')]
                .find(b => b.closest('.border-brand-500\\\\/60')) || document.querySelector('#setupWizard [data-setup=ol-pull]');
                if (!b) return null; b.scrollIntoView({block: 'center', behavior: 'smooth'}); return b.dataset.name; })()""")
            await asyncio.sleep(1.2)
            pulled = None
            if pull:
                at = await s.js(f"""(() => {{ const b = document.querySelector('#setupWizard [data-setup=ol-pull][data-name="{pull}"]');
                    const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()""")
                await s.click(at=at, move=0.9)
                pulled = pull
                await asyncio.sleep(7.0)
            takes.append(Take(await s.stop_recording(), "Want a smarter one?|*One click* downloads it.", seconds=9))
            if pulled:
                await s.js(f"fetch('/api/ollama/pull/cancel', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{name: '{pulled}'}})}}); true")
                print("private: started, then stopped, downloading", pulled)

            await s.js("document.querySelector('[data-setup=close]')?.click(); true")
            await asyncio.sleep(1.0)
            await s.start_recording()
            await self.ask(s, "Write a short, friendly text to my landlord: the kitchen sink is leaking again.")
            await self.until(s, self.ANSWER_STARTED, 300)
            takes.append(Take(await s.stop_recording(), "Ask it *anything*.", seconds=9))
            await s.start_recording()
            await self.until(s, self.DONE, 300)
            await asyncio.sleep(2.5)
            await s.move(205, 27, 1.0)
            await asyncio.sleep(2.5)
            takes.append(Take(await s.stop_recording(), "It answers right on your PC.|*Nothing leaves it.*", seconds=12))
        return takes

    async def research(self) -> List[Take]:
        takes = []
        q = "Compare the Sony WH-1000XM6 and Bose QuietComfort Ultra headphones: price, battery life and noise cancelling."
        async with Studio(width=APP_W, height=APP_H, scale=APP_SCALE, port=9512) as s:
            await self.open_app(s)
            await s.start_recording()
            await self.ask(s, q, cps=28)
            await asyncio.sleep(1.5)
            takes.append(Take(await s.stop_recording(), "Ask a *real* question.", seconds=8))
            await s.move(APP_W - 180, 300, 0.6)
            await s.start_recording()
            await self.until(s, self.ANSWER_STARTED, 900)
            takes.append(Take(await s.stop_recording(), "It searches and reads|the pages *itself*.", seconds=9))
            await s.start_recording()
            await self.until(s, self.DONE, 900)
            await asyncio.sleep(2.0)
            takes.append(Take(await s.stop_recording(), "Then answers from|what it *actually read*.", seconds=10))
            await s.start_recording()
            await s.move(APP_W / 2, APP_H / 2, 0.5)
            await self.scroll_answer(s, speed_px=3)
            # Point at the sites it read.
            at = await s.js("""(() => { const a = [...document.querySelectorAll('.prose-custom a, a[href^="http"]')].filter(a => a.offsetParent).pop();
                if (!a) return null; const r = a.getBoundingClientRect(); return [r.left + Math.min(r.width / 2, 60), r.top + r.height / 2]; })()""")
            if at:
                await s.move(at[0], at[1], 1.0)
            await asyncio.sleep(2.5)
            takes.append(Take(await s.stop_recording(), "Every answer shows|its *sources*.", seconds=9))
            sid = await s.js("currentSessionId")

            # Turn it into a routine: every Monday at 8.
            btn = await s.js("""(() => { const b = [...document.querySelectorAll('button')].filter(b => /save as routine/i.test(b.innerText || '')).pop();
                if (!b) return null; b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()""")
            await s.start_recording()
            if btn:
                await s.click(at=btn, move=0.8)
                await asyncio.sleep(1.4)
                try:
                    await s.click("#rtKind", move=0.6)
                    await s.js("(() => { const k = document.getElementById('rtKind'); k.value = 'weekly'; k.dispatchEvent(new Event('change', {bubbles: true})); })()")
                    await asyncio.sleep(0.6)
                    await s.click("#rtDays input[value='mon']", move=0.5)
                    await s.js("(() => { const t = document.getElementById('rtTime'); t.value = '08:00'; t.dispatchEvent(new Event('change', {bubbles: true})); })()")
                    await asyncio.sleep(0.8)
                    await s.click("[data-ed='save']", move=0.6)
                except RuntimeError as e:
                    print("research: routine form:", e)
                await asyncio.sleep(1.8)
            takes.append(Take(await s.stop_recording(), "Want it every Monday?|Make it a *routine*.", seconds=9))
            print("research: session", sid, "routine button found:", bool(btn))

            # A follow-up, like you'd ask a friend.
            await s.start_recording()
            await self.ask(s, "Which one is better for long flights?", cps=18)
            await self.until(s, "document.querySelectorAll('.prose-custom').length > 1 && " + self.DONE, 600)
            await asyncio.sleep(1.0)
            await self.scroll_answer(s, speed_px=3)
            await asyncio.sleep(2.5)
            takes.append(Take(await s.stop_recording(), "Then just ask|a *follow-up*.", seconds=13))
        return takes

    async def tidy(self) -> List[Take]:
        takes = []
        async with Studio(width=APP_W, height=APP_H, scale=APP_SCALE, port=9513) as s:
            await self.open_app(s)
            await s.start_recording()
            await self.ask(s, "What's in my Downloads folder?", cps=20)
            await self.until(s, self.DONE + " && " + self.ANSWER_STARTED, 300)
            await asyncio.sleep(1.0)
            await self.scroll_answer(s)
            await asyncio.sleep(1.2)
            takes.append(Take(await s.stop_recording(), "25 random files.|*Sound familiar?*", seconds=12))
            await s.start_recording()
            await self.ask(s, "Tidy it up into folders.", cps=18)
            await self.until(s, self.DONE + " && document.querySelectorAll('.prose-custom').length > 1 && " + self.ANSWER_STARTED, 300)
            await asyncio.sleep(1.5)
            await self.scroll_answer(s)
            await asyncio.sleep(1.5)
            takes.append(Take(await s.stop_recording(), "Then: *tidy it up*.|Sorted in seconds.", seconds=13))

            await s.start_recording()
            await s.js("openActivityPanel(); true")
            await asyncio.sleep(2.2)
            btn = await s.js("""(() => { const b = [...document.querySelectorAll('button')].find(b => /^\\s*undo\\s*$/i.test(b.innerText) && b.offsetParent);
                if (!b) return null; const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()""")
            if btn:
                await s.move(btn[0], btn[1], 1.1)
                await asyncio.sleep(0.6)
                await s.click(at=btn, move=0.1)
            await asyncio.sleep(3.0)
            takes.append(Take(await s.stop_recording(), "Changed your mind?|One click puts it *all back*.", seconds=9))
            print("tidy: undo button found:", bool(btn))
        return takes

    async def chores(self) -> List[Take]:
        takes = []
        mock = subprocess.Popen([sys.executable, str(HERE.parents[1] / "tests" / "browser_missions" / "mock_jobs.py")],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(2)
            request = ("Reply to Dana at Acme on the job board (http://localhost:8799/messages): \"Hi Dana, thanks for the call "
                       "today. I'm excited about the role and happy to share more examples.\"")
            async with Studio(width=APP_W, height=APP_H, scale=APP_SCALE, port=9514) as s:
                await self.open_app(s)
                await s.start_recording()
                await s.js("openTasksPanel(); true")
                await asyncio.sleep(1.2)
                await s.click("#newTaskInput", move=0.8)
                await s.type(request, cps=30)
                await asyncio.sleep(0.5)
                await s.js("createTaskFromPanel(); true")
                await asyncio.sleep(2.0)
                takes.append(Take(await s.stop_recording(), "Give it a *chore*.", seconds=10))

                await s.start_recording()
                waited = await self.until(s, "(async () => { const r = await fetch('/api/tasks').then(r => r.json()); "
                                             "return (r.tasks || r).some(t => ['waiting', 'done', 'failed'].includes(t.status)); })()", 900, 2)
                await asyncio.sleep(2.0)
                takes.append(Take(await s.stop_recording(), "It does the work|in *its own browser*.", seconds=8, speed=3))
                print(f"chores: waiting for OK after {waited:.0f}s")

                await s.start_recording()
                await s.js("openTasksPanel(); true")
                await asyncio.sleep(1.5)
                btn = await s.js("""(() => { const b = [...document.querySelectorAll('button')].find(b => /approve|send it|go ahead|yes/i.test(b.innerText) && b.offsetParent);
                    if (!b) return null; b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()""")
                if btn:
                    await s.move(btn[0], btn[1], 1.4)
                    await asyncio.sleep(1.8)
                takes.append(Take(await s.stop_recording(), "Before anything is sent,|it *asks you*.", seconds=8))

                await s.start_recording()
                if btn:
                    await s.click(at=btn, move=0.1)
                await self.until(s, "(async () => { const r = await fetch('/api/tasks').then(r => r.json()); "
                                    "return (r.tasks || r).some(t => ['done', 'failed'].includes(t.status)); })()", 600, 2)
                await asyncio.sleep(2.5)
                takes.append(Take(await s.stop_recording(), "You say OK. *Done.*", seconds=8, speed=2))
            sent = json.loads(urllib.request.urlopen("http://localhost:8799/admin/state").read()).get("messages") or []
            print("chores: approve button found:", bool(btn), "| messages sent:", len(sent))
        finally:
            mock.terminate()
        return takes

    async def build(self) -> List[Take]:
        takes = []
        q = "Build me a pomodoro timer app: 25 minute focus, 5 minute break, start and reset buttons, a nice dark design."
        # The preview may be another origin: keep it in this page's process, so its button can be found and pressed.
        async with Studio(width=APP_W, height=APP_H, scale=APP_SCALE, port=9515,
                          chrome_args=["--disable-site-isolation-trials", "--disable-features=IsolateOrigins,site-per-process"]) as s:
            await self.open_app(s)
            await s.start_recording()
            await self.ask(s, q, cps=28)
            await asyncio.sleep(1.5)
            takes.append(Take(await s.stop_recording(), "Say what you *want*.", seconds=8))
            await s.start_recording()
            await self.until(s, "!!document.querySelector('iframe[src*=\"/preview/\"]')", 600)
            takes.append(Take(await s.stop_recording(), "It writes the code…", seconds=8, speed=3))
            await s.start_recording()
            await self.until(s, self.DONE, 600)
            await asyncio.sleep(1.5)
            takes.append(Take(await s.stop_recording(), "…runs it, checks for errors|and *fixes them*.", seconds=15, speed=1.5))

            # The code itself: the preview's Files tab, then back to the app.
            tab = """(name) => { const b = [...document.querySelectorAll('button, [role=tab], a')].find(b => b.offsetParent && b.innerText.trim() === name);
                if (!b) return null; const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }"""
            files = await s.js(f"({tab})('Files')")
            await s.start_recording()
            if files:
                await s.click(at=files, move=0.9)
                await asyncio.sleep(1.5)
                code = await s.js("""(() => { const f = [...document.querySelectorAll('button, li, a, div')].find(e => e.offsetParent && /^main\\.js/.test(e.innerText.trim()) && e.children.length < 4);
                    if (!f) return null; const r = f.getBoundingClientRect(); return [r.left + Math.min(r.width / 2, 50), r.top + r.height / 2]; })()""")
                if code:
                    await s.click(at=code, move=0.8)
                await asyncio.sleep(3.5)
            takes.append(Take(await s.stop_recording(), "Real code.|*Yours* to keep.", seconds=8))
            back = await s.js(f"({tab})('Preview')")
            if back:
                await s.click(at=back, move=0.5)
                await asyncio.sleep(1.5)

            # Use it: press Start in the preview, watch the clock tick. The preview is a sandboxed frame the page can't
            # look into, so its button is found through Chrome itself (an isolated world in that frame).
            await s.start_recording()
            frame = await self.in_preview(s, "(() => { const b = [...document.querySelectorAll('button')].find(b => /start/i.test(b.innerText));"
                                             " if (!b) return null; const q = b.getBoundingClientRect(); return [q.left + q.width / 2, q.top + q.height / 2]; })()")
            if frame:
                await s.click(at=frame, move=1.0)
            await asyncio.sleep(7.0)
            takes.append(Take(await s.stop_recording(), "And it *works*.", seconds=9))
            print("build: Files tab:", bool(files), "| start button found:", bool(frame))
        return takes


async def make(name: str, sc: Scenes, kit: Kit, out_dir: Path) -> Path:
    work = kit.work / name
    work.mkdir(parents=True, exist_ok=True)
    print(f"== {name}: recording", flush=True)
    takes = await getattr(sc, name)()
    frame, mask = await kit.frame(), await kit.mask()
    pieces = [await kit.card("hook?h=" + urllib.parse.quote(HOOKS[name][0]) + "&s=" + urllib.parse.quote(HOOKS[name][1]),
                             work / "hook.mp4", 3.8)]
    for i, t in enumerate(takes, 1):
        if not t.frames:
            print(f"   take {i}: nothing recorded, skipped")
            continue
        sp = t.play_speed()
        raw = work / f"take{i}.mp4"
        frames_to_mp4(t.frames, raw, size=(int(APP_W * APP_SCALE), int(APP_H * APP_SCALE)), speed=sp)
        badge = await kit.badge(f"⏩ sped up {round(sp)}×") if sp >= 1.8 else None
        pieces.append(compose(raw, frame, mask, await kit.caption(t.caption), work / f"piece{i}.mp4", badge))
        print(f"   take {i}: {t.raw():.0f}s recorded → {duration(raw):.1f}s on screen ({sp:.1f}×) “{t.caption}”", flush=True)
    pieces.append(await kit.card("end", work / "end.mp4", 6.5))
    out = out_dir / f"xod-{name}.mp4"
    join(pieces, out)
    print(f"== {name}: {out} ({duration(out):.1f}s)", flush=True)
    return out


MESSY = {   # name: KB
    "Invoice-March-2026.pdf": 180, "Invoice-April-2026.pdf": 175, "lease-agreement-signed.pdf": 920, "boarding-pass-LIS.pdf": 90,
    "resume_final_v3.docx": 64, "meeting-notes.txt": 4, "tax-return-2025.pdf": 640, "IMG_2041.jpg": 3400, "IMG_2042.jpg": 3100,
    "IMG_2058.HEIC": 2800, "beach-sunset.png": 5200, "logo-draft.svg": 22, "Screenshot 2026-10-02 at 14.11.03.png": 610,
    "Screenshot 2026-10-09 at 09.40.51.png": 540, "budget-2026.xlsx": 48, "expenses-q3.csv": 12, "pitch-deck.pptx": 8800,
    "family-trip.mp4": 48000, "podcast-ep12.mp3": 31000, "project-files.zip": 22000, "photos-backup.7z": 64000,
    "ChromeSetup.exe": 1300, "VSCodeUserSetup-x64.exe": 98000, "node-v22-x64.msi": 31000, "notes-app.py": 6,
}


def messy_downloads(folder: Path) -> None:
    """A messy Downloads folder for the "tidy" video: placeholder files (sparse, so almost no disk) with real-looking
    names, sizes and dates. Refuses a folder that already has anything in it, so it can't be pointed at a real one."""
    import os
    import random
    folder.mkdir(parents=True, exist_ok=True)
    if any(folder.iterdir()):
        sys.exit(f"{folder} isn't empty: give --messy-downloads a new folder (the demo home's Downloads)")
    random.seed(7)
    for name, kb in MESSY.items():
        p = folder / name
        with open(p, "wb") as f:
            f.truncate(kb * 1024)
        t = time.time() - random.uniform(2, 120) * 86400
        os.utime(p, (t, t))
    print(f"{len(MESSY)} files in {folder}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--messy-downloads", default="", help="make a messy demo Downloads folder there (must be empty) and stop")
    ap.add_argument("--url", default="http://127.0.0.1:8125")
    ap.add_argument("--out", default=str(HERE.parents[1] / "docs" / "media" / "shorts"))
    ap.add_argument("--work", default=str(Path.home() / ".xod-shorts-work"))
    ap.add_argument("--only", default="private,research,tidy,chores,build")
    a = ap.parse_args()
    if a.messy_downloads:
        return messy_downloads(Path(a.messy_downloads))
    out, work = Path(a.out), Path(a.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    sc, kit = Scenes(a.url, work), Kit(work)
    for name in a.only.split(","):
        await make(name.strip(), sc, kit, out)


if __name__ == "__main__":
    asyncio.run(main())
