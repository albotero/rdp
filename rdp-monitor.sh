#!/usr/bin/env bash

set -eo pipefail

CONFIG_DIR="$HOME/.config/rdp"
STATE_FILE="$CONFIG_DIR/state.env"

source "$CONFIG_DIR/profiles.sh"

LAST_IP=""

log() {
    echo "[RDP] $*"
}

get_rdp_server_port() {
    local port

    port="$(ss -Hltnp 2>/dev/null | awk '
        function endpoint_port(ep, m) {
            if (match(ep, /:([0-9]+)$/, m))
                return m[1]
            return ""
        }

        /krdpserver/ {
            print endpoint_port($4)
            exit
        }
    ')"

    if [[ -z "$port" ]]
    then
        port="${RDP_SERVER_PORT:-3389}"
    fi

    echo "$port"
}

get_client_identity() {
    local rdp_port="$1"

    ss -Htnp state established | awk -v target_rdp_port="$rdp_port" '
        function endpoint_ip(ep, ip) {
            ip = ep
            gsub(/^\[/, "", ip)
            sub(/\](:[0-9]+)?$/, "", ip)
            sub(/:[0-9]+$/, "", ip)
            sub(/^::ffff:/, "", ip)
            return ip
        }

        function endpoint_port(ep, m) {
            if (match(ep, /:([0-9]+)$/, m))
                return m[1]
            return ""
        }

        function is_loopback(ip) {
            return (ip == "127.0.0.1" || ip == "::1" || ip ~ /^127\./)
        }

        {
            local_ep = $4
            peer_ep = $5
            local_port = endpoint_port(local_ep)
            peer_port = endpoint_port(peer_ep)
            local_ip = endpoint_ip(local_ep)
            peer_ip = endpoint_ip(peer_ep)
            pid = ""

            if (match($0, /pid=([0-9]+)/, p))
                pid = p[1]

            if (local_port == target_rdp_port) {
                rdp_local_port = local_port
                rdp_peer_ip = peer_ip
                rdp_peer_port = peer_port
            }

            if (pid != "" && local_port == "22" && !is_loopback(peer_ip))
                ssh_remote_by_pid[pid] = peer_ip

            if (pid != "" && local_port != "22" && peer_port == target_rdp_port) {
                ssh_tunnel_pid_by_port[local_port] = pid
            }

            if (local_port == "22" && !is_loopback(peer_ip))
                ssh_remote_set[peer_ip] = 1
        }

        END {
            if (rdp_peer_ip == "") {
                print "||" target_rdp_port
                exit
            }

            if (!is_loopback(rdp_peer_ip)) {
                print rdp_peer_ip "|direct|" rdp_local_port
                exit
            }

            tunnel_pid = ssh_tunnel_pid_by_port[rdp_peer_port]
            if (tunnel_pid != "" && ssh_remote_by_pid[tunnel_pid] != "") {
                print ssh_remote_by_pid[tunnel_pid] "|ssh_tunnel|" rdp_local_port
                exit
            }

            unique_count = 0
            for (ip in ssh_remote_set) {
                unique_ip = ip
                unique_count++
            }

            if (unique_count == 1) {
                print unique_ip "|ssh_tunnel|" rdp_local_port
                exit
            }

            print rdp_peer_ip "|loopback|" rdp_local_port
        }
    '
}

find_profile() {

    local ip="$1"

    for pattern in "${!IP_PROFILE[@]}"
    do
        [[ "$ip" == $pattern ]] && {
            echo "${IP_PROFILE[$pattern]}"
            return
        }
    done

    echo "unknown"
}

write_state() {

    local profile="$1"
    local ip="$2"
    local source="$3"
    local rdp_port="$4"
    local keyboard="$5"
    local display="$6"

cat > "$STATE_FILE" <<EOF
PROFILE="$profile"
PROFILE_NAME="${PROFILE_NAME[$profile]}"
CLIENT_IP="$ip"
CLIENT_SOURCE="$source"
RDP_PORT="$rdp_port"
CONNECTED="$(date +%s)"
CONNECTED_TEXT="$(date '+%Y-%m-%d %H:%M:%S')"
KEYBOARD="${KEYBOARD_NAME[$keyboard]}"
RESOLUTION="${DISPLAY_MODE[$display]}"
HOSTNAME="$(hostname)"
EOF
}

apply_profile() {

    local profile="$1"
    local ip="$2"
    local source="$3"
    local rdp_port="$4"

    if [[ "$profile" == "unknown" ]]
    then
        log "Unknown client: $ip"

        cat > "$STATE_FILE" <<EOF
PROFILE=unknown
PROFILE_NAME=❓ Unknown
CLIENT_IP="$ip"
CLIENT_SOURCE="$source"
RDP_PORT="$rdp_port"
CONNECTED="$(date +%s)"
CONNECTED_TEXT="$(date '+%Y-%m-%d %H:%M:%S')"
KEYBOARD=Unknown
RESOLUTION=Unknown
HOSTNAME="$(hostname)"
EOF

        return
    fi

    local keyboard="${PROFILE_KEYBOARD[$profile]}"
    local display="${PROFILE_DISPLAY[$profile]}"

    log "Applying ${PROFILE_NAME[$profile]}"

    busctl --user call \
        org.kde.keyboard \
        /Layouts \
        org.kde.KeyboardLayouts \
        setLayout u "${KEYBOARD_INDEX[$keyboard]}" \
        >/dev/null

    kscreen-doctor \
        "output.Virtual-1.mode.${DISPLAY_MODE[$display]}"

    write_state "$profile" "$ip" "$source" "$rdp_port" "$keyboard" "$display"
}

while true
do

    RDP_LISTEN_PORT="$(get_rdp_server_port)"

    CLIENT_INFO="$(get_client_identity "$RDP_LISTEN_PORT")"
    CLIENT_IP="${CLIENT_INFO%%|*}"
    CLIENT_REST="${CLIENT_INFO#*|}"
    CLIENT_SOURCE="${CLIENT_REST%%|*}"
    RDP_PORT="${CLIENT_REST#*|}"

    if [[ -z "$CLIENT_IP" ]]
    then
        LAST_IP=""

        rm -f "$STATE_FILE"

        sleep 1

        continue
    fi

    if [[ "$CLIENT_IP" != "$LAST_IP" || ! -f "$STATE_FILE" ]]
    then
        PROFILE="$(find_profile "$CLIENT_IP")"

        apply_profile "$PROFILE" "$CLIENT_IP" "$CLIENT_SOURCE" "$RDP_PORT"

        LAST_IP="$CLIENT_IP"
    fi

    sleep 1

done
