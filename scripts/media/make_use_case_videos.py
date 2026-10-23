#!/usr/bin/env python3
"""
Five vertical (9:16) videos, about a minute each, of things Xod is good at, for TikTok, Reels and Shorts:

  private    free, private AI on your own computer: it finds your model, knows your hardware, answers
  research   a question answered from the pages it read, with sources, then saved as a weekly routine
  tidy       a messy Downloads folder sorted in seconds, and one click to put it all back
  chores     a reply on a job board in its own browser, stopping for your OK before Send
  build      an app from one sentence: written, run, checked for errors, working
  hired      "find me jobs", "do it all": your résumé found, an answer written for each job, your OK, sent
  trap       a job post with instructions for AI hidden in it; Xod catches them and ignores them
  forms      a job profile that fills itself in from your résumé, then the real application filled in its browser
  apps       your Mac's own apps: Abilities, places from Apple Maps, and a text that waits for your OK (declined on camera)
  agents     "have Claude Code fix the failing tests": a job in its own copy of the repo, each command OK'd, the diff, merged.
             Needs Claude Code signed in, and a demo repo at ~/code/tip-calc with failing tests (your home folder shows as ~)
  models     the model switcher: the free built-in model answers, its memory is freed in one click, then a cloud model
             (the top bar names the model in every frame). Needs the built-in Qwen 3.5 4B downloaded and a cloud key saved

hired, trap and forms run on the practice careers site against the demo Xod from demo/run.py (made-up companies,
tagged Practice; nothing goes to a real company), with the made-up résumé:
  python3 demo/run.py --sample --offline        # then, in another terminal:
  python scripts/media/make_use_case_videos.py --url http://127.0.0.1:8020 --only hired,trap,forms

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
    "hired": ("I applied to *3 jobs*|before my coffee was ready.", "One sentence. Xod did the rest,|and asked before sending."),
    "trap": ("This job post tries|to *hack your AI*.", "Watch what Xod does."),
    "forms": ("Never fill out|a job application *again*.", "Xod fills it in from your résumé.|You just check it."),
    "apps": ("Your AI should know|*your Mac's apps*.", "Maps, Messages, Calendar,|Reminders, Contacts, Home."),
    "agents": ("Hand a bug to|*Claude Code*.", "It works in its own copy.|You review, then merge."),
    "models": ("One app.|*Any AI you want.*", "Free on your own Mac, or a cloud|model with your own key."),
}
SITE = "http://127.0.0.1:8765"          # the practice careers site (demo/careers)


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


def compose(app: Path, frame: Path, mask: Path, caption: Path, out: Path, badge: Optional[Path] = None,
            note: Optional[Path] = None) -> Path:
    """The app clip with rounded corners in the frame, the caption above it, a badge if it's sped up, and a note in the
    other corner (which model answered, when it isn't one on this computer)."""
    x, y, w, h = APP_BOX
    ins = ["-loop", "1", "-i", str(frame), "-i", str(app), "-loop", "1", "-i", str(mask), "-loop", "1", "-i", str(caption)]
    fc = (f"[1:v]scale={w}:{h}:flags=lanczos,format=rgba[a];[2:v]scale={w}:{h},format=gray[m];[a][m]alphamerge[am];"
          f"[0:v][am]overlay={x}:{y}[b];[b][3:v]overlay=0:0[c]")
    last = "c"
    if badge:
        idx = sum(1 for a in ins if a == "-i")
        ins += ["-loop", "1", "-i", str(badge)]
        fc += f";[{last}][{idx}:v]overlay={BADGE_AT[0]}:{BADGE_AT[1]}[d]"
        last = "d"
    if note:
        from PIL import Image
        nw = Image.open(note).size[0]
        idx = sum(1 for a in ins if a == "-i")
        ins += ["-loop", "1", "-i", str(note)]
        fc += f";[{last}][{idx}:v]overlay={x + w - nw - 28}:{BADGE_AT[1]}[n]"
        last = "n"
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
    def __init__(self, url: str, work: Path, model_note: str = "", size=(APP_W, APP_H, APP_SCALE)):
        self.url, self.work, self.model_note = url.rstrip("/"), work, model_note
        self.w, self.h, self.scale = size          # the app's window: phone-shaped for the shorts, wide for the film

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
        async with Studio(width=self.w, height=self.h, scale=self.scale, port=9511) as s:
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
            await s.move(*(chips or (self.w / 2, 200)), 0.8)          # the GPU and memory it found
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
        async with Studio(width=self.w, height=self.h, scale=self.scale, port=9512) as s:
            await self.open_app(s)
            await s.start_recording()
            await self.ask(s, q, cps=28)
            await asyncio.sleep(1.5)
            takes.append(Take(await s.stop_recording(), "Ask a *real* question.", seconds=8))
            await s.move(self.w - 180, 300, 0.6)
            await s.start_recording()
            await self.until(s, self.ANSWER_STARTED, 900)
            takes.append(Take(await s.stop_recording(), "It searches and reads|the pages *itself*.", seconds=9))
            await s.start_recording()
            await self.until(s, self.DONE, 900)
            await asyncio.sleep(2.0)
            takes.append(Take(await s.stop_recording(), "Then answers from|what it *actually read*.", seconds=10))
            await s.start_recording()
            await s.move(self.w / 2, self.h / 2, 0.5)
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
        async with Studio(width=self.w, height=self.h, scale=self.scale, port=9513) as s:
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
            async with Studio(width=self.w, height=self.h, scale=self.scale, port=9514) as s:
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
        async with Studio(width=self.w, height=self.h, scale=self.scale, port=9515,
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


    # ------------------------------------------------------------ the job videos (practice careers site)

    def fresh_take(self) -> None:
        """Each job video starts clean: no profile, tasks, results or received applications; the made-up résumé."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("demo_run", HERE.parents[1] / "demo" / "run.py")
        demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)
        demo.reset()
        demo.sample_resume()

    TASKS_SETTLED = ("(async () => { const r = await fetch('/api/tasks').then(r => r.json()); const t = r.tasks || r;"
                     " return t.length > 0 && t.every(x => ['waiting', 'done', 'failed'].includes(x.status)); })()")
    TASKS_DONE = ("(async () => { const r = await fetch('/api/tasks').then(r => r.json()); const t = r.tasks || r;"
                  " return t.length > 0 && t.every(x => ['done', 'failed'].includes(x.status)); })()")

    # Xod's own browser, big: the screenshot of each step it takes (from /api/browser/mission/live, as the Cockpit shows
    # it, without the Cockpit's developer buttons around it), and what it's doing.
    LIVE_BROWSER = """(() => { const d = document.createElement('div'); d.id = 'studioLive';
        d.style.cssText = 'position:fixed;inset:56px 10px 10px;z-index:90;background:#0b0d18;border:1px solid rgba(255,255,255,.1);border-radius:16px;display:flex;flex-direction:column;overflow:hidden';
        d.innerHTML = '<div style="display:flex;align-items:center;gap:8px;padding:10px 14px;color:#e4e4e7;font:600 13px system-ui"><span style="width:8px;height:8px;border-radius:9px;background:#34d399;box-shadow:0 0 8px #34d399"></span>Xod\\'s browser · live</div>'
          + '<div style="flex:1;display:grid;place-items:center;background:#fff;overflow:hidden"><img id="studioLiveImg" style="width:100%;height:100%;object-fit:contain"></div>'
          + '<div id="studioLiveNow" style="padding:10px 14px;color:#c8e6b4;font:500 12.5px system-ui;min-height:38px"></div>';
        document.body.appendChild(d);
        window.__live = setInterval(async () => { try { const m = await fetch('/api/browser/mission/live').then(r => r.json());
          const st = (m.steps || []).filter(x => x.screenshot_uri).pop(); if (st) document.getElementById('studioLiveImg').src = st.screenshot_uri;
          const last = (m.steps || []).pop(); if (last) document.getElementById('studioLiveNow').textContent = 'Step ' + last.step_num + ': ' + String(last.reasoning || last.action).slice(0, 110);
        } catch (_) {} }, 700); return true; })()"""

    def approve_all(self) -> int:
        n = 0
        for t in self.api("/api/tasks").get("tasks", []):
            if t["status"] == "waiting" and t.get("waiting_kind") == "approval":
                self.api(f"/api/tasks/{t['id']}/approve", {})
                n += 1
        return n

    async def approve_on_screen(self, s: Studio) -> bool:
        """Moves to the first Approve button in Tasks and presses it (the rest are approved the same way, off screen)."""
        btn = await s.js("""(() => { const b = [...document.querySelectorAll('button')].find(b => /approve|go ahead/i.test(b.innerText) && b.offsetParent);
            if (!b) return null; b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()""")
        if btn:
            await s.move(btn[0], btn[1], 1.2)
            await asyncio.sleep(1.2)
            await s.click(at=btn, move=0.1)
        return bool(btn)

    async def received(self, s: Studio, scroll_to: str = "") -> None:
        """The practice site's inbox: what was really submitted."""
        await s.goto(SITE + "/received", wait=1.5)
        await s.js(f"""(async () => {{ const end = {scroll_to or 'document.body.scrollHeight * 0.45'};
            for (let y = 0; y < end; y += 6) {{ window.scrollTo(0, y); await new Promise(r => setTimeout(r, 16)); }} return true; }})()""")

    async def hired(self) -> List[Take]:
        self.fresh_take()
        takes = []
        async with Studio(width=self.w, height=self.h, scale=self.scale, port=9516) as s:
            await self.open_app(s)
            await s.start_recording()
            await self.ask(s, "find me forward deployed engineer jobs", cps=20)
            await self.until(s, "!!document.querySelector('.jobs-card')", 120)
            await asyncio.sleep(2.5)
            takes.append(Take(await s.stop_recording(), "Real search.|*Practice* job site.", seconds=9))

            await s.start_recording()
            await self.ask(s, "apply to all", cps=16)
            await self.until(s, "!!document.querySelector('.jp-wrap label.is-filled')", 180)
            await asyncio.sleep(3.0)
            takes.append(Take(await s.stop_recording(), "It finds my résumé|and *fills in my profile*.", seconds=9, speed=2))

            await s.start_recording()
            await s.js("document.querySelector('.jp-wrap')?.remove(); true")
            await self.ask(s, "do it all", cps=14)
            await self.until(s, "/Started \\d+ application/.test([...document.querySelectorAll('.prose-custom')].pop()?.innerText || '')", 300, 1)
            await asyncio.sleep(1.0)
            await self.scroll_answer(s)
            await asyncio.sleep(3.0)
            takes.append(Take(await s.stop_recording(), "\"Do it all.\"|An answer *for every job*.", seconds=11, speed=2))

            await s.start_recording()
            await s.js("openTasksPanel(); true")
            waited = await self.until(s, self.TASKS_SETTLED, 900, 2)
            await asyncio.sleep(2.0)
            takes.append(Take(await s.stop_recording(), "It fills in each one|in *its own browser*.", seconds=9, speed=4))
            print(f"hired: applications ready after {waited:.0f}s")

            await s.start_recording()
            pressed = await self.approve_on_screen(s)
            await asyncio.sleep(0.8)
            n = self.approve_all()
            await asyncio.sleep(2.0)
            takes.append(Take(await s.stop_recording(), "Nothing is sent|until *I say OK*.", seconds=7))
            await self.until(s, self.TASKS_DONE, 600, 2)

            await s.start_recording()
            await self.received(s)
            await asyncio.sleep(2.5)
            takes.append(Take(await s.stop_recording(), "*Sent*, with my résumé|and a real answer.", seconds=9))
            print("hired: approve pressed on screen:", pressed, "| approved:", n + int(pressed))
        return takes

    async def trap(self) -> List[Take]:
        self.fresh_take()
        takes = []
        async with Studio(width=self.w, height=self.h, scale=self.scale, port=9517) as s:
            # The job post, as anyone would see it…
            await s.goto(SITE + "/jobs/atlas-fde", wait=1.5)
            await s.start_recording()
            await s.js("""(async () => { const t = document.querySelector('.trap'); const end = t.getBoundingClientRect().top + scrollY - 260;
                for (let y = 0; y < end; y += 3) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 16)); } return true; })()""")
            await asyncio.sleep(3.0)
            takes.append(Take(await s.stop_recording(), "A normal-looking|*job post*.", seconds=7))
            # …and the text that's there for AI only (white, one pixel tall), made visible.
            await s.start_recording()
            at = await s.js("""(() => { const t = document.querySelector('.trap'); const r = t.getBoundingClientRect(); return [r.left + 60, r.top]; })()""")
            await s.move(at[0], at[1], 1.0)
            await s.js("""(() => { const t = document.querySelector('.trap'); t.style.transition = 'all .6s'; t.scrollIntoView({block: 'center', behavior: 'smooth'});
                Object.assign(t.style, {color: '#c4001a', fontSize: '15px', lineHeight: '1.45', background: '#ffe9ec', padding: '10px 12px',
                borderRadius: '8px', border: '2px dashed #c4001a', margin: '10px 0'}); return true; })()""")
            await asyncio.sleep(5.5)
            takes.append(Take(await s.stop_recording(), "Hidden in it: *orders*|*for AI agents*.", seconds=8))

            await self.open_app(s)
            await s.start_recording()
            await self.ask(s, "find me forward deployed engineer jobs", cps=22)
            await self.until(s, "!!document.querySelector('.jobs-card')", 120)
            await asyncio.sleep(1.0)
            await self.ask(s, "apply to the Atlas Freight one", cps=20)
            await self.until(s, "!!document.querySelector('.jp-wrap label.is-filled')", 180)
            await asyncio.sleep(1.0)
            await s.js("document.querySelector('.jp-wrap')?.remove(); true")
            await self.ask(s, "do it all", cps=14)
            await self.until(s, "/Started \\d+ application/.test([...document.querySelectorAll('.prose-custom')].pop()?.innerText || '')", 300, 1)
            await asyncio.sleep(1.5)
            takes.append(Take(await s.stop_recording(), "I ask Xod|to *apply*.", seconds=9, speed=2.5))

            await s.start_recording()
            await s.js("openTasksPanel(); true")
            caught = await self.until(s, "/hides instructions for AI/.test(document.body.innerText) || (async () => { const r = await fetch('/api/tasks').then(r => r.json());"
                                         " return (r.tasks || r).some(t => (t.history || []).some(h => /hides instructions for AI/.test(h.text || ''))); })()", 600, 1)
            await asyncio.sleep(1.0)
            hit = await s.js("""(() => { const all = [...document.querySelectorAll('#tasksPanel *, [id*=task] *')].filter(e => e.offsetParent && e.children.length === 0 && /hides instructions for AI/.test(e.textContent));
                const e = all[0]; if (!e) return null; e.scrollIntoView({block: 'center'}); e.style.transition = 'background .4s'; e.style.background = 'rgba(147,209,127,.18)';
                const r = e.getBoundingClientRect(); return [r.left + 30, r.top + r.height / 2]; })()""")
            if not hit:     # not shown in the list yet: open the task to its steps
                await s.js("""(() => { document.querySelectorAll('details').forEach(d => { if (/How it went/.test(d.innerText) && /Atlas/.test(d.parentElement?.innerText || '')) d.open = true; }); return true; })()""")
                await asyncio.sleep(1.5)
                hit = await s.js("""(() => { const e = [...document.querySelectorAll('*')].filter(e => e.offsetParent && e.children.length === 0 && /hides instructions for AI/.test(e.textContent))[0];
                    if (!e) return null; e.scrollIntoView({block: 'center'}); e.style.background = 'rgba(147,209,127,.18)'; const r = e.getBoundingClientRect(); return [r.left + 30, r.top + r.height / 2]; })()""")
            if hit:
                await s.move(hit[0], hit[1], 1.0)
            await asyncio.sleep(4.5)
            takes.append(Take(await s.stop_recording(), "Xod *catches it*,|and ignores it.", seconds=9))
            print(f"trap: caught after {caught:.0f}s | shown on screen: {bool(hit)}")

            await self.until(s, self.TASKS_SETTLED, 900, 2)
            self.approve_all()
            await self.until(s, self.TASKS_DONE, 600, 2)
            await s.start_recording()
            await self.received(s, "300")
            await asyncio.sleep(1.0)
            leaked = await s.js("/talent-desk/.test(document.body.innerText)")
            await asyncio.sleep(2.5)
            takes.append(Take(await s.stop_recording(), "My résumé went|*only where I said*.", seconds=8))
            print("trap: trap address in what was sent:", leaked)
        return takes

    async def forms(self) -> List[Take]:
        self.fresh_take()
        takes = []
        async with Studio(width=self.w, height=self.h, scale=self.scale, port=9518) as s:
            await self.open_app(s)
            # Something to apply to, off camera.
            await self.ask(s, "find me forward deployed engineer jobs", cps=60)
            await self.until(s, "!!document.querySelector('.jobs-card')", 120)
            await s.start_recording()
            await s.js("XodJobs.openProfile([]); true")
            await asyncio.sleep(1.2)
            await s.js("""(async () => { const f = document.querySelector('.jp'); for (let y = 0; y < 260; y += 4) { f.scrollTop = y; await new Promise(r => setTimeout(r, 16)); }
                for (let y = 260; y > 0; y -= 8) { f.scrollTop = y; await new Promise(r => setTimeout(r, 16)); } return true; })()""")
            await asyncio.sleep(1.0)
            takes.append(Take(await s.stop_recording(), "The same boxes.|*Every. Single. Time.*", seconds=6))

            await s.start_recording()
            await s.click(".jp [data-auto]", move=0.9)
            await self.until(s, "document.querySelectorAll('.jp label.is-filled').length >= 6", 120)
            await asyncio.sleep(1.0)
            await s.js("""(async () => { const f = document.querySelector('.jp'); for (let y = 0; y < 420; y += 3) { f.scrollTop = y; await new Promise(r => setTimeout(r, 16)); } return true; })()""")
            await asyncio.sleep(1.5)
            takes.append(Take(await s.stop_recording(), "One click:|*from my résumé*.", seconds=10, speed=2))

            await s.start_recording()
            await s.click(".jp .jp-save", move=0.9)
            await asyncio.sleep(1.2)
            await self.ask(s, "apply to the Northwind Robotics one", cps=20)
            await self.until(s, "/Started \\d+ application/.test([...document.querySelectorAll('.prose-custom')].pop()?.innerText || '')", 300, 1)
            await asyncio.sleep(1.0)
            await s.js(self.LIVE_BROWSER)
            await asyncio.sleep(1.0)
            await self.until(s, self.TASKS_SETTLED, 900, 2)
            await asyncio.sleep(3.0)
            takes.append(Take(await s.stop_recording(), "Then it fills in|*the real form*.", seconds=14, speed=4))

            await s.start_recording()
            await s.js("document.getElementById('studioLive')?.remove(); clearInterval(window.__live); openTasksPanel(); true")
            await asyncio.sleep(1.0)
            pressed = await self.approve_on_screen(s)
            await self.until(s, self.TASKS_DONE, 600, 2)
            await asyncio.sleep(1.0)
            takes.append(Take(await s.stop_recording(), "And waits for *my OK*|before it sends.", seconds=8, speed=2))

            await s.start_recording()
            await self.received(s, "520")
            await asyncio.sleep(2.0)
            takes.append(Take(await s.stop_recording(), "*Done.* I didn't type|a single box.", seconds=8))
            print("forms: approve pressed on screen:", pressed)
        return takes


    def clear_chats(self):
        for sess in self.api("/api/sessions").get("sessions", []):
            urllib.request.urlopen(urllib.request.Request(f"{self.url}/api/sessions/{sess['id']}", method="DELETE"), timeout=10)

    async def apps(self) -> List[Take]:
        """Abilities, then places from Apple Maps, then a text that stops for your OK. Nothing personal is on screen:
        the place is a public one, and the text goes to a made-up 555 number and is declined on camera, never sent."""
        takes = []
        self.clear_chats()
        async with Studio(width=self.w, height=self.h, scale=self.scale, port=9519) as s:
            await self.open_app(s)
            await s.start_recording()
            await s.js("openAbilities(); true")
            await self.until(s, "[...document.querySelectorAll('*')].some(e => e.offsetParent && e.children.length === 0 && e.textContent.trim() === 'Contacts')", 30)
            await asyncio.sleep(0.8)
            for sel in ("Messages", "Location", "Calendar", "Home"):
                at = await s.js(f"""(() => {{ const e = [...document.querySelectorAll('*')].find(e => e.offsetParent && e.children.length === 0 && e.textContent.trim() === '{sel}');
                    if (!e) return null; const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()""")
                if at:
                    await s.move(at[0], at[1], 0.7)
                    await asyncio.sleep(0.5)
            await asyncio.sleep(0.8)
            takes.append(Take(await s.stop_recording(), "It can use|*your Mac's own apps*.", seconds=8))

            await s.js("document.querySelectorAll('.fixed.inset-0').forEach(e => { if (/Abilities/.test(e.textContent)) e.classList.add('hidden'); }); true")
            await asyncio.sleep(0.5)
            await s.start_recording()
            await self.ask(s, "Find coffee shops near Union Square in San Francisco", cps=20)
            await self.until(s, self.ANSWER_STARTED, 900)
            takes.append(Take(await s.stop_recording(), "Ask for places,|*anywhere*.", seconds=9))
            await s.start_recording()
            await self.until(s, self.DONE, 900)
            await asyncio.sleep(1.0)
            await s.move(self.w / 2, self.h / 2, 0.5)
            # Bring the table of places into view and stay there (not the source list under it).
            await s.js("""(async () => { const box = document.querySelector('#chatMessages'); const t = [...document.querySelectorAll('.prose-custom table')].pop();
                if (!box || !t) return true; const end = t.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 90;
                const step = end > box.scrollTop ? 3 : -5;           // the chat may already be past it, at the bottom
                for (let y = box.scrollTop; step > 0 ? y < end : y > end; y += step) { box.scrollTop = y; await new Promise(r => setTimeout(r, 16)); }
                box.scrollTop = end; return true; })()""")
            await asyncio.sleep(1.0)
            at = await s.js("""(() => { const a = [...document.querySelectorAll('.prose-custom td a')].filter(a => a.offsetParent)[1];
                if (!a) return null; const r = a.getBoundingClientRect(); return [r.left + Math.min(r.width / 2, 50), r.top + r.height / 2]; })()""")
            if at:
                await s.move(at[0], at[1], 1.0)
            await asyncio.sleep(3.0)
            takes.append(Take(await s.stop_recording(), "Real places from *Apple Maps*.|No account. No key.", seconds=12))

            await s.start_recording()
            await self.ask(s, "Send a text to +1 415 555 0134 saying: running 10 minutes late, sorry!", cps=20)
            asked = await self.until(s, "!!document.querySelector('[data-mcp-approval] [data-mcp=decline]')", 900)
            await self.until(s, self.DONE, 300)          # the page stops moving before the button is measured
            DECLINE = """(() => { const b = document.querySelector('[data-mcp-approval] [data-mcp=decline]'); if (!b) return null;
                b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()"""
            await s.js(DECLINE)
            await asyncio.sleep(2.0)
            takes.append(Take(await s.stop_recording(), "Sending a text?|It *always asks first*.", seconds=10))
            await s.start_recording()
            at = await s.js(DECLINE)
            if at:
                await s.move(at[0], at[1], 1.0)
                await asyncio.sleep(0.6)
                at = await s.js(DECLINE)
                await s.click(at=at, move=0.1)          # Decline: nothing is sent
            shown = await self.until(s, "/Declined/.test(document.querySelector('[data-mcp-approval]')?.innerText || '')", 8)
            await asyncio.sleep(2.5)
            takes.append(Take(await s.stop_recording(), "You decide.|*Every time.*", seconds=6))
            left = self.api("/api/mcp").get("pending", [])
            for p in left:                                # never leave a text waiting to be approved
                self.api(f"/api/mcp/decline/{p['id']}", {})
            print(f"apps: approval card after {asked:.0f}s | declined on screen: {not left} | "
                  f"declined afterwards through the API: {len(left)}")
        return takes

    # Your home folder as "~" on screen (paths in the Crew board and answers would show your username).
    MASK_HOME = """(() => { const H = %s; const fix = (n) => { if (n.nodeType === 3) { if (n.nodeValue.includes(H)) n.nodeValue = n.nodeValue.split(H).join('~'); }
        else n.childNodes && n.childNodes.forEach(fix); };
        fix(document.body); new MutationObserver((ms) => ms.forEach((m) => { m.addedNodes.forEach(fix); if (m.type === 'characterData') fix(m.target); }))
        .observe(document.body, {childList: true, subtree: true, characterData: true}); return true; })()""" % json.dumps(str(Path.home()))

    async def agents(self) -> List[Take]:
        """Claude Code fixes a failing test in a demo repo, in its own copy; each command is approved on screen, then
        the change is reviewed and merged."""
        takes = []
        self.clear_chats()
        since = time.time()

        def this_job():          # the job this recording started (not one left from an earlier take)
            return next((j for j in self.api("/api/crew")["jobs"] if j.get("started", 0) >= since), None)
        async with Studio(width=self.w, height=self.h, scale=self.scale, port=9520) as s:
            await self.open_app(s)
            await s.js(self.MASK_HOME)
            await s.start_recording()
            await self.ask(s, "Have Claude Code fix the failing tests in ~/code/tip-calc", cps=20)
            await self.until(s, self.DONE + " && " + self.ANSWER_STARTED, 300)
            await asyncio.sleep(2.5)
            takes.append(Take(await s.stop_recording(), "Ask for a fix.|*Claude Code* takes the job.", seconds=10))

            await s.start_recording()
            for _ in range(60):
                if this_job():
                    break
                await asyncio.sleep(1)
            jid = this_job()["id"]
            await s.js("openCrew(); true")
            await asyncio.sleep(1.5)
            if not await s.js(f"!!document.querySelector(\"[data-c-pick='{jid}']\")?.offsetParent"):
                await s.click("[data-c=back]", move=0.6)           # a narrow window shows the open job: back to the list
                await asyncio.sleep(0.8)
            await s.click(f"[data-c-pick='{jid}']", move=0.7)
            await asyncio.sleep(1.0)
            approved, t0 = 0, time.time()
            while time.time() - t0 < 300:
                at = await s.js("""(() => { const b = document.querySelector('[data-c-decide][data-d=session]'); if (!b) return null;
                    b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()""")
                if at:
                    await asyncio.sleep(1.2)                      # long enough to read what it wants to run
                    await s.click(at=at, move=0.6)
                    approved += 1
                    await asyncio.sleep(0.8)
                    continue
                job = this_job()
                if job["status"] in ("done", "failed", "stopped", "interrupted"):
                    break
                print(f"   agents: {time.time() - t0:.0f}s, job {job['status']}, {len(job.get('approvals') or [])} waiting, "
                      f"approved {approved}, button found {bool(at)}", flush=True)
                if job.get("approvals"):                          # one is waiting but isn't drawn yet: open the job again
                    await s.js(f"document.querySelector(\"[data-c-pick='{jid}']\")?.click(); true")
                await asyncio.sleep(0.8)
            await asyncio.sleep(1.0)
            takes.append(Take(await s.stop_recording(), "It works in *its own copy*.|You OK what it runs.", seconds=16))

            await s.start_recording()
            # Its closing message ("Both tests pass now…") is at the end of the steps.
            await s.js("""(async () => { const sc = document.querySelector('#crewPanel [data-c-scroll]'); if (!sc) return true;
                for (let y = sc.scrollTop; y < sc.scrollHeight; y += 12) { sc.scrollTop = y; await new Promise(r => setTimeout(r, 16)); } return true; })()""")
            await asyncio.sleep(3.0)
            takes.append(Take(await s.stop_recording(), "*Tests pass.*", seconds=7))

            def at(sel):      # a button inside the board's scrolling panel: into view first, then where it is
                return s.js(f"""(() => {{ const b = document.querySelector({json.dumps(sel)}); if (!b) return null; b.scrollIntoView({{block: 'center'}});
                    const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()""")
            print("   agents: loop ended; job", this_job()["status"], flush=True)
            await s.start_recording()
            await s.click(at=await at("[data-c-land=review]"), move=0.8)
            await self.until(s, "!!document.querySelector('details summary') && /The diff/.test(document.body.innerText)", 30)
            await s.js("(() => { const d = [...document.querySelectorAll('details')].find(d => /The diff/.test(d.innerText)); if (d) { d.open = true; d.scrollIntoView({block: 'center', behavior: 'smooth'}); } return true; })()")
            await asyncio.sleep(4.0)
            takes.append(Take(await s.stop_recording(), "See exactly|*what changed*.", seconds=9))

            await s.start_recording()
            await s.click(at=await at("[data-c-land=merge]"), move=0.8)
            merged = await self.until(s, "/merged/i.test(document.querySelector('#crewPanel')?.innerText || '')", 20)
            await asyncio.sleep(2.5)
            takes.append(Take(await s.stop_recording(), "Merge it.|*Your repo, fixed.*", seconds=6))
            job = this_job()
            print(f"agents: approved on screen {approved} times | job {job['status']} | its copy: {(job.get('worktree') or {}).get('closed')}")
        return takes

    async def models(self) -> List[Take]:
        takes = []
        self.clear_chats()

        async def pick(s, label):
            await s.click("#modelSwitcherBtn", move=0.7)
            await self.until(s, f"[...document.querySelectorAll('#modelSwitcherPanel button')].some(b => b.innerText.includes({json.dumps(label)}))", 30)
            await asyncio.sleep(1.0)
            at = await s.js(f"""(() => {{ const b = [...document.querySelectorAll('#modelSwitcherPanel button')].find(b => b.innerText.includes({json.dumps(label)}));
                const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()""")
            await s.click(at=at, move=0.8)
            await asyncio.sleep(1.5)

        async with Studio(width=self.w, height=self.h, scale=self.scale, port=9521) as s:
            await self.open_app(s)
            await s.start_recording()
            await s.click("#modelSwitcherBtn", move=0.8)
            await self.until(s, "document.querySelectorAll('#modelSwitcherPanel button').length > 3", 30)
            for i in range(1, 6):
                at = await s.js(f"""(() => {{ const b = document.querySelectorAll('#modelSwitcherPanel button')[{i}]; if (!b) return null;
                    const r = b.getBoundingClientRect(); return [r.left + 60, r.top + r.height / 2]; }})()""")
                if at:
                    await s.move(at[0], at[1], 0.5)
            await asyncio.sleep(1.0)
            await s.js("document.getElementById('modelSwitcherPanel').classList.add('hidden'); true")
            takes.append(Take(await s.stop_recording(), "Every model you have,|*one click away*.", seconds=8))

            await s.start_recording()
            await pick(s, "Qwen 3.5 4B")
            await self.ask(s, "Write a haiku about Monday mornings.", cps=20)
            await self.until(s, self.DONE + " && " + self.ANSWER_STARTED, 300)
            await asyncio.sleep(2.0)
            takes.append(Take(await s.stop_recording(), "A *free* model,|running on this Mac.", seconds=12))

            await s.start_recording()
            await s.click("#localServersBtn", move=0.8)
            await asyncio.sleep(2.0)
            stop = await s.js("""(() => { const b = [...document.querySelectorAll('#localServersPanel button')].find(b => /stop|unload/i.test(b.innerText));
                if (!b) return null; const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()""")
            if stop:
                await s.click(at=stop, move=0.8)
            await asyncio.sleep(2.5)
            await s.js("document.getElementById('localServersPanel').classList.add('hidden'); true")
            takes.append(Take(await s.stop_recording(), "See the memory it uses.|*Free it* in one click.", seconds=8))

            await s.start_recording()
            await pick(s, "Fireworks")
            await self.ask(s, "Now one about Friday afternoons.", cps=20)
            await self.until(s, "document.querySelectorAll('.prose-custom').length > 1 && " + self.DONE, 300)
            await asyncio.sleep(2.5)
            takes.append(Take(await s.stop_recording(), "Or a cloud model,|*with your own key*.", seconds=10))
            print("models: memory freed on screen:", bool(stop))
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
        note = await kit.badge(sc.model_note) if sc.model_note else None
        pieces.append(compose(raw, frame, mask, await kit.caption(t.caption), work / f"piece{i}.mp4", badge, note))
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
    ap.add_argument("--only", default="private,research,tidy,chores,build")      # also: hired, trap, forms (see the top)
    ap.add_argument("--model-note", default="", help='said on every app clip, e.g. "☁ Cloud model: Fireworks · GLM 5.3 Flash"')
    a = ap.parse_args()
    if a.messy_downloads:
        return messy_downloads(Path(a.messy_downloads))
    out, work = Path(a.out), Path(a.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    sc, kit = Scenes(a.url, work, a.model_note), Kit(work)
    for name in a.only.split(","):
        await make(name.strip(), sc, kit, out)


if __name__ == "__main__":
    asyncio.run(main())
