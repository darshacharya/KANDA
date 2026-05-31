# KANDA Robot — Complete Wiring Guide (Verified)

## Problem Diagnosed

The motor driver (TB6612FNG) back-feeds current into the ESP32 when its GPIO pins
are set as OUTPUT without proper power. The fix requires:

1. **Battery connected to TB6612 VM** (motor power must be live)
2. **Decoupling capacitors** on power rails to filter motor noise
3. **220Ω series resistors** on motor signal lines to limit current spikes
4. **Voltage dividers** on ultrasonic ECHO pins (5V → 3.3V)

---

## Power Architecture

```
        ┌─────────────────────────────────────────┐
        │  2× 18650 Li-ion cells (2S, 7.4V nom)  │
        └───────────┬─────────────────────────────┘
                    │
       ┌────────────┴────────────┐
       │                         │
       │ (raw)              ┌────┴─────┐
       │                    │   Buck   │
       │                    │  → 5V    │
       │                    └────┬─────┘
       │                         │
       ▼                         ▼
  TB6612 VM              ESP32 VIN (5V)
  (motor power)          (logic power)
                               │
                          ESP32 3.3V out
                               │
                    ┌──────────┼──────────┐
                    ▼          ▼          │
              TB6612 VCC   TB6612 STBY   │
              (logic)      (enable)      │
                                         │
                    Buck 5V ─────────────────→ HC-SR04 VCC
                                               (all 3 sensors)


    Raspberry Pi ← powered separately by Power Bank (5V USB-C)
    ESP32 ↔ Pi  ← USB cable (data, no modification needed)
```

---

## TB6612FNG Motor Driver

```
TB6612 Pin    →  Connect To              →  Notes
──────────────────────────────────────────────────────────────
VM            →  Battery + (7.4V raw)    →  add 10µF electrolytic to GND
VCC           →  ESP32 3.3V             →  add 0.1µF ceramic to GND
GND           →  Common GND             →  ALL grounds tied together
STBY          →  ESP32 3.3V             →  always enabled (tie to VCC)

AIN1          →  220Ω ← ESP32 GPIO18    →  Motor A direction
AIN2          →  220Ω ← ESP32 GPIO19    →  Motor A direction
PWMA          →  220Ω ← ESP32 GPIO23    →  Motor A speed
BIN1          →  220Ω ← ESP32 GPIO26    →  Motor B direction
BIN2          →  220Ω ← ESP32 GPIO27    →  Motor B direction
PWMB          →  220Ω ← ESP32 GPIO14    →  Motor B speed

AO1, AO2      →  Left motor terminals   →  add 0.1µF ceramic across motor
BO1, BO2      →  Right motor terminals  →  add 0.1µF ceramic across motor
```

---

## ESP32 Full Pin Map

```
ESP32 GPIO   →  Function                →  Connection
──────────────────────────────────────────────────────────────
GPIO1  (TX)  →  USB Serial TX           →  Pi via USB (reserved)
GPIO3  (RX)  →  USB Serial RX           →  Pi via USB (reserved)

GPIO18       →  Motor A IN1             →  220Ω → TB6612 AIN1
GPIO19       →  Motor A IN2             →  220Ω → TB6612 AIN2
GPIO23       →  Motor A PWM             →  220Ω → TB6612 PWMA
GPIO26       →  Motor B IN1             →  220Ω → TB6612 BIN1
GPIO27       →  Motor B IN2             →  220Ω → TB6612 BIN2
GPIO14       →  Motor B PWM             →  220Ω → TB6612 PWMB

GPIO5        →  Ultrasonic Front TRIG   →  direct to sensor
GPIO34       →  Ultrasonic Front ECHO   →  via voltage divider (see below)
GPIO13       →  Ultrasonic Left TRIG    →  direct to sensor
GPIO35       →  Ultrasonic Left ECHO    →  via voltage divider (see below)
GPIO4        →  Ultrasonic Right TRIG   →  direct to sensor
GPIO32       →  Ultrasonic Right ECHO   →  via voltage divider (see below)

GPIO21       →  OLED SDA                →  direct to SSD1306
GPIO22       →  OLED SCL                →  direct to SSD1306

VIN          →  5V from buck converter  →  ESP32 power
3.3V         →  TB6612 VCC + STBY       →  logic supply for motor driver
GND          →  Common GND bus          →  everything connects here
```

---

## Ultrasonic Sensors (HC-SR04) — VOLTAGE DIVIDER REQUIRED

HC-SR04 ECHO pin outputs **5V**. ESP32 GPIOs are **3.3V max**.
You MUST use a voltage divider on each ECHO line:

```
                HC-SR04 ECHO pin
                      │
                   [1kΩ]
                      │
                      ├──── ESP32 GPIO (34/35/32)
                      │
                   [1kΩ]
                      │
                     GND
```

This gives ~2.5V at the GPIO — safe for ESP32 input.

```
Sensor    TRIG      ECHO (via divider)     VCC    GND
────────────────────────────────────────────────────────
Front     GPIO5     GPIO34                 5V     GND
Left      GPIO13    GPIO35                 5V     GND
Right     GPIO4     GPIO32                 5V     GND
```

**Note:** TRIG pins can be driven directly (ESP32 OUTPUT → sensor INPUT, 3.3V is
enough to trigger HC-SR04). Only ECHO needs the divider.

---

## OLED Display (SSD1306 128x64, I2C)

```
OLED Pin  →  Connect To
─────────────────────────
VCC       →  ESP32 3.3V
GND       →  Common GND
SDA       →  ESP32 GPIO21
SCL       →  ESP32 GPIO22
```

