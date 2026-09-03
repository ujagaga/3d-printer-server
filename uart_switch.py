import serial
import time
import threading
import json
import socket

CDC_RELAY_BAUD = 115200


class RelayServiceController:
    """Client with the same interface as the serial controllers, backed by the
    persistent localhost relay service."""

    def __init__(self, port, timeout=3):
        self.port = port
        self.timeout = timeout

    def request(self, payload):
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=self.timeout) as sock:
                sock.sendall(json.dumps(payload).encode('utf-8') + b'\n')
                reply = b''
                while not reply.endswith(b'\n'):
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    reply += chunk
            return json.loads(reply.decode('utf-8'))
        except Exception as e:
            print(f"ERROR contacting relay service: {e}")
            return None

    def get_state(self):
        reply = self.request({'command': 'get'})
        return reply.get('state') if reply and reply.get('status') == 'ok' else []

    def set_socket(self, socket_number, state):
        reply = self.request({'command': 'set', 'socket': socket_number, 'state': state})
        return reply.get('state') if reply and reply.get('status') == 'ok' else None


class PowerSocketsController:
    def __init__(self, port, baudrate, timeout=1, settle_time=3):
        self.port = port
        self.baud = baudrate
        self.timeout = timeout
        self.settle_time = settle_time
        self.lock = threading.Lock()  # Thread lock to ensure only one thread uses the UART port
        self.serial = None

        try:
            self.serial = serial.Serial(port, baudrate, timeout=timeout)
            time.sleep(settle_time)
        except Exception as e:
            print(f"ERROR: {e}")

    def reinit(self):
        """Re-initialization in case of failure."""
        if self.serial:
            self.serial.close()
        try:
            self.serial = serial.Serial(self.port, self.baud, timeout=self.timeout)
            time.sleep(self.settle_time)
            return self.get_state()
        except Exception as e:
            print(f"ERROR: {e}")
        return None

    def send_command(self, cmd):
        """Send command to the device and return response."""
        with self.lock:  # Ensure thread-safe access to the serial port
            try:
                self.serial.reset_input_buffer()  # Clear any junk left over
                self.serial.write(cmd.encode('utf-8'))
                response = self.serial.readline().decode('utf-8').strip()

                if response:
                    if not response.startswith('s:'):
                        raise ValueError(f"Unexpected response: {response}")
                    states = response[2:]  # Remove "s:" part
                    if len(states) != 2:
                        raise ValueError(f"Invalid state format: {states}")
                    return [int(state) for state in states]
            except Exception as e:
                print(f"ERROR during command send: {e}")
                self.reinit()  # Attempt to reinitialize on error

        return None

    def get_state(self):
        """Get the current state of the sockets."""
        return self.send_command('g:00')  # Query device for state

    def set_socket(self, socket_number, state):
        """Set individual socket ON (1) or OFF (0)."""
        if socket_number not in (0, 1):
            print("ERROR: Socket number must be 0 or 1")
            return None
        if state not in (0, 1):
            print("ERROR: State must be 0 (OFF) or 1 (ON)")
            return None

        cmd = f"s:{socket_number}{state}"
        return self.send_command(cmd)

    def set_socket_on(self, socket_number):
        return self.set_socket(socket_number, 1)

    def set_socket_off(self, socket_number):
        return self.set_socket(socket_number, 0)

    def close(self):
        """Close the serial connection."""
        with self.lock:  # Ensure thread-safe access to the serial port while closing
            try:
                if self.serial:
                    self.serial.close()
            except Exception as e:
                print(f"ERROR while closing serial: {e}")


