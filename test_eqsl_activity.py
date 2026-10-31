import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import eqsl_activity
from protocol import Spot


DATA=(b'List of AG members as of 30-Oct-2026 11:15 UTC - Dated\r\n'
      b'lz3zz, 2026-01-02\r\nLZ3ZZ, 2024-01-01\r\nOA/LZ3ZZ, 0000-00-00\r\n'
      b'DG3PJ,p, 0000-00-00\r\n2E\xd8GIO, 0000-00-00\r\n')


class EQSLTests(unittest.TestCase):
    def test_exact_matching_dates_and_unsupported_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'eqsl.txt';path.write_bytes(DATA)
            result=eqsl_activity.read_activity(path)
            self.assertEqual(result.calls['LZ3ZZ'],'2026-01-02')
            self.assertEqual(result.calls['OA/LZ3ZZ'],'0000-00-00')
            self.assertNotIn('LZ3ZZ/P',result.calls)
            self.assertEqual(result.skipped,2);self.assertEqual(result.list_time,'2026-10-30 11:15 UTC')
            path.write_bytes(DATA.replace(b'30-Oct-2026',b'29-fEB-2024'))
            self.assertEqual(eqsl_activity.read_activity(path).list_time,'2024-02-29 11:15 UTC')
            for bad in (b'',b'<html>error</html>',DATA+b'K1ABC, 2026-02-30\n',DATA+b'bad line\n'):
                path.write_bytes(bad)
                with self.subTest(bad=bad[-20:]),self.assertRaises(ValueError):eqsl_activity.read_activity(path)

    def test_download_replaces_only_after_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'eqsl.txt';path.write_bytes(DATA)
            with patch.object(eqsl_activity,'urlopen',return_value=io.BytesIO(b'bad')):
                with self.assertRaises(ValueError):eqsl_activity.download_activity(path)
            self.assertEqual(path.read_bytes(),DATA);self.assertEqual(list(Path(directory).iterdir()),[path])
            with patch.object(eqsl_activity,'urlopen',return_value=io.BytesIO(DATA)):
                self.assertIn('LZ3ZZ',eqsl_activity.download_activity(path).calls)

    def test_ui_marker_detail_restoration_and_failed_update(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            path=Path(directory)/'eqsl.txt';path.write_bytes(DATA)
            window=app.App();window.withdraw()
            def wait():
                deadline=time.monotonic()+5
                while window.eqsl_loading and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertFalse(window.eqsl_loading)
            try:
                window.start_eqsl_load(str(path),True);wait()
                window.add_spot(Spot('OA1ABC','14074','LZ3ZZ','1200Z','CQ'));window.render_spots()
                row=window.spots.get_children()[0]
                self.assertEqual(window.spots.item(row,'values')[3],'LZ3ZZ *')
                window.spots.selection_set(row);window.show_spot_detail();detail=window.spot_detail.get('1.0','end')
                self.assertIn('eQSL AG: last upload/import 2026-01-02',detail)
                self.assertIn('not a QSO confirmation',detail)
                window.lotw_index={'LZ3ZZ':'2026-01-02T03:04:05'};window.render_spots()
                self.assertEqual(window.spots.item(row,'values')[3],'LZ3ZZ +*')
                invalid=Path(directory)/'bad.txt';invalid.write_text('invalid')
                window.start_eqsl_load(str(invalid),True);wait()
                self.assertIn('previous data remains active',window.eqsl_status.get())
                self.assertEqual(window.eqsl_file,str(path))
            finally:window.quit_app()
            restored=app.App();restored.withdraw();restored._save_settings=lambda:None
            try:
                window=restored;wait();self.assertIn('LZ3ZZ',restored.eqsl_index.calls)
            finally:restored.quit_app()


if __name__=='__main__':unittest.main()
