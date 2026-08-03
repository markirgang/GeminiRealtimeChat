/*
  Birds Project - ESP32 Controller Firmware (Dual Board Compatible)
  
  This sketch runs on either ESP32 board (Left or Right) for the Birds Project.
  It configures all 11 output GPIO pins listed in 'Birds On_Off Buttons ESP32.xlsx':

    GPIO 0  : Parrot Mouth
    GPIO 1  : Parrot Eyes
    GPIO 2  : Parrot Body
    GPIO 3  : Parrot Light
    GPIO 4  : Parrot Mouth Select
    GPIO 5  : Rear Bird Rear Move
    GPIO 12 : Rear Bird Rear Light
    GPIO 13 : Front Bird Move
    GPIO 14 : Front Bird Light
    GPIO 15 : Bird Front Chirp
    GPIO 16 : Center Bird Move

  Serial Baud Rate: 115200

  Command Protocol (sent over USB Serial from Thinker Window):
    - "<gpio>"         (e.g., "12")      -> Toggles the current state of GPIO 12.
    - "<gpio>:1"       (e.g., "12:1")    -> Turns GPIO 12 HIGH (ON).
    - "<gpio>:0"       (e.g., "12:0")    -> Turns GPIO 12 LOW (OFF).
    - "<gpio>:PULSE"   (e.g., "12:PULSE")-> Pulses GPIO 12 HIGH for 300ms, then LOW.
    - "1"              (Legacy)          -> Turns GPIO 2 (Body / LED) HIGH.
    - "0"              (Legacy)          -> Turns GPIO 2 (Body / LED) LOW.
*/

#include <Arduino.h>

// All 11 active GPIO pins for the Birds functions
const int NUM_PINS = 11;
const int PROJECT_PINS[NUM_PINS] = {0, 1, 2, 3, 4, 5, 12, 13, 14, 15, 16};

// Track current state for each pin (LOW = 0, HIGH = 1)
int pinStates[128]; 

void setup() {
  // Initialize serial communication
  Serial.begin(115200);
  while (!Serial && millis() < 2000) {
    // Wait for serial monitor / connection
  }

  // Initialize pin states array
  for (int i = 0; i < 128; i++) {
    pinStates[i] = LOW;
  }

  // Configure project GPIO pins as OUTPUTs (skipping UART0 USB Serial RX/TX pins 1 and 3)
  for (int i = 0; i < NUM_PINS; i++) {
    int pin = PROJECT_PINS[i];
    if (pin == 1 || pin == 3) {
      // Reserved for USB Serial communication (TX0/RX0). Setting pinMode OUTPUT overrides UART RX/TX.
      continue;
    }
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
    pinStates[pin] = LOW;
  }

  Serial.println("==========================================");
  Serial.println("🦜 Birds Project ESP32 Firmware Ready!");
  Serial.println("Configured GPIOs: 0, 2, 4, 5, 12, 13, 14, 15, 16");
  Serial.println("Note: GPIO 1 & 3 are reserved for USB Serial (RX/TX)");
  Serial.println("Send '<gpio>' to toggle or '<gpio>:1' / '<gpio>:0'");
  Serial.println("==========================================");
}

void loop() {
  if (Serial.available() > 0) {
    String inputStr = Serial.readStringUntil('\n');
    inputStr.trim();

    if (inputStr.length() == 0) return;

    // Handle legacy single-character commands ('1' / '0') for GPIO 2
    if (inputStr == "1") {
      digitalWrite(2, HIGH);
      pinStates[2] = HIGH;
      Serial.println("[ESP32] GPIO 2 -> HIGH (ON)");
      return;
    } else if (inputStr == "0") {
      digitalWrite(2, LOW);
      pinStates[2] = LOW;
      Serial.println("[ESP32] GPIO 2 -> LOW (OFF)");
      return;
    }

    // Parse "<gpio>:<cmd>" or "<gpio>"
    int colonIdx = inputStr.indexOf(':');
    int targetPin = -1;
    String subCmd = "";

    if (colonIdx != -1) {
      targetPin = inputStr.substring(0, colonIdx).toInt();
      subCmd = inputStr.substring(colonIdx + 1);
      subCmd.toUpperCase();
    } else {
      targetPin = inputStr.toInt();
    }

    // Prevent overriding UART0 Serial RX/TX pins
    if (targetPin == 1 || targetPin == 3) {
      Serial.print("[ESP32] Notice: GPIO ");
      Serial.print(targetPin);
      Serial.println(" is reserved for USB Serial (RX/TX) and cannot be toggled as a digital output.");
      return;
    }

    // Validate GPIO pin number
    bool isValidPin = false;
    for (int i = 0; i < NUM_PINS; i++) {
      if (PROJECT_PINS[i] == targetPin) {
        isValidPin = true;
        break;
      }
    }

    if (!isValidPin) {
      Serial.print("[ESP32] Warning: GPIO ");
      Serial.print(targetPin);
      Serial.println(" is not in configured project pins list.");
      if (targetPin < 0 || targetPin > 39) {
        return;
      }
      pinMode(targetPin, OUTPUT);
    }

    // Execute requested command on targetPin
    if (subCmd == "1" || subCmd == "ON" || subCmd == "HIGH") {
      digitalWrite(targetPin, HIGH);
      pinStates[targetPin] = HIGH;
      Serial.print("[ESP32] GPIO ");
      Serial.print(targetPin);
      Serial.println(" -> HIGH (ON)");
    } 
    else if (subCmd == "0" || subCmd == "OFF" || subCmd == "LOW") {
      digitalWrite(targetPin, LOW);
      pinStates[targetPin] = LOW;
      Serial.print("[ESP32] GPIO ");
      Serial.print(targetPin);
      Serial.println(" -> LOW (OFF)");
    } 
    else if (subCmd == "PULSE") {
      Serial.print("[ESP32] Pulsing GPIO ");
      Serial.println(targetPin);
      digitalWrite(targetPin, HIGH);
      delay(300);
      digitalWrite(targetPin, LOW);
      pinStates[targetPin] = LOW;
      Serial.print("[ESP32] GPIO ");
      Serial.print(targetPin);
      Serial.println(" -> PULSED (OFF)");
    } 
    else {
      // Default behavior for plain pin number: Toggle pin state
      int newState = (pinStates[targetPin] == LOW) ? HIGH : LOW;
      digitalWrite(targetPin, newState);
      pinStates[targetPin] = newState;

      Serial.print("[ESP32] Toggled GPIO ");
      Serial.print(targetPin);
      Serial.print(" -> ");
      Serial.println(newState == HIGH ? "HIGH (ON)" : "LOW (OFF)");
    }
  }
}
