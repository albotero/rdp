#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
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
DEFAULT_DISCONNECT_RESTART_SECONDS = max(
    int(os.environ.get("KRDP_DISCONNECT_RESTART_SECONDS", "20")), 0)
KRDP_USER_UNIT = "app-org.kde.krdpserver.service"


get_profile_for_ip = profiles.get_profile_for_ip
get_unknown_profile_name = profiles.get_unknown_profile_name


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


def read_loginctl_session_properties(session_id: str) -> dict[str, str]:
    result = run_command([
        "loginctl",
        "show-session",
        session_id,
        "-p",
        "Service",
        "-p",
        "Remote",
        "-p",
        "RemoteHost",
        "-p",
        "State",
    ])
    if result.returncode != 0:
        return {}

    properties: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        properties[key.strip()] = value.strip()
    return properties


def find_ssh_remote_host() -> str | None:
    result = run_command(["loginctl", "list-sessions", "--no-legend"])
    if result.returncode != 0:
        return None

    active_hosts: list[str] = []
    fallback_hosts: list[str] = []

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 1:
            continue

        session_id = parts[0]
        properties = read_loginctl_session_properties(session_id)
        if properties.get("Service") != "sshd":
            continue
        if properties.get("Remote", "no") != "yes":
            continue

        remote_host = normalize_ip(properties.get("RemoteHost", ""))
        if not remote_host or is_loopback(remote_host):
            continue

        if properties.get("State") == "active":
            active_hosts.append(remote_host)
        else:
            fallback_hosts.append(remote_host)

    unique_active_hosts = list(dict.fromkeys(active_hosts))
    if len(unique_active_hosts) == 1:
        return unique_active_hosts[0]

    unique_fallback_hosts = list(dict.fromkeys(fallback_hosts))
    if len(unique_fallback_hosts) == 1:
        return unique_fallback_hosts[0]

    return None


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

    for line in run_ss(["-Htnp", "state", "established"]):
        parts = line.split()
        endpoints = get_socket_endpoints(parts)
        if endpoints is None:
            continue

        local_endpoint, peer_endpoint = endpoints
        _, local_port = parse_endpoint(local_endpoint)
        peer_ip, peer_port = parse_endpoint(peer_endpoint)

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

    if direct_connection is not None:
        return direct_connection

    if loopback_connection is None:
        return None

    ssh_remote_host = find_ssh_remote_host()
    if ssh_remote_host is not None:
        return ConnectionInfo(
            client_ip=ssh_remote_host,
            connection_type="ssh_tunnel",
            rdp_port=rdp_port,
            peer_port=loopback_connection.peer_port,
        )

    return loopback_connection


def get_unit_main_pid(unit_name: str) -> int | None:
    result = run_command(["systemctl", "--user", "show",
                         "-p", "MainPID", "--value", unit_name])
    if result.returncode != 0:
        return None

    value = (result.stdout or "").strip()
    if not value.isdigit():
        return None

    pid = int(value)
    if pid <= 0:
        return None
    return pid


def restart_krdp_service() -> bool:
    before_pid = get_unit_main_pid(KRDP_USER_UNIT)
    result = run_command(["systemctl", "--user", "restart", KRDP_USER_UNIT])
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "unknown error"
        log(f"Failed to restart {KRDP_USER_UNIT}: {details}")
        return False

    after_pid = get_unit_main_pid(KRDP_USER_UNIT)
    if before_pid is not None and after_pid == before_pid:
        log(
            f"Restart kept same KRDP PID {after_pid}; forcing stop/start for a clean session reset"
        )
        stop_result = run_command(
            ["systemctl", "--user", "stop", KRDP_USER_UNIT])
        if stop_result.returncode != 0:
            details = stop_result.stderr.strip() or stop_result.stdout.strip() or "unknown error"
            log(f"Failed to stop {KRDP_USER_UNIT}: {details}")
            return False

        start_result = run_command(
            ["systemctl", "--user", "start", KRDP_USER_UNIT])
        if start_result.returncode != 0:
            details = start_result.stderr.strip() or start_result.stdout.strip() or "unknown error"
            log(f"Failed to start {KRDP_USER_UNIT}: {details}")
            return False

        final_pid = get_unit_main_pid(KRDP_USER_UNIT)
        if final_pid is not None:
            log(f"Force-restarted {KRDP_USER_UNIT} (PID {before_pid} -> {final_pid})")
        else:
            log(f"Force-restarted {KRDP_USER_UNIT}")
        return True

    if before_pid is not None and after_pid is not None:
        log(f"Restarted {KRDP_USER_UNIT} (PID {before_pid} -> {after_pid})")
    else:
        log(f"Restarted {KRDP_USER_UNIT}")
    return True


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

    if find_wayland_display() is None:
        load_x11_layouts()

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


