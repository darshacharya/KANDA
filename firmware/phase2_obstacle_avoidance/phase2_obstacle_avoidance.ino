/**
 * Kanda Robot — Phase 2 / Phase 3 Firmware
 *
 * Hardware:
 *   - ESP32 (Arduino core v3+)
 *   - TB6612FNG dual motor driver
 *   - 3x HC-SR04 ultrasonic sensors (with voltage dividers on ECHO pins)
 *   - SSD1306 OLED (128x64, I2C)
 *
 * Operating modes (selected automatically):
 *   AI_MODE   — Raspberry Pi sends {"action":"...","speed":N} JSON over Serial
 *               ESP32 executes the command; falls back to rule-based if Pi goes silent
 *   AUTO_MODE — Standalone rule-based obstacle avoidance (Phase 2 behaviour)
 *
 * Mode switches automatically:
 *   - A valid JSON line received  → AI_MODE
 *   - No JSON for AI_TIMEOUT_MS   → back to AUTO_MODE
 */

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ArduinoJson.h>

// ─── OLED ────────────────────────────────────────────────────────────────────
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

// ─── ULTRASONIC SENSORS ───────────────────────────────────────────────────────
//   ECHO pins use 1kΩ + 1kΩ voltage divider → ~2.5V (safe for ESP32)
#define TRIG_F 5
#define ECHO_F 34

#define TRIG_L 13
#define ECHO_L 35

#define TRIG_R 4
#define ECHO_R 32

// ─── MOTOR DRIVER (TB6612FNG) ─────────────────────────────────────────────────
#define AIN1 18
#define AIN2 19
#define PWMA 23

#define BIN1 26
#define BIN2 27
#define PWMB 14

// ─── TUNING ───────────────────────────────────────────────────────────────────
int speedVal      = 100;   // 0–255 (8-bit PWM)
#define OBSTACLE_DIST 20   // cm — hard stop + turn
#define WALL_DIST     15   // cm — soft correction

// ─── AI MODE ──────────────────────────────────────────────────────────────────
#define AI_TIMEOUT_MS 5000   // ms of silence before falling back to AUTO_MODE

enum RobotMode { AUTO_MODE, AI_MODE };
RobotMode currentMode = AUTO_MODE;
unsigned long lastAiCommandMs = 0;

// ─────────────────────────────────────────────────────────────────────────────
// SENSOR
// ─────────────────────────────────────────────────────────────────────────────

float readDistance(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);

  long duration = pulseIn(echo, HIGH, 30000);
  if (duration == 0) return -1;
  return duration * 0.034 / 2;
}

// ─────────────────────────────────────────────────────────────────────────────
// MOTOR PRIMITIVES
// ─────────────────────────────────────────────────────────────────────────────

void setSpeed(int spd) {
  ledcWrite(PWMA, spd);
  ledcWrite(PWMB, spd);
}

// Direction is code-corrected (AIN/BIN logic swapped to match physical wiring).
// If robot still goes wrong way after flashing, swap AO1↔AO2 or BO1↔BO2 on the driver.

void forward() {
  digitalWrite(AIN1, LOW);
  digitalWrite(AIN2, HIGH);
  digitalWrite(BIN1, LOW);
  digitalWrite(BIN2, HIGH);
}

void backward() {
  digitalWrite(AIN1, HIGH);
  digitalWrite(AIN2, LOW);
  digitalWrite(BIN1, HIGH);
  digitalWrite(BIN2, LOW);
}

void left() {
  digitalWrite(AIN1, HIGH);
  digitalWrite(AIN2, LOW);
  digitalWrite(BIN1, LOW);
  digitalWrite(BIN2, HIGH);
}

void right() {
  digitalWrite(AIN1, LOW);
  digitalWrite(AIN2, HIGH);
  digitalWrite(BIN1, HIGH);
  digitalWrite(BIN2, LOW);
}

void slightLeft() {
  ledcWrite(PWMA, speedVal * 0.5);
  ledcWrite(PWMB, speedVal);
  forward();
}

void slightRight() {
  ledcWrite(PWMA, speedVal);
  ledcWrite(PWMB, speedVal * 0.5);
  forward();
}

void stopMotors() {
  digitalWrite(AIN1, LOW);
  digitalWrite(AIN2, LOW);
  digitalWrite(BIN1, LOW);
  digitalWrite(BIN2, LOW);
}

// ─────────────────────────────────────────────────────────────────────────────
// AI COMMAND EXECUTOR
// ─────────────────────────────────────────────────────────────────────────────

