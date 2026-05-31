/**
 * KANDA Robot — Simple Firmware (no OLED, no I2C)
 *
 * Use this if firmware_phase4.ino hangs (OLED not connected / I2C issue).
 * Full motor control + ultrasonic safety still works.
 *
 * Pi → ESP32 : {"action":"forward","speed":120,"state":"acting"}\n
 * ESP32 → Pi : F:30.5 L:21.0 R:16.4 -> FORWARD\n
 */

#include <ArduinoJson.h>

// ─── ULTRASONIC SENSORS ──────────────────────────────────────────────────────
#define TRIG_F 5
#define ECHO_F 34
#define TRIG_L 13
#define ECHO_L 35
#define TRIG_R 4
#define ECHO_R 32

// ─── MOTOR DRIVER (TB6612FNG) ────────────────────────────────────────────────
#define AIN1 18
#define AIN2 19
#define PWMA 23
#define BIN1 26
#define BIN2 27
#define PWMB 14

// ─── SAFETY ──────────────────────────────────────────────────────────────────
#define OBSTACLE_DIST 15   // cm

// ─── STATE ───────────────────────────────────────────────────────────────────
int    speedVal      = 100;
String currentAction = "stop";
bool   movingForward = false;

// ─── SENSORS ─────────────────────────────────────────────────────────────────

float readDistance(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);
  long duration = pulseIn(echo, HIGH, 30000);
  if (duration == 0) return -1;
  return duration * 0.034 / 2.0;
}

// ─── MOTOR PRIMITIVES ────────────────────────────────────────────────────────

void setSpeed(int spd) {
  // Use analogWrite (works on all ESP32 core versions without ledcSetup)
  analogWrite(PWMA, spd);
  analogWrite(PWMB, spd);
}

void stopMotors() {
  digitalWrite(AIN1, LOW); digitalWrite(AIN2, LOW);
  digitalWrite(BIN1, LOW); digitalWrite(BIN2, LOW);
  setSpeed(0);
  movingForward = false;
  currentAction = "STOP";
}

void moveForward(int spd) {
  digitalWrite(AIN1, HIGH); digitalWrite(AIN2, LOW);
  digitalWrite(BIN1, HIGH); digitalWrite(BIN2, LOW);
  setSpeed(spd);
  movingForward = true;
  currentAction = "FORWARD";
}

void moveBackward(int spd) {
  digitalWrite(AIN1, LOW); digitalWrite(AIN2, HIGH);
  digitalWrite(BIN1, LOW); digitalWrite(BIN2, HIGH);
  setSpeed(spd);
  movingForward = false;
  currentAction = "BACKWARD";
}

void turnLeft(int spd) {
  digitalWrite(AIN1, LOW);  digitalWrite(AIN2, HIGH);
  digitalWrite(BIN1, HIGH); digitalWrite(BIN2, LOW);
  setSpeed(spd);
  movingForward = false;
  currentAction = "LEFT";
}

void turnRight(int spd) {
  digitalWrite(AIN1, HIGH); digitalWrite(AIN2, LOW);
  digitalWrite(BIN1, LOW);  digitalWrite(BIN2, HIGH);
  setSpeed(spd);
  movingForward = false;
  currentAction = "RIGHT";
}

// ─── COMMAND PARSER ──────────────────────────────────────────────────────────

void executeCommand(const String& json) {
  StaticJsonDocument<200> doc;
  DeserializationError err = deserializeJson(doc, json);
  if (err) return;

  const char* action = doc["action"] | "stop";
  int speed = doc["speed"] | 100;
  speedVal = constrain(speed, 0, 255);

  String act = String(action);
  act.toLowerCase();

  if      (act == "forward")  moveForward(speedVal);
  else if (act == "backward") moveBackward(speedVal);
  else if (act == "left")     turnLeft(speedVal);
  else if (act == "right")    turnRight(speedVal);
  else                        stopMotors();
}

// ─── TELEMETRY ───────────────────────────────────────────────────────────────

void sendTelemetry(float dF, float dL, float dR, const String& state) {
  Serial.print("F:"); Serial.print(dF, 1);
  Serial.print(" L:"); Serial.print(dL, 1);
  Serial.print(" R:"); Serial.print(dR, 1);
  Serial.print(" -> "); Serial.println(state);
}

// ─── SETUP ───────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);

  // Ultrasonic
  pinMode(TRIG_F, OUTPUT); pinMode(ECHO_F, INPUT);
  pinMode(TRIG_L, OUTPUT); pinMode(ECHO_L, INPUT);
  pinMode(TRIG_R, OUTPUT); pinMode(ECHO_R, INPUT);

  // Motors
  pinMode(AIN1, OUTPUT); pinMode(AIN2, OUTPUT);
  pinMode(BIN1, OUTPUT); pinMode(BIN2, OUTPUT);

  // analogWrite handles PWM setup automatically — no ledcSetup needed
  analogWrite(PWMA, 0);
  analogWrite(PWMB, 0);

  stopMotors();
  Serial.println("KANDA-simple ready");
}

// ─── MAIN LOOP ───────────────────────────────────────────────────────────────

void loop() {
  float dF = readDistance(TRIG_F, ECHO_F);
  float dL = readDistance(TRIG_L, ECHO_L);
  float dR = readDistance(TRIG_R, ECHO_R);

  // Receive command from Pi
  if (Serial.available()) {
    String incoming = Serial.readStringUntil('\n');
    incoming.trim();
    if (incoming.length() > 0 && incoming.startsWith("{")) {
      executeCommand(incoming);
    }
  }

  // Safety: emergency stop if obstacle while moving forward
  String reportState = currentAction;
  if (movingForward && dF > 0 && dF < OBSTACLE_DIST) {
    stopMotors();
    reportState = "OBSTACLE";
  }

  // Send telemetry
  sendTelemetry(dF, dL, dR, reportState);

  delay(100);
}
