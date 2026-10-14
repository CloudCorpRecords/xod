"""
What this computer can run, on any computer: Windows, macOS or Linux; NVIDIA, AMD, Intel or Apple graphics.

profile() answers "how much memory can an AI model use here, right now?" and the things around it:
  - graphics cards with their real memory (Windows' 32-bit WMI figure stops at 4 GB, so it reads the driver's
    64-bit size from the registry), whether each is dedicated or shares the computer's memory, and how Ollama
    can use it (CUDA, ROCm, Metal, or not at all)
  - live: GPU memory other programs are using (a game, another model), free RAM, battery or plugged in
  - ai_memory_gb: what a model plus its context can use when nothing else is running;
    ai_free_gb: what it can use right now

The parts that don't change (cards, processor) are read once; the live parts at most every LIVE_TTL seconds,
in the background, so asking is always instant. Parsing is kept in small functions that take the raw text,
so tests can feed them other computers' output.
"""
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

logger = logging.getLogger("xod.machine")

SYSTEM = platform.system()          # "Windows", "Darwin", "Linux"
LIVE_TTL = 20                       # seconds a live reading stays fresh
HEADROOM_GB = 1.5                   # left for the screen, the browser and the model server's own buffers
VENDORS = {"10de": "NVIDIA", "1002": "AMD", "8086": "Intel", "106b": "Apple", "1414": "Microsoft"}
NO_WINDOW = subprocess.CREATE_NO_WINDOW if SYSTEM == "Windows" else 0


def _run(cmd: List[str], timeout: float = 8) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW).stdout
    except Exception:
        return ""


# ---------------------------------------------------------------- parsing (pure: tested with other computers' output)

def parse_nvidia_smi(out: str) -> List[Dict[str, Any]]:
    """`nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader,nounits`"""
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            try:
                gpus.append({"name": parts[0], "vendor": "NVIDIA", "memory_gb": round(float(parts[1]) / 1024, 1),
                             "used_gb": round(float(parts[2]) / 1024, 1), "driver": parts[3] if len(parts) > 3 else None})
            except ValueError:
                continue
    return gpus


def classify(gpu: Dict[str, Any]) -> Dict[str, Any]:
    """Adds dedicated/accel: whether the card has its own memory, and how Ollama can use it.
    Integrated graphics report a small carve-out (≤ 2 GB) and share the computer's memory."""
    name, vendor, mem = gpu.get("name") or "", gpu.get("vendor"), gpu.get("memory_gb") or 0
    if vendor == "Apple":
        return {**gpu, "dedicated": False, "unified": True, "accel": "metal"}
    dedicated = mem >= 2 and not re.search(r"Microsoft Basic|Remote Display|Virtual", name, re.I)
    if vendor == "NVIDIA" and dedicated:
        accel = "cuda"
    elif vendor == "AMD" and dedicated:
        accel = "rocm"            # recent Radeon RX cards; older ones fall back to the processor (Ollama decides)
    else:
        accel = "none"            # Intel and integrated graphics: Ollama runs on the processor
    return {**gpu, "dedicated": dedicated, "unified": False, "accel": accel}


