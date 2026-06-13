/*
  ESP32 Onboard LED Serial Control
  
  This sketch runs on the ESP32 dev module and listens for commands
  received over USB serial at 115200 baud:
  - Send '1' to turn the internal LED ON.
  - Send '0' to turn the internal LED OFF.
  
  Note: Most ESP32 dev modules have their onboard LED connected to GPIO 2 (LED_BUILTIN).
  If your board uses a different pin, you can adjust the LED_PIN define below.
*/

#ifndef LED_BUILTIN
#define LED_PIN 13  // Default onboard LED pin for most ESP32 Dev Modules
#else
#define LED_PIN LED_BUILTIN
#endif

void setup() {
  // Initialize serial communication at 115200 baud
  Serial.begin(115200);
  
  // Configure the LED pin as an output
  pinMode(LED_PIN, OUTPUT);
  
  // Turn the LED off initially

  digitalWrite(LED_PIN, LOW);
  delay(1000);
    digitalWrite(4, HIGH);
    digitalWrite(7, HIGH);
    digitalWrite(9, HIGH);
    digitalWrite(6, HIGH);
    digitalWrite(2, HIGH);
    digitalWrite(1, HIGH);
    digitalWrite(13, HIGH);
    delay(3000);
  digitalWrite(LED_PIN, LOW);
  
  Serial.println("ESP32 Ready. Send '1' to turn on, '0' to turn off.");
}

void loop() {
  // Check if character is available to read
  if (Serial.available() > 0) {
    char command = Serial.read();
    
    if (command == '1') {
      digitalWrite(LED_PIN, HIGH);
      Serial.println("LED status: ON");
    } 
    else if (command == '0') {
      digitalWrite(LED_PIN, LOW);
      Serial.println("LED status: OFF");
    }
  }
}
