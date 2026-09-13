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
    def __init__(self, fail=False):
        self.pending = bytearray()
        self.lines = []
        self.fail = fail

    def reset_input_buffer(self):
        self.pending.clear()

    @property
    def in_waiting(self):
        return min(3, len(self.pending))  # Split acknowledgments across reads.

    def write(self, line):
        self.lines.append(line)
        if self.fail and line.startswith(b'G1'):
            self.pending.extend(b'Error: SD write failed\nok\n')
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
        self.assertEqual(serial.lines[-1], b'M29\n')
        self.assertNotIn(b'G1 X2\n', serial.lines)


if __name__ == '__main__':
    unittest.main()
