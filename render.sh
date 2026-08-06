#!/usr/bin/bash

STATE="$HOME/.config/rdp/state.env"

[[ -f "$STATE" ]] || exit 0

source "$STATE"

RDP_PORT="${RDP_PORT:-3389}"

has_active_rdp_session() {
    ss -Htn state established 2>/dev/null | awk -v target_port="$RDP_PORT" '
        function endpoint_port(ep, m) {
            if (match(ep, /:([0-9]+)$/, m))
                return m[1]
            return ""
        }

        {
            if (endpoint_port($4) == target_port || endpoint_port($5) == target_port) {
                found = 1
                exit
            }
        }

        END {
            if (found)
                exit 0
            exit 1
        }
    '
}

if ! has_active_rdp_session
then
    rm -f "$STATE"
    exit 0
fi

CLIENT_SOURCE="${CLIENT_SOURCE:-direct}"
CLIENT_IP_DISPLAY="$CLIENT_IP"

if [[ "$CLIENT_SOURCE" == "ssh_tunnel" ]]
then
    CLIENT_IP_DISPLAY="$CLIENT_IP (ssh)"
fi

ELAPSED=$(( $(date +%s) - CONNECTED ))

DAYS=$((ELAPSED/86400))
HOURS=$(((ELAPSED%86400)/3600))
MINUTES=$(((ELAPSED%3600)/60))
SECONDS=$((ELAPSED%60))

if (( DAYS > 0 ))
then
    ELAPSED=$(printf "%dd %02d:%02d:%02d" \
        "$DAYS" "$HOURS" "$MINUTES" "$SECONDS")
else
    ELAPSED=$(printf "%02d:%02d:%02d" \
        "$HOURS" "$MINUTES" "$SECONDS")
fi

#cat <<EOF
#${PROFILE_NAME}
#────────────────────────────
#Host         $HOSTNAME
#Client IP    $CLIENT_IP
#Connected    $CONNECTED_TEXT
#Elapsed      $ELAPSED
#Resolution   $RESOLUTION
#Keyboard     $KEYBOARD
#EOF

printf "%-12s %s\n" "Location" "$PROFILE_NAME"
printf "%-12s %s\n" "Client IP" "$CLIENT_IP_DISPLAY"
printf "%-12s %s\n" "Connected" "$CONNECTED_TEXT"
printf "%-12s %s\n" "Elapsed" "$ELAPSED"
printf "%-12s %s\n" "Display" "$RESOLUTION"
printf "%-12s %s\n" "Keyboard" "$KEYBOARD"
