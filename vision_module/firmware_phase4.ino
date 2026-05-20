/**
 * KANDA Robot — Phase 4 Firmware (Pi-controlled + OLED face animations)
 *
 * Design:
 *   - ALL movement decisions are made by the Raspberry Pi
 *   - ESP32 executes commands received from Pi over USB Serial
 *   - ESP32 ONLY intervenes if obstacle while moving forward (safety stop)
 *     then sends "OBSTACLE" in telemetry so Pi decides next action
 *   - OLED shows animated face reflecting robot state sent by Pi
 *
 * Communication (USB cable):
 *   Pi → ESP32 : {"action":"forward","speed":120,"state":"acting"}\n
 *   ESP32 → Pi : F:30.5 L:21.0 R:16.4 -> FORWARD\n
 *              : F:12.0 L:21.0 R:16.4 -> OBSTACLE\n
 */

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ArduinoJson.h>

// ─── OLED ────────────────────────────────────────────────────────────────────
#define SCREEN_WIDTH  128
#define SCREEN_HEIGHT  64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
bool oledOk = false;

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
#define OBSTACLE_DIST 15   // cm — emergency stop threshold

// ─── STATE ───────────────────────────────────────────────────────────────────
int    speedVal      = 100;
String currentAction = "stop";
String currentState  = "idle";   // from Pi JSON: idle/listening/thinking/acting/searching/speaking/reporting_ok/reporting_fail/obstacle
bool   movingForward = false;

// ─── ANIMATION ───────────────────────────────────────────────────────────────
unsigned long lastAnimMs   = 0;
int           animFrame    = 0;
bool          blinkOpen    = true;
unsigned long lastBlinkMs  = 0;
int           thinkOffset  = 0;   // -2..+2 for eye scanning
int           thinkDir     = 1;

// ─── SENSOR ──────────────────────────────────────────────────────────────────

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

// ─── MOTOR PRIMITIVES ────────────────────────────────────────────────────────

void setSpeed(int spd) {
  ledcWrite(PWMA, spd);
  ledcWrite(PWMB, spd);
}

void forward() {
  digitalWrite(AIN1, LOW);  digitalWrite(AIN2, HIGH);
  digitalWrite(BIN1, LOW);  digitalWrite(BIN2, HIGH);
  movingForward = true;
}

void backward() {
  digitalWrite(AIN1, HIGH); digitalWrite(AIN2, LOW);
  digitalWrite(BIN1, HIGH); digitalWrite(BIN2, LOW);
  movingForward = false;
}

void left() {
  digitalWrite(AIN1, HIGH); digitalWrite(AIN2, LOW);
  digitalWrite(BIN1, LOW);  digitalWrite(BIN2, HIGH);
  movingForward = false;
}

void right() {
  digitalWrite(AIN1, LOW);  digitalWrite(AIN2, HIGH);
  digitalWrite(BIN1, HIGH); digitalWrite(BIN2, LOW);
  movingForward = false;
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
  digitalWrite(AIN1, LOW); digitalWrite(AIN2, LOW);
  digitalWrite(BIN1, LOW); digitalWrite(BIN2, LOW);
  movingForward = false;
}

// ─── COMMAND EXECUTOR ────────────────────────────────────────────────────────

bool executeCommand(const String& jsonLine) {
  StaticJsonDocument<192> doc;
  if (deserializeJson(doc, jsonLine)) return false;

  const char* action = doc["action"] | "stop";
  int spd = doc["speed"] | speedVal;
  spd = constrain(spd, 0, 255);
  setSpeed(spd);
  speedVal = spd;

  // Read optional state field for OLED face
  const char* stateStr = doc["state"] | "";
  if (strlen(stateStr) > 0) {
    currentState = String(stateStr);
    currentState.toLowerCase();
  }

  if      (strcmp(action, "forward")      == 0) forward();
  else if (strcmp(action, "backward")     == 0) backward();
  else if (strcmp(action, "left")         == 0) left();
  else if (strcmp(action, "right")        == 0) right();
  else if (strcmp(action, "slight_left")  == 0) slightLeft();
  else if (strcmp(action, "slight_right") == 0) slightRight();
  else                                          stopMotors();

  currentAction = action;
  currentAction.toUpperCase();
  return true;
}

// ─── TELEMETRY ───────────────────────────────────────────────────────────────

void sendTelemetry(float dF, float dL, float dR, const String& state) {
  Serial.print("F:"); Serial.print(dF, 1);
  Serial.print(" L:"); Serial.print(dL, 1);
  Serial.print(" R:"); Serial.print(dR, 1);
  Serial.print(" -> "); Serial.println(state);
}

