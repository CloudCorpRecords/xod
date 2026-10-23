// Whether the Xod server answering on the usual port is this Xod's own: same data folder and same version (its
// /api/cli/hello reply). Any other one, an older version left running or one a test started with a throwaway folder,
// would show someone else's settings and none of your keys.
const fs = require("fs");

function sameFolder(a, b) {
  try { return fs.realpathSync(a) === fs.realpathSync(b); } catch (e) { return false; }
}

function isOwnServer(helloBody, dataDir, version) {
  let h = {};
  try { h = JSON.parse(helloBody || "{}"); } catch (e) { return false; }
  return h.app === "xod" && h.version === version && sameFolder(h.data_dir || "", dataDir);
}

module.exports = { isOwnServer };
