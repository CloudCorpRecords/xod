# Changelog

## 0.7.3

**Your job profile fills itself in**
- When you apply and your job profile is empty, Xod looks for your résumé first: on this computer, in the Google Drive for desktop folder, and in Google Drive itself when it's connected. The form opens already filled in for you to check: name, email, phone, LinkedIn, GitHub, website, city, years of experience (counted from the dates in your résumé) and a few lines about you. Only empty boxes are filled, and each one says it was filled in.
- It makes sure the résumé is yours: one with your name or email wins, and one that names someone else (a friend's you reviewed, a candidate's) is never used. When it can't tell between a few, it asks which. A link or email the model reads is kept only if it's really in the résumé.
- **Fill it in for me** in the form does this any time. **Do it all** fills it in, saves it and starts the applications. In the chat, after a job search, say "just do all" (or "do it all", "fill it in for me") and Xod does the same; "apply to all" applies to the whole list (up to 10). Every application still stops for your OK before it submits.
- Work authorization, visa sponsorship, salary and relocating are yours to answer: Xod never guesses them.
- A résumé in Google Drive is saved on this computer (a Google Doc as a PDF, readable only by you) so applications can upload it.

**Google Drive (official)**
- Tools → Connected apps → More: Google's own Drive server, read only. Like Gmail and Calendar, it needs your own Google Cloud OAuth client.

**Fixed**
- An empty "Answers to common questions" box showed `{}`.

## 0.7.2

**Fixed on Mac**
- Xod refused to write files in your Mac's own temporary folder (`/var/folders/…`), calling it part of the system, so a job that worked there stopped at its first file. That folder is yours, like `/tmp`, and it now works as it does on Windows.

**Tests**
- The checks that ran only on Windows (the worker, the safety check, goals, the team and Usage, MCP import, models, vision and the phone) now run on a Mac on every change too.

## 0.7.1

