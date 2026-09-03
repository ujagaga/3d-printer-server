#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Persistent owner of the relay board's serial connection.

Flask talks to this service over localhost TCP, allowing the web service to restart
without reopening and resetting the Arduino relay controller.
"""

import json
import logging
import socketserver

import settings
import uart_switch

logger = logging.getLogger(__name__)
controller = uart_switch.detect_controller(settings.UART_SW_PORT, settings.UART_SW_BAUD)


class RequestHandler(socketserver.StreamRequestHandler):
    timeout = 10

    def send_reply(self, payload):
        self.wfile.write(json.dumps(payload).encode('utf-8') + b'\n')

    def handle(self):
        try:
            request = json.loads(self.rfile.readline().decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_reply({'status': 'error', 'error': 'Invalid request'})
            return

        if controller is None:
            self.send_reply({'status': 'error', 'error': 'Relay board unavailable'})
        elif request.get('command') == 'get':
            state = controller.get_state()
            if state is None:
                self.send_reply({'status': 'error', 'error': 'Could not read relay state'})
            else:
                self.send_reply({'status': 'ok', 'state': state})
        elif request.get('command') == 'set':
            state = controller.set_socket(request.get('socket'), request.get('state'))
            if state is None:
                self.send_reply({'status': 'error', 'error': 'Could not set relay state'})
            else:
                self.send_reply({'status': 'ok', 'state': state})
        else:
            self.send_reply({'status': 'error', 'error': 'Unknown command'})


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S'
    )
    relay_tcp_port = getattr(settings, 'RELAY_TCP_PORT', 5032)
    with Server(('127.0.0.1', relay_tcp_port), RequestHandler) as server:
        logger.info(f'Relay service listening on 127.0.0.1:{relay_tcp_port}')
        server.serve_forever()