def find_x11_display() -> str | None:
    if os.environ.get("DISPLAY"):
        return os.environ["DISPLAY"]
    for socket_path in sorted(Path("/tmp/.X11-unix").glob("X[0-9]*")):
        if socket_path.stat().st_uid == os.getuid():
            return f":{socket_path.name[1:]}"
    return None


def load_x11_layouts() -> None:
    # xrdp loads only the client's layout, so KDE's group index would wrap back to "us".
    display = find_x11_display()
    if display is None:
        return
    layouts = ",".join(
        kb.key for kb in sorted(profiles.KEYBOARDS.values(), key=lambda kb: kb.index))
    result = subprocess.run(
        ["setxkbmap", "-model", "pc105", "-layout", layouts],
        check=False, text=True, capture_output=True,
        env={**os.environ, "DISPLAY": display})
    if result.returncode != 0:
        log(f"setxkbmap failed: {result.stderr.strip() or result.stdout.strip()}")


def find_wayland_display() -> str | None:
    runtime_dir = Path(os.environ.get(
        "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    for socket_path in sorted(runtime_dir.glob("wayland-[0-9]*")):
        if socket_path.is_socket():
            return socket_path.name
    return None


def apply_display(resolution: str) -> None:
    wayland_display = find_wayland_display()
    if wayland_display is None:
        # No Plasma Wayland session (e.g. xrdp X11): the client sets resolution itself.
        log("Skipping display update: no Wayland session for kscreen-doctor")
        return

    # KRDP/KScreen may report the session before the virtual output is
    # fully initialized. Give KScreen a moment before changing the mode.
    time.sleep(3)

    env = {**os.environ, "WAYLAND_DISPLAY": wayland_display,
           "QT_QPA_PLATFORM": "wayland"}
    env.pop("DISPLAY", None)
    result = subprocess.run(
        ["kscreen-doctor", f"output.Virtual-1.mode.{resolution}"],
        check=False, text=True, capture_output=True, env=env)
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
        and current.keyboard == previous.keyboard
        and current.resolution == previous.resolution
    )


def monitor(
    once: bool,
    dry_run: bool,
    interval: float,
    test_client_ip: str | None,
    test_source: str,
    test_rdp_port: int | None,
    disconnect_restart_seconds: int,
) -> int:
    disconnected_since: float | None = None
    disconnect_restart_done = False
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
                clear_state()
                if disconnected_since is None:
                    disconnected_since = time.time()
                    disconnect_restart_done = False

                if (
                    not once
                    and test_client_ip is None
                    and disconnect_restart_seconds > 0
                    and not disconnect_restart_done
                    and disconnected_since is not None
                    and (time.time() - disconnected_since) >= disconnect_restart_seconds
                ):
                    log(
                        f"No active RDP session for {disconnect_restart_seconds}s; restarting {KRDP_USER_UNIT}"
                    )
                    restart_krdp_service()
                    disconnect_restart_done = True

                if once:
                    log("No active RDP session detected")
                if once:
                    return 0
                time.sleep(interval)
                continue

            disconnected_since = None
            disconnect_restart_done = False

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
        "--disconnect-restart-seconds",
        type=int,
        default=DEFAULT_DISCONNECT_RESTART_SECONDS,
        help="Restart KRDP after this many seconds with no active RDP session. Use 0 to disable.",
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
        test_source=args.test_source if hasattr(
            args, "test_source") else args.source,
        test_rdp_port=args.rdp_port,
        disconnect_restart_seconds=max(args.disconnect_restart_seconds, 0),
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
