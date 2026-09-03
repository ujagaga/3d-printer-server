# 3D Printer Server

3D Printer Server is a lightweight Raspberry Pi-hosted Flask dashboard for monitoring and controlling a Creality Ender 3 3D printer, or any Marlin firmware based. It combines a live camera stream, authenticated printer power control, SD-card file management, print start/stop controls, and live print progress.


## Features

- Google OAuth login with administrator approval for new users.
- Responsive MJPEG camera view powered by `ustreamer`.
- One printer-power switch with a selectable serial-relay or USB-power backend.
- Persistent serial connections so restarting Flask does not reset the relay board or printer.
- Printer SD-card file listing, print start, immediate print abort, and byte-based progress.
- Email notification to all registered users when a print finishes.
- Asynchronous G-code upload to the printer SD card with transfer progress.
- SQLite user data kept in `/dev/shm` during normal operation to reduce SD-card wear.

## Architecture

| Component | Default address | Purpose |
|---|---:|---|
| `3d-printer-server.service` | `0.0.0.0:5030` | Gunicorn/Flask web application |
| `printer_service.service` | `127.0.0.1:5031` | Persistent Ender serial connection and G-code bridge |
| `relay_service.service` | `127.0.0.1:5032` | Persistent UART relay connection |
| `ustreamer` | `0.0.0.0:8013` | MJPEG camera stream |

Both hardware bridges bind only to localhost. Flask communicates with them over TCP.

Opening either USB serial device can reset its controller. Keeping serial ownership in separate long-running services allows `3d-printer-server.service` to restart without turning the printer off or interrupting a print.

## Installation

The recommended target is a Raspberry Pi running Debian or Raspberry Pi OS.

1. Clone the repository and enter it.
2. Copy the settings template:

   ```bash
   cp settings.py.example settings.py
   ```

3. Configure `settings.py`, including serial by-ID paths, OAuth/admin values, camera URL, and the desired printer-power backend.
4. Place the Google OAuth client configuration at `client_secret.json` in the project root.
5. Run:

   ```bash
   ./install.sh
   ```

The installer installs system dependencies, creates `.venv`, installs the Python packages, and enables all three systemd services.

During installation it asks whether to install optional USB printer-power support. Pressing Enter or answering `N` skips it. Answering `Y`:

- Installs `uhubctl`.
- Resolves the installed executable path.
- Generates and validates a narrowly scoped `NOPASSWD` sudoers rule.
- Lists controllable hubs and prints the required settings.

## Configuration

All local configuration belongs in the ignored `settings.py`. See `settings.py.example` for every option.

### Printer power

Only one power switch is shown in the dashboard. Choose its backend with:

```python
PRINTER_POWER_CONTROL = "serial"
```

Supported values:

- `"serial"` uses the relay board through `relay_service.py`.
- `"usb"` uses a switchable USB port through `uhubctl`.

For the serial backend, prefer a stable `/dev/serial/by-id/...` path:

```python
UART_SW_PORT = "/dev/serial/by-id/usb-..."
UART_SW_BAUD = 9600
RELAY_TCP_PORT = 5032
```

The custom single-relay board in [`hardware/UART_Relay`](hardware/UART_Relay) is detected at 115200 baud. The older two-relay protocol uses `UART_SW_BAUD`; the web interface intentionally exposes only its first channel as printer power.

For the USB backend, run `sudo uhubctl` to identify a switchable location and port, then configure:

```python
PRINTER_POWER_CONTROL = "usb"
USB3_TEST_HUB_LOCATION = "1-1"
USB3_TEST_PORT = 1
```

### Printer connection

```python
PRINTER_PORT = "/dev/serial/by-id/usb-..."
PRINTER_BAUD = 115200
PRINTER_TCP_PORT = 5031
```

The printer port may exist only while printer power is on. `printer_service.py` watches for it and opens it once when it appears.

### Camera

For a local camera stream:

```python
USTREAMER_URL = "http://localhost:8013/stream"
```

For a public deployment, point this at the externally routed camera endpoint. `STREAM_TIMEOUT` controls how long a stream process remains alive without a page request refreshing it.

### Authentication

Set `SUPER_ADMIN` to the initial administrator account. Other users are added after their first Google login and must be approved by an administrator.

