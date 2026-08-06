#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import profiles


CONFIG_DIR = Path(__file__).resolve().parent
STATE_JSON_PATH = CONFIG_DIR / "state.json"
STATE_ENV_PATH = CONFIG_DIR / "state.env"
DEFAULT_RDP_PORT = int(os.environ.get("RDP_SERVER_PORT", "3389"))
DEFAULT_STALE_SECONDS = max(
    int(os.environ.get("KRDP_STALE_SECONDS", "120")), 0)
KRDP_USER_UNIT = "app-org.kde.krdpserver.service"


get_profile_for_ip = profiles.get_profile_for_ip
get_unknown_profile_name = profiles.get_unknown_profile_name


PID_PATTERN = re.compile(r"pid=(\d+)")
LOOPBACK_IPS = {"127.0.0.1", "::1", "localhost"}
KEYBOARD_LAYOUT_INDEX = {
    "English (US)": 0,
    "Spanish (Latin America)": 1,
    "Spanish": 2,
}


@dataclass(frozen=True)
class ConnectionInfo:
    client_ip: str
    connection_type: str
    rdp_port: int
    peer_port: int


@dataclass(frozen=True)
class ConnectionActivity:
    peer_port: int
    bytes_sent: int
    bytes_received: int
    last_send_ms: int
    last_recv_ms: int


@dataclass(frozen=True)
class State:
    profile: str
    profile_name: str
    client_ip: str
    client_source: str
    rdp_port: int
    connected: int
    connected_text: str
    keyboard: str
    resolution: str
    hostname: str


def log(message: str) -> None:
    print(f"[RDP] {message}")


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True)


def run_ss(arguments: list[str]) -> list[str]:
    result = run_command(["ss", *arguments])
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def run_ss_blocks(arguments: list[str]) -> list[list[str]]:
    result = run_command(["ss", *arguments])
    if result.returncode != 0:
        return []

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        if line[0].isspace():
            if current:
                current.append(line.strip())
            continue
        if current:
            blocks.append(current)
        current = [line.strip()]

    if current:
        blocks.append(current)

    return blocks


def normalize_ip(value: str) -> str:
    value = value.strip()
    if value.startswith("::ffff:"):
        return value[7:]
    return value


def parse_endpoint(endpoint: str) -> tuple[str, int | None]:
    endpoint = endpoint.strip()
    if not endpoint:
        return "", None

    if endpoint.startswith("["):
        host, _, remainder = endpoint[1:].partition("]")
        port = remainder[1:] if remainder.startswith(":") else ""
        return normalize_ip(host), int(port) if port.isdigit() else None

    if endpoint.count(":") == 1:
        host, port = endpoint.rsplit(":", 1)
        return normalize_ip(host), int(port) if port.isdigit() else None

    return normalize_ip(endpoint), None


def get_socket_endpoints(parts: list[str]) -> tuple[str, str] | None:
    if len(parts) < 4:
        return None

    if parts[0].isalpha():
        if len(parts) < 5:
            return None
        return parts[3], parts[4]

    return parts[2], parts[3]


def is_loopback(ip_address: str) -> bool:
    if ip_address in LOOPBACK_IPS or ip_address.startswith("127."):
        return True
    try:
        return socket.gethostbyname(ip_address).startswith("127.")
    except OSError:
        return False


def extract_pid(line: str) -> str | None:
    match = PID_PATTERN.search(line)
    return match.group(1) if match else None


def read_process_environment(pid: str) -> dict[str, str]:
    environ_path = Path("/proc") / pid / "environ"
    try:
        raw = environ_path.read_bytes()
    except OSError:
        return {}

    environment: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        environment[key.decode(errors="ignore")] = value.decode(
            errors="ignore")
    return environment


def resolve_ssh_origin_ip(pid: str, fallback_ip: str) -> str:
    environment = read_process_environment(pid)
    for variable in ("SSH_CONNECTION", "SSH_CLIENT"):
        value = environment.get(variable)
        if not value:
            continue

        candidate = normalize_ip(value.split()[0])
        if candidate and not is_loopback(candidate):
            return candidate

    return fallback_ip


