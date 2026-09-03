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

            def send(line):
                self.serial.write(line.rstrip(b'\r\n') + b'\n')
                self.serial.flush()
                deadline = time.time() + REPLY_TIMEOUT
                errors = []
                while time.time() < deadline:
                    reply = self.serial.readline().decode('ascii', 'replace').strip()
                    if reply.startswith('ok'):
                        if errors:
                            raise RuntimeError(errors[-1])
                        return
                    if 'error' in reply.lower() or 'failed' in reply.lower():
                        errors.append(reply)
                raise RuntimeError('Printer response timed out')

            self.serial.reset_input_buffer()
            send(f'M28 {filename}'.encode('ascii'))
            with open(path, 'rb') as source:
                for line in source:
                    if line.strip():
                        send(line)
                    progress['written'] += len(line)
            send(b'M29')


printer = Printer()
upload_lock = threading.Lock()
upload_status = {'state': 'idle', 'filename': None, 'written': 0, 'size': 0, 'error': None}


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
        line = self.rfile.readline().decode('ascii', 'replace').strip()
        if not line:
            return

        if line == '?status':
            self.wfile.write(b"online\n" if printer.online() else b"offline\n")
            return

        if line == '?upload-status':
            with upload_lock:
                status = dict(upload_status)
            self.wfile.write((json.dumps(status) + '\n').encode('utf-8'))
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

        reply = printer.command(line)
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
