# RDP Profile Monitor

This directory contains a small KDE Plasma automation setup for KRDP.

It does three things:

1. Detects the current RDP client.
2. Applies a local display and keyboard profile for that client.
3. Exposes the current session state for Conky or other status tooling.

The project is built around three Python entrypoints:

- `rdp-monitor.py`: watches KRDP connections, applies profiles, and writes session state.
- `profiles.py`: loads and validates profile definitions from `profiles.json`.
- `render.py`: renders the current session state for Conky.

## Files

- `profiles.json`: profile definitions, display presets, keyboard presets, and IP matching rules.
- `profiles.py`: loader and matcher for `profiles.json`.
- `rdp-monitor.py`: main monitor.
- `render.py`: status renderer.
- `conky.conf`: Conky widget that shows the active session.
- `install-rdp-profile-service.sh`: installs a user systemd service for the monitor.
- `state.json`: generated runtime state.
- `state.env`: generated shell-style runtime state.

## Requirements

This setup expects:

- Linux
- KDE Plasma with KRDP enabled
- `python3`
- `ss`
- `busctl`
- `kscreen-doctor`
- `systemctl --user`

Optional:

- Conky, if you want the status widget

Python note:

- The project uses only the Python standard library.
- A `requirements.txt` file is not needed unless you later add third-party Python packages.
- The virtual environment created by `install-rdp-profile-service.sh` is only used to provide an isolated Python interpreter path for the user service.

## How It Works

`rdp-monitor.py` polls the local RDP server port and active TCP sessions.

- Direct RDP connections are matched from the peer IP.
- SSH-tunneled connections try to recover the original client IP from the SSH process environment (`SSH_CONNECTION` or `SSH_CLIENT`).
- If no specific mapping matches, the fallback rule in `profiles.json` is used.

When a profile is selected, the monitor:

- switches the keyboard layout through D-Bus
- switches the virtual display mode through `kscreen-doctor`
- writes the current state to `state.json` and `state.env`

## Configuration

All profile configuration lives in `profiles.json`.

### Keyboards

The `keyboards` object maps a short key to:

- `index`: KDE keyboard layout index
- `name`: label written to state files

Example:

```json
"keyboards": {
  "us": {
    "index": 0,
    "name": "English (US)"
  }
}
```

### Display Modes

The `display_modes` object maps a short key to a `kscreen-doctor` mode string.

Example:

```json
"display_modes": {
  "4k": "1920x1080@60"
}
```

### Profiles

Each profile references one keyboard and one display mode.

Example:

```json
"profiles": {
  "home4k": {
    "name": "Home 4K",
    "keyboard": "us",
    "display": "4k"
  }
}
```

### IP Rules

The `ip_profile_rules` array is evaluated from top to bottom. The first matching rule wins.

Patterns use shell-style wildcards through `fnmatch`.

Example:

```json
"ip_profile_rules": [
  { "pattern": "172.17.20.10", "profile": "home4k" },
  { "pattern": "172.17.20.*", "profile": "home" },
  { "pattern": "*.*.*.*", "profile": "default" }
]
```

Keep specific matches above broader ones.

## Usage

Run once:

```bash
python3 ~/.config/rdp/rdp-monitor.py --once
```

Run continuously:

```bash
python3 ~/.config/rdp/rdp-monitor.py
```

Dry run without changing keyboard or display:

```bash
python3 ~/.config/rdp/rdp-monitor.py --dry-run
```

Test a client IP without a live RDP session:

```bash
python3 ~/.config/rdp/rdp-monitor.py --once --client-ip 172.17.20.10 --source direct
```

Clear saved session state:

```bash
python3 ~/.config/rdp/rdp-monitor.py stop
```

Restart the KRDP user service manually:

```bash
python3 ~/.config/rdp/rdp-monitor.py restart-server
```

If your local file is not marked executable, invoke it the same way the user service does:

```bash
~/.config/rdp/.venv/bin/python ~/.config/rdp/rdp-monitor.py restart-server
```