Already connected and working.

---

## Capacitor Placement

```
Cap              Where (solder CLOSE to the chip)        Purpose
────────────────────────────────────────────────────────────────────
100µF electro    ESP32 VIN → GND                         Bulk power smoothing
0.1µF ceramic    ESP32 VIN → GND                         High-freq noise filter
10µF electro     TB6612 VM → GND                         Motor current spikes
0.1µF ceramic    TB6612 VCC → GND                        Logic noise filter
```

---

## Resistor Placement

```
220Ω × 6:   ESP32 GPIO ──[220Ω]──→ TB6612 motor input
             (on all 6 signal lines: AIN1, AIN2, PWMA, BIN1, BIN2, PWMB)

1kΩ × 6:    Voltage dividers for 3 ultrasonic ECHO pins
             (2 resistors per sensor, 3 sensors = 6 resistors)
```

---

## Ground Bus

**ALL grounds on a single thick copper strip:**

```
ESP32 GND ──┬── TB6612 GND
             ├── Buck converter GND
             ├── Battery negative
             ├── Ultrasonic GNDs (×3)
             ├── OLED GND
             └── Pi GND (via USB cable)
```

---

## Raspberry Pi Connections

```
Pi Port         →  Connection
───────────────────────────────────────
USB-A           →  USB cable to ESP32 (serial data)
USB-A           →  USB audio adapter (for microphone input)
CSI ribbon      →  Pi Camera Module v2.1
3.5mm jack      →  Audio output only (speaker/BT adapter)
USB-C power     →  Power bank (5V 2A+)
```

### USB Audio Adapter (Microphone Input)

Raspberry Pi 4 does NOT support mic input through the 3.5mm jack (output only).
Use a USB audio adapter with 3.5mm combo jack:

```
USB Audio Adapter (₹150-300)
     │
     └── 3.5mm combo jack ← wired earphone with mic
```

Setup after plugging in:

```bash
# 1. Find the device
arecord -l
# Look for: card 1: Device [USB Audio Device], device 0

# 2. Test mic (speak for 3 seconds)
arecord -D hw:1,0 -f cd -d 3 test.wav && aplay test.wav

# 3. Update config.py if card number differs
#    AUDIO_DEVICE_INDEX = 1  (match card number from arecord -l)
```

Once mic is working, the wake word detector ("Hey Jarvis" via openWakeWord)
activates automatically. No Telegram needed for voice — just speak directly.

---

## Perf Board Layout

```
┌───────────────────────────────────────────────────────┐
│                                                       │
│  [Buck 5V]──[100µF]──[0.1µF]──→ VIN bus              │
│       │                                               │
│  [Batt+]────────────[10µF]──→ VM (to TB6612 VM)      │
│                                                       │
│  ═══════════ GND BUS (wide trace) ═══════════════     │
│                                                       │
│  [ESP32 module]           [TB6612FNG module]           │
│   VIN ← 5V bus            VM ← battery direct         │
│   3.3V → VCC bus          VCC ← 3.3V bus              │
│   GND → GND bus           STBY ← 3.3V bus             │
│                            GND → GND bus               │
│   GPIO18 ─[220Ω]─ AIN1                               │
│   GPIO19 ─[220Ω]─ AIN2    AO1 ─┐                     │
│   GPIO23 ─[220Ω]─ PWMA    AO2 ─┴─ Motor A (Left)     │
│   GPIO26 ─[220Ω]─ BIN1                               │
│   GPIO27 ─[220Ω]─ BIN2    BO1 ─┐                     │
│   GPIO14 ─[220Ω]─ PWMB    BO2 ─┴─ Motor B (Right)    │
│                                                       │
│  [Ultrasonic ×3]                                      │
│   VCC ← 5V bus (from buck)                           │
│   TRIG ← ESP32 direct                                │
│   ECHO → [1k]─┬─ ESP32 GPIO                          │
│                [1k]                                    │
│                GND                                    │
│                                                       │
│  [OLED]                                               │
│   VCC ← 3.3V    SDA ← GPIO21    SCL ← GPIO22         │
│                                                       │
└───────────────────────────────────────────────────────┘
```

---

## Checklist Before Powering On

1. [ ] ALL GNDs connected (ESP32, TB6612, buck, battery, sensors, OLED)
2. [ ] 100µF + 0.1µF caps on ESP32 VIN (close to chip)
3. [ ] 10µF cap on TB6612 VM (close to chip)
4. [ ] 0.1µF cap on TB6612 VCC (close to chip)
5. [ ] 220Ω resistors on ALL 6 motor signal lines
6. [ ] Voltage dividers (1kΩ + 1kΩ) on ALL 3 ECHO pins
7. [ ] Battery connected to TB6612 VM
8. [ ] Buck 5V → ESP32 VIN
9. [ ] ESP32 3.3V → TB6612 VCC and STBY
10. [ ] OLED on GPIO21 (SDA) and GPIO22 (SCL)
11. [ ] No shorts between VM and VCC (check with multimeter!)
12. [ ] USB cable between ESP32 and Pi (data)

---

## After Soldering — Test Sequence

1. Multimeter: verify buck outputs 5V, no shorts
2. Power on battery → ESP32 LED should light
3. Flash `firmware_minimal_motor.ino` (full version with sensors)
4. Pi serial test: should see `F:X L:Y R:Z -> STOP`
5. Send motor command from Pi: verify wheels spin
6. `KANDA_NO_UART=1 python3 main.py` → Telegram + camera test
7. `python3 main.py` → full system with motors
