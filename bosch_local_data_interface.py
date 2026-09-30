"""Local data interface: read-only status plus the LAN-only stream source.

Cloud status endpoint (per camera, GET):
  200 {"username": str}            -> active
  404                              -> inactive (normal "off" state)
  449                              -> unsupported firmware
Any other status / network error / malformed body yields ``None`` (unknown).
Only Gen2 cameras on firmware >= ``MIN_FIRMWARE`` are ever queried.

When the interface is active and a password is stored for the camera, the
stream is read straight from the camera over the LAN (RTSP over TLS, Digest
auth) via ``/rtsp_tunnel``: ``inst=1`` high quality, ``inst=2`` low quality,
``enableaudio=1`` adds AAC audio (16 kHz mono). The camera allows only a few
(about 3) concurrent RTSP sessions and closes PLAY while privacy mode is on. The password is never logged or printed.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any
from urllib.parse import quote

import requests

_LOGGER = logging.getLogger(__name__)

STATUS_ENDPOINT = "onvif_user"
MIN_FIRMWARE: tuple[int, ...] = (9, 40, 105)
USER = "localuser"
PORT = 9554
STREAM_PATH = "/rtsp_tunnel"
LINE = 1
INST_HIGH = 1
INST_LOW = 2
PASSWORDS_KEY = "local_passwords"

STATE_ACTIVE = "active"
STATE_INACTIVE = "inactive"
STATE_UNSUPPORTED = "unsupported"

ACTION_CLOUD = "cloud"
ACTION_LOCAL = "local"
ACTION_BLOCKED = "blocked"

MASK = "********"


def parse_firmware(version: object) -> tuple[int, ...] | None:
    """Parse a dotted numeric firmware string; None for anything else."""
    if not isinstance(version, str):
        return None
    parts = version.strip().split(".")
    # Length cap: int() raises ValueError on absurdly long digit strings.
    if not all(p.isascii() and p.isdigit() and len(p) <= 9 for p in parts):
        return None
    return tuple(int(p) for p in parts)


def firmware_supports(version: object) -> bool:
    """True when the installed firmware is at or above the interface gate."""
    parsed = parse_firmware(version)
    return parsed is not None and parsed >= MIN_FIRMWARE


def is_gen2(model: object) -> bool:
    """True for Gen2 hardware identifiers (HOME_Eyes_* / *_GEN2)."""
    return isinstance(model, str) and (model.startswith("HOME_Eyes_") or model.endswith("_GEN2"))


def eligible(model: object, firmware: object) -> bool:
    """Only Gen2 cameras on qualifying firmware are queried; Gen1 never."""
    return is_gen2(model) and firmware_supports(firmware)


def state_from_response(status: int, body: object) -> str | None:
    """Map an HTTP result to a state string, or None when it is unknown."""
    if status == 200:
        if isinstance(body, dict) and isinstance(body.get("username"), str):
            return STATE_ACTIVE
        return None
    if status == 404:
        return STATE_INACTIVE
    if status == 449:
        return STATE_UNSUPPORTED
    return None


def fetch_state(session: requests.Session, cloud_api: str, cam_id: str) -> str | None:
    """GET the interface status; None when it cannot be determined."""
    try:
        resp = session.get(f"{cloud_api}/v11/video_inputs/{cam_id}/{STATUS_ENDPOINT}", timeout=10)
    except requests.exceptions.RequestException as err:
        _LOGGER.debug("local data interface status failed: %s", type(err).__name__)
        return None
    try:
        body: object = resp.json()
    except ValueError:
        body = None
    return state_from_response(resp.status_code, body)


def query_state(
    session: requests.Session, cloud_api: str, cam_id: str, model: object, firmware: object
) -> str | None:
    """fetch_state() for eligible cameras only; None (not queried) otherwise."""
    if not eligible(model, firmware):
        return None
    return fetch_state(session, cloud_api, cam_id)


def valid_password(password: object) -> bool:
    """A usable sticker password: non-blank text without control characters."""
    return (
        isinstance(password, str)
        and bool(password.strip())
        and not any(ord(c) < 32 or ord(c) == 127 for c in password)
    )


def get_password(cfg: dict[str, Any], cam_id: str) -> str | None:
    """Stored password for a camera, or None when missing/unusable."""
    passwords = cfg.get(PASSWORDS_KEY)
    if not isinstance(passwords, dict):
        return None
    password = passwords.get(cam_id)
    return password if valid_password(password) else None


def safe_lan_ip(ip: object) -> str | None:
    """Private IPv4 only: no loopback, link-local, unspecified or public."""
    if not isinstance(ip, str):
        return None
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None
    if addr.version != 4:
        return None
    if addr.is_loopback or addr.is_link_local or addr.is_unspecified or not addr.is_private:
        return None
    return str(addr)


def build_url(ip: str, password: str, quality: str = "high", audio: bool = True) -> str:
    """Credentialed local stream URL (caller must never log it unredacted).

    quality "low" selects inst=2, anything else inst=1 (high); audio adds AAC.
    """
    inst = INST_LOW if quality == "low" else INST_HIGH
    return (
        f"rtsps://{USER}:{quote(password, safe='')}@{ip}:{PORT}{STREAM_PATH}"
        f"?line={LINE}&inst={inst}&enableaudio={1 if audio else 0}"
    )


def plan_source(
    cfg: dict[str, Any],
    cam_id: str,
    state: str | None,
    lan_ip: object,
    quality: str = "high",
    audio: bool = True,
) -> tuple[str, str | None, str | None]:
    """Decide the stream source as (action, url, message).

    cloud   -> unchanged existing behaviour (interface not active or no password)
    local   -> url holds the LAN source; no cloud stream session may be opened
    blocked -> interface active with a password but no safe LAN address:
               fail closed, message explains why
    """
    if state != STATE_ACTIVE:
        return ACTION_CLOUD, None, None
    password = get_password(cfg, cam_id)
    if password is None:
        return (
            ACTION_CLOUD,
            None,
            "Local data interface is active: set the camera password with "
            "'local-data set-password <camera>' to stream locally.",
        )
    ip = safe_lan_ip(lan_ip)
    if ip is None:
        return (
            ACTION_BLOCKED,
            None,
            "Local data interface is active but no valid LAN IP is known: "
            "set one with 'lan-ips set <camera> <ip>'. No cloud stream is opened.",
        )
    return ACTION_LOCAL, build_url(ip, password, quality, audio), None
