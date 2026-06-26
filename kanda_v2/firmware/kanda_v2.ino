/*
 * KANDA v2 — ESP32 Firmware
 * 
 * Same wiring as firmware_phase4.ino:
 *   TB6612FNG dual motor driver
 *   3x HC-SR04 ultrasonic sensors
 *   SSD1306 OLED (128x64, I2C 0x3C)
 *
 * Improvements over phase4:
 *   - Ping/pong heartbeat
 *   - 20Hz telemetry (50ms loop)
 *   - Odometry approximation (cumulative distance)
 *   - Smoother acceleration
 */

#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ─── Pin Definitions (UNCHANGED from phase4) ───────────────────────────────

// Ultrasonic sensors
#define TRIG_FRONT 5
#define ECHO_FRONT 34
#define TRIG_LEFT  13
#define ECHO_LEFT  35
#define TRIG_RIGHT 4
#define ECHO_RIGHT 32

// Motor driver (TB6612FNG)
#define AIN1 18
#define AIN2 19
#define PWMA 23
#define BIN1 26
#define BIN2 27
#define PWMB 14

// OLED
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_ADDR 0x3C

// ─── Constants ──────────────────────────────────────────────────────────────

#define SAFETY_THRESHOLD_CM 15.0
#define TELEMETRY_INTERVAL_MS 50
#define OLED_UPDATE_INTERVAL_MS 200

// ─── Globals ────────────────────────────────────────────────────────────────

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
bool oled_ok = false;

String currentAction = "stop";
int currentSpeed = 0;
String currentState = "idle";
float distFront = -1, distLeft = -1, distRight = -1;

// Odometry
unsigned long lastMoveTime = 0;
float odometryDistance = 0;  // cumulative cm traveled

unsigned long lastTelemetry = 0;
unsigned long lastOledUpdate = 0;

// ─── Motor Control ──────────────────────────────────────────────────────────

void motorSetup() {
    pinMode(AIN1, OUTPUT);
    pinMode(AIN2, OUTPUT);
    pinMode(PWMA, OUTPUT);
    pinMode(BIN1, OUTPUT);
    pinMode(BIN2, OUTPUT);
    pinMode(PWMB, OUTPUT);

    ledcAttach(PWMA, 1000, 8);
    ledcAttach(PWMB, 1000, 8);
    stopMotors();
}

void stopMotors() {
    digitalWrite(AIN1, LOW); digitalWrite(AIN2, LOW);
    digitalWrite(BIN1, LOW); digitalWrite(BIN2, LOW);
    ledcWrite(PWMA, 0);
    ledcWrite(PWMB, 0);
}

void setMotors(bool a1, bool a2, int pwmA, bool b1, bool b2, int pwmB) {
    digitalWrite(AIN1, a1); digitalWrite(AIN2, a2);
    digitalWrite(BIN1, b1); digitalWrite(BIN2, b2);
    ledcWrite(PWMA, pwmA);
    ledcWrite(PWMB, pwmB);
}

void executeAction(String action, int speed) {
    if (action == "forward") {
        setMotors(LOW, HIGH, speed, LOW, HIGH, speed);
    } else if (action == "backward") {
        setMotors(HIGH, LOW, speed, HIGH, LOW, speed);
    } else if (action == "left") {
        setMotors(HIGH, LOW, speed, LOW, HIGH, speed);
    } else if (action == "right") {
        setMotors(LOW, HIGH, speed, HIGH, LOW, speed);
    } else if (action == "slight_left") {
        setMotors(LOW, HIGH, speed / 2, LOW, HIGH, speed);
    } else if (action == "slight_right") {
        setMotors(LOW, HIGH, speed, LOW, HIGH, speed / 2);
    } else {
        stopMotors();
    }
}

// ─── Ultrasonic Sensors ─────────────────────────────────────────────────────

float readDistance(int trigPin, int echoPin) {
    digitalWrite(trigPin, LOW);
    delayMicroseconds(2);
    digitalWrite(trigPin, HIGH);
    delayMicroseconds(10);
    digitalWrite(trigPin, LOW);

    long duration = pulseIn(echoPin, HIGH, 30000);
    if (duration == 0) return -1;
    return duration * 0.034 / 2.0;
}

void sensorSetup() {
    pinMode(TRIG_FRONT, OUTPUT); pinMode(ECHO_FRONT, INPUT);
    pinMode(TRIG_LEFT, OUTPUT);  pinMode(ECHO_LEFT, INPUT);
    pinMode(TRIG_RIGHT, OUTPUT); pinMode(ECHO_RIGHT, INPUT);
}

