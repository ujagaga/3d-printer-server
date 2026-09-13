import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, settings=types.ModuleType('settings'),
                    serial=types.ModuleType('serial')):
        spec.loader.exec_module(module)
    return module


helper = load('cache_helper', 'helper.py')
bridge = load('cache_bridge', 'printer_service.py')
LISTING = ['Begin file list', 'TEST.GCO 123', 'End file list', 'ok']


class CacheTests(unittest.TestCase):
    def setUp(self):
        bridge.print_started_at = None
        bridge.active_sd_file = None
        bridge.selected_sd_file = None
        bridge.power_off_when_done = False
        bridge.print_seen_running = False
        bridge.power_off_pending = False

    def test_auto_power_off_after_completion_only_once(self):
        bridge.remember_print_file('M24', ['ok'])
        self.assertEqual(self.request('?power-off-when-done 1').strip(), 'true')
        bridge.remember_print_file('M27', ['Not SD printing', 'ok'])
        bridge.remember_print_file('M27', ['Not SD printing', 'ok'])
        with patch.object(bridge.printer, 'command', return_value=['Not SD printing', 'ok']), patch.object(bridge, 'power_off_printer', return_value=True) as off:
            self.request('?power-off-if-done')
            self.request('?power-off-if-done')
            off.assert_called_once()

    def test_auto_power_off_ignores_abort_disconnect_and_new_print(self):
        for ending in ('M524', 'offline', 'new_print', 'disabled'):
            with self.subTest(ending=ending):
                self.setUp()
                bridge.remember_print_file('M24', ['ok'])
                self.request('?power-off-when-done 1')
                if ending == 'M524':
                    bridge.remember_print_file('M524', ['ok'])
                elif ending == 'offline':
                    bridge.remember_print_file('M27', ['offline'])
                else:
                    bridge.remember_print_file('M27', ['Not SD printing', 'ok'])
                    if ending == 'new_print':
                        bridge.remember_print_file('M24', ['ok'])
                    else:
                        self.request('?power-off-when-done 0')
                with patch.object(bridge, 'power_off_printer') as off:
                    self.request('?power-off-if-done')
                    off.assert_not_called()

    def test_auto_power_off_rechecks_printer_and_uses_configured_default(self):
        with patch.object(bridge.settings, 'POWER_OFF_WHEN_DONE', True, create=True):
            bridge.remember_print_file('M24', ['ok'])
        self.assertTrue(bridge.power_off_when_done)
        bridge.remember_print_file('M27', ['Not SD printing', 'ok'])
        with patch.object(bridge.printer, 'command', return_value=['TF printing byte 1/100', 'ok']), patch.object(bridge, 'power_off_printer') as off:
            self.request('?power-off-if-done')
            off.assert_not_called()

    def test_start_time_is_preserved_until_print_ends(self):
        with patch.object(bridge.time, 'time', return_value=1000):
            bridge.remember_print_file('M24', ['error: failed', 'ok'])
            self.assertIsNone(bridge.print_started_at)
            bridge.remember_print_file('M24', ['ok'])
        with patch.object(bridge.time, 'time', return_value=1100):
            bridge.remember_print_file('M24', ['ok'])
        self.assertEqual(json.loads(self.request('?print-started-at')), 1000)
        bridge.remember_print_file('M27', ['Not SD printing', 'ok'])
        self.assertIsNone(bridge.print_started_at)

    def test_eta_from_elapsed_time_and_progress(self):
        for current, started, expected in [(25, '1000', 300), (0, '1000', None), (100, '1000', 0), (25, 'null', None)]:
            with self.subTest(current=current, started=started), patch.object(helper.time, 'time', return_value=1100), patch.object(helper, 'printer_request', side_effect=[[f'TF printing byte {current}/100', 'ok'], [started]]):
                self.assertEqual(helper.printer_print_status()['remaining_seconds'], expected)

    def test_active_file_tracks_successful_start_and_clears_on_finish(self):
        bridge.selected_sd_file = bridge.active_sd_file = None
        bridge.remember_print_file('M23 TEST.GCO', ['ok'])
        self.assertIsNone(bridge.active_sd_file)
        bridge.remember_print_file('M24', ['error: failed', 'ok'])
        self.assertIsNone(bridge.active_sd_file)
        bridge.remember_print_file('M24', ['ok'])
        self.assertEqual(json.loads(self.request('?active-sd-file')), 'TEST.GCO')
        bridge.remember_print_file('M27', ['TF printing byte 1/100', 'ok'])
        self.assertEqual(bridge.active_sd_file, 'TEST.GCO')
        bridge.remember_print_file('M27', ['Not SD printing', 'ok'])
        self.assertIsNone(bridge.active_sd_file)

    def test_failed_selection_and_abort(self):
        bridge.remember_print_file('M23 TEST.GCO', ['ok'])
        bridge.remember_print_file('M23 MISSING.GCO', ['open failed', 'ok'])
        bridge.remember_print_file('M24', ['ok'])
        self.assertIsNone(bridge.active_sd_file)
        bridge.remember_print_file('M23 TEST.GCO', ['ok'])
        bridge.remember_print_file('M24', ['ok'])
        bridge.remember_print_file('M524', ['ok'])
        self.assertIsNone(bridge.active_sd_file)

    def test_start_rejected_while_printing(self):
        with patch.object(helper, 'printer_print_status', return_value={'state': 'printing'}), patch.object(helper, 'printer_request') as request:
            self.assertFalse(helper.start_printer_sd_file('TEST.GCO'))
            request.assert_not_called()

    def request(self, command):
        handler = object.__new__(bridge.RequestHandler)
        handler.rfile = io.BytesIO((command + '\n').encode())
        handler.wfile = io.BytesIO()
        handler.handle()
        return handler.wfile.getvalue().decode()

    def test_bridge_keeps_complete_listing_through_failed_refresh_and_upload(self):
        bridge.sd_files_cache = None
        with patch.object(bridge.printer, 'command', return_value=LISTING):
            self.request('M20')
        with patch.object(bridge.printer, 'command', return_value=['ok']):
            self.request('M20')
        with patch.dict(bridge.upload_status, state='uploading'), patch.object(bridge.printer, 'command') as command:
            self.assertEqual(json.loads(self.request('?sd-files-cache')), LISTING)
            command.assert_not_called()
        with patch.object(bridge.printer, 'command', return_value=['Begin file list', 'End file list']):
            self.request('M20')
        self.assertEqual(json.loads(self.request('?sd-files-cache')), ['Begin file list', 'End file list'])

    def test_busy_display_uses_cache_without_listing_command(self):
        for state, uploading in [('printing', False), ('offline', False), ('idle', True)]:
            with self.subTest(state=state, uploading=uploading), patch.object(helper, 'printer_print_status', return_value={'state': state}), patch.object(helper, 'printer_request', return_value=[json.dumps(LISTING)]) as request:
                self.assertEqual(helper.printer_sd_files_for_display(uploading), ([{'name': 'TEST.GCO', 'size': 123}], True))
                request.assert_called_once_with('?sd-files-cache')

    def test_idle_refresh_and_failed_refresh_fallback(self):
        for reply, cached in [(LISTING, False), (['busy'], True)]:
            with patch.object(helper, 'printer_print_status', return_value={'state': 'idle'}), patch.object(helper, 'printer_request', side_effect=[reply, json.dumps(LISTING).splitlines()]):
                self.assertEqual(helper.printer_sd_files_for_display()[1], cached)

    def test_missing_and_empty_cache_are_distinct(self):
        with patch.object(helper, 'printer_request', return_value=['null']):
            self.assertEqual(helper.printer_sd_files_for_display(True), (None, False))
        self.assertEqual(helper.parse_printer_sd_files(['Begin file list', '', 'End file list']), [])
        self.assertIsNone(helper.parse_printer_sd_files(['Begin file list', 'TEST.GCO 123']))


if __name__ == '__main__':
    unittest.main()
