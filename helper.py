import string
import random
from hashlib import sha256
import sys
import settings
from datetime import datetime, timezone
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import re
import os
import subprocess
import socket
import json

logger = logging.getLogger(__name__)
DATE_FORMAT = "%Y-%m-%d"
current_path = os.path.dirname(os.path.realpath(__file__))

def generate_token():
    return ''.join(random.choices(string.ascii_letters, k=32))


def hash_password(password: str):
    return sha256(password.encode('utf-8')).hexdigest()


def generate_random_string():
    return hash_password(generate_token())


def string_to_date(valid_until: str):
    result = None

    try:
        result = datetime.strptime(valid_until, DATE_FORMAT)
        # Setting to middle of the day for easier comparison.
        result = result.replace(hour=12, minute=0)
    except Exception as exc:
        exc_type, exc_obj, exc_tb = sys.exc_info()
        logger.error("ERROR converting string to date on line {}!\n\t{}".format(exc_tb.tb_lineno, exc))

    return result


def date_to_string(valid_date: datetime) -> str:
    return valid_date.strftime(DATE_FORMAT)


def to_int(number, default = 0):
    try:
        return int(number)
    except:
        return default


def iso_to_epoch(iso_str: str) -> int:
    dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
    return int(dt.timestamp())


def epoch_to_iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def rough_time_ago(seconds_ago: int) -> str:
    minutes = seconds_ago // 60
    hours = minutes // 60
    days = hours // 24

    if hours < 24:
        return "today"
    elif days == 1:
        return "yesterday"
    elif days < 7:
        return f"{days} days ago"
    elif days < 30:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    elif days < 365:
        months = days // 30
        return f"{months} month{'s' if months > 1 else ''} ago"
    else:
        return "more than a year ago"


'''
Sends an email using configured credentials. 
'''
def send_email(recipient, subject, body):
    msg = MIMEMultipart()
    msg['From'] = settings.SMTP_USER
    msg['To'] = recipient
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        logger.info(f"Sending email to: {recipient}")
        with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
            server.ehlo()
            if settings.SMTP_STARTTLS:
                server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASS)
            server.sendmail(settings.SMTP_USER, recipient, msg.as_string())
            logger.info("Sending email done")

    except Exception as e:
        logger.exception(f"Error sending email: {e}")


def run_ustreamer(video_device="0", resolution="", start=True):
    if start:
        cmd = "start"
    else:
        cmd = "stop"

    script_path = os.path.join(current_path, settings.USTREAMER_SCRIPT)
    subprocess.call([script_path, cmd, f"{video_device}", resolution, f"{settings.STREAM_TIMEOUT}"])


def check_supported_resolutions(camera_index):
    try:
        output = subprocess.check_output(
            ["v4l2-ctl", f"--device=/dev/video{camera_index}", "--list-formats-ext"],
            stderr=subprocess.STDOUT
        ).decode()
    except subprocess.CalledProcessError as e:
        print(f"Error calling v4l2-ctl: {e.output.decode()}")
        return []

    resolutions = {}
    for line in output.splitlines():
        match = re.search(r'\s+Size:\s+Discrete\s+(\d+)x(\d+)', line)
        if match:
            width, height = match.groups()
            width_int = width
            try:
                width_int = int(width)
            except ValueError:
                pass

            resolutions[width_int] = height

    sorted_list = []

    for key in sorted(resolutions.keys()):
        resolution = f"{key}x{resolutions[key]}"
        sorted_list.append(resolution)

    return sorted_list


def get_usb3_test_state(location, port):
    """Query power state of an uhubctl-controlled port. Returns True/False, or None on failure."""
    try:
        output = subprocess.check_output(
            ["sudo", "uhubctl", "-l", location, "-p", str(port)],
            stderr=subprocess.STDOUT
        ).decode()
    except Exception as e:
        logger.error(f"uhubctl status error: {e}")
        return None

    prefix = f"Port {port}:"
    for line in output.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return "power" in line.split(":", 1)[1]
    return None


def set_usb3_test_state(location, port, state):
    """Turn an uhubctl-controlled port on/off. Returns True on success."""
    try:
        subprocess.check_call(["sudo", "uhubctl", "-l", location, "-p", str(port), "-a", "on" if state else "off"])
        return True
    except Exception as e:
        logger.error(f"uhubctl set error: {e}")
        return False


