import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import lotw_activity
from protocol import Spot


DATA = b'LZ3ZZ,2026-01-02,03:04:05\nOA/LZ3ZZ,2025-02-03,04:05:06\n'


class LoTWTests(unittest.TestCase):
    def test_parse_exact_calls_and_latest_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'list.csv'
            path.write_bytes(DATA + b'LZ3ZZ,2024-01-01,00:00:00\n')
            index = lotw_activity.read_activity(path)
            self.assertEqual(index['LZ3ZZ'], '2026-01-02T03:04:05')
            self.assertEqual(index['OA/LZ3ZZ'], '2025-02-03T04:05:06')
            self.assertNotIn('LZ3ZZ/P', index)
            for bad in (b'', b'bad', b'LZ3ZZ,2026-02-30,00:00:00', b'LZ3ZZ,2026-01-01,25:00:00', b'LZ3ZZ,2026-01-01,00:00:00\ninvalid'):
                path.write_bytes(bad)
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    lotw_activity.read_activity(path)

    def test_download_only_replaces_cache_after_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'activity.csv'; path.write_bytes(DATA)
            with patch.object(lotw_activity, 'urlopen', return_value=io.BytesIO(b'not a list')):
                with self.assertRaises(ValueError): lotw_activity.download_activity(path)
            self.assertEqual(path.read_bytes(), DATA)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            new = b'K1ABC,2026-10-01,12:00:00\n'
            with patch.object(lotw_activity, 'urlopen', return_value=io.BytesIO(new)):
                index = lotw_activity.download_activity(path)
            self.assertIn('K1ABC', index); self.assertEqual(path.read_bytes(), new)

    def test_load_display_restore_and_failure_preserve_data(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'SETTINGS', Path(directory) / 'settings.json'):
            file = Path(directory) / 'activity.csv'; file.write_bytes(DATA)
            window = app.App(); window.withdraw()
            def wait():
                deadline = time.monotonic() + 5
                while window.lotw_loading and time.monotonic() < deadline:
                    window.update(); time.sleep(.01)
                self.assertFalse(window.lotw_loading)
            try:
                window.start_lotw_load(str(file), True); wait()
                window.add_spot(Spot('OA1ABC', '14074', 'LZ3ZZ', '1200Z', 'CQ'))
                window.render_spots(); row = window.spots.get_children()[0]
                self.assertEqual(window.spots.item(row, 'values')[3], 'LZ3ZZ +')
                window.spots.selection_set(row); window.show_spot_detail()
                self.assertIn('2026-01-02 03:04:05 UTC', window.spot_detail.get('1.0', 'end'))
                self.assertEqual(next(iter(window.rows))[1].dx, 'LZ3ZZ')
                invalid = Path(directory) / 'bad.csv'; invalid.write_text('bad')
                window.start_lotw_load(str(invalid), True); wait()
                self.assertIn('previous data remains active', window.lotw_status.get())
                self.assertEqual(window.lotw_file, str(file))
            finally: window.quit_app()
            restored = app.App(); restored.withdraw(); restored._save_settings=lambda:None
            try:
                window = restored; wait()
                self.assertIn('LZ3ZZ', restored.lotw_index)
            finally: restored.quit_app()


if __name__ == '__main__': unittest.main()
