#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ArduinoJson.h>

// ================= OLED =================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64

Adafruit_SSD1306 display(
  SCREEN_WIDTH,
  SCREEN_HEIGHT,
  &Wire,
  -1
);

// ================= ULTRASONIC =================
#define TRIG_F 5
#define ECHO_F 34

#define TRIG_L 13
#define ECHO_L 35

#define TRIG_R 4
#define ECHO_R 32

// ================= MOTOR DRIVER =================
#define AIN1 18
#define AIN2 19
#define PWMA 23

#define BIN1 26
#define BIN2 27
#define PWMB 14

// ================= STATUS LED =================
#define STATUS_LED 2

// ================= SETTINGS =================
int motorSpeed = 80;
String action = "READY";

// ================= AI MODE =================
bool aiMode = true;    // Start in AI mode — only move when Pi commands
unsigned long lastAiCmd = 0;
#define AI_TIMEOUT_MS 30000  // 30s timeout before auto (effectively disabled)

// ================= FACE STATE =================
enum FaceState {
  FACE_IDLE,
  FACE_LISTENING,
  FACE_THINKING,
  FACE_ACTING,
  FACE_SEARCHING,
  FACE_SPEAKING,
  FACE_REPORTING_OK,
  FACE_REPORTING_FAIL,
  FACE_OBSTACLE
};

FaceState currentFace = FACE_IDLE;
unsigned long frameCount = 0;
unsigned long lastFrameTime = 0;
#define FRAME_INTERVAL_MS 80

// Status message from Pi (displayed on OLED)
String statusMsg = "Ready";

// ================= DISTANCE =================
float getDistance(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);

  long duration = pulseIn(echo, HIGH, 30000);
  if (duration == 0) return 999;
  return duration * 0.034 / 2;
}

// ================= OLED RENDER =================

const char* getStateLabel() {
  switch (currentFace) {
    case FACE_IDLE:           return "IDLE";
    case FACE_LISTENING:      return "LISTENING...";
    case FACE_THINKING:       return "THINKING...";
    case FACE_ACTING:         return "MOVING";
    case FACE_SEARCHING:      return "SEARCHING";
    case FACE_SPEAKING:       return "SPEAKING";
    case FACE_REPORTING_OK:   return "FOUND!";
    case FACE_REPORTING_FAIL: return "NOT FOUND";
    case FACE_OBSTACLE:       return "!! OBSTACLE !!";
  }
  return "KANDA";
}

void drawDirectionArrow(float front, float left, float right) {
  int cx = 64, cy = 38;

  if (action == "FORWARD") {
    display.fillTriangle(cx, cy - 10, cx - 6, cy + 2, cx + 6, cy + 2, WHITE);
    display.setCursor(46, cy + 5);
    display.print("FORWARD");
  } else if (action == "BACKWARD") {
    display.fillTriangle(cx, cy + 10, cx - 6, cy - 2, cx + 6, cy - 2, WHITE);
    display.setCursor(42, cy - 14);
    display.print("BACKWARD");
  } else if (action == "LEFT" || action == "SLIGHT_L") {
    display.fillTriangle(cx - 10, cy, cx + 2, cy - 6, cx + 2, cy + 6, WHITE);
    display.setCursor(cx + 6, cy - 3);
    display.print("LEFT");
  } else if (action == "RIGHT" || action == "SLIGHT_R") {
    display.fillTriangle(cx + 10, cy, cx - 2, cy - 6, cx - 2, cy + 6, WHITE);
    display.setCursor(cx - 30, cy - 3);
    display.print("RIGHT");
  } else if (action == "STOP") {
    display.drawRect(cx - 6, cy - 6, 12, 12, WHITE);
    display.setCursor(50, cy + 9);
    display.print("STOPPED");
  } else if (action == "OBSTACLE") {
    display.setCursor(30, cy - 4);
    display.setTextSize(1);
    display.print("!! BLOCKED !!");
    display.setCursor(30, cy + 8);
    display.print("F:");
    display.print((int)front);
    display.print("cm");
  } else {
    display.setCursor(40, cy);
    display.print("READY");
  }
}

void updateOLED(float front, float left, float right) {
  display.clearDisplay();
  display.setTextColor(WHITE);

  // ─── Row 1: State (large) ───
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print(getStateLabel());

  // Mode indicator top-right
  display.setCursor(108, 0);
  display.print(aiMode ? "[AI]" : "[AT]");

  // ─── Row 2: Divider ───
  display.drawLine(0, 10, 127, 10, WHITE);

  // ─── Row 3: Status message / direction ───
  display.setTextSize(1);
  if (currentFace == FACE_ACTING || currentFace == FACE_SEARCHING || currentFace == FACE_OBSTACLE) {
    drawDirectionArrow(front, left, right);
  } else {
    // Show status message centered
    int msgLen = statusMsg.length();
    int x = max(0, (128 - msgLen * 6) / 2);
    display.setCursor(x, 20);
    display.print(statusMsg);

    // Show speed if moving
    if (motorSpeed > 0 && currentFace != FACE_IDLE) {
      display.setCursor(0, 34);
      display.print("Speed: ");
      display.print(motorSpeed);
    }
  }

  // ─── Row 4: Divider ───
  display.drawLine(0, 52, 127, 52, WHITE);

  // ─── Row 5: Sensor bar (always visible) ───
  display.setTextSize(1);
  display.setCursor(0, 55);
  display.print("F:");
  display.print((int)front);

  display.setCursor(44, 55);
  display.print("L:");
  display.print((int)left);

  display.setCursor(88, 55);
  display.print("R:");
  display.print((int)right);

  display.display();
}