def detect_rdp_server_port() -> int:
    for line in run_ss(["-Hltnp"]):
        if "krdpserver" not in line:
            continue

        parts = line.split()
        endpoints = get_socket_endpoints(parts)
        if endpoints is None:
            continue

        local_endpoint, _ = endpoints
        _, port = parse_endpoint(local_endpoint)
        if port is not None:
            return port

    return DEFAULT_RDP_PORT


def detect_connection(rdp_port: int) -> ConnectionInfo | None:
    direct_connection: ConnectionInfo | None = None
    loopback_connection: ConnectionInfo | None = None
    ssh_remote_by_pid: dict[str, str] = {}
    ssh_tunnel_pid_by_port: dict[int, str] = {}
    unique_ssh_remote_ips: set[str] = set()

    for line in run_ss(["-Htnp", "state", "established"]):
        parts = line.split()
        endpoints = get_socket_endpoints(parts)
        if endpoints is None:
            continue

        local_endpoint, peer_endpoint = endpoints
        _, local_port = parse_endpoint(local_endpoint)
        peer_ip, peer_port = parse_endpoint(peer_endpoint)
        pid = extract_pid(line)

        if local_port == rdp_port and peer_port is not None:
            candidate = ConnectionInfo(
                client_ip=peer_ip,
                connection_type="direct" if not is_loopback(
                    peer_ip) else "loopback",
                rdp_port=rdp_port,
                peer_port=peer_port,
            )
            if is_loopback(peer_ip):
                loopback_connection = candidate
            else:
                direct_connection = candidate

        if pid and local_port == 22 and peer_ip and not is_loopback(peer_ip):
            resolved_peer_ip = resolve_ssh_origin_ip(pid, peer_ip)
            ssh_remote_by_pid[pid] = resolved_peer_ip
            unique_ssh_remote_ips.add(resolved_peer_ip)

        if pid and local_port is not None and local_port not in {22} and peer_port == rdp_port:
            ssh_tunnel_pid_by_port[local_port] = pid

    if direct_connection is not None:
        return direct_connection

    if loopback_connection is None:
        return None

    tunnel_pid = ssh_tunnel_pid_by_port.get(loopback_connection.peer_port)
    if tunnel_pid and tunnel_pid in ssh_remote_by_pid:
        return ConnectionInfo(
            client_ip=ssh_remote_by_pid[tunnel_pid],
            connection_type="ssh_tunnel",
            rdp_port=rdp_port,
            peer_port=loopback_connection.peer_port,
        )

    if len(unique_ssh_remote_ips) == 1:
        return ConnectionInfo(
            client_ip=next(iter(unique_ssh_remote_ips)),
            connection_type="ssh_tunnel",
            rdp_port=rdp_port,
            peer_port=loopback_connection.peer_port,
        )

    return loopback_connection


def parse_stat_value(stats_line: str, key: str) -> int | None:
    match = re.search(rf"\b{re.escape(key)}:(\d+)", stats_line)
    if match is None:
        return None
    return int(match.group(1))


def detect_connection_activity(
    rdp_port: int,
    connection: ConnectionInfo,
) -> ConnectionActivity | None:
    for block in run_ss_blocks(["-tinp", "state", "established"]):
        parts = block[0].split()
        endpoints = get_socket_endpoints(parts)
        if endpoints is None:
            continue

        local_endpoint, peer_endpoint = endpoints
        _, local_port = parse_endpoint(local_endpoint)
        _, peer_port = parse_endpoint(peer_endpoint)
        if local_port != rdp_port or peer_port != connection.peer_port:
            continue

        stats_line = " ".join(block[1:]) if len(block) > 1 else ""
        bytes_sent = parse_stat_value(stats_line, "bytes_sent")
        bytes_received = parse_stat_value(stats_line, "bytes_received")
        last_send_ms = parse_stat_value(stats_line, "lastsnd")
        last_recv_ms = parse_stat_value(stats_line, "lastrcv")
        if any(v is None for v in {bytes_sent, bytes_received, last_send_ms, last_recv_ms}):
            return None

        return ConnectionActivity(
            peer_port=connection.peer_port,
            bytes_sent=bytes_sent,  # type: ignore
            bytes_received=bytes_received,  # type: ignore
            last_send_ms=last_send_ms,  # type: ignore
            last_recv_ms=last_recv_ms,  # type: ignore
        )

    return None


