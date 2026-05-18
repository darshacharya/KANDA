# Kanda Robot — ESP32 Pin Configuration

## Microcontroller
**ESP32** (38-pin DevKit, Arduino core v3+)

---

## Ultrasonic Sensors (HC-SR04 × 3)

| Sensor    | TRIG Pin | ECHO Pin | Note                                      |
|-----------|----------|----------|-------------------------------------------|
| Front (F) | GPIO 5   | GPIO 34  | GPIO34 is input-only — safe for ECHO      |
| Left (L)  | GPIO 13  | GPIO 35  | GPIO35 is input-only — safe for ECHO      |
| Right (R) | GPIO 4   | GPIO 32  |                                           |

> **Voltage Divider on all ECHO pins**
> HC-SR04 outputs 5V on ECHO. ESP32 is 3.3V tolerant only.
> Divider: 1kΩ (series) + 1kΩ (to GND) → ~2.5V at ESP32 pin.
> (Exact 2kΩ unavailable → 1k+1k used — safe and functional)

---

## Motor Driver (TB6612FNG)

| Signal | ESP32 Pin | Description               |
|--------|-----------|---------------------------|
| AIN1   | GPIO 18   | Motor A direction bit 1   |
| AIN2   | GPIO 19   | Motor A direction bit 2   |
| PWMA   | GPIO 23   | Motor A speed (PWM)       |
| BIN1   | GPIO 26   | Motor B direction bit 1   |
| BIN2   | GPIO 27   | Motor B direction bit 2   |
| PWMB   | GPIO 14   | Motor B speed (PWM)       |
| STBY   | 3.3V      | Always HIGH to enable     |

> PWM configured via `ledcAttach()` (ESP32 Arduino core v3 API)
> Frequency: 1000 Hz, Resolution: 8-bit (0–255)

---

## OLED Display (SSD1306, 128×64)

| Signal | ESP32 Pin |
|--------|-----------|
| SDA    | GPIO 21   |
| SCL    | GPIO 22   |
| VCC    | 3.3V      |
| GND    | GND       |

> I2C address: `0x3C`
> Requires explicit `Wire.begin(21, 22)` in setup.

---

## Power Architecture

```
Battery (7.4V LiPo)
    │
   BMS
    │
  Switch
    │
    ├──→ TB6612FNG VM pin  (motor power, high current)
    │
    └──→ Buck Converter (→ 5V)
              │
              └──→ ESP32 VIN

All grounds tied together (common GND).
Capacitors across motor power lines to suppress voltage spikes.
```

> Motors are NOT powered through the buck converter.
> Buck converter handles ESP32 only.

---

## Avoided / Reserved Pins

| GPIO | Reason Avoided                         |
|------|----------------------------------------|
| 0    | Strapping pin (boot mode)              |
| 2    | Strapping pin                          |
| 12   | Strapping pin (flash voltage)          |
| 15   | Strapping pin                          |
| 1    | UART0 TX (Serial monitor)              |
| 3    | UART0 RX (Serial monitor)              |
| 36   | Unstable behavior observed in testing  |
| 6–11 | Internal flash — never use             |
