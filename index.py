#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
pip install flask authlib flask-wtf requests
"""

from flask import (Flask, g, render_template, request, flash, redirect, make_response, jsonify, url_for as flask_url_for)
import time
import json
import sys
import os
import database
import helper
from authlib.integrations.flask_client import OAuth
import logging
import threading
from logging.handlers import RotatingFileHandler
import settings
import uart_switch
from flask_wtf import CSRFProtect


sys.path.insert(0, os.path.dirname(__file__))

UPLOAD_FOLDER = "firmware"
ALLOWED_EXTENSIONS = ["bin"]

application = Flask(__name__, static_url_path='/static', static_folder='static')
application.config['SECRET_KEY'] = settings.APP_SECRET_KEY
application.config['SESSION_COOKIE_NAME'] = 'printer_server'
application.config['WTF_CSRF_SECRET_KEY'] = application.config['SECRET_KEY']
application.config['APPLICATION_ROOT'] = '/'
application.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
application.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024

if not os.path.exists(database.temp_dir):
    os.makedirs(database.temp_dir, exist_ok=True)

file_handler = RotatingFileHandler(os.path.join(database.temp_dir, "app.log"), maxBytes=65535, backupCount=1)
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    "[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
    datefmt='%Y-%m-%dT%H:%M:%S'
)
file_handler.setFormatter(formatter)
# Always include file handler
handlers = [file_handler]
# If running in debug mode, also add console logs
if application.debug:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    handlers.append(console_handler)
logging.basicConfig(level=logging.DEBUG, handlers=handlers)

logger = logging.getLogger(__name__)

controller = uart_switch.RelayServiceController(getattr(settings, 'RELAY_TCP_PORT', 5032))

PRINT_STATUS_POLL_SECONDS = 120
_print_monitor_lock = threading.Lock()
_print_monitor_was_printing = False
_print_monitor_stop_requested = False


def _registered_user_emails():
    """Return every unique email address currently registered in the application."""
    connection = database.open_db()
    try:
        rows = connection.execute(
            "SELECT DISTINCT email FROM users WHERE email IS NOT NULL AND email != ''"
        ).fetchall()
        return [row['email'] for row in rows]
    finally:
        database.close_db(connection)


def _send_print_finished_email():
    subject = f"{settings.APP_TITLE} print finished"
    body = f"The 3D printer has finished its print.\n\n{settings.APP_TITLE}"
    for email in _registered_user_emails():
        helper.send_email(recipient=email, subject=subject, body=body)


def _print_completion_monitor():
    """Notify registered users once when an SD print changes to idle."""
    global _print_monitor_was_printing, _print_monitor_stop_requested

    while True:
        try:
            status = helper.printer_print_status()
            should_notify = False
            with _print_monitor_lock:
                if status['state'] == 'printing':
                    _print_monitor_was_printing = True
                elif status['state'] == 'idle' and _print_monitor_was_printing:
                    should_notify = not _print_monitor_stop_requested
                    _print_monitor_was_printing = False
                    _print_monitor_stop_requested = False
                elif status['state'] == 'offline':
                    _print_monitor_was_printing = False
                    _print_monitor_stop_requested = False

            if should_notify:
                _send_print_finished_email()
        except Exception:
            logger.exception('Print completion monitor failed')

        time.sleep(PRINT_STATUS_POLL_SECONDS)


threading.Thread(
    target=_print_completion_monitor,
    name='print-completion-monitor',
    daemon=True
).start()

csrf = CSRFProtect(application)

CLIENT_SECRETS_FILE = "client_secret.json"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

if application.debug:
    google = None
else:
    if os.path.isfile(CLIENT_SECRETS_FILE):
        with open(CLIENT_SECRETS_FILE) as f:
            client_secrets = json.load(f)['web']  # Assumes the JSON structure is under 'web'

        # Configure OAuth
        oauth = OAuth(application)

        google = oauth.register(
            name='google',
            client_id=client_secrets['client_id'],
            client_secret=client_secrets['client_secret'],
            access_token_url=client_secrets['token_uri'],
            access_token_params=None,
            authorize_url=client_secrets['auth_uri'],
            authorize_params=None,
            api_base_url='https://www.googleapis.com/oauth2/v1/',
            userinfo_endpoint='https://www.googleapis.com/oauth2/v3/userinfo',
            client_kwargs={'scope': 'email'},
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration'
        )

'''
On a CGI hosting, the flasks url_for populates the url with script path,
so you get junk data that does not resolve to a valid url. This is an override to clean it up.
'''
def safe_url_for(endpoint, **values):

    url = flask_url_for(endpoint, **values)
    script_name = request.environ.get('SCRIPT_NAME', '')
    if script_name and url.startswith(script_name):
        url = url[len(script_name):] or '/'
    return url


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_logged_in_user():
    token = request.cookies.get('token')
    if not token:
        return None
    return database.get_user(connection=g.db, token=token)


@application.before_request
def before_request():
    g.db = database.open_db()


@application.teardown_request
def teardown_request(exception):
    if hasattr(g, 'db'):
        database.close_db(g.db)


@application.route('/authorize')
def authorize():
    auth_url = safe_url_for('oauth2callback')
    return google.authorize_redirect(f"https://{request.host}{auth_url}")


@application.route('/login', methods=['GET'])
def login():
    if application.debug:
        token = helper.generate_token()
        user_email = settings.SUPER_ADMIN
        user = database.get_user(connection=g.db, email=user_email)
        if not user:
            database.add_user(connection=g.db, email=user_email, token=token )
            user = database.get_user(connection=g.db, email=user_email)
        database.update_user(connection=g.db, email=user_email, token=token, authorized=2)

        response = make_response(redirect(safe_url_for('index')))
        response.set_cookie('token', token, max_age=settings.MAX_COOKIE_AGE, expires=time.time() + settings.MAX_COOKIE_AGE)
        return response

    return render_template('signin.html', title=settings.APP_TITLE, url_for=safe_url_for)


@application.route('/oauth2callback')
def oauth2callback():
    global google

    if application.debug:
        # Just redirect to index, since login is automatic in /login for debug
        return redirect(safe_url_for('index'))

    try:
        google.authorize_access_token()
        resp = google.get('userinfo')
        user_info = resp.json()
        email = user_info["email"]
        picture = user_info.get("picture")

        token = helper.generate_token()

        user = database.get_user(connection=g.db, email=email)
        if not user:
            database.add_user(connection=g.db, email=email, token=token)
            user = database.get_user(connection=g.db, email=email)

        database.update_user(connection=g.db, email=email, token=token, picture=picture)

        if user.get("authorized") > 0:
            response = make_response(redirect(safe_url_for('index')))
            response.set_cookie('token', token, max_age=settings.MAX_COOKIE_AGE, expires=time.time() + settings.MAX_COOKIE_AGE)
        else:
            flash("Your account has not been authorized yet.")
            response = redirect(safe_url_for("login"))
            response.set_cookie('token', 'None', expires=0)

    except Exception as e:
        logger.exception(f"OAuth2 callback error {e}")
        # Restart the login flow
        google = oauth.register(
            name='google',
            client_id=client_secrets['client_id'],
            client_secret=client_secrets['client_secret'],
            access_token_url=client_secrets['token_uri'],
            access_token_params=None,
            authorize_url=client_secrets['auth_uri'],
            authorize_params=None,
            api_base_url='https://www.googleapis.com/oauth2/v1/',
            userinfo_endpoint='https://www.googleapis.com/oauth2/v3/userinfo',
            client_kwargs={'scope': 'email'},
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration'
        )

        response = redirect(safe_url_for("login"))
        response.set_cookie('token', 'None', expires=0)

    return response


@application.route('/', methods=['GET'])
def index():
    token = request.cookies.get('token')
    if not token:
        return redirect(safe_url_for('login'))

    user = database.get_user(connection=g.db, token=token)
    if not user:
        return redirect(safe_url_for('login'))

    args = request.args
    resolution_request = args.get("resolution")
    device_request = args.get("device")

    helper.run_ustreamer(start=False)
    time.sleep(1)
    video_devices = helper.list_video_devices()

    if video_devices:
        if not device_request:
            device_request = user.get("device")
        if not resolution_request:
            resolution_request = user.get("resolution")

        if not device_request or device_request not in video_devices.keys():
            device_request = list(video_devices.keys())[0]
        if not resolution_request or resolution_request not in video_devices[device_request]:
            resolution_index = 0
            resolution_count = len(video_devices[device_request])
            if resolution_count > 2:
                resolution_index = resolution_count - 2
            elif resolution_count > 1:
                resolution_index = resolution_count - 1

            resolution_request = video_devices[device_request][resolution_index]

        database.update_user(connection=g.db, email=user["email"], resolution=resolution_request, device=device_request)

        helper.run_ustreamer(video_device=device_request, resolution=resolution_request, start=True)

    unauthorized_users = database.get_user(connection=g.db, authorized=0)

    power_control = getattr(settings, 'PRINTER_POWER_CONTROL', 'serial')
    if power_control == 'usb':
        power_state = helper.get_usb3_test_state(settings.USB3_TEST_HUB_LOCATION, settings.USB3_TEST_PORT)
        relays = {'relay1_1': int(power_state)} if power_state is not None else {}
    else:
        relay_states = controller.get_state() if controller else []
        relays = {'relay1_1': relay_states[0]} if relay_states else {}

    # Render the template
    resp = make_response(render_template(
        'home.html',
        admin=user.get("authorized", 0) > 1,
        unauthorized_users=unauthorized_users,
        user=user,
        title=settings.APP_TITLE,
        url_for=safe_url_for,
        video_devices=video_devices,
        resolution=resolution_request,
        device=device_request,
        stream_url=settings.USTREAMER_URL,
        relays=relays,
        printer_connected=helper.printer_connected()
    ))

    # Add no-cache headers
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"

    return resp


@application.route('/relay', methods=['POST'])
def set_relay():
    if settings.GUARD_RELAY and not get_logged_in_user():
        return jsonify({"status": "error", "error": "Not logged in"}), 401

    data = request.get_json(force=True)
    relay_id = data.get("relay_id")
    state = data.get("state")

    if relay_id is None or state not in (0, 1):
        return jsonify({"status": "error", "error": "Invalid parameters"}), 400

    power_control = getattr(settings, 'PRINTER_POWER_CONTROL', 'serial')
    if power_control == 'usb':
        if relay_id != 'relay1_1':
            return jsonify({"status": "error", "error": "Invalid relay id"}), 400
        if not helper.set_usb3_test_state(settings.USB3_TEST_HUB_LOCATION, settings.USB3_TEST_PORT, state):
            return jsonify({"status": "error", "error": "Failed to set printer USB power"}), 500
        return jsonify({"status": "ok", "relay": relay_id, "state": state})

    if not controller:
        return jsonify({"status": "error", "error": "Relay service unavailable"}), 503

    try:
        socket_number = int(relay_id.rsplit("_", 1)[1]) - 1
    except (ValueError, IndexError):
        return jsonify({"status": "error", "error": "Invalid relay id"}), 400

    sw = controller.set_socket(socket_number, state)
    if sw is None:
        return jsonify({"status": "error", "error": "Failed to set relay state"}), 500

    return jsonify({"status": "ok", "relay": relay_id, "state": state})


@application.route('/usb3_test', methods=['POST'])
def set_usb3_test():
    if not get_logged_in_user():
        return jsonify({"status": "error", "error": "Not logged in"}), 401

    data = request.get_json(force=True)
    state = data.get("state")

    if state not in (0, 1):
        return jsonify({"status": "error", "error": "Invalid parameters"}), 400

    if not helper.set_usb3_test_state(settings.USB3_TEST_HUB_LOCATION, settings.USB3_TEST_PORT, state):
        return jsonify({"status": "error", "error": "Failed to set USB3 test port state"}), 500

    return jsonify({"status": "ok", "state": state})


@application.route('/settings', methods=['GET'])
def printer_settings():
    user = get_logged_in_user()
    if not user:
        return redirect(safe_url_for('login'))

    upload_status = helper.printer_upload_status()
    upload_active = upload_status and upload_status.get('state') in ('receiving', 'uploading')
    return render_template(
        'settings.html',
        user=user,
        title=settings.APP_TITLE,
        url_for=safe_url_for,
        sd_files=[] if upload_active else helper.list_printer_sd_files(),
        upload_active=upload_active
    )


@application.route('/printer/start', methods=['POST'])
def start_printer():
    global _print_monitor_stop_requested

    if not get_logged_in_user():
        return redirect(safe_url_for('login'))

    filename = request.form.get('filename', '')
    if helper.start_printer_sd_file(filename):
        with _print_monitor_lock:
            _print_monitor_stop_requested = False
        flash(f'Started printing {filename}.', 'success')
    else:
        flash(f'Could not start printing {filename or "the selected file"}.', 'error')

    return redirect(safe_url_for('printer_settings'))


@application.route('/printer/stop', methods=['POST'])
def stop_printer():
    global _print_monitor_stop_requested

    if not get_logged_in_user():
        return jsonify({'status': 'error', 'error': 'Not logged in'}), 401

    with _print_monitor_lock:
        _print_monitor_stop_requested = True

    if not helper.stop_printer_sd_print():
        with _print_monitor_lock:
            _print_monitor_stop_requested = False
        return jsonify({'status': 'error', 'error': 'Could not stop the print'}), 502

    return jsonify({'status': 'ok'})


@application.route('/printer/upload', methods=['POST'])
def upload_printer_file():
    if not get_logged_in_user():
        return redirect(safe_url_for('login'))

    uploaded = request.files.get('gcode_file')
    if not uploaded or not uploaded.filename.lower().endswith(('.gcode', '.gco', '.g')):
        flash('Choose a G-code file to upload.', 'error')
        return redirect(safe_url_for('printer_settings'))

    sd_name = helper.upload_printer_sd_file(uploaded.filename, uploaded.read())
    if sd_name:
        flash(f'Uploading as {sd_name}.', 'success')
    else:
        flash('Could not start the upload. The printer may be busy, offline, or the 8.3 filename may already exist.', 'error')
    return redirect(safe_url_for('printer_settings'))


@application.route('/printer/upload/status', methods=['GET'])
def printer_file_upload_status():
    if not get_logged_in_user():
        return jsonify({'status': 'error', 'error': 'Not logged in'}), 401
    status = helper.printer_upload_status()
    return jsonify(status) if status else (jsonify({'state': 'unavailable'}), 502)


@application.route('/printer/print/status', methods=['GET'])
def printer_current_print_status():
    if not get_logged_in_user():
        return jsonify({'status': 'error', 'error': 'Not logged in'}), 401
    return jsonify(helper.printer_print_status())


@application.route('/manage_users', methods=['GET'])
def manage_users():
    token = request.cookies.get('token')
    if not token:
        return redirect(safe_url_for('login'))

    user = database.get_user(connection=g.db, token=token)
    if not user:
        return redirect(safe_url_for('login'))
    elif user["authorized"] < 2:
        flash("You are not authorized to authorize users.")
        return redirect(safe_url_for('index'))

    unauthorized_users = database.get_user(connection=g.db, authorized=0)

    authorized_users = (
        database.get_user(connection=g.db, authorized=1)
        + database.get_user(connection=g.db, authorized=2)
    )

    # ✅ Sort: put current user last, others alphabetical by email
    authorized_users_sorted = sorted(
        authorized_users,
        key=lambda u: (u["email"].lower() == user["email"].lower(), u["email"].lower())
    )

    # ✅ Sort unauthorized users too, if you want
    unauthorized_users_sorted = sorted(
        unauthorized_users,
        key=lambda u: u["email"].lower()
    )

    return render_template(
        'manage_users.html',
        unauthorized_users=unauthorized_users_sorted,
        authorized_users=authorized_users_sorted,
        user=user,
        title=settings.APP_TITLE,
        url_for=safe_url_for
    )


@application.route('/manage_users', methods=['POST'])
def manage_users_post():
    token = request.cookies.get('token')
    if not token:
        return redirect(safe_url_for('login'))

    user = database.get_user(connection=g.db, token=token)
    if not user or user["authorized"] < 2:
        flash("You are not authorized to perform this action.")
        return redirect(safe_url_for('index'))

    email = request.form.get('email')
    action = request.form.get('action')
    make_admin = request.form.get('make_admin')

    if not email or not action:
        return redirect(safe_url_for('manage_users'))

    if action == 'authorize':
        level = 2 if make_admin else 1
        database.update_user(connection=g.db, email=email, authorized=level)

    elif action == 'remove':
        database.delete_user(connection=g.db, email=email)

    elif action == 'make_admin':
        database.update_user(connection=g.db, email=email, authorized=2)


    database.sync_temp_db_to_disk(connection=g.db)

    return redirect(safe_url_for('manage_users'))


@application.route('/approve_user', methods=['GET'])
def approve_user():
    email = request.args.get('email')
    token = request.args.get('token')

    if not email or not token:
        return "Invalid request.", 400

    user = database.get_user(connection=g.db, email=email)
    if not user:
        return "User not found.", 404

    # Validate token matches
    if user.get('token') != token:
        return "Invalid or expired approval token.", 403

    # Approve and clear token — optional
    database.update_user(connection=g.db, email=email, authorized=1, token=None)

    body = (f"Your application for {settings.APP_TITLE} has been approved.\n "
            f"You may now access {request.host_url}")

    helper.send_email(
        recipient=email,
        subject=f"{settings.APP_TITLE} registration approved",
        body=body
    )

    database.sync_temp_db_to_disk(connection=g.db)

    return f"✅ User {email} has been approved and email sent as confirmation! They can now log in."


if __name__ == "__main__":
    database.setup_initial_db()
    application.run(debug=True, use_reloader=True, port=8000)
