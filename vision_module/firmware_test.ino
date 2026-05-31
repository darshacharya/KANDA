/**
 * KANDA — Minimal serial test (no motors, no sensors, no PWM)
 * Flash this first to confirm ESP32 + USB serial is working.
 * You should see "ping N" every second in Serial Monitor or from Pi.
 */

int counter = 0;

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("ESP32 alive");
}

void loop() {
  counter++;
  Serial.print("ping ");
  Serial.println(counter);
  delay(1000);
}