Production OAuth requires HTTPS. A reverse tunnel or reverse proxy should route the Flask service and camera stream, provide TLS, and disable inappropriate caching of dynamic endpoints.

`GUARD_RELAY = True` requires a logged-in user for power control. Disabling it should be limited to trusted development environments.

#### Configure Google OAuth

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create or select a project.
2. In **Google Auth Platform**, configure **Branding** with an application name, support email, and developer contact email.
3. Under **Audience**, choose the appropriate user type:

   - Choose **Internal** only when every user belongs to the same Google Workspace organization.
   - Otherwise choose **External**. While the app is in testing, add every account that needs access as a test user. Publish the app when it should be available beyond those test users.

4. Under **Data Access**, add the `email` scope. The application requests only the signed-in user's email address.
5. Under **Clients**, create an OAuth client with the **Web application** type.
6. Add the application's exact public callback URL as an authorized redirect URI:

   ```text
   https://YOUR_PUBLIC_HOST/oauth2callback
   ```

   Replace `YOUR_PUBLIC_HOST` with the hostname users visit. The scheme, hostname, port, path, case, and trailing slash must match exactly; this application always generates an HTTPS callback URL.
7. Download the client configuration, rename it to `client_secret.json`, and place it in the project root beside `index.py`.
8. Set `SUPER_ADMIN` in `settings.py` to the Google account that should become the initial administrator, then restart `3d-printer-server.service`.

`client_secret.json` contains credentials and is ignored by Git. Do not commit or publish it. OAuth is bypassed when Flask debug mode is enabled.

See Google's [OAuth web-server application guide](https://developers.google.com/identity/protocols/oauth2/web-server) and [audience configuration guide](https://support.google.com/cloud/answer/15549945) for current console details.

## Printer controls

The dashboard polls printer status every five seconds. When printer power is switched on, the Settings control appears automatically as soon as the printer bridge reports online; no page refresh is required.

The printer settings page provides:

- An SD-card file list using Marlin `M20`.
- Print selection and start using `M23` followed by `M24`.
- G-code upload using `M28`/`M29`.

The camera page provides:

- Live print progress using `M27` byte counters.
- A footer Stop control using `M524` to abort the active SD print.

### Print completion email

The Flask service checks `M27` every two minutes independently of the browser. When an active SD print changes from printing to idle, it sends a separate completion email to every address in the users database, including the super administrator. Separate messages keep recipients' addresses private.

Email uses `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, and `SMTP_STARTTLS` from `settings.py`. A print stopped through the dashboard does not generate a completion notification. Because status is checked every two minutes, delivery can occur up to approximately two minutes after a print finishes.

### Upload behavior and filename limitation

Uploads accept `.gcode`, `.gco`, and `.g` files up to 32 MiB. The HTTP request spools the file to the printer bridge, which writes it asynchronously while holding the serial lock. Other printer commands return busy until the transfer finishes.

This Ender firmware does not expose long filenames through `M20 L` or `M33`. Uploaded names are therefore converted to a safe DOS 8.3 name, such as `My detailed model.gcode` to `MYDETAIL.GCO`. Existing SD filenames are not overwritten.

## Service management

```bash
sudo systemctl status 3d-printer-server.service relay_service.service printer_service.service
sudo systemctl restart 3d-printer-server.service
journalctl -u 3d-printer-server.service -n 100 --no-pager
journalctl -u relay_service.service -n 100 --no-pager
journalctl -u printer_service.service -n 100 --no-pager
```

Restarting only `3d-printer-server.service` leaves both serial bridges running. Restarting `printer_service.service` reopens the printer serial port and may reset the printer, so first confirm that no print is active.

## Development

Install the dependencies in a virtual environment, configure a development `settings.py`, then run:

```bash
FLASK_DEBUG=1 .venv/bin/python index.py
```

Debug mode uses `SUPER_ADMIN` for local login instead of starting the Google OAuth flow.

## Deployment workflow

The normal update workflow for this installation is:

1. Make and verify changes on the development computer.
2. Commit and push from the development computer.
3. Pull with `git pull --ff-only` on the Raspberry Pi using its read-only deploy key.
4. Restart only the services whose code changed.
5. Confirm service health and that the development, origin, and deployed revisions match.