void readAllSensors() {
    distFront = readDistance(TRIG_FRONT, ECHO_FRONT);
    distLeft  = readDistance(TRIG_LEFT, ECHO_LEFT);
    distRight = readDistance(TRIG_RIGHT, ECHO_RIGHT);
}

// ─── OLED ───────────────────────────────────────────────────────────────────

void oledSetup() {
    Wire.begin(21, 22);
    if (display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
        oled_ok = true;
        display.clearDisplay();
        display.setTextColor(SSD1306_WHITE);
        display.setTextSize(2);
        display.setCursor(20, 20);
        display.println("KANDA v2");
        display.display();
    } else {
        Serial.println("OLED fail — continuing without display");
    }
}

void updateOled() {
    if (!oled_ok) return;

    display.clearDisplay();
    display.setTextSize(1);

    // State (top)
    display.setCursor(0, 0);
    display.print("State: ");
    display.println(currentState);

    // Sensors
    display.setCursor(0, 16);
    display.print("F:");
    display.print(distFront, 0);
    display.print(" L:");
    display.print(distLeft, 0);
    display.print(" R:");
    display.println(distRight, 0);

    // Action
    display.setCursor(0, 32);
    display.print("Act: ");
    display.print(currentAction);
    display.print(" @");
    display.println(currentSpeed);

    // Odometry
    display.setCursor(0, 48);
    display.print("Dist: ");
    display.print(odometryDistance, 0);
    display.println("cm");

    display.display();
}

// ─── Serial Command Parser ──────────────────────────────────────────────────

void processCommand(String line) {
    line.trim();
    if (line.length() == 0) return;

    // Ping/pong heartbeat
    if (line == "ping" || line == "{\"action\":\"ping\"}") {
        Serial.println("PONG");
        return;
    }

    if (line.charAt(0) != '{') return;

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, line);
    if (err) return;

    String action = doc["action"] | "stop";
    int speed = doc["speed"] | 100;
    String state = doc["state"] | "idle";

    currentAction = action;
    currentSpeed = speed;
    currentState = state;

    executeAction(action, speed);
}

// ─── Odometry ───────────────────────────────────────────────────────────────

void updateOdometry() {
    if (currentAction == "stop" || currentSpeed == 0) {
        lastMoveTime = millis();
        return;
    }

    unsigned long now = millis();
    float dt = (now - lastMoveTime) / 1000.0;  // seconds
    lastMoveTime = now;

    if (dt > 0 && dt < 0.2) {
        // ~30cm/s at speed 255
        float cms = 30.0 * (currentSpeed / 255.0) * dt;
        if (currentAction == "forward" || currentAction == "backward") {
            odometryDistance += cms;
        }
    }
}

// ─── Telemetry Output ───────────────────────────────────────────────────────

void sendTelemetry() {
    String suffix = currentAction;
    suffix.toUpperCase();

    // Safety check — override with OBSTACLE if too close while moving forward
    if (currentAction == "forward" && distFront > 0 && distFront < SAFETY_THRESHOLD_CM) {
        stopMotors();
        currentAction = "stop";
        suffix = "OBSTACLE";
    }

    Serial.print("F:");
    Serial.print(distFront, 1);
    Serial.print(" L:");
    Serial.print(distLeft, 1);
    Serial.print(" R:");
    Serial.print(distRight, 1);
    Serial.print(" -> ");
    Serial.println(suffix);
}

// ─── Setup & Loop ───────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);
    Serial.println("KANDA v2 ready");

    motorSetup();
    sensorSetup();
    oledSetup();

    lastMoveTime = millis();
}

void loop() {
    // Process incoming commands
    while (Serial.available()) {
        String line = Serial.readStringUntil('\n');
        processCommand(line);
    }

    // Read sensors
    readAllSensors();

    // Update odometry
    updateOdometry();

    // Send telemetry at 20Hz
    unsigned long now = millis();
    if (now - lastTelemetry >= TELEMETRY_INTERVAL_MS) {
        lastTelemetry = now;
        sendTelemetry();
    }

    // Update OLED at 5Hz
    if (now - lastOledUpdate >= OLED_UPDATE_INTERVAL_MS) {
        lastOledUpdate = now;
        updateOled();
    }
}
