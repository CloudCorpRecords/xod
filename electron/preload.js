// The one bridge between Xod's page and the desktop app, kept to the minimum the conversation needs:
// tell the app when Xod is listening (so the mic keeps working with the window in the background),
// and hear the ⌃⌥Space "talk" shortcut from anywhere on the Mac. No Node, no files, no shell.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("xodDesktop", {
  setListening: (on) => ipcRenderer.send("xod:listening", !!on),
  onTalk: (cb) => { ipcRenderer.on("xod:talk", () => { try { cb(); } catch (e) {} }); },
});
