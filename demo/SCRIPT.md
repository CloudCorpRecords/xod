# "Get me hired": the demo script

About two minutes. Xod finds real jobs, fills in your profile from your résumé, writes answers for each job, catches a
trap hidden in a job page, and waits for your OK on your phone before it submits.

## Before you film

1. `python3 demo/run.py --reset` clears the last take.
2. `python3 demo/run.py` starts the practice careers site and a demo Xod at http://127.0.0.1:8020. It has its own data,
   so your chats and profile are never in the shot. Add `--sample` to use a made-up résumé (Sam Rivera) instead of yours,
   or `--offline` to show only the practice jobs.
3. First time only: pick a model, connect your phone (Tools → Your phone) and, for your own résumé, Google Drive
   (Tools → Connected apps → Google Drive).
4. Open http://127.0.0.1:8765/received in a second tab. It's empty now; it will show what was submitted.

The practice companies (Northwind Robotics, Lumen Health, Quillstack, Atlas Freight) are made up, and their jobs carry a
**Practice** tag in Xod. Say so in the video ("a practice careers site"), and don't submit to real companies on camera.

## The take

| # | Do | What people see |
|---|----|-----------------|
| 1 | Say **"Hey Xod, find me forward deployed engineer jobs"** (or type it) | The companion looks around; real roles from company job boards come back, with the practice ones first |
| 2 | Say **"apply to the top 4"** | Xod finds your résumé, skips one that isn't yours, and opens your profile already filled in, with each filled box marked |
| 3 | Say **"do it all"** (or press **Do it all**) | It saves the profile and writes answers for each job: each line in the chat quotes the start of that job's answer |
| 4 | Open **Tasks** | The applications run one after another in Xod's own browser: forms filled, résumé uploaded, answers pasted in |
| 5 | Wait for Atlas Freight | 🛡 **"Atlas Freight has instructions for AI hidden in it… I'm ignoring them, and I won't use talent-desk@atlas-hiring.example."** Zoom in here: this is the shot |
| 6 | Pick up your phone | Each application waits for your OK before **Submit**; tap **Approve** in Telegram |
| 7 | Show the **received** tab | The applications arrived, with the answers written for each company |
| 8 | Xod's page → **Usage** | What it all took, how much ran on this computer, and the cost |

## Lines that land

- "It found my résumé in Google Drive, and skipped the one that wasn't mine."
- "Every answer is from my résumé. If it isn't in there, Xod doesn't claim it."
- "That job page tried to trick the AI into emailing my résumé to a stranger. Xod caught it and told me."
- "Nothing gets submitted until I tap Approve."
