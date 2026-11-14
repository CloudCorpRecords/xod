#!/usr/bin/env python3
"""
Fails when a built app.asar cannot run its own main process.

  python3 scripts/check_asar.py path/to/app.asar
  python3 scripts/check_asar.py path/to/Xod.app        # finds Contents/Resources/app.asar

Opens the archive, reads the entry point named by the packaged package.json, and asserts
that EVERY relative require reachable from it resolves to a file inside the archive,
walking the same resolution Node uses (exact path, then .js, .json, .node, then
/index.js). Bare specifiers (core modules, node_modules) are out of scope by design:
this check exists for the failure where the app's own code is missing from its own package.

Why this exists: v1.1.3 shipped on 2026-11-13 with an app.asar of exactly 8 files. The
electron-builder "files" allowlist in electron/package.json omitted liveness.js while
electron/main.js requires it unconditionally at line 8, so every v1.1.3 build (Mac arm64,
Mac x64, Windows NSIS) died at startup with "Cannot find module './liveness'" before any
window, backend, or updater code ran. The release was reported verified on a DMG hash
match alone; nobody had opened the artifact or cold-launched it. This check is the gate
step that would have caught it (docs/RELEASE-PROCESS.md, step 2): after every build,
before anything is published, the built archive must pass, and the packaged app must
cold-launch with no main-process exception where a launch is feasible.

Uses only the standard library. Exit 0 when every reachable relative require resolves,
1 when any does not, 2 on a usage or unreadable-archive error.
"""
import json
import re
import struct
import sys
from pathlib import Path

REQUIRE_RE = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")
RESOLVE_SUFFIXES = ("", ".js", ".json", ".node", "/index.js")


def read_header(data: bytes):
    """Parse an asar header. Returns (tree, data_start) or raises ValueError."""
    if len(data) < 16:
        raise ValueError("file is too small to be an asar archive")
    _pickle_size, header_payload_size, _json_pickle_size, json_size = struct.unpack_from("<IIII", data, 0)
    json_bytes = data[16:16 + json_size]
    if len(json_bytes) != json_size:
        raise ValueError("asar header is truncated")
    tree = json.loads(json_bytes.decode("utf-8"))
    data_start = 8 + header_payload_size
    if data_start > len(data):
        raise ValueError("asar header size runs past the end of the file")
    return tree, data_start


def flatten(tree: dict, prefix: str = ""):
    """Map every file path in the archive to its header entry."""
    out = {}
    for name, entry in tree.get("files", {}).items():
        path = f"{prefix}{name}"
        if "files" in entry:
            out.update(flatten(entry, prefix=path + "/"))
        else:
            out[path] = entry
    return out


class Archive:
    def __init__(self, asar_path: Path):
        self.path = asar_path
        self.data = asar_path.read_bytes()
        self.tree, self.data_start = read_header(self.data)
        self.files = flatten(self.tree)

    def read_text(self, rel: str):
        entry = self.files.get(rel)
        if entry is None:
            return None
        if entry.get("unpacked"):
            sibling = self.path.parent / (self.path.name + ".unpacked") / rel
            if sibling.is_file():
                return sibling.read_text(encoding="utf-8", errors="replace")
            return None
        offset = self.data_start + int(entry["offset"])
        size = int(entry["size"])
        return self.data[offset:offset + size].decode("utf-8", errors="replace")


def normalize(base_dir: str, spec: str) -> str:
    """Resolve a ./ or ../ specifier against the requiring file's folder, POSIX style."""
    parts = [p for p in base_dir.split("/") if p]
    for seg in spec.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
        else:
            parts.append(seg)
    return "/".join(parts)


def resolve(archive: Archive, base_dir: str, spec: str):
    """Return the archive path the specifier resolves to, or None."""
    target = normalize(base_dir, spec)
    for suffix in RESOLVE_SUFFIXES:
        if target + suffix in archive.files:
            return target + suffix
    return None


def check(archive: Archive):
    """Walk the require graph from the packaged entry point. Returns (problems, visited)."""
    problems = []
    pkg_text = archive.read_text("package.json")
    entry_rel = "main.js"
    if pkg_text:
        try:
            entry_rel = json.loads(pkg_text).get("main") or entry_rel
        except ValueError:
            problems.append("package.json inside the archive is not valid JSON")
    entry_rel = entry_rel.lstrip("./")
    if entry_rel not in archive.files:
        problems.append(f"entry point {entry_rel} is not inside the archive")
        return problems, set()
    visited = set()
    stack = [entry_rel]
    while stack:
        rel = stack.pop()
        if rel in visited or not rel.endswith(".js"):
            continue
        visited.add(rel)
        text = archive.read_text(rel)
        if text is None:
            problems.append(f"{rel}: listed in the archive but its bytes are unreadable")
            continue
        base_dir = rel.rsplit("/", 1)[0] if "/" in rel else ""
        for m in REQUIRE_RE.finditer(text):
            spec = m.group(1)
            if not spec.startswith(("./", "../")):
                continue
            line = text.count("\n", 0, m.start()) + 1
            found = resolve(archive, base_dir, spec)
            if found is None:
                problems.append(f'{rel}:{line}: require "{spec}" does not resolve inside the archive')
            else:
                stack.append(found)
    return problems, visited


def find_asar(target: Path) -> Path:
    if target.is_file():
        return target
    for candidate in (target / "Contents" / "Resources" / "app.asar",
                      target / "resources" / "app.asar",
                      target / "app.asar"):
        if candidate.is_file():
            return candidate
    raise ValueError(f"no app.asar found at or under {target}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python3 scripts/check_asar.py <app.asar | Xod.app | resources dir>")
        return 2
    try:
        asar_path = find_asar(Path(sys.argv[1]))
        archive = Archive(asar_path)
    except (OSError, ValueError) as e:
        print(f"✋ Cannot check: {e}")
        return 2
    problems, visited = check(archive)
    print(f"  archive: {asar_path} ({len(archive.files)} files)")
    print(f"  reachable modules checked: {len(visited)} ({', '.join(sorted(visited))})")
    if problems:
        print("✋ Asar check failed: the packaged app cannot run its own main process.")
        for p in problems:
            print(f"  {p}")
        print("Fix the packaging (electron/package.json \"files\") and rebuild; do not publish this artifact.")
        return 1
    print("✓ Every relative require reachable from the entry point resolves inside the archive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