// ─── OLED FACE DRAWING ───────────────────────────────────────────────────────
// Face centered in 128x64. Eyes at y~20, mouth at y~48.
// Eye positions: left eye x=32, right eye x=88
// Eye normal size: w=18, h=12  (filled rect)
// Eye squint size: w=18, h=6

#define EYE_L_X   32
#define EYE_R_X   88
#define EYE_Y     18
#define EYE_W     18
#define EYE_H     12
#define EYE_H_SQ   6    // squint height
#define EYE_HALF_W  9
#define EYE_HALF_H  6
#define MOUTH_Y   48
#define MOUTH_W   36
#define MOUTH_H    8

// Draw one eye (filled rounded rect). ox,oy = top-left, h = height
void drawEye(int ox, int oy, int h) {
  display.fillRoundRect(ox - EYE_HALF_W, oy, EYE_W, h, 3, WHITE);
}

// Draw eyebrows (small lines above eyes)
void drawBrows() {
  display.drawLine(EYE_L_X - EYE_HALF_W,     EYE_Y - 5,
                   EYE_L_X + EYE_HALF_W - 1, EYE_Y - 5, WHITE);
  display.drawLine(EYE_R_X - EYE_HALF_W,     EYE_Y - 5,
                   EYE_R_X + EYE_HALF_W - 1, EYE_Y - 5, WHITE);
}

// Draw open mouth (rectangle)
void drawMouth(bool open) {
  if (open) {
    display.fillRect(SCREEN_WIDTH/2 - MOUTH_W/2, MOUTH_Y, MOUTH_W, MOUTH_H, WHITE);
  }
}

// Draw smile curve (arc approximated with line segments)
void drawSmile(bool happy) {
  int cx = SCREEN_WIDTH / 2;
  int cy = MOUTH_Y + 4;
  for (int i = -16; i <= 16; i++) {
    int y = happy ? (i * i / 12) : -(i * i / 12);
    display.drawPixel(cx + i, cy + y, WHITE);
    display.drawPixel(cx + i, cy + y + 1, WHITE);
  }
}

// State: IDLE — two eyes, slow blink every 3s
void drawFaceIdle() {
  unsigned long now = millis();
  if (now - lastBlinkMs > 3000) {
    blinkOpen = !blinkOpen;
    if (!blinkOpen) lastAnimMs = now;
    lastBlinkMs = now;
  }
  // Eye is closed for 150ms then opens
  bool eyeOpen = blinkOpen || ((millis() - lastAnimMs) > 150);
  if (eyeOpen) {
    drawEye(EYE_L_X, EYE_Y, EYE_H);
    drawEye(EYE_R_X, EYE_Y, EYE_H);
  } else {
    // Closed — thin lines
    display.drawLine(EYE_L_X - EYE_HALF_W, EYE_Y + EYE_HALF_H,
                     EYE_L_X + EYE_HALF_W, EYE_Y + EYE_HALF_H, WHITE);
    display.drawLine(EYE_R_X - EYE_HALF_W, EYE_Y + EYE_HALF_H,
                     EYE_R_X + EYE_HALF_W, EYE_Y + EYE_HALF_H, WHITE);
  }
}

// State: LISTENING — wide eyes + raised brows
void drawFaceListening() {
  drawEye(EYE_L_X, EYE_Y - 2, EYE_H + 4);
  drawEye(EYE_R_X, EYE_Y - 2, EYE_H + 4);
  drawBrows();
}

// State: THINKING — eyes scan left-right
void drawFaceThinking() {
  unsigned long now = millis();
  if (now - lastAnimMs > 200) {
    thinkOffset += thinkDir;
    if (thinkOffset >= 5 || thinkOffset <= -5) thinkDir = -thinkDir;
    lastAnimMs = now;
  }
  drawEye(EYE_L_X + thinkOffset, EYE_Y, EYE_H);
  drawEye(EYE_R_X + thinkOffset, EYE_Y, EYE_H);
}

// State: ACTING — squinted eyes (determined)
void drawFaceActing() {
  drawEye(EYE_L_X, EYE_Y + EYE_HALF_H - EYE_H_SQ/2, EYE_H_SQ);
  drawEye(EYE_R_X, EYE_Y + EYE_HALF_H - EYE_H_SQ/2, EYE_H_SQ);
}

// State: SEARCHING — left normal, right squinted, alternating every 500ms
void drawFaceSearching() {
  unsigned long now = millis();
  if (now - lastAnimMs > 500) {
    animFrame = !animFrame;
    lastAnimMs = now;
  }
  if (animFrame == 0) {
    drawEye(EYE_L_X, EYE_Y, EYE_H);                                           // normal
    drawEye(EYE_R_X, EYE_Y + EYE_HALF_H - EYE_H_SQ/2, EYE_H_SQ);            // squint
  } else {
    drawEye(EYE_L_X, EYE_Y + EYE_HALF_H - EYE_H_SQ/2, EYE_H_SQ);            // squint
    drawEye(EYE_R_X, EYE_Y, EYE_H);                                           // normal
  }
}

