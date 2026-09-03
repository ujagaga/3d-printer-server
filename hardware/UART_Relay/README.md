# UART Relay

Arduino Nano (old bootloader, ATmega328P + CH340 USB-serial) driving a single relay over USB serial commands. Powered via USB-C; the relay draws off the Nano's 5V pin. Casing designed in FreeCAD, 3D-printable.

## Hardware

- Board: Arduino Nano clone (ATmega328P, "old bootloader" variant)
- USB: CH340 (idVendor 1a86, idProduct 7523) — appears as `/dev/ttyUSB0` on Linux
- Relay powered from the Nano's 5V pin
- See `Schematic.png` for wiring
- Casing: `Casing3D.FCStd` (FreeCAD source), printable parts in `stl/` (`Box.stl`, `BoxCap.stl`, `PcbArmature.stl`, `PcbCap.stl`)

## Install

Requires `arduino-cli`. Run:

```
tools/install_dependencies.sh
```

This installs the `arduino:avr` platform (covers Nano/ATmega328P + CH340). No external libraries are needed — the sketch only uses built-in AVR APIs.

## Build

```
tools/build.sh
```

Compiles `UART_Relay.ino` for `arduino:avr:nano:cpu=atmega328old`, output in `build/`.

## Upload

```
tools/upload_usb.sh [port]
```

Uploads the build to the board. Defaults to `/dev/ttyUSB0` if no port is given.

## Usage

Serial, 115200 baud, line ending `\r\n`. Send one command per line:

| Command | Effect |
|---|---|
| `on` | Activate relay |
| `off` | Deactivate relay |
| `help` | Print command list |

Any other input replies `unknown command`.

## Known issue: device disappears on some USB ports (Linux)

Symptom: plugging into some USB ports, `/dev/ttyUSB0` appears for about a second, then vanishes (e.g. GTKterm shows the port open, then closed). This can look like a power/USB2-vs-USB3 issue but usually isn't — check `dmesg` first:

```
sudo dmesg -w
```

If you see something like:

```
usbfs: interface 0 claimed by ch341 while 'brltty' sets config #1
ch341-uart ttyUSB0: ch341-uart converter now disconnected from ttyUSB0
```

the cause is **brltty**, the Braille display accessibility daemon. It auto-probes USB serial devices and grabs the Nano's CH341 chip (VID 1a86:PID 7523), evicting the kernel's `ch341-uart` driver right after enumeration.

Fix (if you don't use a Braille display):

```
sudo systemctl mask brltty.service brltty-udev.service
```

If brltty is needed for actual accessibility use on the machine, instead blacklist just this device in `/etc/brltty.conf`:

```
ignore-usb-device 1a86:7523
```