// ================= STATE PARSER =================

void parseFaceState(const char* state) {
  if (strcmp(state, "idle") == 0)              { currentFace = FACE_IDLE; statusMsg = "Ready"; }
  else if (strcmp(state, "listening") == 0)    { currentFace = FACE_LISTENING; statusMsg = "Listening..."; }
  else if (strcmp(state, "thinking") == 0)     { currentFace = FACE_THINKING; statusMsg = "Processing..."; }
  else if (strcmp(state, "acting") == 0)       { currentFace = FACE_ACTING; }
  else if (strcmp(state, "searching") == 0)    { currentFace = FACE_SEARCHING; statusMsg = "Looking..."; }
  else if (strcmp(state, "speaking") == 0)     { currentFace = FACE_SPEAKING; statusMsg = "Speaking..."; }
  else if (strcmp(state, "reporting") == 0)    { currentFace = FACE_REPORTING_OK; statusMsg = "Done!"; }
  else if (strcmp(state, "reporting_ok") == 0) { currentFace = FACE_REPORTING_OK; statusMsg = "Target found!"; }
  else if (strcmp(state, "reporting_fail") == 0) { currentFace = FACE_REPORTING_FAIL; statusMsg = "Not found"; }
  else if (strcmp(state, "obstacle") == 0)     { currentFace = FACE_OBSTACLE; statusMsg = "Blocked!"; }
}

// ================= MOVEMENT =================
void moveForward() {
  action = "FORWARD";
  digitalWrite(STATUS_LED, HIGH);
  digitalWrite(AIN1, LOW);
  digitalWrite(AIN2, HIGH);
  digitalWrite(BIN1, LOW);
  digitalWrite(BIN2, HIGH);
  analogWrite(PWMA, motorSpeed);
  analogWrite(PWMB, motorSpeed);
}

void moveBackward() {
  action = "BACKWARD";
  digitalWrite(STATUS_LED, HIGH);
  digitalWrite(AIN1, HIGH);
  digitalWrite(AIN2, LOW);
  digitalWrite(BIN1, HIGH);
  digitalWrite(BIN2, LOW);
  analogWrite(PWMA, motorSpeed);
  analogWrite(PWMB, motorSpeed);
}

void stopMotors() {
  action = "STOP";
  digitalWrite(STATUS_LED, LOW);
  analogWrite(PWMA, 0);
  analogWrite(PWMB, 0);
}

void turnLeft() {
  action = "LEFT";
  digitalWrite(STATUS_LED, HIGH);
  digitalWrite(AIN1, HIGH);
  digitalWrite(AIN2, LOW);
  digitalWrite(BIN1, LOW);
  digitalWrite(BIN2, HIGH);
  analogWrite(PWMA, 75);
  analogWrite(PWMB, 75);
}

void turnRight() {
  action = "RIGHT";
  digitalWrite(STATUS_LED, HIGH);
  digitalWrite(AIN1, LOW);
  digitalWrite(AIN2, HIGH);
  digitalWrite(BIN1, HIGH);
  digitalWrite(BIN2, LOW);
  analogWrite(PWMA, 75);
  analogWrite(PWMB, 75);
}

void slightLeft() {
  action = "SLIGHT_L";
  digitalWrite(STATUS_LED, HIGH);
  digitalWrite(AIN1, LOW);
  digitalWrite(AIN2, HIGH);
  digitalWrite(BIN1, LOW);
  digitalWrite(BIN2, HIGH);
  analogWrite(PWMA, motorSpeed / 2);
  analogWrite(PWMB, motorSpeed);
}

void slightRight() {
  action = "SLIGHT_R";
  digitalWrite(STATUS_LED, HIGH);
  digitalWrite(AIN1, LOW);
  digitalWrite(AIN2, HIGH);
  digitalWrite(BIN1, LOW);
  digitalWrite(BIN2, HIGH);
  analogWrite(PWMA, motorSpeed);
  analogWrite(PWMB, motorSpeed / 2);
}