def is_same_activity(current: ConnectionActivity, previous: ConnectionActivity | None) -> bool:
    if previous is None:
        return False
    return (
        current.peer_port == previous.peer_port
        and current.bytes_sent == previous.bytes_sent
        and current.bytes_received == previous.bytes_received
    )


def is_activity_stale(activity: ConnectionActivity, stale_seconds: int) -> bool:
    stale_ms = stale_seconds * 1000
    return activity.last_send_ms >= stale_ms and activity.last_recv_ms >= stale_ms


def restart_krdp_service() -> bool:
    result = run_command(["systemctl", "--user", "restart", KRDP_USER_UNIT])
    if result.returncode == 0:
        log(f"Restarted {KRDP_USER_UNIT}")
        return True

    details = result.stderr.strip() or result.stdout.strip() or "unknown error"
    log(f"Failed to restart {KRDP_USER_UNIT}: {details}")
    return False


def build_test_connection(client_ip: str, source: str, rdp_port: int) -> ConnectionInfo:
    return ConnectionInfo(
        client_ip=client_ip,
        connection_type=source,
        rdp_port=rdp_port,
        peer_port=rdp_port,
    )


def build_state(connection: ConnectionInfo) -> State:
    profile = get_profile_for_ip(connection.client_ip)
    connected_at = int(time.time())
    connected_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hostname = socket.gethostname()

    if profile is None:
        return State(
            profile="unknown",
            profile_name=get_unknown_profile_name(),
            client_ip=connection.client_ip,
            client_source=connection.connection_type,
            rdp_port=connection.rdp_port,
            connected=connected_at,
            connected_text=connected_text,
            keyboard="Unknown",
            resolution="Unknown",
            hostname=hostname,
        )

    return State(
        profile=profile.key,
        profile_name=profile.name,
        client_ip=connection.client_ip,
        client_source=connection.connection_type,
        rdp_port=connection.rdp_port,
        connected=connected_at,
        connected_text=connected_text,
        keyboard=profile.keyboard.name,
        resolution=profile.resolution,
        hostname=hostname,
    )


def shell_quote(value: object) -> str:
    return shlex.quote(str(value))


def write_state(state: State) -> None:
    payload = asdict(state)
    STATE_JSON_PATH.write_text(json.dumps(
        payload, indent=2) + "\n", encoding="utf-8")
    env_lines = [
        f"PROFILE={shell_quote(state.profile)}",
        f"PROFILE_NAME={shell_quote(state.profile_name)}",
        f"CLIENT_IP={shell_quote(state.client_ip)}",
        f"CLIENT_SOURCE={shell_quote(state.client_source)}",
        f"RDP_PORT={shell_quote(state.rdp_port)}",
        f"CONNECTED={shell_quote(state.connected)}",
        f"CONNECTED_TEXT={shell_quote(state.connected_text)}",
        f"KEYBOARD={shell_quote(state.keyboard)}",
        f"RESOLUTION={shell_quote(state.resolution)}",
        f"HOSTNAME={shell_quote(state.hostname)}",
    ]
    STATE_ENV_PATH.write_text("\n".join(env_lines) + "\n", encoding="utf-8")


def clear_state() -> None:
    STATE_JSON_PATH.unlink(missing_ok=True)
    STATE_ENV_PATH.unlink(missing_ok=True)


def load_state() -> State | None:
    if not STATE_JSON_PATH.exists():
        return None
    try:
        payload = json.loads(STATE_JSON_PATH.read_text(encoding="utf-8"))
        return State(**payload)
    except (OSError, TypeError, json.JSONDecodeError):
        return None


def apply_keyboard(layout_name: str) -> None:
    layout_index = KEYBOARD_LAYOUT_INDEX.get(layout_name)
    if layout_index is None:
        return

    result = run_command(
        [
            "busctl",
            "--user",
            "call",
            "org.kde.keyboard",
            "/Layouts",
            "org.kde.KeyboardLayouts",
            "setLayout",
            "u",
            str(layout_index),
        ]
    )
    if result.returncode != 0:
        log(f"Keyboard update failed: {result.stderr.strip() or result.stdout.strip()}")


