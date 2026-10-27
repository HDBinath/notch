"""Read the computer's own battery percentage without a network service."""

from __future__ import annotations

import ctypes
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BatteryStatus:
    percent: int
    charging: bool = False


def battery_status() -> BatteryStatus | None:
    """Return the installed battery's charge and charging state, when available."""
    try:
        system = platform.system()
        if system == "Windows":
            return _windows_battery_status()
        if system == "Linux":
            return _linux_battery_status()
        if system == "Darwin":
            return _mac_battery_status()
    except Exception:
        # Battery reporting is optional; a platform API failure must not break the notch.
        pass
    return None


def battery_percent() -> int | None:
    """Return 0–100 for an installed battery, or None for desktops/unknown data."""
    status = battery_status()
    return status.percent if status is not None else None


def _windows_battery() -> int | None:
    status = _windows_battery_status()
    return status.percent if status is not None else None


def _windows_battery_status() -> BatteryStatus | None:
    class SystemPowerStatus(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", ctypes.c_ulong),
            ("BatteryFullLifeTime", ctypes.c_ulong),
        ]

    status = SystemPowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return None
    if status.BatteryFlag == 255 or status.BatteryFlag & 128 or status.BatteryLifePercent == 255:
        return None
    return BatteryStatus(int(status.BatteryLifePercent), bool(status.BatteryFlag & 8))


def _linux_battery(root: Path = Path("/sys/class/power_supply")) -> int | None:
    status = _linux_battery_status(root)
    return status.percent if status is not None else None


def _linux_battery_status(root: Path = Path("/sys/class/power_supply")) -> BatteryStatus | None:
    if not root.is_dir():
        return None
    for source in root.iterdir():
        try:
            if (source / "type").read_text().strip() != "Battery":
                continue
            present = source / "present"
            if present.exists() and present.read_text().strip() == "0":
                continue
            value = int((source / "capacity").read_text().strip())
            if 0 <= value <= 100:
                state = source / "status"
                charging = state.exists() and state.read_text().strip().lower() == "charging"
                return BatteryStatus(value, charging)
        except (OSError, ValueError):
            continue
    return None


def _mac_battery() -> int | None:
    status = _mac_battery_status()
    return status.percent if status is not None else None


def _mac_battery_status() -> BatteryStatus | None:
    """Read an internal battery through Apple's IOPowerSources/CoreFoundation APIs."""
    try:
        status = _mac_iokit_battery_status()
        if status is not None:
            return status
    except (OSError, ValueError, AttributeError, ctypes.ArgumentError):
        pass
    # pmset is a local system fallback for macOS versions with different IOKit symbols.
    try:
        output = subprocess.run(["pmset", "-g", "batt"], capture_output=True,
                                text=True, timeout=2, check=False)
        match = re.search(r"InternalBattery[^\n]*?\b(\d{1,3})%[^\n]*", output.stdout)
        if output.returncode == 0 and match:
            value = int(match.group(1))
            if 0 <= value <= 100:
                charging = bool(re.search(r"\bcharging\b", match.group(0), re.I))
                return BatteryStatus(value, charging)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _mac_iokit_battery() -> int | None:
    status = _mac_iokit_battery_status()
    return status.percent if status is not None else None


def _mac_iokit_battery_status() -> BatteryStatus | None:
    iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
    core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    pointer = ctypes.c_void_p

    iokit.IOPSCopyPowerSourcesInfo.restype = pointer
    iokit.IOPSCopyPowerSourcesList.argtypes = [pointer]
    iokit.IOPSCopyPowerSourcesList.restype = pointer
    iokit.IOPSGetPowerSourceDescription.argtypes = [pointer, pointer]
    iokit.IOPSGetPowerSourceDescription.restype = pointer
    core.CFArrayGetCount.argtypes = [pointer]
    core.CFArrayGetCount.restype = ctypes.c_long
    core.CFArrayGetValueAtIndex.argtypes = [pointer, ctypes.c_long]
    core.CFArrayGetValueAtIndex.restype = pointer
    core.CFDictionaryGetValue.argtypes = [pointer, pointer]
    core.CFDictionaryGetValue.restype = pointer
    core.CFStringCompare.argtypes = [pointer, pointer, ctypes.c_ulong]
    core.CFStringCompare.restype = ctypes.c_long
    core.CFNumberGetValue.argtypes = [pointer, ctypes.c_int, pointer]
    core.CFNumberGetValue.restype = ctypes.c_bool

    core.CFBooleanGetValue.argtypes = [pointer]
    core.CFBooleanGetValue.restype = ctypes.c_bool
    core.CFRelease.argtypes = [pointer]

    def key(name: str) -> pointer:
        return pointer.in_dll(iokit, name)

    info = iokit.IOPSCopyPowerSourcesInfo()
    if not info:
        return None
    try:
        sources = iokit.IOPSCopyPowerSourcesList(info)
        if not sources:
            return None
        try:
            for index in range(core.CFArrayGetCount(sources)):
                source = core.CFArrayGetValueAtIndex(sources, index)
                details = iokit.IOPSGetPowerSourceDescription(info, source)
                if not details:
                    continue
                kind = core.CFDictionaryGetValue(details, key("kIOPSTypeKey"))
                if not kind or core.CFStringCompare(kind, key("kIOPSInternalBatteryType"), 0) != 0:
                    continue
                current = ctypes.c_int()
                maximum = ctypes.c_int()
                current_value = core.CFDictionaryGetValue(details, key("kIOPSCurrentCapacityKey"))
                maximum_value = core.CFDictionaryGetValue(details, key("kIOPSMaxCapacityKey"))
                # kCFNumberSInt32Type = 3.
                if (current_value and maximum_value
                        and core.CFNumberGetValue(current_value, 3, ctypes.byref(current))
                        and core.CFNumberGetValue(maximum_value, 3, ctypes.byref(maximum))
                        and maximum.value > 0):
                    charging_value = core.CFDictionaryGetValue(details, key("kIOPSIsChargingKey"))
                    charging = bool(charging_value and core.CFBooleanGetValue(charging_value))
                    return BatteryStatus(max(0, min(100, round(current.value * 100 / maximum.value))), charging)
        finally:
            core.CFRelease(sources)
    finally:
        core.CFRelease(info)
    return None
