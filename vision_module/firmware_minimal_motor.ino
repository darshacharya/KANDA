/**
 * KANDA — Pin isolation test
 * Same as firmware_test but with motor pins EXCEPT GPIO14.
 * If clean: GPIO14 is the problem.
 * If garbage: another pin is the problem.
 */

int counter = 0;

void setup() {
  Serial.begin(115200);
  delay(1000);

  // Motor pins WITHOUT GPIO14
  pinMode(18, OUTPUT);
  pinMode(19, OUTPUT);
  pinMode(23, OUTPUT);
  pinMode(26, OUTPUT);
  pinMode(27, OUTPUT);
  // pinMode(14, OUTPUT);   // SKIPPED — testing if this is the culprit

  digitalWrite(18, LOW);
  digitalWrite(19, LOW);
  digitalWrite(23, LOW);
  digitalWrite(26, LOW);
  digitalWrite(27, LOW);

  Serial.println("test no pin14");
}

void loop() {
  counter++;
  Serial.print("ping ");
  Serial.println(counter);
  delay(1000);
}