def apply_display(resolution: str) -> None:
    result = run_command(
        ["kscreen-doctor", f"output.Virtual-1.mode.{resolution}"])
    if result.returncode != 0:
        log(f"Display update failed: {result.stderr.strip() or result.stdout.strip()}")


def apply_state(state: State, dry_run: bool) -> None:
    if state.profile == "unknown":
        log(f"Unknown client identity: {state.client_ip} ({state.client_source})")
        write_state(state)
        return

    log(f"Applying {state.profile_name} for {state.client_ip} via {state.client_source}")
    if not dry_run:
        apply_keyboard(state.keyboard)
        apply_display(state.resolution)
    write_state(state)


def states_match(current: State | None, previous: State | None) -> bool:
    if current is None or previous is None:
        return False
    return (
        current.profile == previous.profile
        and current.client_ip == previous.client_ip
        and current.client_source == previous.client_source
        and current.rdp_port == previous.rdp_port
    )


def monitor(
    once: bool,
    dry_run: bool,
    interval: float,
    test_client_ip: str | None,
    test_source: str,
    test_rdp_port: int | None,
    stale_seconds: int,
) -> int:
    previous_activity: ConnectionActivity | None = None
    try:
        while True:
            if test_client_ip is not None:
                rdp_port = test_rdp_port or detect_rdp_server_port()
                connection = build_test_connection(
                    test_client_ip, test_source, rdp_port)
                log(f"Using test client {test_client_ip} via {test_source}")
            else:
                rdp_port = detect_rdp_server_port()
                connection = detect_connection(rdp_port)

            if connection is None:
                previous_activity = None
                clear_state()
                if once:
                    log("No active RDP session detected")
                if once:
                    return 0
                time.sleep(interval)
                continue

            activity = detect_connection_activity(rdp_port, connection)
            if (
                stale_seconds > 0
                and activity is not None
                and is_same_activity(activity, previous_activity)
                and is_activity_stale(activity, stale_seconds)
            ):
                log(
                    f"Dropping stale KRDP session for port {connection.peer_port} after {stale_seconds}s idle"
                )
                restart_krdp_service()
                previous_activity = None
                clear_state()
                if once:
                    return 0
                time.sleep(interval)
                continue

            previous_activity = activity

            next_state = build_state(connection)
            previous_state = load_state()

            if not states_match(next_state, previous_state) or previous_state is None:
                apply_state(next_state, dry_run=dry_run)
            elif not STATE_ENV_PATH.exists():
                write_state(previous_state)

            if once:
                return 0

            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor RDP sessions and apply local profiles.")
    parser.add_argument("action", nargs="?", choices=[
                        "stop", "restart-server"], help="Manage the monitor or KRDP server state.")
    parser.add_argument("--once", action="store_true",
                        help="Inspect the current state once and exit.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Detect and write state without changing keyboard or display.")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Polling interval in seconds.")
    parser.add_argument(
        "--client-ip", help="Test mode: build state for this client IP without requiring a live RDP session.")
    parser.add_argument(
        "--source",
        choices=["direct", "ssh_tunnel", "loopback"],
        default="direct",
        help="Test mode: client source to write into the generated state.",
    )
    parser.add_argument("--rdp-port", type=int,
                        help="Override the detected RDP port in test mode.")
    parser.add_argument(
        "--stale-seconds",
        type=int,
        default=DEFAULT_STALE_SECONDS,
        help="Restart KRDP when the same RDP socket is idle for this many seconds. Use 0 to disable.",
    )
    return parser.parse_args(list(argv))


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)

    if args.action == "stop":
        clear_state()
        return 0

    if args.action == "restart-server":
        return 0 if restart_krdp_service() else 1

    return monitor(
        once=args.once,
        dry_run=args.dry_run,
        interval=max(args.interval, 0.2),
        test_client_ip=args.client_ip,
        test_source=args.source,
        test_rdp_port=args.rdp_port,
        stale_seconds=max(args.stale_seconds, 0),
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
