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
BUSY_REPLY_TIMEOUT = 2
MAX_UPLOAD_SIZE = 32 * 1024 * 1024
UPLOAD_ATTEMPTS = 3
POWER_OFF_TEMPERATURE = 50

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
                    # so M27 would report a print that is not running. Power loss
                    # resume is not used here: delete its record in the SD file 'bin',
                    # or the next M21 resumes the print, then release the card to clear it.
                    self._command('M30 bin')
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

    def command(self, line, timeout=REPLY_TIMEOUT):
        """Send one G-code line and collect the reply. Returns None if the printer is not there."""
        with self.lock:
            return self._command(line, timeout)

    def _command(self, line, timeout=REPLY_TIMEOUT):
        """Send one G-code line with the serial lock already held."""
        if self.serial is None:
            return None

        reply = []
        try:
            self.serial.reset_input_buffer()
            self.serial.write(line.encode('ascii') + b"\n")
            self.serial.flush()

            deadline = time.time() + timeout
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

    def drain(self):
        """Return the lines the printer sent since the last command."""
        with self.lock:
            lines = []
            try:
                while self.serial is not None and self.serial.in_waiting:
                    got = self.serial.readline().decode('ascii', 'replace').strip()
                    if got:
                        lines.append(got)
            except Exception as e:
                logger.error(f"ERROR reading printer: {e}")
            return lines

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
            # The card is released when the printer connects and after a stop.
            send(b'M21')
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
print_progress = None       # last M27 state of the selected file
m27_unanswered = False
command_lock = threading.Lock()


def power_off_printer():
    if getattr(settings, 'PRINTER_POWER_CONTROL', 'serial') == 'usb':
        import helper
        return helper.set_usb3_test_state(settings.USB3_TEST_HUB_LOCATION, settings.USB3_TEST_PORT, 0)
    from uart_switch import RelayServiceController
    return RelayServiceController(getattr(settings, 'RELAY_TCP_PORT', 5032)).set_socket(0, 0) is not None


def print_idle(reply):
    """True when an M27 reply shows no SD print running, False while one runs,
    None when the reply is unreadable. Creality firmware never says 'Not TF
    printing': it reports 0/0 with no file selected, and once a print finishes it
    keeps reporting the full size of that file until another one is selected."""
    for line in reply:
        if re.search(r'not (?:sd|tf) printing', line, re.IGNORECASE):
            return True
        match = re.search(r'printing byte\s+(\d+)\s*/\s*(\d+)', line, re.IGNORECASE)
        if match:
            current, total = (int(value) for value in match.groups())
            return current >= total
    return None


def nozzle_cooled(reply):
    """True when an M105 reply shows the nozzle below the power off temperature."""
    for line in reply or []:
        match = re.search(r'\bT0?:\s*(-?[\d.]+)', line)
        if match:
            return float(match.group(1)) < POWER_OFF_TEMPERATURE
    return False


def remember_print_file(command, reply):
    global selected_sd_file, active_sd_file, print_started_at
    global power_off_when_done, print_seen_running, power_off_pending
    global print_progress, m27_unanswered
    if not reply:
        return
    # M24 always tries to delete the power loss record, which is already gone.
    lowered = [line.lower() for line in reply if line.lower() != 'deletion(bin) failed.']
    success = any(line.startswith('ok') for line in lowered) and not any(
        marker in line for line in lowered for marker in ('error:', 'failed'))
    if command == 'M24':
        logger.info(f"M24 reply (success={success}): {reply}")
    with sd_files_lock:
        if command == 'M27':
            m27_unanswered = print_idle(reply) is None
            for line in reply:
                match = re.search(r'printing byte\s+(\d+)\s*/\s*(\d+)', line, re.IGNORECASE)
                if match:
                    print_progress = f'SD printing byte {match.group(1)}/{match.group(2)}'
                elif re.search(r'not (?:sd|tf) printing', line, re.IGNORECASE):
                    print_progress = 'Not SD printing'
        if command.startswith('M23 '):
            selected_sd_file = command[4:].strip() if success else None
            power_off_pending = False
            size = next((match.group(1) for match in (re.search(r'size:\s*(\d+)', line) for line in lowered) if match), None)
            print_progress = f'SD printing byte 0/{size}' if success and size else None
        elif command == 'M24' and success:
            active_sd_file = selected_sd_file
            power_off_pending = False
            # A print starts by heating, which leaves M27 unanswered.
            m27_unanswered = True
            if print_started_at is None:
                print_started_at = time.time()
                power_off_when_done = bool(getattr(settings, 'POWER_OFF_WHEN_DONE', False))
            print_seen_running = True
        elif command == 'M27' and print_idle(reply) is False:
            print_seen_running = True
        elif (command == 'M25' and success) or (command == 'M27' and print_idle(reply)):
            if print_started_at is not None:
                logger.info(f"{command} cleared print start time: {reply}")
            active_sd_file = None
            selected_sd_file = None
            print_started_at = None
            if command == 'M25':
                print_progress = None
            power_off_pending = command == 'M27' and (power_off_pending or (print_seen_running and power_off_when_done))
            print_seen_running = False
        elif reply == ['offline']:
            print_seen_running = False
            power_off_pending = False
            print_progress = None
            m27_unanswered = False


def busy_reply(command):
    """Marlin answers nothing while it blocks on heating or homing: it queues
    commands, repeats 'busy: processing' and reports temperatures instead.
    Queued M27 and M105 would outlast the client timeouts and pile up, so
    answer them from what the printer sent since the last command. Returns the
    reply, if there is one, and whether the printer is busy."""
    lines = printer.drain()
    if print_idle(lines) is not None:
        remember_print_file('M27', lines)   # a late answer to an earlier M27
    if not any('busy:' in line for line in lines):
        return None, False
    if command == 'M105':
        temperatures = [line for line in lines if re.search(r'\bT0?:.*\bB:', line)]
        return temperatures[-1:] or None, True
    with sd_files_lock:
        return [print_progress] if m27_unanswered and print_progress else None, True


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
                    reply = printer.command('M27')
                    idle = print_idle(reply) if reply else False
                    if idle is False:
                        power_off_pending = False
                    elif idle and nozzle_cooled(printer.command('M105')):
                        # Cutting power stops the hotend fan, so wait for the
                        # nozzle to cool or the filament can clog it.
                        power_off_pending = False
                        if not power_off_printer():
                            logger.error('Automatic printer power off failed')
            self.wfile.write(b'ok\n')
            return

        if line == '?power-off-pending':
            with sd_files_lock:
                pending = power_off_pending
            self.wfile.write((json.dumps(pending) + '\n').encode('ascii'))
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
            reply, busy = busy_reply(line) if line in ('M27', 'M105') else (None, False)
            if reply is None:
                # A busy printer answers late, if at all; do not hold the line for it.
                reply = printer.command(line, BUSY_REPLY_TIMEOUT if busy else REPLY_TIMEOUT)
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
