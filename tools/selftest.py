import os, sys, subprocess, json
from pathlib import Path

def run(cmd):
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return True, out.strip()
    except Exception as e:
        return False, str(e)

def section(title):
    print("\n" + "="*60)
    print(title)
    print("="*60)

def main():
    section("Python / Environment")
    print("python:", sys.executable)
    print("cwd:", os.getcwd())
    print("PYTHONPATH:", os.environ.get("PYTHONPATH",""))
    print("uid:", os.getuid())

    section("Config / Logs (write test)")
    logs = Path("logs")
    logs.mkdir(exist_ok=True)
    test_path = logs / "selftest_write.txt"
    test_path.write_text("ok\n")
    print("wrote:", test_path)

    section("UART device presence")
    for p in ["/dev/serial0", "/dev/ttyAMA0", "/dev/ttyS0"]:
        print(p, "exists" if Path(p).exists() else "missing")
    ok, out = run(["bash","-lc","ls -l /dev/serial* /dev/ttyAMA* /dev/ttyS* 2>/dev/null || true"])
    print(out)

    section("Bluetooth service / adapter")
    ok, out = run(["bash","-lc","systemctl is-active bluetooth || true"])
    print("bluetooth service:", out)
    ok, out = run(["bash","-lc","bluetoothctl show 2>/dev/null || true"])
    print(out if out else "(no bluetoothctl output)")
    ok, out = run(["bash","-lc","rfkill list bluetooth 2>/dev/null || true"])
    print(out if out else "(no rfkill output)")

    section("Camera enumeration")
    ok, out = run(["bash","-lc","libcamera-hello --list-cameras 2>/dev/null || true"])
    print(out if out else "(libcamera not installed or no CSI camera)")
    ok, out = run(["bash","-lc","v4l2-ctl --list-devices 2>/dev/null || true"])
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