def list_video_devices():
    video_devices = {}
    dev_dir = '/dev'
    pattern = re.compile(r'^video\d+$')

    for entry in os.listdir(dev_dir):
        if pattern.match(entry):
            dev_id = entry.replace("video", "")
            resolutions = check_supported_resolutions(dev_id)
            if len(resolutions) > 1:
                video_devices[dev_id] = resolutions

    if len(video_devices.keys()) > 0:
        return video_devices
    else:
        return None


def printer_request(command):
    """Send one line to the printer service and return its reply lines, or None if it
    could not be reached. The service holds the serial port open, so asking it for
    something does not reset the printer."""
    try:
        with socket.create_connection(("127.0.0.1", settings.PRINTER_TCP_PORT), timeout=15) as sock:
            sock.sendall(command.encode('ascii') + b"\n")
            reply = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                reply += chunk
    except Exception as e:
        logger.error(f"printer service error: {e}")
        return None

    return reply.decode('ascii', 'replace').splitlines()


def printer_connected():
    """True while the printer service reports the printer online."""
    return printer_request('?status') == ['online']


def list_printer_sd_files():
    """Ask Marlin for the SD card listing (M20). Returns a list of {"name", "size"} dicts, or
    None if the printer could not be reached. Size is in bytes, or None when not reported."""
    lines = printer_request('M20')
    if lines is None or lines == ['offline']:
        return None

    files = []
    listing = False
    for line in lines:
        if line.startswith("Begin file list"):
            listing = True
        elif line.startswith("End file list"):
            break
        elif listing:
            parts = line.split()
            if parts[0].startswith("/TRASH"):
                continue
            size = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            files.append({"name": parts[0], "size": size})

    return files


def printer_command_succeeded(lines):
    """Return True when Marlin acknowledged a command without reporting an error."""
    if not lines or lines == ['offline']:
        return False

    error_markers = ('error:', 'failed', 'not sd printing')
    lowered = [line.lower() for line in lines]
    return any(line.startswith('ok') for line in lowered) and not any(
        marker in line for line in lowered for marker in error_markers
    )


def start_printer_sd_file(filename):
    """Select an existing SD-card file and start printing it."""
    if not filename or any(char in filename for char in ('\r', '\n')):
        return False

    files = list_printer_sd_files()
    if files is None or filename not in {item['name'] for item in files}:
        return False

    if not printer_command_succeeded(printer_request(f'M23 {filename}')):
        return False

    return printer_command_succeeded(printer_request('M24'))


def stop_printer_sd_print():
    """Immediately abort the active SD-card print."""
    return printer_command_succeeded(printer_request('M524'))


def sd_upload_filename(filename):
    """Convert a browser filename to a safe, printable DOS 8.3 SD filename."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = ''.join(char for char in stem.upper() if char.isascii() and char.isalnum())[:8]
    return f'{stem}.GCO' if stem else None


def upload_printer_sd_file(filename, data):
    """Hand a G-code payload to the printer service for asynchronous writing."""
    sd_name = sd_upload_filename(filename)
    if not sd_name or not data:
        return None
    files = list_printer_sd_files()
    if files is None or sd_name in {item['name'].lstrip('/') for item in files}:
        return None
    try:
        with socket.create_connection(('127.0.0.1', settings.PRINTER_TCP_PORT), timeout=15) as sock:
            sock.sendall(f'?upload {sd_name} {len(data)}\n'.encode('ascii'))
            sock.sendall(data)
            sock.shutdown(socket.SHUT_WR)
            reply = sock.recv(256).decode('utf-8', 'replace').strip()
        return sd_name if reply == 'accepted' else None
    except Exception as e:
        logger.error(f'printer upload service error: {e}')
        return None


def printer_upload_status():
    lines = printer_request('?upload-status')
    if not lines:
        return None
    try:
        return json.loads(lines[0])
    except json.JSONDecodeError:
        return None


def printer_print_status():
    """Return SD print state and byte-based progress reported by Marlin M27."""
    lines = printer_request('M27')
    if not lines or lines == ['offline']:
        return {'state': 'offline', 'percent': 0, 'current': 0, 'total': 0}

    for line in lines:
        match = re.search(r'(?:SD|TF) printing byte\s+(\d+)\s*/\s*(\d+)', line, re.IGNORECASE)
        if match:
            current, total = (int(value) for value in match.groups())
            percent = min(100, round(current * 100 / total, 1)) if total else 0
            return {
                'state': 'printing' if total else 'idle',
                'percent': percent,
                'current': current,
                'total': total
            }
        if 'not sd printing' in line.lower():
            return {'state': 'idle', 'percent': 0, 'current': 0, 'total': 0}

    return {'state': 'unknown', 'percent': 0, 'current': 0, 'total': 0}
