// The one bridge between Xod's page and the desktop app, kept to the minimum the conversation needs:
// tell the app when Xod is listening (so the mic keeps working with the window in the background),
// hear the ⌃⌥Space "talk" shortcut from anywhere on the Mac, and open an app Xod built in its own
// sandboxed window (only /preview/ pages; main.js checks), and Xod's own updates (check, Update). No Node,
// no files, no shell.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("xodDesktop", {
  setListening: (on) => ipcRenderer.send("xod:listening", !!on),
  onTalk: (cb) => { ipcRenderer.on("xod:talk", () => { try { cb(); } catch (e) {} }); },
  openApp: (url, title) => ipcRenderer.send("xod:open-app", String(url || ""), String(title || "")),
  updates: {
    status: () => ipcRenderer.invoke("xod:update-status"),
    check: () => ipcRenderer.invoke("xod:update-check"),
    install: () => ipcRenderer.invoke("xod:update-install"),
    onState: (cb) => { ipcRenderer.on("xod:update-state", (_e, st) => { try { cb(st); } catch (e) {} }); },
  },
});
