# LANES.md: how a lane uses browser-ops on the Razer

Read this before driving the shared Chrome (ChromeTikTok, CDP port 9222).
The toolkit is at `~/workspace/browser-ops/`; call it through the wrapper:

```
B=~/workspace/browser-ops/bin/bops
```

Your owner id is your job id (for example `x-reaction-replies`). Use the
same owner id for the whole session.

## The rules

1. **Register every tab you open.** Open tabs through the toolkit so they
   are registered to you from the first second:

   ```
   $B open --owner <your-job-id> --task "<what this tab is for>" --url <url>
   ```

   If you inherit or navigate to a tab you did not open (a leftover from
   setup, a tab the browser restored), claim it before using it:

   ```
   $B register --owner <your-job-id> --task "<what this tab is for>" --target <target-id>
   ```

   A tab you cannot register because another lane owns it is not yours.
   Stand down per the contention rule; do not take it over, do not close
   it, do not post from it.

2. **Look before you act.** At session start, and before acting on any
   tab you did not just open, check the whole browser state:

   ```
   $B list
   ```

   It shows every registered tab with its owner and live title, plus every
   unregistered tab. If you need to be sure what a tab actually shows,
   screenshot it and look at the image:

   ```
   $B shot-tab --target <target-id>    # then view the PNG at local_path
   $B shot-desktop                     # whole screen, when the tab is not the question
   ```

   A URL list is a guess. A screenshot is evidence.

3. **Heartbeat on anything long.** The sweeper closes registered tabs
   whose heartbeat is older than 45 minutes, and it cannot tell a slow
   lane from a dead one. If your session or a single tab's work runs
   longer than about 15 minutes, heartbeat:

   ```
   $B heartbeat --owner <your-job-id>
   ```

4. **Finish what you start.** When a tab is done, say so, and at the end
   of your session close your own tabs:

   ```
   $B done --owner <your-job-id> --target <target-id>   # per tab, as you finish
   $B close-mine --owner <your-job-id>                  # at session end
   ```

   `close-mine` only ever closes your own registered tabs. If you crash
   or time out instead, the sweeper will close your tabs once they are
   marked done or go stale, and log it. That is the backstop, not the
   plan.

5. **Never close a tab you do not own.** Not with the toolkit (it will
   refuse patterns outside your ownership), and not with raw CDP calls,
   the browser UI, or any other route. Especially: do not close
   unregistered tabs or another lane's tabs during your end-of-session
   cleanup. Cleanup means YOUR tabs, the ones in the registry under your
   owner id. Tabs you "adopted" by using them without registering are the
   failure mode this system exists to end; register first (rule 1), then
   they are yours to close.

6. **Leave the browser as a browser.** Never close the last page tab in
   the browser. The toolkit enforces this on its own closes (it opens a
   blank tab first); honor it in anything you do by hand.

## Quick reference

| Need | Command |
| --- | --- |
| Whole browser state, with owners | `$B list` |
| Open a tab that is yours | `$B open --owner <id> --task <text> --url <url>` |
| Claim an existing tab | `$B register --owner <id> --task <text> --target <target-id>` |
| Stay alive on a long task | `$B heartbeat --owner <id>` |
| Mark a tab finished | `$B done --owner <id> --target <target-id>` |
| Close all your tabs | `$B close-mine --owner <id>` |
| See one tab | `$B shot-tab --target <target-id>` |
| See the desktop | `$B shot-desktop` |
| Clean up dead lanes' tabs | `$B sweep` (also runs on a schedule) |
