#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import json
from pathlib import Path


@dataclass(frozen=True)
class KeyboardLayout:
    key: str
    index: int
    name: str


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    keyboard: KeyboardLayout
    resolution: str


CONFIG_PATH = Path(__file__).resolve().with_name("profiles.json")


def load_config() -> dict[str, object]:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("profiles.json must contain a top-level object")
    return payload


def build_keyboards(config: dict[str, object]) -> dict[str, KeyboardLayout]:
    keyboard_config = config.get("keyboards")
    if not isinstance(keyboard_config, dict):
        raise ValueError("profiles.json is missing a valid 'keyboards' object")

    keyboards: dict[str, KeyboardLayout] = {}
    for key, details in keyboard_config.items():
        if not isinstance(details, dict):
            raise ValueError(f"Keyboard '{key}' must be an object")
        keyboards[key] = KeyboardLayout(
            key=key,
            index=int(details["index"]),
            name=str(details["name"]),
        )
    return keyboards


def build_display_modes(config: dict[str, object]) -> dict[str, str]:
    display_config = config.get("display_modes")
    if not isinstance(display_config, dict):
        raise ValueError(
            "profiles.json is missing a valid 'display_modes' object")
    return {key: str(value) for key, value in display_config.items()}


def build_profiles(
    config: dict[str, object],
    keyboards: dict[str, KeyboardLayout],
    display_modes: dict[str, str],
) -> dict[str, Profile]:
    profile_config = config.get("profiles")
    if not isinstance(profile_config, dict):
        raise ValueError("profiles.json is missing a valid 'profiles' object")

    profiles: dict[str, Profile] = {}
    for key, details in profile_config.items():
        if not isinstance(details, dict):
            raise ValueError(f"Profile '{key}' must be an object")

        keyboard_key = str(details["keyboard"])
        display_key = str(details["display"])

        if keyboard_key not in keyboards:
            raise ValueError(
                f"Profile '{key}' references unknown keyboard '{keyboard_key}'")
        if display_key not in display_modes:
            raise ValueError(
                f"Profile '{key}' references unknown display mode '{display_key}'")

        profiles[key] = Profile(
            key=key,
            name=str(details["name"]),
            keyboard=keyboards[keyboard_key],
            resolution=display_modes[display_key],
        )

    return profiles


def build_ip_profile_rules(config: dict[str, object]) -> list[tuple[str, str]]:
    rules_config = config.get("ip_profile_rules")
    if not isinstance(rules_config, list):
        raise ValueError(
            "profiles.json is missing a valid 'ip_profile_rules' array")

    rules: list[tuple[str, str]] = []
    for index, rule in enumerate(rules_config, start=1):
        if not isinstance(rule, dict):
            raise ValueError(f"Rule #{index} must be an object")
        pattern = str(rule["pattern"])
        profile_key = str(rule["profile"])
        rules.append((pattern, profile_key))
    return rules


CONFIG = load_config()
KEYBOARDS = build_keyboards(CONFIG)
DISPLAY_MODES = build_display_modes(CONFIG)
PROFILES = build_profiles(CONFIG, KEYBOARDS, DISPLAY_MODES)
IP_PROFILE_RULES = build_ip_profile_rules(CONFIG)


def find_profile_key(ip_address: str) -> str | None:
    for pattern, profile_key in IP_PROFILE_RULES:
        if fnmatchcase(ip_address, pattern):
            return profile_key
    return None


def get_profile_for_ip(ip_address: str) -> Profile | None:
    profile_key = find_profile_key(ip_address)
    if profile_key is None:
        return None
    return PROFILES.get(profile_key)


def get_unknown_profile_name() -> str:
    return "Unknown"