// ================= AI COMMAND PARSER =================
bool executeAiCommand(String line) {
  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, line);
  if (err) return false;

  const char* act = doc["action"] | "stop";
  int spd = doc["speed"] | motorSpeed;
  spd = constrain(spd, 0, 255);
  motorSpeed = spd;

  // Parse face state from Pi
  const char* state = doc["state"] | "";
  if (strlen(state) > 0) {
    parseFaceState(state);
  }

  // Optional status message for OLED
  const char* msg = doc["msg"] | "";
  if (strlen(msg) > 0) {
    statusMsg = String(msg);
  }

  if (strcmp(act, "mode") == 0) {
    const char* mode = doc["mode"] | "ai";
    if (strcmp(mode, "auto") == 0) {
      aiMode = false;
      statusMsg = "AUTO mode";
      currentFace = FACE_IDLE;
    } else {
      aiMode = true;
      stopMotors();
      statusMsg = "AI mode";
      currentFace = FACE_IDLE;
    }
    return true;
  }
  else if (strcmp(act, "forward") == 0)       moveForward();
  else if (strcmp(act, "backward") == 0)      moveBackward();
  else if (strcmp(act, "left") == 0)          turnLeft();
  else if (strcmp(act, "right") == 0)         turnRight();
  else if (strcmp(act, "slight_left") == 0)   slightLeft();
  else if (strcmp(act, "slight_right") == 0)  slightRight();
  else if (strcmp(act, "stop") == 0)          stopMotors();
  else                                        stopMotors();

  lastAiCmd = millis();
  aiMode = true;
  return true;
}

// ================= AUTO MODE (obstacle avoidance) =================
void autoMode(float front, float left, float right) {
  if (front < 20) {
    stopMotors();
    delay(200);
    moveBackward();
    delay(500);
    stopMotors();
    delay(200);

    if (left > right) {
      turnRight();
      delay(500);
    } else {
      turnLeft();
      delay(500);
    }
  } else {
    moveForward();
  }
}

// ================= SAFETY: obstacle override in AI mode =================
bool obstacleOverride(float front) {
  if (aiMode && front < 18 && action == "FORWARD") {
    stopMotors();
    action = "OBSTACLE";
    currentFace = FACE_OBSTACLE;
    return true;
  }
  return false;
}

// ================= SETUP =================
void setup() {
  Serial.begin(115200);

  Wire.begin(21, 22);
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("OLED FAIL");
    while (1);
  }

  pinMode(TRIG_F, OUTPUT);
  pinMode(ECHO_F, INPUT);
  pinMode(TRIG_L, OUTPUT);
  pinMode(ECHO_L, INPUT);
  pinMode(TRIG_R, OUTPUT);
  pinMode(ECHO_R, INPUT);

  pinMode(AIN1, OUTPUT);
  pinMode(AIN2, OUTPUT);
  pinMode(BIN1, OUTPUT);
  pinMode(BIN2, OUTPUT);
  pinMode(PWMA, OUTPUT);
  pinMode(PWMB, OUTPUT);
  pinMode(STATUS_LED, OUTPUT);

  stopMotors();

  // Boot splash
  display.clearDisplay();
  display.setTextSize(2);
  display.setTextColor(WHITE);
  display.setCursor(20, 8);
  display.println("KANDA");
  display.setTextSize(1);
  display.setCursor(20, 35);
  display.println("Embodied AI Agent");
  display.setCursor(20, 50);
  display.println("Waiting for Pi...");
  display.display();
  delay(1500);

  lastFrameTime = millis();
}

// ================= LOOP =================
void loop() {
  // Read sensors
  float front = getDistance(TRIG_F, ECHO_F);
  float left  = getDistance(TRIG_L, ECHO_L);
  float right = getDistance(TRIG_R, ECHO_R);

  // Send telemetry to Pi (format Pi expects)
  Serial.print("F:");
  Serial.print(front, 1);
  Serial.print(" L:");
  Serial.print(left, 1);
  Serial.print(" R:");
  Serial.print(right, 1);
  Serial.print(" -> ");
  Serial.println(action);

  // Check for JSON commands from Pi
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0 && line[0] == '{') {
      executeAiCommand(line);
    }
  }

  // Safety: obstacle override even in AI mode
  obstacleOverride(front);

  // AI timeout → stop motors and wait
  if (aiMode && lastAiCmd > 0 && (millis() - lastAiCmd > AI_TIMEOUT_MS)) {
    stopMotors();
    action = "READY";
    currentFace = FACE_IDLE;
    statusMsg = "Waiting...";
    lastAiCmd = 0;  // prevent repeated triggers
  }

  // AUTO mode: self-driving with obstacle avoidance
  if (!aiMode) {
    autoMode(front, left, right);
  }

  // Advance animation frame counter at fixed interval
  unsigned long now = millis();
  if (now - lastFrameTime >= FRAME_INTERVAL_MS) {
    frameCount++;
    lastFrameTime = now;
  }

  updateOLED(front, left, right);
  delay(50);
}
