#!/usr/bin/env bash

SERVICE_NAME=rrmonitor.service
SERVICE_FILE=/etc/systemd/system/$SERVICE_NAME
SERVICE_USER=${SUDO_USER:-$(id -un)}

if [[ ! "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]]; then
  echo "Error: Cannot create services or sudoers rules for invalid user: $SERVICE_USER"
  exit 1
fi

# --- Installation Section ---
echo "Installing dependencies..."
if ! sudo apt update -y; then
  echo "Error: Failed to update apt repositories. Aborting installation."
  exit 1
fi

if ! sudo apt install -y python3-pip python3-venv ustreamer v4l-utils; then
  echo "Error: Failed to install dependencies. Aborting installation."
  exit 1
fi

# --- Optional USB Printer Power Support ---
read -r -p "Install optional USB printer-power support using uhubctl? [y/N] " INSTALL_USB_POWER
if [[ "$INSTALL_USB_POWER" =~ ^[Yy]$ ]]; then
  echo "Installing uhubctl..."
  if ! sudo apt install -y uhubctl; then
    echo "Error: Failed to install uhubctl. Aborting installation."
    exit 1
  fi

  UHUBCTL_PATH=$(command -v uhubctl)
  if [ -z "$UHUBCTL_PATH" ]; then
    echo "Error: uhubctl was installed but its executable could not be found."
    exit 1
  fi

  SUDOERS_TMP=$(mktemp)
  printf '%s ALL=(root) NOPASSWD: %s\n' "$SERVICE_USER" "$UHUBCTL_PATH" > "$SUDOERS_TMP"
  if ! sudo visudo -cf "$SUDOERS_TMP"; then
    echo "Error: Generated uhubctl sudoers rule is invalid."
    rm -f "$SUDOERS_TMP"
    exit 1
  fi
  if ! sudo install -m 0440 "$SUDOERS_TMP" /etc/sudoers.d/rrmonitor-uhubctl; then
    echo "Error: Failed to install the uhubctl sudoers rule."
    rm -f "$SUDOERS_TMP"
    exit 1
  fi
  rm -f "$SUDOERS_TMP"

  echo
  echo "USB printer-power support installed. Controllable hubs:"
  sudo "$UHUBCTL_PATH" || true
  echo
  echo "To use it, set these values in settings.py:"
  echo '  PRINTER_POWER_CONTROL = "usb"'
  echo '  USB3_TEST_HUB_LOCATION = "<hub location shown above>"'
  echo '  USB3_TEST_PORT = <port number>'
else
  echo "Skipping optional USB printer-power support."
fi

echo "Creating virtual environment..."
python3 -m venv .venv
if [ $? -ne 0 ]; then
  echo "Error: Failed to create virtual environment. Aborting installation."
  exit 1
fi

echo "Activating virtual environment..."
source .venv/bin/activate
if [ $? -ne 0 ]; then
  echo "Error: Failed to activate virtual environment. Aborting installation."
  exit 1
fi

echo "Installing Python packages..."
pip3 install flask authlib flask-wtf requests gunicorn pyserial
if [ $? -ne 0 ]; then
  echo "Error: Failed to install python libraries. Aborting installation."
  exit 1
fi

echo "Deactivating virtual environment..."
deactivate

echo "Making run_server.sh executable..."
chmod +x run_server.sh
if [ $? -ne 0 ]; then
  echo "Error: Failed to make run_server.sh executable. Aborting installation."
  exit 1
fi

# --- Service File Creation ---
echo "Creating systemd service file: $SERVICE_FILE"
cat <<EOF > "$PWD/$SERVICE_NAME"
[Unit]
Description=R_R_Monitor
After=network-online.target relay_service.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$SERVICE_USER
ExecStart=$PWD/run_server.sh
WorkingDirectory=$PWD
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
if [ $? -ne 0 ]; then
  echo "Error: Failed to create the service file. Aborting installation."
  exit 1
fi
sudo mv "$PWD/$SERVICE_NAME" "$SERVICE_FILE"
if [ $? -ne 0 ]; then
  echo "Error: Failed to move the service file to $SERVICE_FILE. Aborting installation."
  exit 1
fi

# --- Service Management ---
echo "Enabling and starting the service..."
sudo systemctl enable "$SERVICE_NAME"
if [ $? -ne 0 ]; then
  echo "Error: Failed to enable the service. Installation incomplete."
  exit 1
fi
sudo systemctl start "$SERVICE_NAME"
if [ $? -ne 0 ]; then
  echo "Error: Failed to start the service. Installation incomplete."
  exit 1
fi

# --- Relay Service ---
RELAY_SERVICE_NAME=relay_service.service
RELAY_SERVICE_FILE=/etc/systemd/system/$RELAY_SERVICE_NAME

echo "Creating systemd service file: $RELAY_SERVICE_FILE"
cat <<EOF > "$PWD/$RELAY_SERVICE_NAME"
[Unit]
Description=Persistent UART Relay Bridge
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$SERVICE_USER
ExecStart=$PWD/.venv/bin/python3 -u $PWD/relay_service.py
WorkingDirectory=$PWD
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
if [ $? -ne 0 ]; then
  echo "Error: Failed to create the relay service file. Installation incomplete."
  exit 1
fi
sudo mv "$PWD/$RELAY_SERVICE_NAME" "$RELAY_SERVICE_FILE"
sudo systemctl enable "$RELAY_SERVICE_NAME"
sudo systemctl start "$RELAY_SERVICE_NAME"
if [ $? -ne 0 ]; then
  echo "Error: Failed to start the relay service. Installation incomplete."
  exit 1
fi

# --- Printer Service ---
PRINTER_SERVICE_NAME=printer_service.service
PRINTER_SERVICE_FILE=/etc/systemd/system/$PRINTER_SERVICE_NAME

echo "Creating systemd service file: $PRINTER_SERVICE_FILE"
cat <<EOF > "$PWD/$PRINTER_SERVICE_NAME"
[Unit]
Description=3D Printer Serial Bridge
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$SERVICE_USER
ExecStart=$PWD/.venv/bin/python3 -u $PWD/printer_service.py
WorkingDirectory=$PWD
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
if [ $? -ne 0 ]; then
  echo "Error: Failed to create the printer service file. Installation incomplete."
  exit 1
fi
sudo mv "$PWD/$PRINTER_SERVICE_NAME" "$PRINTER_SERVICE_FILE"
if [ $? -ne 0 ]; then
  echo "Error: Failed to move the printer service file to $PRINTER_SERVICE_FILE. Installation incomplete."
  exit 1
fi

echo "Enabling and starting the printer service..."
sudo systemctl enable "$PRINTER_SERVICE_NAME"
if [ $? -ne 0 ]; then
  echo "Error: Failed to enable the printer service. Installation incomplete."
  exit 1
fi
sudo systemctl start "$PRINTER_SERVICE_NAME"
if [ $? -ne 0 ]; then
  echo "Error: Failed to start the printer service. Installation incomplete."
  exit 1
fi

echo "Radina Radionica Monitor installation and service started successfully!"

exit 0
