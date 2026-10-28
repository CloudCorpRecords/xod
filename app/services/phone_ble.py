"""
"Pair a phone" over Bluetooth: the same one-time offer as the QR, handed over without a camera.

The phone app's "Pair nearby" advertises SERVICE_UUID over Bluetooth LE for a few minutes. Xod scans for it
(find()), and offer_to() writes a fresh pairing offer (phone_pair.create_offer(): post office, computer address,
safety number, one-time code) to OFFER_UUID. The phone shows "Pair with <computer>?" with the safety number and
pairs only on the person's tap, exactly as if it had scanned the QR.

Wire format: the offer's JSON text in UTF-8, written in chunks no larger than the link allows, the last one ending in
"\n". The phone joins the chunks per sender and acts on the newline.

What Bluetooth adds and doesn't: it only carries the offer. Pairing itself still goes through the post office with the
one-time code, and the safety numbers on both screens are still the check that the phone paired with this computer.
"""
import asyncio
import logging
from typing import Any, Dict, List

logger = logging.getLogger("xod.phone_ble")

SERVICE_UUID = "7c0d0001-7a1d-4f3e-9b5a-58f1d0c0de01"
OFFER_UUID = "7c0d0002-7a1d-4f3e-9b5a-58f1d0c0de01"
SCAN_SEC = 6.0

_seen: Dict[str, Any] = {}      # address -> BLEDevice from the last scan (Windows connects best to the scanned object)


def available() -> bool:
    try:
        import bleak  # noqa: F401
        return True
    except ImportError:
        return False


def chunks(text: str, size: int) -> List[bytes]:
    """The offer split for writing: each piece at most `size` bytes, the last ending in a newline."""
    data = text.encode("utf-8") + b"\n"
    size = max(20, size)
    return [data[i:i + size] for i in range(0, len(data), size)]


async def find(timeout: float = SCAN_SEC) -> Dict[str, Any]:
    """Phones nearby that are in "Pair nearby" mode. {success, phones: [{id, name, rssi}]}"""
    if not available():
        return {"success": False, "error": "Bluetooth pairing needs the 'bleak' package (pip install bleak)."}
    from bleak import BleakScanner
    try:
        found = await BleakScanner.discover(timeout=timeout, service_uuids=[SERVICE_UUID], return_adv=True)
    except Exception as e:
        return {"success": False, "error": f"Couldn't use Bluetooth on this computer: {e}"}
    _seen.clear()
    phones = []
    for addr, (dev, adv) in found.items():
        if SERVICE_UUID not in [u.lower() for u in (adv.service_uuids or [])]:
            continue
        _seen[addr] = dev
        phones.append({"id": addr, "name": adv.local_name or dev.name or "Android phone", "rssi": adv.rssi})
    phones.sort(key=lambda p: -(p["rssi"] or -999))
    return {"success": True, "phones": phones}


async def offer_to(phone_id: str) -> Dict[str, Any]:
    """Makes a fresh pairing offer and hands it to that phone. {success, safety, computer} or {success: False, error}"""
    if not available():
        return {"success": False, "error": "Bluetooth pairing needs the 'bleak' package (pip install bleak)."}
    from bleak import BleakClient
    from app.services import phone_pair
    offer = phone_pair.create_offer()
    if not offer.get("success"):
        return offer
    target = _seen.get(phone_id, phone_id)
    try:
        async with BleakClient(target, timeout=15.0) as client:
            for piece in chunks(offer["qr_text"], (client.mtu_size or 23) - 3):
                await client.write_gatt_char(OFFER_UUID, piece, response=True)
    except Exception as e:
        phone_pair.cancel()
        logger.info(f"phone_ble: offer to {phone_id} failed: {e}")
        return {"success": False, "error": f"Couldn't reach the phone over Bluetooth ({e}). Keep 'Pair nearby' open on it and try again."}
    return {"success": True, "safety": offer["safety"], "computer": offer["computer"], "expires_at": offer["expires_at"],
            "message": "Sent. Tap Pair on your phone, then check the safety numbers match."}


if __name__ == "__main__":      # python -m app.services.phone_ble: list phones in "Pair nearby"
    print(asyncio.run(find()))
