import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'upload_bridge', Path(__file__).resolve().parents[1] / 'printer_service.py')
bridge = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, serial=types.ModuleType('serial'),
                settings=types.ModuleType('settings')):
    spec.loader.exec_module(bridge)


class Serial:
    def __init__(self, fail=False, write_errors=0, unmounted=False):
        self.pending = bytearray()
        self.unmounted = unmounted
        self.lines = []
        self.fail = fail
        self.write_errors = write_errors

    def reset_input_buffer(self):
        self.pending.clear()

    @property
    def in_waiting(self):
        return min(3, len(self.pending))  # Split acknowledgments across reads.

    def write(self, line):
        self.lines.append(line)
        if line.startswith(b'M28') and not self.unmounted:
            self.pending.extend(b'Writing to file: TEST.GCO\nok\n')
        elif self.fail and line.startswith(b'G1'):
            self.pending.extend(b'Error: SD write failed\nok\n')
        elif self.write_errors and line.startswith(b'G1'):
            self.write_errors -= 1
            self.pending.extend(b'Error:error writing to file\nok\n')
        else:
            self.pending.extend(b'echo: accepted\r\nok\n')

    def read(self, count):
        result = self.pending[:count]
        del self.pending[:count]
        return bytes(result)


class UploadTests(unittest.TestCase):
    def upload(self, serial):
        printer = bridge.Printer()
        printer.serial = serial
        progress = {'written': 0}
        data = b';comment\r\nG1 X1 ; inline\n\nG1 X2\n'
        with tempfile.NamedTemporaryFile() as source:
            source.write(data)
            source.flush()
            printer.upload('TEST.GCO', source.name, progress)
        self.assertEqual(progress['written'], len(data))

    def test_fragmented_replies_and_comments(self):
        serial = Serial()
        self.upload(serial)
        self.assertEqual(serial.lines,
                         [b'M28 TEST.GCO\n', b'G1 X1\n', b'G1 X2\n', b'M29\n'])

    def test_write_error_closes_file_and_stops_upload(self):
        serial = Serial(fail=True)
        with self.assertRaisesRegex(RuntimeError, 'SD write failed'):
            self.upload(serial)
        self.assertEqual(serial.lines[-3:], [b'M29\n', b'M21\n', b'M30 TEST.GCO\n'])
        self.assertNotIn(b'G1 X2\n', serial.lines)

    def test_intermittent_write_error_restarts_upload(self):
        serial = Serial(write_errors=1)
        self.upload(serial)
        self.assertEqual(serial.lines,
                         [b'M28 TEST.GCO\n', b'G1 X1\n', b'M29\n', b'M21\n', b'M30 TEST.GCO\n',
                          b'M28 TEST.GCO\n', b'G1 X1\n', b'G1 X2\n', b'M29\n'])

    def test_repeated_write_errors_give_up(self):
        serial = Serial(write_errors=bridge.UPLOAD_ATTEMPTS)
        with self.assertRaisesRegex(RuntimeError, 'error writing to file'):
            self.upload(serial)
        self.assertEqual(serial.lines.count(b'M28 TEST.GCO\n'), bridge.UPLOAD_ATTEMPTS)

    def test_unmounted_card_sends_no_gcode(self):
        serial = Serial(unmounted=True)
        with self.assertRaisesRegex(RuntimeError, 'did not open'):
            self.upload(serial)
        self.assertFalse(any(line.startswith(b'G1') for line in serial.lines))


if __name__ == '__main__':
    unittest.main()
