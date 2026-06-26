"""
KANDA Calibration Script
Tests each movement direction one at a time with sensor readings.
Place the robot at a known position, then run this script.

Usage: python3 calibrate.py
"""

import serial
import time
import json
import sys

PORT = "/dev/ttyUSB1"
BAUD = 115200
SPEED = 120

def send(ser, action, speed=SPEED):
    cmd = json.dumps({"action": action, "speed": speed, "state": "acting"})
    ser.write((cmd + "\n").encode())
    ser.flush()

def stop(ser):
    cmd = json.dumps({"action": "stop", "speed": 0, "state": "idle"})
    ser.write((cmd + "\n").encode())
    ser.flush()

def read_sensors(ser):
    ser.reset_input_buffer()
    time.sleep(0.5)
    for _ in range(5):
        if ser.in_waiting:
            line = ser.readline().decode("utf-8", errors="replace").strip()
            if line.startswith("F:"):
                return line
        time.sleep(0.2)
    return "(no reading)"

def run_test(ser, action, duration, speed=SPEED):
    print(f"\n{'='*50}")
    print(f"  TEST: {action.upper()} | speed={speed} | duration={duration}s")
    print(f"{'='*50}")

    before = read_sensors(ser)
    print(f"  Before: {before}")

    input("  Press ENTER to start...")

    print(f"  Running {action} for {duration}s...")
    for i in range(int(duration * 2)):
        send(ser, action, speed)
        time.sleep(0.5)

    stop(ser)
    time.sleep(1)

    after = read_sensors(ser)
    print(f"  After:  {after}")
    print(f"  Done.")

def main():
    ser = serial.Serial(PORT, BAUD, timeout=2)
    time.sleep(2)

    print("\n" + "=" * 50)
    print("  KANDA CALIBRATION TEST")
    print("=" * 50)
    print(f"  Port: {PORT}  Speed: {SPEED}")

    sensors = read_sensors(ser)
    print(f"  Sensors: {sensors}")
    print()
    print("  Place robot at starting position.")
    print("  Each test waits for ENTER before moving.")
    print()

    tests = [
        ("forward",  3),
        ("backward", 3),
        ("left",     2),
        ("right",    2),
        ("left",     4),   # longer turn for 180 degree calibration
        ("right",    4),
    ]

    for action, duration in tests:
        run_test(ser, action, duration, SPEED)
        input("\n  Reset robot position if needed. Press ENTER for next test...")

    # Full 360 spin test
    print(f"\n{'='*50}")
    print("  360 DEGREE SPIN TEST")
    print("  Time how long it takes for a full rotation.")
    print(f"{'='*50}")
    input("  Press ENTER to start LEFT spin...")

    start = time.time()
    while True:
        send(ser, "left", SPEED)
        time.sleep(0.5)
        elapsed = time.time() - start
        sys.stdout.write(f"\r  Spinning... {elapsed:.1f}s (press ENTER to stop)")
        sys.stdout.flush()
        try:
            import select
            ready, _, _ = select.select([sys.stdin], [], [], 0)
            if ready:
                sys.stdin.readline()
                break
        except:
            pass

    stop(ser)
    total = time.time() - start
    ms_per_deg = (total * 1000) / 360
    print(f"\n  Total time: {total:.1f}s")
    print(f"  MS per degree: {ms_per_deg:.1f}")
    print(f"\n  Set KANDA_TURN_MS_PER_DEG={ms_per_deg:.1f} in .env")

    ser.close()
    print("\nCalibration complete!")

if __name__ == "__main__":
    main()
