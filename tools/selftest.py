"""Self-test utility for Polar Feeder environment validation.

This script performs a quick sanity check of the runtime environment, including:
- Python path and working directory
- Ability to write to logs/
- UART device nodes availability
- Bluetooth service and adapter state
- Camera enumerations
- GPIO library imports (without toggling hardware pins)

Intended for use on Raspberry Pi deployment targets before running normal operation.
"""

import os
import sys
import subprocess
from pathlib import Path


def run(cmd):
    """Run a shell command and return success flag and output.

    Args:
        cmd (list[str]): Command and arguments (similar to subprocess.check_output)

    Returns:
        tuple[bool,str]: (True, stdout) if command succeeded; (False, error string) if failed.
    """
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return True, out.strip()
    except Exception as e:
        return False, str(e)


def section(title):
    """Print a visually separated section header."""
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def main():
    """Run all self-test checks in sequence and print human-friendly results."""

    section("Python / Environment")
    print("python:", sys.executable)
    print("cwd:", os.getcwd())
    print("PYTHONPATH:", os.environ.get("PYTHONPATH", ""))
    try:
        print("uid:", os.getuid())
    except AttributeError:
        # Windows has no os.getuid(), but this script is mainly POSIX target.
        print("uid: n/a on this platform")

    section("Config / Logs (write test)")
    logs = Path("logs")
    logs.mkdir(exist_ok=True)
    test_path = logs / "selftest_write.txt"
    test_path.write_text("ok\n")
    print("wrote:", test_path)

    section("UART device presence")
    for p in ["/dev/serial0", "/dev/ttyAMA0", "/dev/ttyS0"]:
        print(p, "exists" if Path(p).exists() else "missing")

    ok, out = run(["bash", "-lc", "ls -l /dev/serial* /dev/ttyAMA* /dev/ttyS* 2>/dev/null || true"])
    print(out)

    section("Bluetooth service / adapter")
    ok, out = run(["bash", "-lc", "systemctl is-active bluetooth || true"])
    print("bluetooth service:", out)

    ok, out = run(["bash", "-lc", "bluetoothctl show 2>/dev/null || true"])
    print(out if out else "(no bluetoothctl output)")

    ok, out = run(["bash", "-lc", "rfkill list bluetooth 2>/dev/null || true"])
    print(out if out else "(no rfkill output)")

    section("Camera enumeration")
    ok, out = run(["bash", "-lc", "libcamera-hello --list-cameras 2>/dev/null || true"])
    print(out if out else "(libcamera not installed or no CSI camera)")

    ok, out = run(["bash", "-lc", "v4l2-ctl --list-devices 2>/dev/null || true"])
    print(out if out else "(v4l2-ctl not installed or no V4L devices)")

    section("GPIO library import check (no pin toggling)")
    # We only test imports; we DO NOT drive pins here.
    for mod in ["gpiozero", "lgpio", "RPi.GPIO", "gpiod", "pigpio"]:
        try:
            __import__(mod)
            print(f"OK import {mod}")
        except Exception as e:
            print(f"NO import {mod}: {e.__class__.__name__}: {e}")


if __name__ == "__main__":
    main()
