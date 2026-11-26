"""
Phone pairing over Bluetooth (services/phone_ble.py): the pure parts.
The chunking contract the companion joins on (bounded pieces, the last
one ending in a newline) and the failure wording, including the
guidance for the characteristic-not-found case whose proven cause is a
stale Windows GATT cache on a computer that paired with the phone
before (phone benchmark, 2026-11-07: the companion's own stack reports
the service and characteristic up, and a cache-free central writes the
offer fine). No Bluetooth hardware or bleak needed.

  python tests/smoke/run_phone_ble_test.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="xod-phone-ble-")
os.environ.update(XOD_DATA_DIR=TMP)

from app.services import phone_ble  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + str(detail)) if not ok else ""))


def main():
    qr = '{"v":1,"kind":"xod-phone-pair","code":"9JPD4_OY-HzW","computer":"pc@host:8790"}'

    pieces = phone_ble.chunks(qr, 20)
    check("chunks join back to the payload plus newline",
          b"".join(pieces) == qr.encode("utf-8") + b"\n")
    check("every chunk within the size bound", all(len(p) <= 20 for p in pieces), pieces)
    check("only the last chunk ends in a newline",
          pieces[-1].endswith(b"\n") and all(not p.endswith(b"\n") for p in pieces[:-1]))
    check("chunk size floor of 20 is honored",
          all(len(p) <= 20 for p in phone_ble.chunks(qr, 3)))
    check("short text is a single chunk",
          phone_ble.chunks("hi", 500) == [b"hi\n"])

    missing = Exception(
        "Characteristic 7c0d0002-7a1d-4f3e-9b5a-58f1d0c0de01 was not found!")
    msg = phone_ble.offer_error_message(missing)
    check("characteristic-not-found names the Windows cache remedy",
          "Windows Bluetooth settings" in msg and "services fresh" in msg, msg)
    check("characteristic-not-found keeps the base advice",
          "Pair nearby" in msg and "was not found" in msg, msg)

    other = Exception("Device with address A4:50:46:16:F2:DC was not found.")
    msg2 = phone_ble.offer_error_message(other)
    check("a plain not-found error gets no cache guidance",
          "Windows Bluetooth settings" not in msg2 and "Pair nearby" in msg2, msg2)

    timeout = Exception("timed out after 15 seconds")
    check("unrelated errors get the base message only",
          phone_ble.offer_error_message(timeout).endswith("try again."),
          phone_ble.offer_error_message(timeout))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