// State: SPEAKING — eyes normal + mouth opens/closes every 300ms
void drawFaceSpeaking() {
  drawEye(EYE_L_X, EYE_Y, EYE_H);
  drawEye(EYE_R_X, EYE_Y, EYE_H);
  unsigned long now = millis();
  if (now - lastAnimMs > 300) {
    animFrame = !animFrame;
    lastAnimMs = now;
  }
  drawMouth(animFrame == 0);
}

// State: REPORTING_OK — smile face
void drawFaceReportingOk() {
  drawEye(EYE_L_X, EYE_Y, EYE_H);
  drawEye(EYE_R_X, EYE_Y, EYE_H);
  drawSmile(true);
}

// State: REPORTING_FAIL — sad face
void drawFaceReportingFail() {
  drawEye(EYE_L_X, EYE_Y, EYE_H);
  drawEye(EYE_R_X, EYE_Y, EYE_H);
  drawSmile(false);
}

// State: OBSTACLE — wide eyes + exclamation
void drawFaceObstacle() {
  drawEye(EYE_L_X, EYE_Y - 3, EYE_H + 6);
  drawEye(EYE_R_X, EYE_Y - 3, EYE_H + 6);
  display.setTextSize(2);
  display.setCursor(SCREEN_WIDTH/2 - 6, MOUTH_Y - 4);
  display.print("!");
}

void drawFace() {
  if (!oledOk) return;
  display.clearDisplay();

  if      (currentState == "idle")           drawFaceIdle();
  else if (currentState == "listening")      drawFaceListening();
  else if (currentState == "thinking")       drawFaceThinking();
  else if (currentState == "acting")         drawFaceActing();
  else if (currentState == "searching")      drawFaceSearching();
  else if (currentState == "speaking")       drawFaceSpeaking();
  else if (currentState == "reporting_ok")   drawFaceReportingOk();
  else if (currentState == "reporting_fail") drawFaceReportingFail();
  else if (currentState == "obstacle")       drawFaceObstacle();
  else                                       drawFaceIdle();   // fallback

  // Small sensor readout in bottom-left corner (tiny text)
  display.setTextSize(1);
  display.setCursor(0, 56);
  display.setTextColor(WHITE);

  display.display();
}

// ─── SETUP ───────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);

  oledOk = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  if (!oledOk) {
    Serial.println("OLED fail — continuing without display");
  } else {
    display.setTextColor(WHITE);
    display.clearDisplay();
    display.setTextSize(1);
    display.setCursor(20, 10); display.print("KANDA Phase 4");
    display.setCursor(28, 26); display.print("PI-CTRL");
    display.setCursor(12, 42); display.print("Waiting for Pi...");
    display.display();
  }

  // Ultrasonic
  pinMode(TRIG_F, OUTPUT); pinMode(ECHO_F, INPUT);
  pinMode(TRIG_L, OUTPUT); pinMode(ECHO_L, INPUT);
  pinMode(TRIG_R, OUTPUT); pinMode(ECHO_R, INPUT);

  // Motors
  pinMode(AIN1, OUTPUT); pinMode(AIN2, OUTPUT);
  pinMode(BIN1, OUTPUT); pinMode(BIN2, OUTPUT);

  // PWM channels
  ledcAttach(PWMA, 1000, 8);
  ledcAttach(PWMB, 1000, 8);

  stopMotors();
  Serial.println("KANDA ready — waiting for Pi commands");
}

// ─── MAIN LOOP ───────────────────────────────────────────────────────────────

void loop() {
  float dF = readDistance(TRIG_F, ECHO_F);
  float dL = readDistance(TRIG_L, ECHO_L);
  float dR = readDistance(TRIG_R, ECHO_R);

  // ── Receive command from Pi ────────────────────────────────────────────────
  if (Serial.available()) {
    String incoming = Serial.readStringUntil('\n');
    incoming.trim();
    if (incoming.length() > 0 && incoming.startsWith("{")) {
      executeCommand(incoming);
    }
  }

  // ── Safety: emergency stop if obstacle while moving forward ───────────────
  String reportState = currentAction;

  if (movingForward && dF > 0 && dF < OBSTACLE_DIST) {
    stopMotors();
    currentAction  = "STOP";
    currentState   = "obstacle";
    movingForward  = false;
    reportState    = "OBSTACLE";
  }

  // ── Send telemetry to Pi ───────────────────────────────────────────────────
  sendTelemetry(dF, dL, dR, reportState);

  // ── Draw OLED face for current state ──────────────────────────────────────
  drawFace();

  delay(100);
}
