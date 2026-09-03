# Project operating context

## Deployment

- Production is a Raspberry Pi 4 reachable with `ssh radionica`.
- Production checkout: `/home/rada/Applications/3d-printer-server`.
- The Pi uses a read-only Git deploy key. Always commit and push from the development computer, then pull on the Pi with `git pull --ff-only`.
- Keep the local branch, `origin/main`, and the Pi checkout synchronized after completed changes.
- Production secrets and mutable data are ignored: `settings.py`, `client_secret.json`, and `database.db`.

## Services

- `3d-printer-server.service`: Gunicorn/Flask on port 5030.
- `printer_service.service`: persistent printer serial bridge on `127.0.0.1:5031`.
- `relay_service.service`: persistent UART relay bridge on `127.0.0.1:5032`.
- `ustreamer`: MJPEG camera stream on port 8013, managed by the Flask application.

The serial bridges exist because opening either serial device may reset its controller. Restart only the service whose code changed.

- Flask/template/CSS/JavaScript/helper changes normally require only `3d-printer-server.service` to restart.
- Never restart `printer_service.service` during an active print. Check with Marlin `M27` first.
- Restarting `relay_service.service` may reset the relay board and turn printer power off.
- After any service operation, verify all three services, relay state, and printer connectivity as applicable.

## Printer power

`PRINTER_POWER_CONTROL` selects exactly one UI/backend:

- `"serial"`: persistent UART relay service.
- `"usb"`: switchable USB power through `uhubctl`.

Production currently uses `"serial"`. Preserve printer power across web deployments by leaving both bridge services running.

## Printer protocol

- `M20`: list SD files.
- `M23` + `M24`: select and start a print.
- `M27`: report byte-based print progress. Creality firmware may say `TF printing byte` instead of `SD printing byte`.
- `M28` / `M29`: upload a G-code file.
- `M524`: immediately abort an SD print.
- This firmware does not expose long filenames through `M20 L` or `M33`; uploads use safe DOS 8.3 names.

## Change workflow

1. Inspect existing code and preserve unrelated user changes.
2. Make changes locally and validate in proportion to risk.
3. Commit and push from this computer.
4. Pull on `radionica` using `git pull --ff-only`.
5. Restart only affected services without interrupting serial bridges unnecessarily.
6. Verify service health, hardware state, and repository synchronization.
