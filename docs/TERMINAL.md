# Xod in the terminal

Chat with Xod and give it jobs from a terminal: the same agent, tools, memory, models, keys and safety check as the
desktop app, with no window open.

```bash
./xod                                          # a conversation (Ctrl-D or /exit to leave)
./xod "summarise ~/Downloads/report.pdf"       # one question, answered and done
cat notes.txt | ./xod "turn this into a to-do list"
./xod -c "and the second one?"                 # carry on from the last terminal chat
./xod task "find three flights to Lisbon in May"
./xod status
```

On Windows it's `xod.cmd`: `.\xod` in PowerShell, `xod` in Command Prompt.

## Setting it up

It runs from a source checkout (see "Build from source" in the README): run `./run.sh` (Windows: `.\run.ps1`) once so
Xod's Python environment exists, then use `./xod` from that folder. To type just `xod` anywhere:

```bash
ln -s "$PWD/xod" /usr/local/bin/xod
```

On Windows, add the checkout's folder to your PATH (Settings → System → About → Advanced system settings →
Environment Variables).

The desktop app you installed works too: a checkout's `xod` talks to the installed app while it's running, and uses
its data, models and keys.

## One Xod, not two

The terminal never runs a second copy of the agent. It talks to the Xod server that already serves the desktop app
(`127.0.0.1:8000`), and only to one that uses the same data folder. If none is running, it starts one in the background
on that port and leaves it running for your next command; when you open the desktop app, the app uses it too.

That way there is one set of chats, memories, tasks and approvals, and one copy of the built-in model in memory. Two
Xods on the same data would both run your tasks, routines and Agent Mail, both load the built-in engine, and fight over
the agent's browser.

- `xod stop` stops a server the terminal started. It never stops the desktop app's: quit the app for that.
- `xod start` starts the background server without asking anything.
- `XOD_PORT` picks another port, `--url http://127.0.0.1:PORT` points at a particular Xod, and `XOD_DATA_DIR` uses
  another data folder (the terminal then ignores a Xod that uses a different one).
- The background server's log is `logs/cli-server.log` in Xod's data folder.

## What you see

The answer streams as it's written. Each tool Xod uses is one short line underneath:

```
you › what's in my Downloads folder that's over 1 GB?
  › computer run command  du -sh ~/Downloads/* | sort -h
  ✓ Ran the command
Three things are over 1 GB: …
```

Inside a conversation:

| Command | What it does |
| --- | --- |
| `/new` | start a new chat |
| `/cloud QUESTION` | ask this one on your cloud model (needs an API key in Settings) |
| `/status` | which model answers, the model servers on this computer, add-ons |
| `/tasks` | your tasks and what they're doing |
| `/approvals` | answer what's waiting for your OK |
| `/exit` | leave (or Ctrl-D) |

Ctrl-C stops the answer in progress. Lines piped into `xod` with no question (`xod < questions.txt`) are sent one by
one as a single conversation.

Chats from the terminal are saved like any other, so they're in the desktop app's chat list too.

## Approvals

When the safety check holds an action, or a connected app wants to change something, the terminal asks the way the app
shows a card:

```
Xod wants to send email (gmail)
  The address bob@example.net came from something it read, not from you.
  { "to": "bob@example.net", … }
Approve and run it? [y/N]
```

`y` runs it; anything else declines it and nothing happens. With nobody to ask (a script, a scheduled job), it's left
waiting: answer it later in the app, from your phone, or with `xod approvals`.

`--yes` approves only ordinary actions of connected apps you've set to ask first (creating a note, adding a calendar
event). It never approves something the safety check held, a delete, or a task about to press Submit: those always
ask.

## Tasks

`xod task "…"` adds a background task (like the Tasks panel) and follows it: its steps as it works, then the result.
If it needs your OK to press a button, or has a question, it asks right there. Ctrl-C stops following; the task keeps
going in Xod. `--no-wait` just adds it. `xod tasks` lists your tasks.

If a web page shows a verification check, open the desktop app and use Take over.

## Status

```
Xod 0.7.10 at http://127.0.0.1:8000  started by the desktop app or ./run.sh
Data: ~/Library/Application Support/Xod
Answering chats: Built-in engine · Qwen3.5-4B (this computer)
Model servers on this computer:
  Built-in engine  on · Qwen 3.5 4B · 3.1 GB  answering
  LM Studio        off
  Ollama           off
Add-ons on (3 of 7): Media Studio, Agent Mail (preview), Model Lab
Connected apps: notion, github
Tasks: 1 working
```

`xod status --json` gives the same as JSON.

## All options

```
-c, --continue   carry on from the last terminal chat
-y, --yes        approve ordinary connected-app actions without asking (see Approvals)
--cloud          answer on your cloud model
--url URL        use the Xod server at URL
--json           status as JSON
--no-wait        add a task without following it
--no-color       plain text (also when NO_COLOR is set, or the output isn't a terminal)
-V, --version    the version
```

A question that is exactly a command word goes through `ask`: `xod ask "status"`.
