const int RELAY_PIN = 13;

char cmdBuf[16];
uint8_t cmdLen = 0;
bool relayOn = false;

void printHelp() {
  Serial.println("USB Relay 1");
  Serial.println("Commands:");
  Serial.println("  on   - activate relay");
  Serial.println("  off  - deactivate relay");
  Serial.println("  help - show this text");
}

void handleCommand(const char* cmd) {
  if (strcmp(cmd, "on") == 0) {
    relayOn = true;
    digitalWrite(RELAY_PIN, HIGH);
    Serial.println("OK");
  } else if (strcmp(cmd, "off") == 0) {
    relayOn = false;
    digitalWrite(RELAY_PIN, LOW);
    Serial.println("OK");
  } else if (strcmp(cmd, "help") == 0) {
    printHelp();
    Serial.println("OK");
  } else {
    Serial.println("unknown command");
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);
  delay(100);
  printHelp();
}

void loop() {
  while (Serial.available() > 0) {
    char rx = Serial.read();

    if (rx == '\n' || rx == '\r') {
      if (cmdLen > 0) {
        cmdBuf[cmdLen] = '\0';
        handleCommand(cmdBuf);
        cmdLen = 0;
      }
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen] = rx;
      cmdLen++;
    }
  }
}