// Execute a movement command received from the Raspberry Pi AI layer.
// Expected JSON: {"action": "forward", "speed": 150}
bool executeAiCommand(const String& jsonLine) {
  StaticJsonDocument<128> doc;
  DeserializationError err = deserializeJson(doc, jsonLine);
  if (err) {
    Serial.print("JSON parse error: ");
    Serial.println(err.c_str());
    return false;
  }

  const char* action = doc["action"] | "stop";
  int spd = doc["speed"] | speedVal;
  spd = constrain(spd, 0, 255);

  setSpeed(spd);

  if      (strcmp(action, "forward")      == 0) { forward();     }
  else if (strcmp(action, "backward")     == 0) { backward();    }
  else if (strcmp(action, "left")         == 0) { left();        }
  else if (strcmp(action, "right")        == 0) { right();       }
  else if (strcmp(action, "slight_left")  == 0) { slightLeft();  }
  else if (strcmp(action, "slight_right") == 0) { slightRight(); }
  else                                           { stopMotors();  }

  return true;
}

// Send sensor readings back to the Raspberry Pi as a telemetry line.
void sendTelemetry(float dF, float dL, float dR, const String& decision) {
  Serial.print("F:"); Serial.print(dF);
  Serial.print(" L:"); Serial.print(dL);
  Serial.print(" R:"); Serial.print(dR);
  Serial.print(" -> "); Serial.println(decision);
}

// ─────────────────────────────────────────────────────────────────────────────
// DISPLAY
// ─────────────────────────────────────────────────────────────────────────────

void updateOLED(float dF, float dL, float dR, const String& decision) {
  display.clearDisplay();
  display.setTextSize(1);

  display.setCursor(0, 0);  display.print("F: "); display.print(dF);
  display.setCursor(0, 10); display.print("L: "); display.print(dL);
  display.setCursor(0, 20); display.print("R: "); display.print(dR);
  display.setCursor(0, 40); display.print("Action:");
  display.setCursor(0, 50); display.print(decision);

  display.display();
}

// ─────────────────────────────────────────────────────────────────────────────
// SETUP
// ─────────────────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);

  Wire.begin(21, 22);

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("OLED init failed");
    while (1);
  }
  display.setTextColor(WHITE);

  // Ultrasonic pins
  pinMode(TRIG_F, OUTPUT); pinMode(ECHO_F, INPUT);
  pinMode(TRIG_L, OUTPUT); pinMode(ECHO_L, INPUT);
  pinMode(TRIG_R, OUTPUT); pinMode(ECHO_R, INPUT);

  // Motor direction pins
  pinMode(AIN1, OUTPUT); pinMode(AIN2, OUTPUT);
  pinMode(BIN1, OUTPUT); pinMode(BIN2, OUTPUT);

  // PWM channels (ESP32 Arduino core v3+)
  ledcAttach(PWMA, 1000, 8);
  ledcAttach(PWMB, 1000, 8);

  setSpeed(speedVal);
}

// ─────────────────────────────────────────────────────────────────────────────
// MAIN LOOP  —  Dual-mode: AI_MODE (Pi controls) / AUTO_MODE (rule-based)
// ─────────────────────────────────────────────────────────────────────────────

void loop() {
  // ── Always read sensors (needed for telemetry + AUTO_MODE decisions) ────────
  float dF = readDistance(TRIG_F, ECHO_F);
  float dL = readDistance(TRIG_L, ECHO_L);
  float dR = readDistance(TRIG_R, ECHO_R);

  // ── Check for incoming JSON command from Raspberry Pi ───────────────────────
  if (Serial.available()) {
    String incoming = Serial.readStringUntil('\n');
    incoming.trim();
    if (incoming.length() > 0 && incoming.startsWith("{")) {
      if (executeAiCommand(incoming)) {
        currentMode      = AI_MODE;
        lastAiCommandMs  = millis();

        // Extract action label for OLED from the JSON
        StaticJsonDocument<128> doc;
        deserializeJson(doc, incoming);
        String action = doc["action"] | "AI";
        action.toUpperCase();
        updateOLED(dF, dL, dR, "AI:" + action);
        sendTelemetry(dF, dL, dR, "AI:" + action);
        delay(100);
        return;   // command handled — skip AUTO_MODE logic this cycle
      }
    }
  }

  // ── Timeout: fall back to AUTO_MODE if Pi goes silent ──────────────────────
  if (currentMode == AI_MODE && (millis() - lastAiCommandMs > AI_TIMEOUT_MS)) {
    currentMode = AUTO_MODE;
    Serial.println("AI timeout — switching to AUTO_MODE");
  }

  // ── AUTO_MODE: rule-based obstacle avoidance (Phase 2 behaviour) ────────────
  String decision = "FORWARD";

  if (dF > 0 && dF < OBSTACLE_DIST) {
    stopMotors();
    delay(200);

    if (dL > dR) {
      decision = "TURN LEFT";
      left();
    } else {
      decision = "TURN RIGHT";
      right();
    }

    delay(400);
    setSpeed(speedVal);

  } else {
    if (dL < WALL_DIST) {
      decision = "ADJUST RIGHT";
      slightRight();
    } else if (dR < WALL_DIST) {
      decision = "ADJUST LEFT";
      slightLeft();
    } else {
      decision = "FORWARD";
      setSpeed(speedVal);
      forward();
    }
  }

  updateOLED(dF, dL, dR, decision);
  sendTelemetry(dF, dL, dR, decision);
  delay(100);
}
