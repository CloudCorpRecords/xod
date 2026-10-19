#!/usr/bin/env node
// `xod` from npm: runs Xod in the terminal (app/cli.py) with the Python and code inside the Xod app you installed,
// so it has the same chats, models, keys and safety check as the app. Nothing else gets installed.
//   XOD_HOME=<a source checkout>   use a checkout instead (its ./xod)
"use strict";
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const DOWNLOAD = "https://github.com/CloudCorpRecords/xod/releases/latest";

function candidates() {
  const home = os.homedir();
  if (process.platform === "darwin") {
    return ["/Applications/Xod.app", path.join(home, "Applications", "Xod.app")].map((app) => ({
      py: path.join(app, "Contents", "Resources", "python", "bin", "python3.12"),
      root: path.join(app, "Contents", "Resources", "backend"),
    }));
  }
  if (process.platform === "win32") {
    const local = process.env.LOCALAPPDATA || path.join(home, "AppData", "Local");
    const progs = [path.join(local, "Programs", "Xod"), path.join(process.env.ProgramFiles || "C:\\Program Files", "Xod")];
    return progs.map((dir) => ({ py: path.join(dir, "resources", "python", "python.exe"), root: path.join(dir, "resources", "backend") }));
  }
  return [];
}

function run(cmd, args, env) {
  const child = spawn(cmd, args, { stdio: "inherit", env: { ...process.env, PYTHONUTF8: "1", ...env } });
  child.on("exit", (code, signal) => process.exit(signal ? 1 : code ?? 0));
  child.on("error", (e) => { console.error(`xod: couldn't start Xod: ${e.message}`); process.exit(1); });
  for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => {});   // Ctrl-C goes to Xod, which handles it
}

const args = process.argv.slice(2);
if (process.env.XOD_HOME) {
  const home = path.resolve(process.env.XOD_HOME);
  const script = path.join(home, process.platform === "win32" ? "xod.cmd" : "xod");
  if (!fs.existsSync(script)) {
    console.error(`xod: XOD_HOME is ${home}, but there's no ${path.basename(script)} there.`);
    process.exit(1);
  }
  run(script, args, {});
} else {
  const found = candidates().find((c) => fs.existsSync(c.py) && fs.existsSync(path.join(c.root, "app", "cli.py")));
  if (!found) {
    const where = process.platform === "darwin" ? "in Applications" : process.platform === "win32" ? "" : "";
    console.error(process.platform === "darwin" || process.platform === "win32"
      ? `xod: the Xod app isn't installed ${where}(or it's older than 0.7.10, which brought the terminal).\n` +
        `Install it from ${DOWNLOAD}, open it once, then run xod again.`
      : `xod: there's no Xod app for this system yet. Run it from a source checkout: XOD_HOME=/path/to/xod xod\n${DOWNLOAD}`);
    process.exit(1);
  }
  run(found.py, ["-m", "app.cli", ...args], { PYTHONPATH: found.root, XOD_PACKAGED: "1", PYTHONNOUSERSITE: "1" });
}