def parse_windows_registry(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Display adapters from HKLM\\...\\Class\\{4d36e968-...}: DriverDesc, HardwareInformation.qwMemorySize, MatchingDeviceId."""
    gpus = []
    for r in rows:
        name = r.get("DriverDesc")
        if not name:
            continue
        ven = re.search(r"ven_([0-9a-f]{4})", (r.get("MatchingDeviceId") or "").lower())
        size = r.get("HardwareInformation.qwMemorySize") or r.get("HardwareInformation.MemorySize") or 0
        if isinstance(size, (bytes, bytearray)):
            size = int.from_bytes(size[:8], "little")
        gpus.append({"name": name, "vendor": VENDORS.get(ven.group(1), "Other") if ven else "Other",
                     "memory_gb": round(int(size) / 1024 ** 3, 1), "driver": r.get("DriverVersion")})
    return gpus


def parse_gpu_counters(out: str) -> float:
    """Total dedicated GPU memory in use, in GB, from Windows' GPU Adapter Memory counters (any vendor)."""
    total = 0.0
    for line in out.splitlines():
        m = re.search(r"([\d.]+)\s*$", line.strip())
        if m:
            total += float(m.group(1))
    return round(total / 1024 ** 3, 1)


def parse_linux_drm(cards: Dict[str, Dict[str, str]]) -> List[Dict[str, Any]]:
    """/sys/class/drm/cardN/device/{vendor, mem_info_vram_total, mem_info_vram_used, product_name}."""
    gpus = []
    for card, f in sorted(cards.items()):
        ven = (f.get("vendor") or "").strip().lower().replace("0x", "")
        total = int(f.get("mem_info_vram_total") or 0)
        if ven not in VENDORS or ven == "10de":     # NVIDIA comes from nvidia-smi
            continue
        gpus.append({"name": (f.get("product_name") or f"{VENDORS[ven]} graphics ({card})").strip(), "vendor": VENDORS[ven],
                     "memory_gb": round(total / 1024 ** 3, 1),
                     "used_gb": round(int(f.get("mem_info_vram_used") or 0) / 1024 ** 3, 1) if total else None})
    return gpus


def ai_memory(gpus: List[Dict[str, Any]], ram_gb: float, ram_free_gb: float) -> Dict[str, Any]:
    """How much memory a model can use: the best usable GPU's (less headroom), else the shared memory of a Mac,
    else half the RAM on the processor. Returns the total and what's free now, and where models run."""
    usable = [g for g in gpus if g.get("dedicated") and g.get("accel") != "none"]
    if usable:
        g = max(usable, key=lambda x: x["memory_gb"])
        total = max(g["memory_gb"] - HEADROOM_GB, 0)
        used = g.get("used_gb")
        free = max(g["memory_gb"] - used - 0.5, 0) if used is not None else None
        return {"runs_on": "gpu", "gpu": g["name"], "ai_memory_gb": round(total, 1),
                "ai_free_gb": round(min(free, total), 1) if free is not None else None}
    if any(g.get("unified") for g in gpus):
        share = 0.75 if ram_gb > 36 else 0.66      # macOS lets the GPU wire about this much of the memory
        total = ram_gb * share - HEADROOM_GB
        return {"runs_on": "unified", "gpu": next(g["name"] for g in gpus if g.get("unified")),
                "ai_memory_gb": round(total, 1), "ai_free_gb": round(min(max(ram_free_gb - 2, 0), total), 1)}
    total = ram_gb / 2
    return {"runs_on": "cpu", "gpu": None, "ai_memory_gb": round(total, 1), "ai_free_gb": round(min(max(ram_free_gb - 2, 0), total), 1)}


# ---------------------------------------------------------------- reading this computer

def _windows_gpus() -> List[Dict[str, Any]]:
    import winreg
    rows = []
    key = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            for i in range(winreg.QueryInfoKey(k)[0]):
                sub = winreg.EnumKey(k, i)
                if not sub.isdigit():
                    continue
                row = {}
                try:
                    with winreg.OpenKey(k, sub) as s:
                        for v in ("DriverDesc", "MatchingDeviceId", "DriverVersion",
                                  "HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                            try:
                                row[v] = winreg.QueryValueEx(s, v)[0]
                            except OSError:
                                pass
                except OSError:
                    continue
                rows.append(row)
    except OSError:
        pass
    return parse_windows_registry(rows)


def _mac_gpus(ram_gb: float) -> List[Dict[str, Any]]:
    if platform.machine() != "arm64":
        return []          # Intel Macs: models run on the processor
    chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or "Apple silicon"
    return [{"name": chip, "vendor": "Apple", "memory_gb": ram_gb}]


def _linux_gpus() -> List[Dict[str, Any]]:
    cards = {}
    for dev in Path("/sys/class/drm").glob("card[0-9]*/device"):
        f = {}
        for name in ("vendor", "mem_info_vram_total", "mem_info_vram_used", "product_name"):
            try:
                f[name] = (dev / name).read_text()
            except OSError:
                pass
        cards[dev.parent.name] = f
    return parse_linux_drm(cards)


def _nvidia() -> List[Dict[str, Any]]:
    exe = shutil.which("nvidia-smi")
    if not exe and SYSTEM == "Windows":
        p = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"
        exe = str(p) if p.exists() else None
    if not exe:
        return []
    return parse_nvidia_smi(_run([exe, "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits"], 5))


def _cpu_name() -> str:
    if SYSTEM == "Windows":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    if SYSTEM == "Darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or platform.processor()
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


_static: Optional[Dict[str, Any]] = None
_live: Dict[str, Any] = {}
_live_at = 0.0
_refreshing = threading.Lock()


def _read_static() -> Dict[str, Any]:
    ram = round(psutil.virtual_memory().total / 1024 ** 3)
    if SYSTEM == "Windows":
        gpus = _windows_gpus()
    elif SYSTEM == "Darwin":
        gpus = _mac_gpus(ram)
    else:
        gpus = _linux_gpus()
    # nvidia-smi knows NVIDIA cards best (and their live use): it replaces the registry's entry for them.
    nv = _nvidia()
    if nv:
        gpus = [g for g in gpus if g["vendor"] != "NVIDIA"] + nv
    gpus = sorted((classify(g) for g in gpus), key=lambda g: (not g["dedicated"], -g["memory_gb"]))
    os_name = (f"macOS {platform.mac_ver()[0]}" if SYSTEM == "Darwin" else
               f"Windows {platform.release()}" if SYSTEM == "Windows" else f"Linux {platform.release()}")
    return {"os": os_name, "arch": platform.machine(), "cpu": _cpu_name(),
            "cores": psutil.cpu_count(logical=False), "threads": psutil.cpu_count(logical=True),
            "ram_gb": ram, "gpus": gpus}


def _read_live(static: Dict[str, Any]) -> Dict[str, Any]:
    vm = psutil.virtual_memory()
    live: Dict[str, Any] = {"ram_free_gb": round(vm.available / 1024 ** 3, 1), "gpu_used_gb": {}}
    nv = {g["name"]: g for g in _nvidia()}
    if nv:
        live["gpu_used_gb"] = {n: g["used_gb"] for n, g in nv.items()}
    elif SYSTEM == "Windows" and any(g["dedicated"] for g in static["gpus"]):
        out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "(Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage').CounterSamples | ForEach-Object { $_.CookedValue }"], 15)
        if out.strip():
            best = next(g for g in static["gpus"] if g["dedicated"])
            live["gpu_used_gb"] = {best["name"]: parse_gpu_counters(out)}
    elif SYSTEM == "Linux":
        live["gpu_used_gb"] = {g["name"]: g["used_gb"] for g in _linux_gpus() if g.get("used_gb") is not None}
    try:
        b = psutil.sensors_battery()
        live["power"] = {"battery": True, "plugged_in": bool(b.power_plugged), "percent": round(b.percent)} if b else {"battery": False, "plugged_in": True}
    except Exception:
        live["power"] = {"battery": False, "plugged_in": True}
    try:
        from app.services.ollama_hub import _models_dir
        live["models_disk_free_gb"] = round(shutil.disk_usage(_models_dir()).free / 1024 ** 3, 1)
    except Exception:
        live["models_disk_free_gb"] = None
    live["checked_at"] = time.time()
    return live


