#!/usr/bin/env bash

# Install arduino-cli (if not already installed):
#
#   Linux/macOS:
#     curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
#     sudo mv bin/arduino-cli /usr/local/bin/
#
#   Or via snap (Linux):
#     sudo snap install arduino-cli
#
#   After installing, initialize the config:
#     arduino-cli config init

# Install AVR platform (covers Arduino Nano / ATmega328P + CH340 USB-serial)
arduino-cli core update-index
arduino-cli core install arduino:avr

# No external libraries required: UART_Relay.ino only uses built-in AVR APIs.