class UartRelayController:
    """Controller for the single-relay USB CDC board (hardware/UART_Relay). Protocol is
    plain 'on'/'off' text commands replying 'OK'; the firmware has no state query, so the
    last commanded state is tracked locally."""

    def __init__(self, port, baudrate=CDC_RELAY_BAUD, timeout=1, settle_time=3):
        self.port = port
        self.baud = baudrate
        self.timeout = timeout
        self.settle_time = settle_time
        self.lock = threading.Lock()
        self.serial = None
        self.state = [0]

        try:
            self.serial = serial.Serial(port, baudrate, timeout=timeout)
            time.sleep(settle_time)
        except Exception as e:
            print(f"ERROR: {e}")

    def reinit(self):
        """Re-initialization in case of failure."""
        if self.serial:
            self.serial.close()
        try:
            self.serial = serial.Serial(self.port, self.baud, timeout=self.timeout)
            time.sleep(self.settle_time)
        except Exception as e:
            print(f"ERROR: {e}")

    def send_command(self, cmd):
        """Send command to the device and return True if it replied OK."""
        with self.lock:  # Ensure thread-safe access to the serial port
            try:
                self.serial.reset_input_buffer()  # Clear any junk left over
                self.serial.write(f"{cmd}\r\n".encode('utf-8'))
                response = self.serial.readline().decode('utf-8').strip()
                return response == 'OK'
            except Exception as e:
                print(f"ERROR during command send: {e}")
        return False

    def get_state(self):
        """Return the last known state of the relay (firmware has no state query)."""
        return list(self.state)

    def set_socket(self, socket_number, state):
        """Set the relay ON (1) or OFF (0). Only socket 0 exists on this board."""
        if socket_number != 0:
            print("ERROR: Socket number must be 0")
            return None
        if state not in (0, 1):
            print("ERROR: State must be 0 (OFF) or 1 (ON)")
            return None

        if self.send_command('on' if state else 'off'):
            self.state[0] = state
            return list(self.state)

        self.reinit()  # Attempt to reinitialize on error
        return None

    def close(self):
        """Close the serial connection."""
        with self.lock:  # Ensure thread-safe access to the serial port while closing
            try:
                if self.serial:
                    self.serial.close()
            except Exception as e:
                print(f"ERROR while closing serial: {e}")


def detect_controller(port, legacy_baud, timeout=1, settle_time=3):
    """Probe `port` to find out which relay board is attached and return the matching
    controller, already connected. Returns None if neither protocol responds."""

    try:
        probe = serial.Serial(port, legacy_baud, timeout=timeout)
        time.sleep(settle_time)
        probe.reset_input_buffer()
        probe.write(b'g:00')
        response = probe.readline().decode('utf-8').strip()
        probe.close()
        if response.startswith('s:') and len(response) == 4:
            print("Detected 2-relay UART board")
            return PowerSocketsController(port, legacy_baud, timeout=timeout, settle_time=settle_time)
    except Exception as e:
        print(f"ERROR probing legacy relay board: {e}")

    try:
        probe = serial.Serial(port, CDC_RELAY_BAUD, timeout=timeout)
        time.sleep(settle_time)
        probe.reset_input_buffer()
        probe.write(b'help\r\n')
        response = []
        while True:
            line = probe.readline().decode('utf-8').strip()
            if not line:
                break
            response.append(line)
            if line == 'OK':
                break
        probe.close()
        if response and response[0] == 'USB Relay 1' and response[-1] == 'OK':
            print("Detected 1-relay CDC board")
            return UartRelayController(port, CDC_RELAY_BAUD, timeout=timeout, settle_time=settle_time)
    except Exception as e:
        print(f"ERROR probing CDC relay board: {e}")

    print("No relay board detected")
    return None


# if __name__ == "__main__":
#     controller = PowerSocketsController(port=config.UART_SW_PORT, baudrate=config.UART_SW_BAUD)
#     print("Connected:", controller.serial)
#     if controller.serial:
#         try:
#             print("Current State:", controller.get_state())
#
#             print("Turning ON socket 0...")
#             print(controller.set_socket_on(0))
#
#             print("Turning ON socket 1...")
#             print(controller.set_socket_on(1))
#
#             print("Turning OFF socket 1...")
#             print(controller.set_socket_off(1))
#
#             print("Turning OFF socket 0...")
#             print(controller.set_socket_off(0))
#
#             print("Reinit:")
#             print(controller.reinit())
#
#         finally:
#             controller.close()