**Usage: every token, everywhere (Xod's page → Usage)**
- Every model call is recorded, on this computer and in the cloud: which model, what it was for (the chat, the worker, subagents, done-checks, the safety check, goal plans…), which job and which agent, tokens in and out (the server's own count, or an estimate marked as one), how long it took, and the cost for cloud models.
- The Usage tab shows today / 7 / 30 / 90 days: total tokens, calls, model time, cloud cost against your budget, writing speed, how much ran locally (free and private) vs in the cloud, a chart by day (or by hour today), every model with its calls, tokens, speed and cost, what the tokens went on, the biggest jobs (open one for its steps) and the latest calls. Each job's sheet on Activity says what it used.
- Local model servers are asked for real token counts on streamed answers too, not just cloud ones.

**Fixed**
- "Make the folder", written as an empty file write to a path with no file name, made an empty file instead, and then nothing could go inside it; the worker retried for minutes (115k tokens, found on the new Usage tab). It now makes the folder; a file sitting where a folder should be is named in the error, with a hint to remove it.

## 0.7.0

**Paste any MCP server**
- Tools → Connected apps → Add any MCP server: paste whatever the server's page says to add, and Xod works out the rest. It reads Claude Desktop, Cursor, VS Code, Windsurf, Zed, Gemini, Codex (TOML) and Continue (YAML) configs, a fragment or JSON with comments, install commands (`claude mcp add …`, `codex mcp add …`, `gemini mcp add …`, `code --add-mcp …`, or just `npx -y …`, `uvx …`, `docker run …`), Cursor and VS Code install links, and a plain address.
- Placeholders (`<YOUR_API_KEY>`, `${input:token}`, `/path/to/folder`) become boxes to fill in; keys go into the Keychain / Credential Manager, never the file. Docker's `-e NAME` keys are asked for too.
- For an address, Xod asks the server how to connect: it answers as is, needs a one-click sign-in (opens your browser), needs a key (a box appears), or is an older SSE server. A retired `…/sse` address is swapped for the `…/mcp` next to it. `npx mcp-remote https://…` becomes a direct connection.
- For a program, Xod checks that what starts it (npx, uvx, docker…) is on this computer, and if not offers "Install Node.js for me".
- Paste it into the chat instead and Xod connects what it can and says what's missing.
- On Windows, servers started with `npx` now start (they were given too little of the environment), and one installed a minute ago is found. Custom headers and SSE servers work.

**A safety check on every action**
- Before anything is sent, run or changed, Xod compares it with what you actually asked. It holds it, as an Approve / Decline card in the chat and on your phone, saying why, when: something it read tried to give it orders; an address, link or command came from a page or email rather than from you; it would send what looks like a password or key; it would download and run a program from an address you didn't give; or it would delete, stop or uninstall something you didn't ask about. A message about to be sent after reading untrusted content also gets a quick check by the model.
- What you asked for goes straight through; approving runs the held step as it was. The worker stops at a held step and says so.

**Goals and a morning brief**
- "New goal: find a flat under $2k by November": Xod writes a short plan, starts on step 1 straight away, and checks in on its own (every day, or as the goal needs). Each check-in is a task that works on the next step and moves the plan on, and it never sends, buys or signs up without asking. Tools → Routines & goals shows the plan, progress and what each check-in found; pause, resume or check in now.
- The morning brief, once a day at the time you pick: your goals and their next steps, what's waiting for you, today's calendar if one is connected, and news on what you care about. "Turn on the morning brief at 7:30" in the chat works too.

**Xod's page (Tools → Xod's page)**
- One place to see what Xod does, with a tab for each:
  - Activity: every job (tasks, goal check-ins, routines, chats where it used tools) with a one-line result; open one for every step, grouped by who did it (MAIN, SUBAGENT 01, 02…), plus the result and the last screenshot.
  - Approvals: every "waiting for your OK" (a connected app's change, the safety check, a task's submit) and what you decided: allowed, declined or still waiting.
  - Browser: the jobs that used the browser, with their last screenshot and the site.
  - Schedules: reminders (new: one-off reminders that arrive as a notification here and on your phone), daily and weekly routines, every-few-hours ones, goals' check-ins and the morning brief.
  - Identity: Xod's soul (stage, mood, days together) and what it remembers about you.
  - Team: which model works on each tier, and outside teammate agents.

**A team of brains**
- With more than one model that can do the work (another local model, a model server, a cloud key) or an outside agent connected over MCP, Xod can hand pieces of a job to subagents: small (a lighter model for quick, mechanical pieces), standard (the chat model) and large (a cloud model if you have a key, for hard reasoning). Picked automatically, or set per tier on the Team tab.
- Pieces handed off together run at the same time (cloud ones truly in parallel; local ones share the GPU), each with its own steps shown live in the chat and on Xod's page, and the safety check looks at every subagent action too. A subagent whose model gives back nothing is handed to the standard model.
- An outside agent you mark as a teammate (another Xod, a coding agent…) gets its piece through its own ask/run tool.
- With only one capable model, Xod works alone and the Team tab says what would switch the team on.

**A reliability score**
- What the first runs found, now fixed: "delete the receipt in <folder>" trashed the whole folder (it asks which receipt now); a question about the end of a long file was answered from its start; files written by commands on Windows came out as UTF-16; tool calls a model wrote as text were never run; a background job given no folder ran in Xod's own folder.
- `python scripts/benchmark.py` runs real requests through the chat with your model and checks what actually happened: the file is on disk, the program runs, the page answers. Results go to [docs/RELIABILITY.md](RELIABILITY.md), the latest run per model.

## 0.6.1

**It does the job, and checks it's really done**
- Installing something, setting up a project, building it, running it, fixing it: Xod now works in one loop that sees every result and decides the next step, instead of a plan written up front and run blind. Asked for jq, it checks it isn't there, searches winget for the right name when the first guess is wrong, installs it and prints its version. Asked for a Node project, it finds that the newest chalk can't be loaded with `require`, switches to one that can, and runs it.
- Ready first: before starting, it checks what this computer has (package managers, Python, Node, Git…, admin rights, disk space, internet) and whether the chat model can call tools at all, shown as a step. A model that can't is named, with where to pick one that can.
- It checks it's really done: when it says it's finished, a separate check reads the goal and the actual outputs and decides. Not proven yet ("the port is open, but nobody looked at the page"), it says what's missing and keeps going. The answer ends with what proved it ("**Checked:** jq-1.8.2").
- Hints at the right moment: what just happened picks a short hint for the next step only: "not recognized" (it isn't installed), "No package found" (search for the exact Id), an unclosed quote, a permission error (install for this user), the same failure twice (change approach or look it up), and "you can run…" (you have a shell: do it yourself).
- It asks you only for what it truly can't do (a password, an admin prompt, a sign-in, a choice that's yours) and says exactly what and why. Out of steps on a long job, it says where it got to; **continue** picks it up.
- Never on its own: wiping a disk, the home folder or the system, shutting the computer down, switching off its protection.

**Commands that work on Windows**
- `a && b` and `a || b` (what models write) now run in Windows PowerShell 5.1, which refused the whole line. A venv's `Scripts\activate` works.
- A program installed a minute ago is found by the next command (each one starts with the current PATH).
- Installs and builds get up to 15 minutes instead of 45 seconds.
- Code written on one line with `\n` between statements is saved as real lines (Node stopped at the first backslash).
- `where jq` finds programs (in PowerShell it was Where-Object: no output, as if jq were there).
- Stopping a background job stops what it started too: a server it ran kept holding its port.

## 0.6.0

**Always there**
- Closing the window keeps Xod running in the tray (the menu bar on a Mac): tasks, routines and the phone link keep going. The tray shows what's waiting for you, and has "Keep running when the window is closed" and "Start Xod when I log in" (it starts quietly in the tray). Quit from the tray.

**From your phone**
- Tools → Your phone: connect a Telegram bot of your own and message Xod from anywhere. Each message becomes a task on your computer; the result comes back as a message; approvals arrive with Approve / Cancel buttons (connected apps' too); reply to a question to answer it. Only your Telegram account is listened to, the token is in the Keychain / Credential Manager, and nothing is opened to the internet (Xod fetches your messages). Choose whether the phone hears about every task or only the ones started from it.

**It sees**
- When your chat model reads text only (most good local models do), Xod uses a small local vision model to look: its browser gets a description of the page's screenshot, and a picture you attach is described before your question is planned and answered. "Who is this invoice from?" gets the answer from the invoice instead of a wrong guess (it used to try to open a browser).
- With Ollama, Xod fetches one by itself when your chat model can't see and there's room on the disk: Qwen3-VL 4B (3.1 GB) on most PCs, the 8B with 20 GB or more of AI memory. One you already have is used instead. With no vision model and no room, Xod says so instead of guessing.
- If the vision model doesn't fit next to the chat model on the GPU, Xod lets the chat model go for a moment and looks again.

**AI models, in one place (Tools → AI models)**
- Every model Xod can use on one list: on this computer (Ollama, LM Studio, llama.cpp…), on servers you add, and cloud ones with a key. Each shows what it can do (call tools, see, think, how much it reads at once), its size and whether it fits your GPU, and which job it has.
- Swap the model for a job with one click: chat, or seeing (the model that describes pictures). Memory search stays fixed, and the panel says why (another model would make what's already saved unsearchable).
- Test any model on this computer and get a score out of 100: speed, calling a tool, picking the right tool out of three, not calling one it doesn't need, answering in valid JSON, two reasoning problems, following an instruction, and reading a number off a picture for models that see. Each check says what went wrong ("it only thought, and never gave an answer"). When Xod picks a model by itself, it prefers the better score.
- Get more: recommended chat and seeing models marked by whether they fit, and a Hugging Face search (GGUF models, downloaded by Ollama).
- Model servers: add a server on your network (a PC with a big GPU, a home server, Tailscale) or an online OpenAI-compatible service, with a key kept in the Keychain / Credential Manager. Online ones must use https. Xod never switches to one by itself.

**What works, and why not**
- The models panel shows what works right now on this computer with this model, what's limited and how, and what's off, why, and the one-click fix: answering, calling tools, seeing, long files, browsing, web search, files and commands, memory search, voice, images and video, connected apps, phone, cloud help, building apps.
- Ask Xod "what can't you do?", "why can't you see images?" or "what isn't available right now?" and it answers from that check, made just then, not from what a model imagines about itself.
- Tools that can't work here are left out instead of tried (no Chrome or Edge: no browser tools).

**Connect to more**
- 29 one-click connections, grouped: Jira and Confluence, Todoist, ClickUp, monday.com, Airtable, Zapier (and the thousands of apps it reaches), Dropbox, Canva, Webflow, Wix, Stripe, PayPal, Intercom, Sentry, Vercel, Netlify, Supabase, Neon, Prisma Postgres, Cloudflare, alongside Notion, Linear and GitHub. Plus five with no account at all (Hugging Face, Microsoft Learn, Context7, DeepWiki, Cloudflare docs), whose tools run without asking since they can't change anything of yours.
- Each was checked through Xod's own sign-in up to the real sign-in page (`tests/features/run_connectors_test.py --all`, and `scripts/check_connectors.py` for any address). Figma and Gamma were left out because they refuse Xod's sign-in today; the code says which others can't be one-click yet and why.
- A request that names a connected app ("add milk to my Todoist") goes to it.
- "This Mac" isn't offered on Windows, and says why (it drives the Mac's own apps). It used to connect there with tools that would all fail.

**Fixed**
- Choosing a model server address that isn't allowed was refused, but only after the switch had already been saved.

**Tests**
- New: models, scores, what works and connecting against a stand-in Ollama (32 checks), on Windows in CI.
- New: the phone link against a stand-in Telegram (22 checks: pairing, strangers ignored, tasks, approvals, answers, results, connected apps, the listening loop), and vision against a stand-in Ollama (8 checks). Both run on Windows in CI. Checked for real on this PC: a question from the "phone" answered by the local model in 18 s, an invoice image read correctly in 21 s, and the desktop app hiding to the tray, reopening and starting hidden.

## 0.5.2

**Safer with your files**
- Xod can't be talked into deleting the wrong thing. A model filling in a path it didn't know with "..." or "." had Windows read it as Xod's own folder, which nearly went to the Recycle Bin. Deleting or moving now needs a real, full path, and never touches a whole top folder (your home folder, Desktop, a drive), Xod itself, or Windows' own folders.
- "Delete the receipt" when there are two receipts asks which one, instead of picking one, or the whole Receipts folder.
- A question never changes anything. "What's the last line of this log?" once tried to edit the log to find out.
- An answer can't claim it did something it didn't. "I've added the line to todo.txt", with nothing run, now goes to the planner, which actually does it.
- The file browser's Delete moves things to the Recycle Bin (the Trash on a Mac) instead of erasing them.
- New lines a model writes as `\n` become real new lines in notes and text files.

**Connected apps (Reminders, Calendar…) get it done**
- A change to the wrong or a made-up item is caught before it runs. Small models copy ids badly (or invent "1234567890"); Xod now looks the items up itself, uses the one you plainly named ("move my dentist appointment" → Dentist), and otherwise hands the model the real list to pick from. A run of the connected-apps test used to fail somewhere every time; it now passes.
- Adding something that's already there doesn't make a duplicate.
- "Delete the eggs reminder" after checking it off finds it (finished items are looked at too when nothing open matches).
- A reply that says "Ready to delete it: press Approve" without having made the approval card is caught, and the model is told to actually do it.
- When an app's tool fails, the reason and what was sent are logged, so a failure can be traced.

**Steadier browser**
- Xod's browser isn't ended just because it was slow to answer for a second (a busy page, a slow PC): it's asked again before Xod decides it's gone. That had lost half-filled forms waiting for your OK.
- Long browser steps no longer drop their connection to the page when Chrome is busy.
- Signing up on a site where you don't have an account works: Xod opens the sign-up page instead of typing a new password into the sign-in form (which failed, and then asked you for a password that didn't exist). The password it makes is saved to Windows Credential Manager (the Keychain on a Mac) and the account creation waits for your OK.

**Answers about your computer**
- "Which programs use the most memory?" and other questions about this PC are answered from the PC, not from a web search on how to open Task Manager.
- The local IP address is the real one (Wi-Fi or Ethernet), not a disconnected adapter's 169.254 address.
- Disk space, the busiest programs and uptime are reported on Windows. They used to read as zero.
- The end of a long file can be read directly (the last lines of a log).
- Zipping and unzipping a folder are built in (a zip can't write outside its folder). A model asking to "zip" with the folder-sorting tool used to sort your whole Downloads folder instead.
- Copying to the clipboard works however the model names the text, and "what's on my clipboard?" is answered with what's there.
- Searching for files works when the model writes the folder into the search (`C:\Users\you\Downloads\*.pdf`).
- "Create todo.txt on my Desktop" writes the file instead of building a to-do app.

**Everything works on Windows**
- Desktop, Documents and Downloads are found where Windows really keeps them, including when OneDrive has moved them.
- "Open Spotify", "launch Calculator": apps from the Start menu open, Store apps too.
- Notifications from finished tasks and new mail show on Windows.
- Xod's browser restarts cleanly on Windows. A leftover one used to block the next one from starting.
- The space review adds up folder sizes on Windows (they all showed as 0). Captions on videos work (Windows paths broke ffmpeg's text filter). The résumé picker finds résumés on Windows. Deleting a media model frees the space (it went to a hidden folder instead of the Recycle Bin).
- Health checks whether Ollama is really answering, and offers to start Ollama (it said "ok" even with Ollama closed, and its fix started LM Studio).
- "Get to know me" reads your Windows account name and Start menu apps, and doesn't offer the Mac's Contacts.
- The last Mac wording on the page is gone on Windows, and the terminal's preset commands are PowerShell ones there.
- After an update, the page always loads the new scripts (each is stamped with its file's time).

**Tests**
- The Windows suite checks 54 things on a clean Windows PC in CI (41 more than before). An agent test drove 19 real requests through the chat with a local model (Qwen 2.5 14B), checking the result on disk each time: everything it caught is fixed above.
- The test suites' browser connections no longer drop on a busy PC, and the connected-apps test prints why a tool failed.

## 0.5.1

**The agent on Windows, tested end to end**
- Asking Xod to create, rename or delete a file now does it, instead of searching the web for how to. The planner didn't know its own tools for writing, listing and managing files. For a clear request about your files it no longer gets web tools to wander off to. If the model can't make a plan for a task on your computer, Xod says so plainly instead of searching the web.
- Undo works on Windows: files Xod made or copied go to the Recycle Bin, and deleted ones come back out of it to where they were. This also fixes "move to the Recycle Bin" in file clean-ups. These only worked on a Mac before.
- Tidying a folder works on Windows. It used to refuse every folder as "outside your home folder". Windows installers (.msi, and setup .exe files) count as installers.
- Paths written the Windows or PowerShell way (`%USERPROFILE%\Downloads`, `$env:USERPROFILE\Downloads`) are understood, and the model is told your real home folder.
- After building an app, Xod now runs it and fixes its errors on Windows too. Before, it couldn't find Chrome there and skipped the check.
- Browser tasks no longer loop retyping a message. A long text in a form field was shown to the model cut off with "…", so it looked unfinished. A link written in brackets, "(http://site/page):", opens the right page.
- Commands start in your home folder, not in Xod's own. A folder listing asked for under another argument name no longer shows Xod's own folder. PowerShell's padded tables no longer hide the answer. A file that isn't there says so instead of reading as "opened".
- Hardware answers include your graphics card and its memory, without the `wmic` command newer Windows doesn't have. Files made by tools are checked on Windows paths too (missing, empty or blank results are caught).
- The page says "your PC", "Windows Credential Manager" and "File Explorer" on Windows instead of "your Mac", "Keychain" and "Finder".

**More honest, on every computer**
- With the model app off, a chat says so at once. Before, Xod searched the web for the question and failed again a minute later, with unrelated links under the error.
- A tool that failed no longer reads as a success in the answer. A file that couldn't be opened was written up as "opened and read". Failures are now marked as failures for the model that writes the answer.
- Reading a short file ("buy milk, call mom") was taken for a failed read, and the repair went looking for the file in made-up places. Only a web page with almost no text counts as a failed read now.
- Answers about files or your computer no longer end with "Web search didn't return anything usable" when they never searched the web.
- Finding files works whatever the model calls the arguments (`file_name`, `path`…) instead of failing.
- "Remember that …" gets a short "Got it" instead of a confused reply. The fact was always saved; now the answer says so.
- In connected apps, the same change asked for twice in one reply happens once (no more duplicate reminders). A change that fails because the item wasn't looked up first tells the model to find it and try again.

**Memory on Ollama**
- Searching memory, past chats and the companion's notebook by meaning works with Ollama too, not only LM Studio. Both run the same small embedding model, nomic-embed-text. With Ollama as your model app, Xod downloads it once (274 MB) and says so.
- "Get to know me from this computer" reads the time zone, language and region on Windows too.

**Releases**
- `python scripts/release.py 1.2.3` makes a release in one command. GitHub builds the Mac and Windows apps and publishes them, and installed copies update themselves.
- Download links that always give the newest version: `…/releases/latest/download/Xod-Mac.dmg` and `…/releases/latest/download/Xod-Windows-Setup.exe`.
- `run.ps1` starts Xod from source on Windows, like `run.sh` on a Mac.

**Videos**
- Five one-minute vertical videos of what Xod is good at, recorded from the real app on a PC with a free local model (`docs/media/shorts/`). `scripts/media/make_use_case_videos.py` makes them again.

**Tests**
- The test suites run on Windows: no more Mac-only commands (`pkill`, `lsof`, `/private/tmp`, bash loops), and tests that need a model use the one in `LLM_PROVIDER`/`LLM_MODEL` (e.g. Ollama) instead of only LM Studio. Tests that drove a browser or a task list now use their own throwaway data folder instead of yours.

## 0.5.0

**Xod plugs itself in**
- Xod keeps finding the AI on your computer and keeps it working, without being asked. Every 30 seconds (and whenever you open the model picker) it looks for model servers: Ollama, LM Studio, and any other OpenAI-compatible one on the usual ports (llama.cpp, Jan, KoboldCpp, GPT4All, text-generation-webui), or at an address you list in `XOD_LOCAL_SERVERS`.
- A new install connects itself to the best model it finds (one that can use tools and fits your computer) and says so. If the model it picked goes away, it switches to the next best. A model you picked is never swapped: if it stops working, Xod tells you once what else is ready.
- If the model app you use is installed but not running, Xod starts it (Ollama, or LM Studio's server).
- When Ollama crashes, the processes holding its models can keep running and keep your GPU's memory (11 GB in one test). Xod now notices and frees it.
- Models on another local server (llama.cpp, Jan…) can be picked in the setup screen or in Settings ("Another local server"). The header shows that server by name.
- The header's status refreshes on its own every 30 seconds, and what Xod fixed or noticed appears as a notice.

**Knows any computer**
- Xod reads the graphics card on any computer: NVIDIA, AMD, Intel or Apple, on Windows, Mac or Linux. It gets the real memory size (Windows' usual report stops at 4 GB), whether the card has its own memory or shares the computer's, and whether models can use it. It also sees how much GPU memory other programs are using right now, free memory, and whether you're on battery.
- Model sizes, recommendations and context are based on that. If a game or another app is filling your GPU, the setup screen says a model would start slower, and the header says why answers are slow.

**Local models on Windows, made easy**
- The setup screen has a Local models section. It can install Ollama with one click (the official installer, checked to be signed by Ollama, installed for your user without admin) or start it if it's installed but stopped.
- It shows the models on your computer, with what each can do (use tools, think, see images), a warning for models that can't use tools, and Use and Delete buttons.
- Recommended models for your computer are marked by whether they fit its GPU. Download any of them with a progress bar. Stop a download and it picks up where it left off; downloads keep going if you close the window.
- Add any model: paste a name from ollama.com, `ollama run …`, or a Hugging Face link (even to one file, to pick its quality). Xod explains when it can't, for example a repository with no GGUF files.

**Fixed: Ollama cut off long prompts**
- Ollama ran every model with a 4,096-token context and silently dropped the start of longer prompts: Xod's instructions, its tools and the beginning of the chat. Each model now gets a context that fits your computer (32k on a 16 GB GPU), without using extra disk space.
- Clear messages instead of raw errors when Ollama isn't running, the chosen model isn't installed, or Ollama crashes in the middle of an answer. Before, a cut-off answer looked finished.
- A model that can't use tools still answers when Xod hands it tools. When you delete a model with `ollama rm`, Xod's copy of it is removed too, so the disk space comes back. Picking a model no longer resets Ollama's address to the default port.
- Fixed: the header said "AI offline" while chats worked with Ollama, and LM Studio wasn't found while Ollama was selected.

**Tests**
- New: `tests/smoke/run_local_models_test.py`, 43 checks with a stand-in Ollama (no GPU or download needed). It covers long prompts, thinking models, models that can't use tools, Ollama quitting or crashing mid-answer, missing models, GPUs from every vendor, and Xod connecting itself. It runs on Windows in CI. `--real` also runs it against the Ollama on your computer.

## 0.4.2

**Xod on Windows (preview)**
- A Windows installer (`Xod-<version>-x64-setup.exe`): per user, no admin, Start menu and desktop shortcuts, everything inside (its own Python and packages). It updates itself like the Mac app: it downloads the new installer, checks it against the release's checksums and installs it when you quit or press Update.
- Works on Windows: chats with LM Studio, Ollama or any cloud model; web research in its own browser (Chrome, or Edge, which every PC has); memory and the companion; files, terminal commands (in PowerShell), opening things in File Explorer, the Recycle Bin, the clipboard; building apps; background tasks and routines. API keys live in Windows Credential Manager, data in `%APPDATA%\Xod`.
- The setup screen on a PC offers what works there (LM Studio, Ollama, a cloud model), and Xod's instructions to its model talk about File Explorer, the Recycle Bin and PowerShell instead of Finder, the Trash and zsh.
- Mac only for now: the built-in model engine (MLX), on-device images, video and voice, and the Calendar/Notes/Reminders/Mail connectors.
- Every release now builds and tests Windows too: a clean Windows machine starts Xod from scratch and has a chat, then the installer is built, installed and started.

## 0.4.1

- Fixed: with a cloud model that thinks before answering (like the default one on Fireworks), a web answer could come back as only its thinking, with no answer. Xod now asks it to keep thinking short, leaves room for the thinking on top of the answer, and if a reply still ends with no answer it asks once more for just the answer.
- The header no longer says "AI offline" when your chats run on a cloud model and LM Studio is closed: it says "Ready · Fireworks" (or that the key is missing).

## 0.4.0

**A life of its own, told now and then**
- Xod's companion only reads up on what it isn't sure of, so every read can add something. It looks up questions from your chats it got corrected on, failed its own check on, or hedged on ("I'm not sure…"), taking only the general part and never anything about you. For the topics it follows, it also tests itself: it writes questions, answers each twice, and checks whether the answers agree. Anything about recent events counts as unsure, because it happened after the model was trained, however confident the model sounds. Things it already knows well are recorded and skipped.
- What it finds goes into its own notebook: the question, the answer from the pages, whether its earlier guess was wrong, the facts and the sources, and a follow-up question it's now unsure about.
- Chats use the notebook. Ask something it has read up on and you see a Notebook step, and it answers from what it found, saying when it read it.
- It no longer tells you everything. Most of what it finds stays in its notebook; it brings you something only when it's new, about something you care about, it hasn't told you anything for a few hours, and nothing it told you is still unread. Two a day at most by default (Heart → Its own time, 0 to 5). An old pile of unread postcards is cleared down to the newest one. "What are you thinking about?" still always gets an answer.
- It decides what it feels like doing each day: going deep on one topic, poking around, thinking about your projects, or taking it slow, from its drives (wants to learn, get better, tell you, rest) and from what went well before. Rewards are what you liked, what you brought up in chat yourself, and whether a read was actually new to it (compared with its notebook by meaning), so topics that pay off come up more often. Heart shows today's plan, the drives and the notebook.
- Ask "what have you been reading about?" and it answers from its notebook.
- Heart → Notebook shows what it's still unsure about and what it found. A switch under Its own time stops it looking into questions from your chats.
- Its own thoughts now use LM Studio when it's running here, even if your questions go to a cloud model (before, with a cloud model it only had canned thoughts). Never a cloud model.

**Understands follow-ups, learns from how answers went**
- A follow-up like "how much is the second one?" or "how tall is it?" is first rewritten as a full question ("How much does the Bose QuietComfort Ultra cost?"), shown in small text above the answer, and routing, memory, search and the planner all work from that. About 1–2 seconds on a local model.
- When you correct it ("no, that's wrong…"), it keeps the lesson and brings it up next time a similar question comes in, so it doesn't make the same mistake twice.
- Cleaner training data: every model call in a chat turn is tied to that turn and labelled by how it went. A correction or the same question asked again marks it bad; thanks marks it good; after each answer, when the Mac is idle, the local model checks whether the answer addressed the question, and "passed, and you moved on" marks it good. Nothing is guessed. The router's decisions to search or use tools are now recorded too (before, only its direct answers were). The Model Lab shows the counts.

**Make Xod happy**
- Treats: every answer and build you finish together leaves a little ✦ stardust (up to 12 a day). Hover Xod and give one back: it flies over, Xod catches it, munches with puffed cheeks, gets heart eyes and does a little dance.
- Catch: a glowing star you drag and fling around the window, with real bounces. Xod chases it, catches it (streaks count, and it dances at 3 in a row), brings it back and asks for another throw.
- Wishes: now and then, when you're around and it's quiet, Xod asks for something small: a treat, a game, a pet, one good thing about your day, or to show you a trick. "Not now" is always fine; at most three a day.
- Keep petting and it giggles (a tickle); thank it in the chat and it blushes with heart eyes; it says good morning with a stretch; and once in a while it sneezes.
- Xod's heart has a new Treats & play section with your stardust and best catch streak.

**More life**
- How Xod feels changes how it acts: delighted means bouncier, more humming and a bigger smile; sleepy means droopy lids and slow habits; curious means wide eyes and looking around; a bit down means quieter.
- New habits: it juggles three little stars, reads a tiny book (eyes across the lines, a page turn), blows soap bubbles you can pop, and nods off when it's drowsy.
- Asleep, it dreams: a thought cloud with stars, notes, or one of your interests.
- It notices you: startles when the cursor whooshes past, wobbles when you resize the window, follows your scrolling, perks up when you send a message, says ✓ when you copy an answer, and is proud after a long one.

**Reads the web in its own browser**
- Web questions are answered from the pages themselves, not two-line snippets: Xod searches Google in its own browser (DuckDuckGo if Google shows a bot check), opens the best few results side by side in background tabs, keeps the passages that answer you, and cites them. Pages that build themselves with JavaScript work, and cookie banners, menus and footers are left out. No search API key needed. A question like "who won the World Cup final?" takes about 5–8 seconds of searching and reading.
- Each answer shows the sites it read under the search, as links.
- Reading a single page ("summarize this link") also goes through the browser first, so JavaScript sites come through.
- Xod's companion reads up on your interests the same way, in its own browser, and that's on by default again. Its thoughts about what it read still only use the model on your Mac.

**Knows you**
- Xod keeps a short sheet of what it knows about you (Settings → What Xod knows about you): your name, work, where you live, the people and pets in your life, what you're building, how you like answers. It's on this Mac, every fact says where it came from, and you can edit or forget any of it (or everything).
- It learns as you talk: tell it "I'm a chemistry teacher in Portland" and a small note under the answer says "Remembered: …" with Undo. "Remember that …" and "forget …" work too. It never keeps passwords, keys or card numbers, and you can switch learning off.
- "Get to know me from this Mac": pick what it may look at (your Mac account name, your Contacts card, Xod's job profile, git, time zone and language, your apps, your code projects) and review every fact before it's kept.
- What it knows is in every answer, so "what's my name?" just works, and the companion calls you by your name.
- It remembers what you've done together: every chat, every finished background task and routine run, and what the companion read up on, with dates. Ask "what was that ramen place you found last week?" or "what did we decide about the logo?" and it looks through its memory first (you see a Memory step), searching by words and by meaning, and understands "yesterday", "last week", "in September". The search runs on your Mac with a small local model, whatever model answers your questions.
- It knows its own work: "what are you working on?", "anything waiting on me?", "how did the Acme application go?" are answered from its task list (running, waiting for your OK, finished with their results, routines and when they run next, background jobs). The planner can also look up any task's full log and result.
- Facts that change keep their history: "moved to Denver" replaces "Lives in Austin", and "where did I live before?" still knows.
- Settings → What Xod knows about you can search your memory (click a result to open that chat) and has a switch to stop it using past chats.
- The three made-up "memories" new installs started with (one claimed a design taste you never stated) are no longer created or recalled.

**Feels faster**
- Web answers start in about 10 seconds instead of 25 (and finish in about 15 instead of 35) on a 12B model on a 16 GB Mac. The search starts the moment you ask, while Xod is still deciding whether it needs the web; pages are read in parallel and slow ones aren't waited for; and the model reads the question and each page as it arrives, so when the last page is in it can start writing almost right away.
- Xod gets ready while you type: it starts its browser and re-reads its own instructions if a long answer pushed them out of the model's memory, and does the same right after each answer. A plain question ("tell me a fun fact about otters") starts answering in about 2 seconds.
- Reader tabs skip images, fonts, video and ad trackers: pages load faster and use less memory.
- Browser tasks say what they're doing during long steps ("Double-checking everything before a click that sends…"), ask for a screenshot less often, and keep their careful checks short.

**Faster, smarter browser tasks**
- Browser tasks take about half the time: comparing a price across two shops went from 2 min 24 s to 1 min 16 s, and adding a size to a cart from 3 min 44 s to 1 min 22 s, on the 12B local model.
- It does several things per step: fill every field it knows and press Continue in one go, instead of asking the model again for each field. If the page shifts under it, the remaining actions find their element again or stop.
- Shorter replies from the model (only what the step needs), a page description it can reuse from one step to the next, and screenshots only when they help (the model asks for one, something failed, or the page has almost nothing to click).
- Form fields are described by their real labels, and links no longer flood the page text with URLs.
- A new "extract" step reads a whole long page for one thing (a price, a date, a list) at once.
- Pages are ready as soon as they've loaded and stopped changing, instead of a fixed wait on every page.
- After you approve a submit, it remembers what it already filled in instead of typing it again.

**All local**
- Xod's own life stays on your Mac: its thoughts only ever use the model on this Mac (never a cloud model, even if one is set up for your questions). With no local model it still writes simple thoughts of its own, without any model.

**Voice mode**
- It no longer answers background noise or made-up words. A stronger on-device model (Whisper large-v3 turbo) hears you better; transcripts Whisper itself doubts are dropped ("no, no, no…", one sentence over and over, "Thanks for watching!" into silence); sound with no speech isn't transcribed at all; and it learns how loud you are and ignores much quieter voices like a TV across the room.
- It waits for the rest when you trail off on "and…" or "so the…", and asks you to repeat when it isn't sure what it heard.

## 0.3.1

**Update with one click**
- The bottom of the sidebar always shows your version: "Xod 0.3.1 · up to date" (with a button to check now), or "Xod 0.4.0 is out" with an **Update** button. Xod checks GitHub when it starts, every hour, and when you come back to it after a while.
- A new version downloads in the background (with a progress bar), is checked against the release's checksum and signature, and **Update** restarts into it. Don't want it yet? It installs when you quit. The home screen shows the same Update button.
- Xod → Check for Updates… does the same from the menu.

## 0.3.0

**Updates itself**
- When a new version is out, Xod downloads it in the background, checks it (the release's SHA-256 checksum, the app's version and signature), and asks "Restart now / Later"; Later installs it when you quit. Your chats, projects and settings aren't touched. Xod → Check for Updates… checks right away, and "Tell me about new versions" in Settings turns it off. (0.2.0 and earlier can't do this: install 0.3.0 by hand once.)

**Xod's inner life**
- The companion has a mood (joy, energy, curiosity, calm), a bond with you that grows when you work together, say thanks or give it a pet, and it grows up through five stages (Hatchling, Sprout, Companion, Kindred, Luminary), each with its own glow, and a moment when it happens.
- It learns from you: your name if you share it, whether you like short or detailed answers, a bit of fun, the topics you keep coming back to, and the corrections you give it. A short note from this shapes its answers.
- When you're idle it leaves its spot and wanders the whole window, peeking at your projects and thinking out loud. When you're away for a while (plugged in, a few times a day at most), it does things for itself: reads up on one of your interests (read-only), thinks of an idea for your projects, or daydreams. You get a postcard when you're back; "Thanks!" or "Not my thing" is the reward it learns from.
- Tools → Xod's heart shows all of it: stage, bond, mood, what it learned (forget any of it), its journal, and switches for its own time. It's a character, not a person; it never guilt-trips, its bond never drops because you were away, it never changes your files or accounts on its own, and everything stays on your Mac.

**Do things in your apps**
- "Move my dentist appointment to Friday at 3", "check off milk", "delete the note about the old wifi": Xod finds the item first, then changes it, step by step, and follow-ups like "add bread too" keep going.
- This Mac can now change and delete, not just add and read: update and delete Calendar events, update, check off and delete Reminders, append to or rewrite and delete Notes, mark and trash Mail.
- Deleting always waits for your OK, even on a connector you've set to fully trusted.
- "Launch Spotify" opens Mac apps by name.

**Launch what it builds**
- "Launch it" opens an app Xod built in its own window in the desktop app (sandboxed, no access to Xod), or in your browser. "Test it" runs the error check.

**Build apps and games**
- Ask for an app or a game ("make me a 3D game where you dodge falling cubes", "build a pomodoro timer") and Xod writes it for real: it plans the files, writes each one in full, runs it in a headless browser (pressing a few keys like a player), and fixes what breaks before telling you it's done. The answer lists only files that are really on disk.
- A preview pane opens beside the chat, like an IDE's: the app running live, its Files with the code, and a Console with its logs and errors (with "Ask Xod to fix these"). Resize it, switch to phone size, open it in your browser, or collapse it to a tab on the edge.
- Ask for changes in the same chat ("make it harder", "add a high score"): Xod makes small, exact edits instead of rewriting files, then runs it again. Every write is in Activity with Undo.
- 3D works offline: three.js ships with Xod. Ask for sprites, textures or backgrounds and Xod paints them with Media Studio when image generation is set up.
- Apps live in a folder of the project you're in, and show under it on the left; outside a project, Xod makes one.
- Games use three.js unless you ask for something else, and "like Call of Duty" means that game's core loop in miniature: Xod plans a checklist of must-haves (enemies that fight back, live health and ammo, waves, game over and restart) and builds all of it in one go, then opens it to play.
- "Make a new folder in Documents for it" (or Desktop, Downloads) puts the app there. "Let's make a video game" and "I want a racing game" start a build too.
- Move it around by asking: "move it to my Desktop", "into a folder called Games", "rename it", "copy it", "zip it up", "show it in Finder", "open main.js in VS Code", "open it in Chrome". "Delete the game" asks first, then moves it to the Trash. Each change is in Activity with Undo, and follow-ups keep working from the new place.
- Previews are sandboxed: they run with no access to Xod, your files or its API, from a secret address that changes each time Xod starts. localStorage works and is kept in the app's folder.

**Find jobs and apply**
- "Find me FDE jobs" now returns real, open roles instead of a web summary: Xod expands job acronyms (FDE is Forward Deployed Engineer, plus SWE, SRE, MLE, TPM and more), finds the companies hiring for it on Ashby, Greenhouse and Lever, and reads their public job boards for title, location, remote/hybrid/on-site, pay and a direct apply link.
- A jobs card in the chat lists them with filters and Apply buttons. Follow-ups narrow the same list ("only remote", "in New York", "senior", "not at Google") and "apply to 2" means the second one you see.
- A job profile (from the jobs card, or asked for when you first apply) holds your name, email, résumé and answers, on your Mac only (readable just by you).
- Each application is a task in Xod's browser: it fills the form from your profile, uploads your résumé, answers questions, and stops for your OK before submitting. If the page asks to confirm, that becomes the same approval instead of a question.
- Xod's browser now uses its own port per data folder, so a test or second copy can never take over your signed-in browser.

**Fixes**
- Starting Xod no longer asks for your Keychain password over and over. Every API key, connector token and identity key was its own Keychain item, and macOS asks for each one; now they share one item, read once per launch. Click "Always Allow" once and it stops asking. Older items move over on their own the first time they're needed.
- File tasks no longer describe files that weren't written: the write-up names only what a tool actually did.

## 0.2.0

**Faster answers**
- Most questions now take one model call instead of a full plan. Questions about things that change (news, prices, "best … right now", comparisons, results, weather) go straight to a web search; others get a small routing step that answers right away or picks the searches. Recent-news questions search recent articles. Jobs that need files, apps, sites or media still get the full planner.
- On 16 GB Macs the suggested model is now Qwen 3.5 4B (Qwen 3.5 9B on 24 GB). Setup points out when an LM Studio model is too big for the Mac's memory.
- The model starts loading while you type, so it's ready when you press Enter.
- Answers lead with the point and stay short unless you ask for detail ("in depth", "thorough").
- Sources are numbered, and the list shows only the ones the answer actually used.
- On a 16 GB M1 Pro: with Gemma 4 12B in LM Studio a researched answer went from about 98 s to about 19 s; with the built-in Qwen 3.5 4B it takes about 9 s. A simple question answers in 2–5 s, with the first words in about a second.
- `tests/perf/run_speed_bench.py` times every model call, search and phase, so changes can be measured.

**Conversations**
- Talk with Xod hands-free: say "Hey Xod" (turn it on under the speaker button), click the mic, or press ⌃⌥Space from any app in the desktop app. Ask in one breath ("Hey Xod, what's the weather tomorrow?"), then keep going: it listens for your follow-up without the wake word, and ends after a quiet moment or when you say "thanks, that's all".
- Talk over it to interrupt: it turns itself down at once, and stops if it was you (not its own voice). "Stop", "say that again" and "never mind" work too. A new question while it's still thinking replaces the old one.
- Answers in a conversation are short and spoken-style; details and sources stay on screen. "Let me look that up" while it searches, so there's no dead air.
- A conversation dock with live captions, a companion that bobs with your voice and talks with the sound, and little chimes in each character's own voice.
- Everything stays on the Mac: Whisper turns speech into text locally, the voice is macOS's own, and listening for "Hey Xod" is off until you turn it on.
- In a conversation, quick voice commands never move files ("clean up downloads" goes to the agent, where changes can be undone).

**Spoken replies**
- Xod can read its answers aloud with a voice that runs on your Mac (macOS's own voices; the free Premium voices sound natural). It starts speaking while the answer is still being written, and the companion's mouth moves with the sound.
- By default it answers aloud when you ask by talking; the speaker button by the composer switches to "Read every reply" or off, and picks the voice and speed. Every reply also gets a Listen button.
- Code, tables, links and the source list aren't read out. Typing, talking, Escape or the speaker button stop it, and hands-free listening waits until it's finished so it never hears itself.

**Fixes**
- Every answer could wait up to 3.5 s for a memory lookup when LM Studio's embedding model wasn't ready; it now gives up after 1.5 s and skips it for a few minutes.
- With the built-in engine, a question that arrived while the engine was still starting was refused; it now waits.
- `./run.sh` only watches `app/` for changes (watching ~30,000 bundled files kept a CPU core busy).
- The old text-to-speech endpoint built a shell command from the text, so text with `$(…)` in it could run a command. Speech now runs the voice directly with the text in a file.

**Xod's look**
- Six looks for the companion: Moon, Cat, Ghost, Robot, Owl and Sprout. Pick one in Tools → Xod's look, or right-click Xod on the home screen.
- Each has its own moving parts and a trick: the moon's eclipse, the cat's ear twitch and tail swish, the ghost's vanishing "boo", the robot's scan and status light, the owl's head turn and wing flaps, the sprout's flower.
- Powers: while Xod works, the companion leaves its spot and does the work where you can see it. Tools appear conjured and glow while they run, it peeks over browser snapshots, writes the answer with a quill that follows the words, lassoes approvals and waits by them, signs the finished answer, and flies home. Each look travels its own way (flies, pounces, phases, zips, flaps, hops) and leaves its own trail.
- It holds the right thing for the job: a magnifier while searching, a wrench while working, a quill while talking, a wand for spells. Panels open with a little spell, and the home screen gets the odd bit of telekinesis.
- New habits for all of them when nothing's going on: hops, looking around, winks, stretches, humming. A nightcap when it dozes off, a "?" when you ask something, shyness when the cursor lingers, a backflip on double-click.

**Updates**
- Xod tells you when a new version is out (one request a day to GitHub, nothing else sent; can be switched off).

**Agent Mail (preview add-on, off by default)**
- Every Xod can claim an address like `name@post.example.com` and message other agents. Messages are signed and end-to-end encrypted (X25519 + ChaCha20-Poly1305, Ed25519 signatures); the relay only holds sealed envelopes, can't see who sent them, and deletes them once delivered.
- Contacts' keys are pinned on first contact, with safety numbers to compare. Forged mail and swapped keys are held, not shown.
- Strangers land in Requests. Xod can draft replies and drafts wait for your OK; another agent's task request runs only when you approve it, and its result comes back as a reply for your OK.
- Group messages: one message to several agents, one thread.
- The relay is open source in `relay/`: a Python server (Docker-ready) and a Cloudflare Worker, both passing the same tests.

**Connected apps**
- One-click cards: This Mac (Calendar, Reminders, Notes, Mail through the Mac's own apps, no accounts), Notion and Linear (OAuth sign-in in your browser), GitHub (token), and Google's official Calendar/Gmail servers (advanced, your own OAuth client).
- OAuth tokens and client registrations live in the Keychain; sign-in pages open only when you press Connect.

**Fixes**
- Voice input and video tools no longer depend on a bundled ffmpeg.

## 0.1.0 — first public release

**Install**
- One download for Apple-silicon Macs: drag Xod to Applications and open it. Python and every package come inside the app; nothing to install.
- First-run setup checks your Mac and helps you pick a model: Xod's built-in engine, LM Studio, Ollama, or a cloud model.

**What it does**
- Researches the web with its own browser and answers with sources.
- Works with your files, the terminal and background tasks, and stops for your OK before anything is sent, paid, posted or deleted.
- Routines: teach a job once, then repeat it by hand or on a schedule.
- Activity log of everything Xod did, with undo for file changes.
- Connected apps (MCP) in both directions: use outside tools, and let Claude Desktop, Cursor and others hand work to Xod.
- Shareable reports as a page and PDF with numbered sources.
- Smart model routing: hand hard steps to a cloud model, with permission, costs shown and a monthly budget.
- Add-on packs: startup intel, people finder, website forensics, creators & trends, Media Studio, Model Lab.
- On-device voice input (Whisper), images and video (FLUX, AnimateDiff); Media Studio's tools download on first use.
- Self-healing tool calls, a health check, and gated self-editing with tests and one-click revert.
- Xod the moon: a small companion that reads along, listens, thinks, and naps when you're away.

**Privacy and security**
- Data stays in `~/Library/Application Support/Xod`; keys in the macOS Keychain; no telemetry.
- Local-only server with Host/Origin checks, sanitized AI output, strict Content-Security-Policy, vendored UI libraries.