def _refresh_live() -> None:
    global _live, _live_at
    if not _refreshing.acquire(blocking=False):
        return                              # one reading at a time
    try:
        _live = _read_live(static())
        _live_at = time.time()
    except Exception as e:
        logger.warning(f"Couldn't read this computer's live state: {e}")
    finally:
        _refreshing.release()


def static() -> Dict[str, Any]:
    global _static
    if _static is None:
        _static = _read_static()
    return _static


def profile(fresh: bool = False) -> Dict[str, Any]:
    """Everything above, merged. Never waits for a slow reading unless there is none yet or fresh=True: a stale
    live reading is returned at once and a new one taken in the background."""
    s = static()
    if fresh or not _live:
        _refresh_live()
    elif time.time() - _live_at > LIVE_TTL:
        threading.Thread(target=_refresh_live, daemon=True, name="machine-live").start()
    live = _live
    gpus = [{**g, "used_gb": live.get("gpu_used_gb", {}).get(g["name"], g.get("used_gb"))} for g in s["gpus"]]
    mem = ai_memory(gpus, s["ram_gb"], live.get("ram_free_gb", s["ram_gb"] / 2))
    return {**s, "gpus": gpus, **mem, **{k: v for k, v in live.items() if k != "gpu_used_gb"},
            "summary": summary(gpus, mem, s["ram_gb"])}


def summary(gpus: List[Dict[str, Any]], mem: Dict[str, Any], ram_gb: float) -> str:
    """One line for people: where models run and how much room they have."""
    if mem["runs_on"] == "gpu":
        g = next(x for x in gpus if x["name"] == mem["gpu"])
        name = re.sub(r"^(NVIDIA |AMD )?(GeForce |Radeon )?", "", g["name"])
        return f"{name} · {g['memory_gb']:g} GB"
    if mem["runs_on"] == "unified":
        return f"{mem['gpu']} · {ram_gb:g} GB shared"
    return f"No usable GPU: models run on the processor ({ram_gb:g} GB)"


def ai_memory_gb() -> float:
    """What a model plus its context can use here when nothing else is running."""
    return profile()["ai_memory_gb"]


def to_json() -> str:
    return json.dumps(profile(), indent=2)
