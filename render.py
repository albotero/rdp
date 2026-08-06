#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path


CONFIG_DIR = Path(__file__).resolve().parent
STATE_JSON_PATH = CONFIG_DIR / "state.json"
STATE_ENV_PATH = CONFIG_DIR / "state.env"


def parse_endpoint_port(endpoint: str) -> int | None:
    endpoint = endpoint.strip()
    if not endpoint:
        return None
    if endpoint.startswith("["):
        _, _, remainder = endpoint[1:].partition("]")
        port = remainder[1:] if remainder.startswith(":") else ""
        return int(port) if port.isdigit() else None
    if endpoint.count(":") == 1:
        _, port = endpoint.rsplit(":", 1)
        return int(port) if port.isdigit() else None
    return None


def get_socket_endpoints(parts: list[str]) -> tuple[str, str] | None:
    if len(parts) < 4:
        return None

    if parts[0].isalpha():
        if len(parts) < 5:
            return None
        return parts[3], parts[4]

    return parts[2], parts[3]


def has_active_rdp_session(rdp_port: int) -> bool:
    result = subprocess.run(
        ["ss", "-Htn", "state", "established"],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return False

    for line in result.stdout.splitlines():
        parts = line.split()
        endpoints = get_socket_endpoints(parts)
        if endpoints is None:
            continue
        local_endpoint, peer_endpoint = endpoints
        local_port = parse_endpoint_port(local_endpoint)
        peer_port = parse_endpoint_port(peer_endpoint)
        if local_port == rdp_port or peer_port == rdp_port:
            return True
    return False


def load_json_state() -> dict[str, object] | None:
    if not STATE_JSON_PATH.exists():
        return None
    try:
        return json.loads(STATE_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_env_state() -> dict[str, object] | None:
    if not STATE_ENV_PATH.exists():
        return None

    state: dict[str, object] = {}
    for line in STATE_ENV_PATH.read_text(encoding="utf-8").splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        state[key.lower()] = value.strip().strip("'\"")
    return state or None


def load_state() -> dict[str, object] | None:
    return load_json_state() or load_env_state()


def format_elapsed(connected_epoch: int) -> str:
    elapsed = max(int(datetime.now().timestamp()) - connected_epoch, 0)
    days, remainder = divmod(elapsed, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days > 0:
        return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def main(argv: list[str]) -> int:
    force = "--force" in argv
    state = load_state()
    if state is None:
        return 0

    rdp_port = int(str(state.get("rdp_port") or state.get("RDP_PORT") or 3389))
    if not force and not has_active_rdp_session(rdp_port):
        STATE_JSON_PATH.unlink(missing_ok=True)
        STATE_ENV_PATH.unlink(missing_ok=True)
        return 0

    client_source = str(state.get("client_source")
                        or state.get("CLIENT_SOURCE") or "direct")
    client_ip = str(state.get("client_ip")
                    or state.get("CLIENT_IP") or "Unknown")
    profile_name = str(state.get("profile_name")
                       or state.get("PROFILE_NAME") or "Unknown")
    connected_text = str(state.get("connected_text")
                         or state.get("CONNECTED_TEXT") or "Unknown")
    connected_value = state.get("connected") or state.get("CONNECTED") or 0
    connected_epoch = int(connected_value) if isinstance(connected_value, (int, str, bytes)) else 0
    resolution = str(state.get("resolution")
                     or state.get("RESOLUTION") or "Unknown")
    keyboard = str(state.get("keyboard") or state.get("KEYBOARD") or "Unknown")

    if client_source == "ssh_tunnel":
        client_ip_display = f"{client_ip} (ssh)"
    elif client_source == "loopback":
        client_ip_display = f"{client_ip} (loopback)"
    else:
        client_ip_display = client_ip

    rows = [
        ("Location", profile_name),
        ("Client IP", client_ip_display),
        ("Connected", connected_text),
        ("Elapsed", format_elapsed(connected_epoch)),
        ("Display", resolution),
        ("Keyboard", keyboard),
    ]

    for label, value in rows:
        print(f"{label:<12} {value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