## Stale Session Watchdog

KRDP can sometimes keep a disconnected session alive. When that happens, the monitor may keep seeing the old session instead of applying the next client profile.

To mitigate that, `rdp-monitor.py` includes a stale-session watchdog.

If the same RDP socket stays idle long enough, the monitor restarts:

- `app-org.kde.krdpserver.service`

Default timeout:

- `120` seconds

Override it on the command line:

```bash
python3 ~/.config/rdp/rdp-monitor.py --stale-seconds 90
```

Or with an environment variable:

```bash
export KRDP_STALE_SECONDS=90
```

Disable the watchdog completely:

```bash
python3 ~/.config/rdp/rdp-monitor.py --stale-seconds 0
```

## Service Installation

Install the user service:

```bash
./install-rdp-profile-service.sh
```

What the installer does:

- creates `~/.config/rdp/.venv` if it does not exist
- writes `~/.config/systemd/user/rdp-profile.service`
- reloads the user systemd daemon
- enables and starts the service

The service runs:

```text
~/.config/rdp/.venv/bin/python ~/.config/rdp/rdp-monitor.py
```

Useful service commands:

```bash
systemctl --user status rdp-profile.service
systemctl --user restart rdp-profile.service
systemctl --user stop rdp-profile.service
journalctl --user-unit rdp-profile.service -f
```

## Conky Integration

`conky.conf` renders the session summary with:

```lua
${execi 1 python3 ~/.config/rdp/render.py}
```

`render.py` shows:

- location/profile name
- client IP
- connection time
- elapsed time
- display mode
- keyboard layout

## Runtime State

Two generated files describe the current session:

- `state.json`: JSON payload for programmatic use
- `state.env`: shell-style key/value file for simple consumers

Typical keys:

- `PROFILE`
- `PROFILE_NAME`
- `CLIENT_IP`
- `CLIENT_SOURCE`
- `RDP_PORT`
- `CONNECTED`
- `CONNECTED_TEXT`
- `KEYBOARD`
- `RESOLUTION`
- `HOSTNAME`

These files are deleted automatically when no active RDP session is detected.

## Troubleshooting

### Profile is not applied

- Run `python3 ~/.config/rdp/rdp-monitor.py --once --dry-run`.
- Check whether the detected `CLIENT_IP` matches a rule in `profiles.json`.
- Make sure the most specific rule appears before broader wildcard rules.

### Keyboard does not change

- Verify the keyboard `index` values match your KDE layout order.
- Check whether `busctl --user` works in your user session.

### Display does not change

- Verify the mode string exists for `Virtual-1` with `kscreen-doctor -o`.

### Stale session is not dropped

- Lower `--stale-seconds` or `KRDP_STALE_SECONDS`.
- Inspect KRDP with `systemctl --user status app-org.kde.krdpserver.service`.
- Check monitor logs with `journalctl --user-unit rdp-profile.service -f`.

### Verify KRDP restart actually happened

Use the KRDP MainPID before and after a manual restart action:

```bash
systemctl --user show -p MainPID --value app-org.kde.krdpserver.service
~/.config/rdp/.venv/bin/python ~/.config/rdp/rdp-monitor.py restart-server
systemctl --user show -p MainPID --value app-org.kde.krdpserver.service
```

Expected result:

- The monitor prints `[RDP] Restarted app-org.kde.krdpserver.service`.
- The second MainPID value differs from the first.

To check whether automatic stale-session restarts were triggered:

```bash
journalctl --user -u rdp-profile.service --since "24 hours ago" --no-pager \
  | grep -E "Dropping stale KRDP session|Restarted app-org.kde.krdpserver.service|Failed to restart app-org.kde.krdpserver.service"
```

## Notes

- This setup is opinionated toward a single-user Plasma session.
- The monitor edits local desktop state immediately when a profile changes.
- Direct connections are usually the most reliable path for profile matching.
