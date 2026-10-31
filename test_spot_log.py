import csv
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from protocol import Spot
from spot_log import HEADER, SpotLog
from app import App


class SpotLogTests(unittest.TestCase):
    def test_records_fields_and_drains_on_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spots.csv'
            log = SpotLog(path)
            self.assertTrue(log.publish(Spot('LZ3ZZ', '14074.0', 'K1ABC', '1200Z', 'comma, quote " and text', '29-Oct-2026', dx_country='United States', dx_locator='FN42'), ('node.example', 7300, 'LZ3ZZ')))
            log.stop(); log.thread.join(3)
            self.assertFalse(log.thread.is_alive())
            self.assertIsNone(log.error)
            self.assertFalse(log.publish(Spot('S', '1', 'D', '0000Z', ''), None))
            with path.open(encoding='utf-8', newline='') as f: rows = list(csv.reader(f))
            self.assertEqual(rows[0], list(HEADER))
            self.assertEqual(rows[1][1:10], ['node.example', '7300', 'LZ3ZZ', '29-Oct-2026', '1200Z', '14074.0', 'K1ABC', 'LZ3ZZ', 'comma, quote " and text'])
            self.assertEqual(dict(zip(rows[0], rows[1]))['extra_fields_json'], '[]')
            self.assertEqual(dict(zip(rows[0], rows[1]))['dx_country'], 'United States')
            self.assertEqual(dict(zip(rows[0], rows[1]))['dx_locator'], 'FN42')

    def test_large_secondary_spot_record_can_be_viewed_and_resumed(self):
        from spot_archive import search_archive
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv';log=SpotLog(path)
            long_comment='X'*10000
            self.assertTrue(log.publish(Spot('S','14074','K1ABC','1200Z',long_comment),('secondary',7300,'LZ3ZZ')))
            log.stop();log.thread.join(3);self.assertIsNone(log.error)
            count,matched,rows=search_archive(path)
            self.assertEqual((count,matched),(1,1));self.assertEqual(rows[0]['comment'],long_comment)
            log=SpotLog(path,append=True);log.stop();log.thread.join(3);self.assertIsNone(log.error)

    def test_oversized_record_stops_without_making_archive_unreadable(self):
        from spot_archive import search_archive
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv';log=SpotLog(path)
            self.assertFalse(log.publish(Spot('S','14074','K1ABC','1200Z','X'*65537),None))
            log.thread.join(3);self.assertIn('exceeds the supported size',log.error)
            self.assertEqual(search_archive(path)[:2],(0,0))
            resumed=SpotLog(path,append=True);resumed.stop();resumed.thread.join(3)

    def test_record_count_rotation_preserves_existing_files_and_all_rows(self):
        from spot_archive import search_archive
        with tempfile.TemporaryDirectory() as directory, patch('spot_log.MAX_RECORDS',2):
            path=Path(directory)/'spots.csv';occupied=Path(directory)/'spots.part-0002.csv';occupied.write_text('keep me',encoding='utf-8')
            log=SpotLog(path)
            for n in range(5):self.assertTrue(log.publish(Spot('S','14074',f'K{n}ABC','1200Z','CQ'),None))
            log.stop();log.thread.join(3);self.assertIsNone(log.error)
            parts=[path,Path(directory)/'spots.part-0003.csv',Path(directory)/'spots.part-0004.csv']
            self.assertEqual(occupied.read_text(encoding='utf-8'),'keep me')
            self.assertEqual([search_archive(part)[0] for part in parts],[2,2,1])
            self.assertEqual([row['dx_call'] for part in parts for row in search_archive(part)[2]],['K0ABC','K1ABC','K2ABC','K3ABC','K4ABC'])
            self.assertEqual(log.current_path,str(parts[-1]))

    def test_byte_rotation_and_resume_of_full_segment(self):
        from spot_archive import search_archive
        with tempfile.TemporaryDirectory() as directory, patch('spot_log.MAX_FILE_BYTES',600), patch('spot_log.MAX_RECORDS',2):
            path=Path(directory)/'spots.csv';log=SpotLog(path)
            for n in range(4):self.assertTrue(log.publish(Spot('S','14074',f'K{n}ABC','1200Z','é'*60),None))
            log.stop();log.thread.join(3);self.assertIsNone(log.error)
            parts=sorted(Path(directory).glob('*.csv'))
            self.assertGreater(len(parts),1)
            self.assertTrue(all(part.stat().st_size<=600 for part in parts))
            self.assertEqual(sum(search_archive(part)[0] for part in parts),4)
            full=parts[-1]
            resumed=SpotLog(full,append=True);self.assertTrue(resumed.publish(Spot('S','7000','W1AW','1201Z','é'*60),None));resumed.stop();resumed.thread.join(3)
            self.assertIsNone(resumed.error);self.assertNotEqual(resumed.current_path,str(full))
            self.assertEqual(search_archive(resumed.current_path)[2][0]['dx_call'],'W1AW')

    def test_desktop_reports_recording_continuation(self):
        with tempfile.TemporaryDirectory() as directory, patch('spot_log.MAX_RECORDS',1):
            path=Path(directory)/'spots.csv';app=App();app.withdraw();app._save_settings=lambda:None
            try:
                with patch('app.filedialog.asksaveasfilename',return_value=str(path)):app.start_spot_log()
                app.add_spot(Spot('S','14074','K1ABC','1200Z','one'))
                app.add_spot(Spot('S','14075','W1AW','1201Z','two'))
                deadline=time.monotonic()+3
                while app.spot_log.current_path==str(path) and time.monotonic()<deadline:time.sleep(.01)
                app.poll_spot_log();self.assertIn('part-0002.csv',app.spot_log_status.get())
                self.assertIn('continued in',app.transcript.get('1.0','end'))
                app.stop_spot_log();app.spot_log.thread.join(3);app.poll_spot_log()
            finally:app.quit_app()

    def test_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spots.csv'; path.write_text('keep me')
            with self.assertRaises(FileExistsError): SpotLog(path)
            self.assertEqual(path.read_text(), 'keep me')

    def test_resume_appends_valid_recording_and_rejects_bad_file_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv';log=SpotLog(path)
            log.publish(Spot('S','14074','K1ABC','1200Z','first'),None);log.stop();log.thread.join(3)
            before=path.read_bytes();log=SpotLog(path,append=True)
            log.publish(Spot('S','7000','W1AW','1201Z','second'),None);log.stop();log.thread.join(3)
            self.assertIsNone(log.error); self.assertTrue(path.read_bytes().startswith(before))
            with path.open(encoding='utf-8',newline='') as f:rows=list(csv.reader(f))
            self.assertEqual(len(rows),3);self.assertEqual(rows[2][7],'W1AW')
            for bad in (b'wrong,header\r\n', before[:-2], before+b'bad,row\r\n'):
                path.write_bytes(bad)
                with self.assertRaises((ValueError,csv.Error,UnicodeError)):SpotLog(path,append=True)
                self.assertEqual(path.read_bytes(),bad)

    def test_desktop_resumes_and_records_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv';log=SpotLog(path);log.stop();log.thread.join(3)
            app=App();app.withdraw();app._save_settings=lambda:None
            try:
                with patch('app.filedialog.askopenfilename',return_value=str(path)):app.resume_spot_log()
                self.assertEqual(str(app.log_resume['state']),'disabled')
                app.add_spot(Spot('S','14074','D','0000Z','resumed'));app.stop_spot_log()
                deadline=time.monotonic()+3
                while app.spot_log and time.monotonic()<deadline:app.poll_spot_log();time.sleep(.01)
                with path.open(encoding='utf-8',newline='') as f:rows=list(csv.reader(f))
                self.assertEqual(len(rows),2);self.assertEqual(rows[1][9],'resumed')
                self.assertEqual(str(app.log_resume['state']),'normal')
            finally:app.quit_app()

    def test_queue_overflow_stops_and_reports_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            log = SpotLog(Path(directory) / 'spots.csv')
            with patch.object(log.queue, 'put_nowait', side_effect=__import__('queue').Full):
                self.assertFalse(log.publish(Spot('S', '1', 'D', '0000Z', ''), None))
            log.thread.join(3)
            self.assertFalse(log.thread.is_alive())
            self.assertIn('some spots were not recorded', log.error)

    def test_write_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            log = SpotLog(Path(directory) / 'spots.csv')
            log.writer = Mock()
            log.writer.writerow.side_effect = OSError('disk failure')
            log.publish(Spot('S', '1', 'D', '0000Z', ''), None)
            log.thread.join(3)
            self.assertFalse(log.thread.is_alive())
            self.assertIn('disk failure', log.error)

    def test_desktop_records_received_spot_and_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spots.csv'
            app = App(); app.withdraw(); app._save_settings = lambda: None
            try:
                with patch('app.filedialog.asksaveasfilename', return_value=str(path)):
                    app.start_spot_log()
                app.spot_source = ('example.invalid', 7300, 'LZ3ZZ')
                app.add_spot(Spot('S', '14074', 'D', '0000Z', 'hello'))
                app.stop_spot_log()
                deadline = time.monotonic() + 3
                while app.spot_log and time.monotonic() < deadline:
                    app.poll_spot_log(); time.sleep(.01)
                self.assertIsNone(app.spot_log)
                self.assertEqual(app.spot_log_status.get(), 'Recording stopped')
                with path.open(encoding='utf-8', newline='') as f: rows = list(csv.reader(f))
                self.assertEqual(rows[1][1:4], ['example.invalid', '7300', 'LZ3ZZ'])
            finally: app.quit_app()


if __name__ == '__main__': unittest.main()
