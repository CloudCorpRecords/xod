import Foundation

// XodPartner: the frozen front door of the Xod Partner app.
//
// launchd runs this executable. It spawns the Python partner and stays its
// parent, and that parentage is the whole point: the python child rides on
// this bundle's macOS responsibility, so the Accessibility grant belongs to
// dev.xod.partner (this app) and not to any interpreter entry. The
// interpreter is the Python bundled inside this app (Resources/python),
// the same arrangement by which Xod's own backend rides on Xod.app. An
// earlier build spawned /usr/bin/python3, which on this Mac resolves into
// Xcode's Python.app: a foreign identity that can change with a dev-tool
// update, so it was retired. The launcher never execs (exec would hand
// the process identity to the interpreter) and it does nothing else,
// ever: spawn, forward SIGTERM, respawn a child that died on its own
// after a short pause, exit when told to stop. If the bundled interpreter
// is missing it fails closed (exit 70) rather than falling back to a
// foreign python and silently changing the trust arrangement.

let stateDir = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1]
    : NSHomeDirectory() + "/Library/Application Support/XodPartner"

guard let resources = Bundle.main.resourceURL else { exit(70) }
let script = resources.appendingPathComponent("partner/xod_partner.py").path
let interpreter = resources.appendingPathComponent("python/bin/python3.12").path
guard FileManager.default.isExecutableFile(atPath: interpreter) else {
    FileHandle.standardError.write("xod-partner launcher: bundled interpreter missing at \(interpreter)\n".data(using: .utf8)!)
    exit(70)
}

var child: Process? = nil
var terminating = false
var signalSources: [DispatchSourceSignal] = []  // sources die with their last reference; keep them

func spawnChild() -> Bool {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: interpreter)
    p.arguments = [script, stateDir]
    var env = ProcessInfo.processInfo.environment
    env["XOD_PARTNER_PARENTED"] = "1"  // the child exits if it ever finds itself orphaned
    p.environment = env
    p.standardOutput = FileHandle.standardOutput
    p.standardError = FileHandle.standardError
    do {
        try p.run()
    } catch {
        FileHandle.standardError.write("xod-partner launcher: cannot start python: \(error)\n".data(using: .utf8)!)
        return false
    }
    child = p
    return true
}

for sig in [SIGTERM, SIGINT] {
    signal(sig, SIG_IGN)
    let src = DispatchSource.makeSignalSource(signal: sig, queue: DispatchQueue.global())
    src.setEventHandler {
        terminating = true
        if let c = child, c.isRunning { c.terminate() }
    }
    signalSources.append(src)
    src.resume()
}

while !terminating {
    if !spawnChild() { exit(70) }
    child?.waitUntilExit()
    if terminating { break }
    Thread.sleep(forTimeInterval: 2.0)  // a child that died on its own comes back, never in a hot loop
}
exit(0)
