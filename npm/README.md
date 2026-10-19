# xod-agent

**Xod in your terminal.** Chat with [Xod](https://github.com/CloudCorpRecords/xod), the local-first AI agent, give it
jobs and approve its actions without opening a window: the same chats, memory, models, keys and safety check as the
desktop app.

```bash
npm install -g xod-agent
xod                                   # a conversation (Ctrl-D or /exit to leave)
xod "summarise ~/Downloads/report.pdf"
cat notes.txt | xod "turn this into a to-do list"
xod task "find three flights to Lisbon in May"
xod status                            # which model answers, what's running
```

It uses the **Xod desktop app** you installed (0.7.10 or newer, [download](https://github.com/CloudCorpRecords/xod/releases/latest)):
its own Python and code, so nothing else is installed. When the app is open, `xod` talks to it; when it isn't, `xod`
starts Xod in the background, and the app uses that one when you open it, so there's only ever one Xod.

From a source checkout instead: `XOD_HOME=/path/to/xod xod`.

The app can also install the command itself, with no npm: **Tools → Terminal command**.

More: [docs/TERMINAL.md](https://github.com/CloudCorpRecords/xod/blob/main/docs/TERMINAL.md). MIT license.
