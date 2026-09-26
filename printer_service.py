#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Bridge between the Flask app and the 3D printer.

Opening the printer serial port resets the board, which aborts a running print, so this
service opens it once when the printer appears and then keeps it open. Commands arrive
over TCP, one per connection: '?status' answers 'online' or 'offline', anything else is
sent on as G-code and the reply is returned up to Marlin's 'ok'.
"""

import logging
import json
import os
import socketserver
import threading
import time
import tempfile
import re
import serial
import settings

POLL_INTERVAL = 5
SETTLE_TIME = 3         # the board reboots when the port opens
REPLY_TIMEOUT = 10
MAX_UPLOAD_SIZE = 32 * 1024 * 1024
UPLOAD_ATTEMPTS = 3

logger = logging.getLogger(__name__)


class Printer:
    def __init__(self):
        self.lock = threading.Lock()
        self.serial = None

    def online(self):
        return self.serial is not None

    def poll(self):
        """Open the port once the printer shows up, and drop it when the power goes."""
        with self.lock:
            present = os.path.exists(settings.PRINTER_PORT)

            if self.serial is None and present:
                try:
                    self.serial = serial.Serial(settings.PRINTER_PORT, settings.PRINTER_BAUD, timeout=2)
                    time.sleep(SETTLE_TIME)
                    self.serial.reset_input_buffer()
                    # The board restores the SD position of the last print on boot,
                    # so M27 would report a print that is not running. Release the
                    # card to clear it; power loss resume is not used here.
                    self._command('M22')
                    logger.info("Printer connected")
                except Exception as e:
                    logger.error(f"ERROR opening printer port: {e}")
                    self.serial = None

            elif self.serial is not None and not present:
                try:
                    self.serial.close()
                except Exception as e:
                    logger.error(f"ERROR closing printer port: {e}")
                self.serial = None
                logger.info("Printer disconnected")

    def command(self, line):
        """Send one G-code line and collect the reply. Returns None if the printer is not there."""
        with self.lock:
            return self._command(line)

    def _command(self, line):
        """Send one G-code line with the serial lock already held."""
        if self.serial is None:
            return None

        reply = []
        try:
            self.serial.reset_input_buffer()
            self.serial.write(line.encode('ascii') + b"\n")
            self.serial.flush()

            deadline = time.time() + REPLY_TIMEOUT
            while time.time() < deadline:
                got = self.serial.readline().decode('ascii', 'replace').strip()
                if not got:
                    continue
                reply.append(got)
                if got.startswith('ok'):
                    break
        except Exception as e:
            logger.error(f"ERROR during printer command: {e}")
            return None

        return reply

    def upload(self, filename, path, progress):
        """Write a spooled G-code file to the printer SD card without allowing
        other serial commands to interleave."""
        with self.lock:
            if self.serial is None:
                raise RuntimeError('Printer is offline')

            received = bytearray()

            def send(line):
                self.serial.write(line.rstrip(b'\r\n') + b'\n')
                # The acknowledgment proves delivery; tcdrain adds a redundant
                # USB/serial wait to every line of a potentially huge file.
                deadline = time.monotonic() + REPLY_TIMEOUT
                errors = []
                replies = []
                while time.monotonic() < deadline:
                    if b'\n' not in received:
                        # pySerial readline reads one byte at a time. Consume
                        # available bytes together, retaining partial replies.
                        received.extend(self.serial.read(self.serial.in_waiting or 1))
                        continue
                    raw, _, rest = received.partition(b'\n')
                    received[:] = rest
                    reply = raw.decode('ascii', 'replace').strip()
                    if reply.startswith('ok'):
                        if errors:
                            raise RuntimeError(errors[-1])
                        return replies
                    replies.append(reply)
                    if ('error' in reply.lower() or 'failed' in reply.lower()
                            or reply.lower().startswith(('resend:', 'rs '))):
                        errors.append(reply)
                raise RuntimeError('Printer response timed out')

            self.serial.reset_input_buffer()
            for attempt in range(1, UPLOAD_ATTEMPTS + 1):
                progress['written'] = 0
                try:
                    # Without a mounted card the firmware acknowledges M28 but
                    # then executes every following line as a live command.
                    if not any('Writing to file' in reply
                               for reply in send(f'M28 {filename}'.encode('ascii'))):
                        raise RuntimeError('Printer did not open the SD file for writing')
                    with open(path, 'rb') as source:
                        for line in source:
                            # Marlin ignores comment-only lines without sending ok.
                            command = line.split(b';', 1)[0].strip()
                            if command:
                                send(command)
                            progress['written'] += len(line)
                    send(b'M29')
                    return
                except Exception as e:
                    # Leave SD write mode even when a transfer fails, and delete
                    # the partial file, which would block another upload of it.
                    # A failed write can leave the file undeletable until the
                    # card is mounted again.
                    for command in (b'M29', b'M21', f'M30 {filename}'.encode('ascii')):
                        try:
                            send(command)
                        except Exception:
                            logger.exception(f'Could not clean up failed SD upload: {command}')
                    # The SD card write fails intermittently; start over.
                    if attempt == UPLOAD_ATTEMPTS or 'error writing to file' not in str(e).lower():
                        raise
                    logger.warning(f'Retrying upload of {filename} after: {e}')


printer = Printer()
upload_lock = threading.Lock()
upload_status = {'state': 'idle', 'filename': None, 'written': 0, 'size': 0, 'error': None}
sd_files_lock = threading.Lock()
sd_files_cache = None
selected_sd_file = None
active_sd_file = None
print_started_at = None
power_off_when_done = bool(getattr(settings, 'POWER_OFF_WHEN_DONE', False))
print_seen_running = False
power_off_pending = False
command_lock = threading.Lock()


def power_off_printer():
    if getattr(settings, 'PRINTER_POWER_CONTROL', 'serial') == 'usb':
        import helper
        return helper.set_usb3_test_state(settings.USB3_TEST_HUB_LOCATION, settings.USB3_TEST_PORT, 0)
    from uart_switch import RelayServiceController
    return RelayServiceController(getattr(settings, 'RELAY_TCP_PORT', 5032)).set_socket(0, 0) is not None


def remember_print_file(command, reply):
    global selected_sd_file, active_sd_file, print_started_at
    global power_off_when_done, print_seen_running, power_off_pending
    if not reply:
        return
    lowered = [line.lower() for line in reply]
    success = any(line.startswith('ok') for line in lowered) and not any(
        marker in line for line in lowered for marker in ('error:', 'failed'))
    with sd_files_lock:
        if command.startswith('M23 '):
            selected_sd_file = command[4:].strip() if success else None
            power_off_pending = False
        elif command == 'M24' and success:
            active_sd_file = selected_sd_file
            power_off_pending = False
            if print_started_at is None:
                print_started_at = time.time()
                power_off_when_done = bool(getattr(settings, 'POWER_OFF_WHEN_DONE', False))
            print_seen_running = True
        elif command == 'M27' and any(re.search(r'printing byte\s+\d+\s*/\s*[1-9]\d*', line) for line in lowered):
            print_seen_running = True
        elif (command == 'M25' and success) or (
                command == 'M27' and any(re.search(r'not (?:sd|tf) printing', line) for line in lowered)):
            active_sd_file = None
            selected_sd_file = None
            print_started_at = None
            power_off_pending = command == 'M27' and (power_off_pending or (print_seen_running and power_off_when_done))
            print_seen_running = False
        elif reply == ['offline']:
            print_seen_running = False
            power_off_pending = False


def run_upload(filename, path):
    try:
        printer.upload(filename, path, upload_status)
        upload_status['state'] = 'complete'
    except Exception as e:
        logger.error(f'ERROR uploading {filename}: {e}')
        upload_status['state'] = 'error'
        upload_status['error'] = str(e)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


class RequestHandler(socketserver.StreamRequestHandler):
    timeout = 20        # do not let a client that never sends hold a thread

    def handle(self):
        global sd_files_cache
        global power_off_when_done, power_off_pending
        line = self.rfile.readline().decode('ascii', 'replace').strip()
        if not line:
            return

        if line in ('?power-off-when-done', '?power-off-when-done 0', '?power-off-when-done 1'):
            with sd_files_lock:
                if ' ' in line:
                    power_off_when_done = line.endswith(' 1')
                    if not power_off_when_done:
                        power_off_pending = False
                enabled = power_off_when_done
            self.wfile.write((json.dumps(enabled) + '\n').encode('ascii'))
            return

        if line == '?power-off-if-done':
            with command_lock, sd_files_lock:
                if power_off_pending:
                    power_off_pending = False
                    reply = printer.command('M27')
                    idle = reply and any(re.search(r'not (?:sd|tf) printing', item, re.IGNORECASE) for item in reply)
                    if idle and not power_off_printer():
                        logger.error('Automatic printer power off failed')
            self.wfile.write(b'ok\n')
            return

        if line == '?status':
            self.wfile.write(b"online\n" if printer.online() else b"offline\n")
            return

        if line == '?upload-status':
            with upload_lock:
                status = dict(upload_status)
            self.wfile.write((json.dumps(status) + '\n').encode('utf-8'))
            return

        if line == '?sd-files-cache':
            with sd_files_lock:
                cached = sd_files_cache
            self.wfile.write((json.dumps(cached) + '\n').encode('ascii'))
            return

        if line == '?active-sd-file':
            with sd_files_lock:
                filename = active_sd_file
            self.wfile.write((json.dumps(filename) + '\n').encode('ascii'))
            return

        if line == '?print-started-at':
            with sd_files_lock:
                started_at = print_started_at
            self.wfile.write((json.dumps(started_at) + '\n').encode('ascii'))
            return

        if line.startswith('?upload '):
            try:
                _, filename, size_text = line.split()
                size = int(size_text)
                if (size < 1 or size > MAX_UPLOAD_SIZE
                        or not re.fullmatch(r'[A-Z0-9]{1,8}\.GCO', filename)):
                    raise ValueError
            except ValueError:
                self.wfile.write(b'error Invalid upload request\n')
                return

            with upload_lock:
                if upload_status['state'] in ('receiving', 'uploading'):
                    self.wfile.write(b'error Upload already in progress\n')
                    return
                upload_status.update(state='receiving', filename=filename, written=0,
                                     size=size, error=None)

            with tempfile.NamedTemporaryFile(prefix='printer-upload-', delete=False) as target:
                remaining = size
                while remaining:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    target.write(chunk)
                    remaining -= len(chunk)
                path = target.name

            if remaining:
                os.unlink(path)
                upload_status.update(state='error', error='Upload was incomplete')
                self.wfile.write(b'error Upload was incomplete\n')
                return

            upload_status['state'] = 'uploading'
            threading.Thread(target=run_upload, args=(filename, path), daemon=True).start()
            self.wfile.write(b'accepted\n')
            return

        if upload_status['state'] in ('receiving', 'uploading'):
            self.wfile.write(b'busy\n')
            return

        with command_lock:
            reply = printer.command(line)
            remember_print_file(line, reply if reply is not None else ['offline'])
        if (line == 'M20' and reply is not None
                and 'Begin file list' in reply and 'End file list' in reply
                and reply.index('Begin file list') < reply.index('End file list')):
            with sd_files_lock:
                sd_files_cache = list(reply)
        if reply is None:
            self.wfile.write(b"offline\n")
        else:
            self.wfile.write(("\n".join(reply) + "\n").encode('ascii'))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def monitor():
    while True:
        printer.poll()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
        datefmt='%Y-%m-%dT%H:%M:%S'
    )

    threading.Thread(target=monitor, daemon=True).start()

    with Server(("127.0.0.1", settings.PRINTER_TCP_PORT), RequestHandler) as server:
        logger.info(f"Printer service listening on 127.0.0.1:{settings.PRINTER_TCP_PORT}")
        server.serve_forever()
