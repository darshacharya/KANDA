"""Direct ESP32 serial test — run standalone (not with main.py)."""
import serial
import json
import time
import glob

# Find port
ports = sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))
if not ports:
    print("ERROR: No serial ports found")
    exit(1)

port = ports[0]
print(f"Using port: {port}")

s = serial.Serial(port, 115200, timeout=2)
time.sleep(1)

# Read telemetry
print("\n--- Reading telemetry (3s) ---")
end = time.time() + 3
lines_received = 0
while time.time() < end:
    if s.in_waiting:
        line = s.readline().decode("utf-8", errors="replace").strip()
        if line:
            print(f"  RX: {line}")
            lines_received += 1
    else:
        time.sleep(0.02)

if lines_received == 0:
    print("  WARNING: No telemetry received! ESP32 may need reset.")
    print("  Press the EN/RST button on the ESP32 board.")

# Send forward command
print("\n--- Sending FORWARD speed=200 for 2s ---")
cmd = json.dumps({"action": "forward", "speed": 200, "state": "acting"})
s.write((cmd + "\n").encode())
time.sleep(2)

# Read after
print("\n--- Telemetry after command ---")
for _ in range(10):
    if s.in_waiting:
        line = s.readline().decode("utf-8", errors="replace").strip()
        if line:
            print(f"  RX: {line}")

# Stop
print("\n--- Sending STOP ---")
cmd = json.dumps({"action": "stop", "speed": 0, "state": "idle"})
s.write((cmd + "\n").encode())
time.sleep(0.5)

for _ in range(5):
    if s.in_waiting:
        line = s.readline().decode("utf-8", errors="replace").strip()
        if line:
            print(f"  RX: {line}")

s.close()
print("\nDone.")
