#!/usr/bin/env bash

#
# ===========================
# Client IP -> Profile
# Wildcards are supported.
# ===========================
#

declare -A IP_PROFILE=(
    ["191.92.151.254"]="home"
    ["172.17.20.10"]="home4k"
    ["172.17.20.*"]="home"
    ["10.5.5.4"]="cary"
    ["190.248.130.50"]="soma"
    ["*.*.*.*"]="default"
)

#
# ===========================
# Profile names
# ===========================
#

declare -A PROFILE_NAME=(
    [home]="Home"
    [home4k]="Home 4K"
    [cary]="Cary"
    [soma]="SOMA"
    [default]="Default"
)

#
# ===========================
# Keyboard layouts
# (Indices correspond to the order configured in KDE)
# ===========================
#

declare -A KEYBOARD_INDEX=(
    [us]=0
    [latam]=1
    [es]=2
)

declare -A KEYBOARD_NAME=(
    [us]="English (US)"
    [latam]="Spanish (Latin America)"
    [es]="Spanish"
)

#
# ===========================
# Display presets
# ===========================
#

declare -A DISPLAY_MODE=(
    [hd]="1360x768@60"
    [wsxga]="1440x900@60"
    [4k]="1920x1080@60"
)

#
# ===========================
# Profile configuration
# ===========================
#

declare -A PROFILE_KEYBOARD=(
    [home]="es"
    [home4k]="us"
    [cary]="latam"
    [soma]="latam"
    [default]="latam"
)

declare -A PROFILE_DISPLAY=(
    [home]="wsxga"
    [home4k]="4k"
    [cary]="4k"
    [soma]="hd"
    [default]="4k"
)
