import csv
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

from app import App
from protocol import Spot
from spot_archive import load_archive, search_archive, search_recordings, search_ve7cc_archive, date_range, record_in_date_range, spot_from_archive
from spot_log import HEADER, SpotLog


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class ArchiveTests(unittest.TestCase):
    def test_closing_viewer_cancels_pending_search_callbacks(self):
        started=threading.Event();release=threading.Event();cancellations=[]
        def search(path,cancelled,predicate):
            cancellations.append(cancelled);started.set();release.wait(3);return None
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            before=set(app.tk.call('after','info'))
            with patch('app.search_archive',side_effect=search),patch('app.filedialog.askopenfilename',return_value='example.csv'):
                app.open_spot_archive();self.assertTrue(started.wait(2))
                dialog=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel))
                entry=next(w for w in descendants(dialog) if type(w) is ttk.Entry);entry.insert(0,'K1')
                pending=set(app.tk.call('after','info'))-before
                self.assertGreaterEqual(len(pending),2)
                dialog.destroy()
                self.assertTrue(cancellations[0].is_set())
                self.assertFalse(pending & set(app.tk.call('after','info')))
                release.set();app.update()
        finally:release.set();app.quit_app()

    def test_saved_spot_conversion_validates_and_preserves_data(self):
        record={'dx_call':'K1ABC','spotter':'W3ABC','frequency_khz':'14074.0','time_utc':'1200',
                'date':'30-Oct-2026','comment':'100%2C','dx_country':'United States',
                'extra_fields_json':'["extra"]','metadata_fields_json':'["one", "two"]'}
        spot=spot_from_archive(record)
        self.assertEqual((spot.dx,spot.time,spot.comment,spot.dx_country,spot.extra_fields),('K1ABC','1200Z','100%2C','United States',('extra',)))
        for key,value in [('time_utc','2500'),('frequency_khz','bad'),('dx_call','bad call'),('date','31-Feb-2026'),('extra_fields_json','[4]')]:
            with self.subTest(key=key),self.assertRaises(ValueError):spot_from_archive({**record,key:value})

    def test_loading_saved_spots_does_not_emit_alarms_or_record_again(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv';log=SpotLog(path)
            log.publish(Spot('W3ABC','14074','K1ABC','1200Z','saved',dx_cq='5'),('node',7300,'LZ3ZZ'))
            log.stop();log.thread.join(3)
            window=App();window.withdraw();window._save_settings=lambda:None
            class Recorder:
                def __init__(self):self.calls=[]
                def publish(self,*args):self.calls.append(args)
            recorder=Recorder()
            try:
                window.alarm_enabled.set(True);window.alarm_calls.set('*');window.apply_alarm()
                with patch('app.filedialog.askopenfilename',return_value=str(path)):window.open_spot_archive()
                dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                button=next(w for w in descendants(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Load shown into spots…')
                deadline=time.monotonic()+4
                while str(button.cget('state'))=='disabled' and time.monotonic()<deadline:window.update();time.sleep(.01)
                window.spot_log=recorder
                with patch('app.messagebox.askyesno',return_value=True):button.invoke()
                self.assertEqual(len(window.rows),1);ident,spot=window.rows[0]
                self.assertEqual((spot.dx,spot.dx_cq),('K1ABC','5'))
                self.assertEqual(window.spot_times[ident].year,1)
                self.assertEqual(window.spot_sources[ident],('archive',0,str(path)))
                self.assertEqual(recorder.calls,[]);self.assertEqual(len(window.alarm_rows),0)
                self.assertIn('[saved]',window.spots.item(ident,'values')[3])
                window.spots.selection_set(ident);window.show_spot_detail()
                self.assertIn('Imported from recording',window.spot_detail.get('1.0','end'))
            finally:window.spot_log=None;window.quit_app()

    def test_invalid_saved_record_is_rejected_before_any_are_added(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv'
            with path.open('w',encoding='utf-8',newline='') as file:
                writer=csv.writer(file);writer.writerow(HEADER[:10])
                writer.writerow(['','','','','30-Oct-2026','1200','14074','K1ABC','W3ABC','good'])
                writer.writerow(['','','','','30-Oct-2026','2500','14074','K2ABC','W3ABC','bad time'])
            window=App();window.withdraw();window._save_settings=lambda:None
            try:
                with patch('app.filedialog.askopenfilename',return_value=str(path)):window.open_spot_archive()
                dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                button=next(w for w in descendants(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Load shown into spots…')
                deadline=time.monotonic()+4
                while str(button.cget('state'))=='disabled' and time.monotonic()<deadline:window.update();time.sleep(.01)
                with patch('app.messagebox.showerror') as error,patch('app.messagebox.askyesno') as confirm:
                    button.invoke();error.assert_called_once();confirm.assert_not_called()
                self.assertEqual(len(window.rows),0)
            finally:window.quit_app()

    def test_import_caps_list_and_does_not_relay_saved_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv'
            with path.open('w',encoding='utf-8',newline='') as file:
                writer=csv.writer(file);writer.writerow(HEADER[:10])
                for index in range(1000):writer.writerow(['','','','','30-Oct-2026','1200','14074',f'K{index}ABC','W3ABC','saved'])
            window=App();window.withdraw();window._save_settings=lambda:None
            try:
                window.add_spot(Spot('W3ABC','14074','LIVE1','1200Z','live'))
                with patch('app.filedialog.askopenfilename',return_value=str(path)):window.open_spot_archive()
                dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                button=next(w for w in descendants(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Load shown into spots…')
                deadline=time.monotonic()+6
                while str(button.cget('state'))=='disabled' and time.monotonic()<deadline:window.update();time.sleep(.01)
                with patch('app.messagebox.askyesno',return_value=True) as confirm:
                    button.invoke();confirm.assert_called_once()
                self.assertEqual(len(window.rows),1000)
                self.assertNotIn('LIVE1',[spot.dx for _,spot in window.rows])
                self.assertEqual(len(window.spot_times),1000);self.assertEqual(len(window.spot_sources),1000)
                self.assertTrue(all(source[:2]==('archive',0) for source in window.spot_sources.values()))
            finally:window.quit_app()
    def test_searches_selected_parts_in_order_and_keeps_filenames(self):
        with tempfile.TemporaryDirectory() as directory:
            first=Path(directory)/'spots.csv';second=Path(directory)/'spots.part-0002.csv'
            for path,call in ((first,'K1ABC'),(second,'W1AW')):
                log=SpotLog(path);log.publish(Spot('S','14074',call,'1200Z','CQ'),None);log.stop();log.thread.join(3)
            total,matched,rows=search_recordings([first,second])
            self.assertEqual((total,matched),(2,2));self.assertEqual([r['dx_call'] for r in rows],['K1ABC','W1AW'])
            self.assertEqual([r['recording_file'] for r in rows],[str(first),str(second)])
            total,matched,rows=search_recordings([second,first],predicate=lambda row:row['dx_call']=='K1ABC')
            self.assertEqual((total,matched,rows[0]['recording_file']),(2,1,str(first)))
            cancelled=threading.Event();cancelled.set();self.assertIsNone(search_recordings([first,second],cancelled))
            with self.assertRaises(ValueError):search_recordings([])
            with self.assertRaises(ValueError):search_recordings([first]*51)

    def test_view_parts_displays_combined_records_without_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            first=Path(directory)/'spots.csv';second=Path(directory)/'spots.part-0002.csv'
            for path,call in ((first,'K1ABC'),(second,'W1AW')):
                log=SpotLog(path);log.publish(Spot('S','14074',call,'1200Z','CQ'),None);log.stop();log.thread.join(3)
            app=App();app.withdraw();app._save_settings=lambda:None
            try:
                with patch('app.filedialog.askopenfilenames',return_value=(str(first),str(second))):app.open_spot_archive_parts()
                dialog=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel))
                tree=next(w for w in descendants(dialog) if isinstance(w,ttk.Treeview));deadline=time.monotonic()+3
                while len(tree.get_children())<2 and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual([tree.item(i,'values')[3] for i in tree.get_children()],['W1AW','K1ABC'])
                tree.selection_set(tree.get_children()[0]);app.update()
                text=next(w for w in descendants(dialog) if isinstance(w,tk.Text))
                self.assertIn(str(second),text.get('1.0','end'));self.assertEqual(len(app.rows),0)
                dialog.destroy()
            finally:app.quit_app()

    def test_reads_recording_and_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv'; log=SpotLog(path)
            log.publish(Spot('S','14074','K1ABC','1200Z','text, with comma',dx_cq='5'),('node',7300,'LZ3ZZ'))
            log.stop();log.thread.join(3)
            count,rows=load_archive(path)
            self.assertEqual(count,1);self.assertEqual(rows[0]['comment'],'text, with comma');self.assertEqual(rows[0]['dx_cq'],'5')

    def test_old_header_and_last_thousand_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'old.csv'
            with path.open('w',encoding='utf-8',newline='') as f:
                writer=csv.writer(f);writer.writerow(HEADER[:10])
                for i in range(1002):writer.writerow(['','','','','','',str(i),'K1ABC','S',''])
            count,rows=load_archive(path)
            self.assertEqual(count,1002);self.assertEqual(len(rows),1000)
            self.assertEqual((rows[0]['frequency_khz'],rows[-1]['frequency_khz']),('2','1001'))

    def test_search_scans_beyond_the_initial_thousand(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv'
            with path.open('w',encoding='utf-8',newline='') as f:
                writer=csv.writer(f);writer.writerow(HEADER[:10])
                for i in range(1002): writer.writerow(['','','','','','', '14074','RARECALL' if i==0 else 'W1AW','S',''])
            total,matched,rows=search_archive(path,predicate=lambda row:row['dx_call']=='RARECALL')
            self.assertEqual((total,matched,len(rows)),(1002,1,1));self.assertEqual(rows[0]['dx_call'],'RARECALL')

    def test_inclusive_date_search_and_missing_dates(self):
        first,last=date_range('2026-10-01','2026-10-31')
        for date,expected in [('01-Oct-2026',True),('31-Oct-2026',True),('30-Sep-2026',False),('',False),('invalid',False)]:
            with self.subTest(date=date): self.assertEqual(record_in_date_range({'date':date},first,last),expected)
        self.assertTrue(record_in_date_range({},*date_range('','')))
        for start,end in [('2026-1-01',''),('2026-02-30',''),('2026-11-01','2026-10-01')]:
            with self.subTest(start=start,end=end),self.assertRaises(ValueError): date_range(start,end)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv'
            with path.open('w',encoding='utf-8',newline='') as f:
                writer=csv.writer(f);writer.writerow(HEADER[:10])
                writer.writerow(['','','','','01-Oct-2026','', '14074','OLD','S',''])
                for i in range(1001):writer.writerow(['','','','','01-Nov-2026','', '7001','NEW','S',''])
            total,matched,rows=search_archive(path,predicate=lambda row:record_in_date_range(row,first,last))
            self.assertEqual((total,matched,rows[0]['dx_call']),(1002,1,'OLD'))

    def test_viewer_filters_unclassified_frequencies(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv'
            with path.open('w',encoding='utf-8',newline='') as f:
                writer=csv.writer(f);writer.writerow(HEADER[:10])
                writer.writerow(['','','','','','', '14074','K1ABC','S',''])
                writer.writerow(['','','','','','', '1000','K2ABC','S',''])
            app=App();app.withdraw();app._save_settings=lambda:None
            try:
                with patch('app.filedialog.askopenfilename',return_value=str(path)):app.open_spot_archive()
                dialog=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel));widgets=list(descendants(dialog))
                tree=next(w for w in widgets if isinstance(w,ttk.Treeview));selector=next(w for w in widgets if isinstance(w,ttk.Combobox))
                self.assertIn('Other',selector.cget('values'));selector.set('Other');deadline=time.monotonic()+5
                while (not tree.get_children() or tree.item(tree.get_children()[0],'values')[3]!='K2ABC') and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual([tree.item(row,'values')[3] for row in tree.get_children()],['K2ABC'])
            finally:app.quit_app()

    def test_viewer_searches_all_records_and_filters_band(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv'
            with path.open('w',encoding='utf-8',newline='') as f:
                writer=csv.writer(f);writer.writerow(HEADER[:10])
                for i in range(1002):writer.writerow(['','','','','','', '14074' if i==0 else '7001','RARECALL' if i==0 else 'W1AW','S',''])
            app=App();app.withdraw();app._save_settings=lambda:None
            try:
                with patch('app.filedialog.askopenfilename',return_value=str(path)):app.open_spot_archive()
                dialog=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel));widgets=list(descendants(dialog))
                tree=next(w for w in widgets if isinstance(w,ttk.Treeview));entry=next(w for w in widgets if type(w) is ttk.Entry)
                entry.insert(0,'RARECALL');deadline=time.monotonic()+5
                while (not tree.get_children() or tree.item(tree.get_children()[0],'values')[3]!='RARECALL') and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(len(tree.get_children()),1);self.assertEqual(tree.item(tree.get_children()[0],'values')[3],'RARECALL')
                selector=next(w for w in widgets if isinstance(w,ttk.Combobox));selector.set('40 m');deadline=time.monotonic()+5
                while tree.get_children() and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(tree.get_children(),());self.assertEqual(len(app.rows),0)
                dialog.destroy()
            finally:app.quit_app()

    def test_original_log_fixture_preserves_ambiguous_escapes(self):
        root=Path(__file__).resolve().parent/'reference'
        total,matched,rows=search_ve7cc_archive(root/'ve7cc-controlled-edge-log.txt')
        self.assertEqual((total,matched),(4,4))
        self.assertEqual(rows[0]['comment'],'COMMA%2C VALUE')
        self.assertEqual(rows[1]['comment'],'QUOTE %22WORD%22')
        self.assertEqual(rows[2]['comment'],'CAFE É')
        self.assertEqual((rows[0]['dx_itu'],rows[0]['spotter_itu'],rows[0]['dx_cq']),(' 8','28',' 5'))
        total,matched,rows=search_ve7cc_archive(root/'ve7cc-controlled-percent-log.txt')
        self.assertEqual(rows[0]['comment'],'PERCENT %2C AND %22')
        self.assertFalse(record_in_date_range(rows[0],*date_range('2026-10-01','')))
        with tempfile.TemporaryDirectory() as directory:
            invalid=Path(directory)/'bad.txt';invalid.write_text('not,a,spot\n')
            with self.assertRaises(ValueError):search_ve7cc_archive(invalid)

    def test_original_log_viewer_displays_without_replaying(self):
        path=Path(__file__).resolve().parent/'reference'/'ve7cc-controlled-edge-log.txt'
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.filedialog.askopenfilename',return_value=str(path)):app.open_spot_archive()
            dialog=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel))
            tree=next(w for w in descendants(dialog) if isinstance(w,ttk.Treeview))
            deadline=time.monotonic()+5
            while len(tree.get_children())!=4 and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertEqual(len(tree.get_children()),4)
            self.assertEqual(tree.item(tree.get_children()[-1],'values')[5], 'COMMA%2C VALUE')
            self.assertEqual(len(app.rows),0);self.assertEqual(len(app.alarm_rows),0)
            dialog.destroy()
        finally:app.quit_app()

    def test_viewer_date_range_filters_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv'
            with path.open('w',encoding='utf-8',newline='') as f:
                writer=csv.writer(f);writer.writerow(HEADER[:10])
                writer.writerow(['','','','','01-Oct-2026','1200Z','14074','OCT','S',''])
                writer.writerow(['','','','','01-Nov-2026','1200Z','14074','NOV','S',''])
            app=App();app.withdraw();app._save_settings=lambda:None
            try:
                with patch('app.filedialog.askopenfilename',return_value=str(path)):app.open_spot_archive()
                dialog=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel)); widgets=list(descendants(dialog))
                tree=next(w for w in widgets if isinstance(w,ttk.Treeview));entries=[w for w in widgets if type(w) is ttk.Entry]
                entries[1].insert(0,'2026-10-01');entries[2].insert(0,'2026-10-31')
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    app.update()
                    if len(tree.get_children())==1 and tree.item(tree.get_children()[0],'values')[3]=='OCT':break
                    time.sleep(.01)
                self.assertEqual(len(tree.get_children()),1)
                self.assertEqual(tree.item(tree.get_children()[0],'values')[3],'OCT')
                self.assertEqual(tree.item(tree.get_children()[0],'values')[1:3],('12:00Z','14074.00'))
                entries[0].insert(0,'14074.00');deadline=time.monotonic()+1
                while time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(len(tree.get_children()),1)
                dialog.destroy()
            finally:app.quit_app()

    def test_viewer_does_not_show_or_load_stale_results_for_invalid_date(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv'
            with path.open('w',encoding='utf-8',newline='') as f:
                writer=csv.writer(f);writer.writerow(HEADER[:10]);writer.writerow(['','','','','01-Oct-2026','1200Z','14074','K1ABC','W1AW','CQ'])
            app=App();app.withdraw();app._save_settings=lambda:True
            try:
                with patch('app.filedialog.askopenfilename',return_value=str(path)):app.open_spot_archive()
                dialog=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel));widgets=list(descendants(dialog))
                tree=next(w for w in widgets if isinstance(w,ttk.Treeview));entries=[w for w in widgets if type(w) is ttk.Entry]
                load=next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Load shown into spots…')
                deadline=time.monotonic()+5
                while not tree.get_children() and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(len(tree.get_children()),1);self.assertNotIn('disabled',load.state())
                entries[1].insert(0,'invalid');app.update()
                self.assertFalse(tree.get_children());self.assertIn('disabled',load.state())
                deadline=time.monotonic()+1
                while time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertFalse(tree.get_children());self.assertIn('disabled',load.state())
                entries[1].delete(0,'end');entries[1].insert(0,'2026-10-01');deadline=time.monotonic()+5
                while not tree.get_children() and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(len(tree.get_children()),1);self.assertNotIn('disabled',load.state())
                dialog.destroy()
            finally:app.quit_app()

    def test_rejects_invalid_and_oversized_input_and_respects_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bad.csv';path.write_text('not,a,recording\n')
            with self.assertRaises(ValueError):load_archive(path)
            path.write_text(','.join(HEADER[:10])+'\n'+'a,'*9+'x'*65537+'\n')
            with self.assertRaises(ValueError):load_archive(path)
            path.write_text(','.join(HEADER[:10])+'\n'+','.join(['x']*10)+'\n')
            cancelled=threading.Event();cancelled.set();self.assertIsNone(load_archive(path,cancelled))

    def test_viewer_does_not_replay_into_live_spots(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spots.csv';log=SpotLog(path)
            log.publish(Spot('S','14074','K1ABC','1200Z','hello'),('node',7300,'LZ3ZZ'));log.stop();log.thread.join(3)
            app=App();app.withdraw();app._save_settings=lambda:None
            try:
                with patch('app.filedialog.askopenfilename',return_value=str(path)): app.open_spot_archive()
                dialog=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel))
                tree=next(w for w in descendants(dialog) if isinstance(w,ttk.Treeview))
                deadline=time.monotonic()+3
                while not tree.get_children() and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(len(tree.get_children()),1);self.assertEqual(len(app.rows),0);self.assertEqual(len(app.alarm_rows),0)
                self.assertEqual(tree.item(tree.get_children()[0],'values')[3],'K1ABC')
                dialog.destroy()
            finally:app.quit_app()

    def test_recording_controls_visible_at_minimum_size(self):
        app=App();app._save_settings=lambda:None
        try:
            app.geometry('780x560');app.notebook.select(7);app.update()
            for label in ('View recording…', 'View parts…'):
                button=next(w for w in descendants(app) if isinstance(w,ttk.Button) and w.cget('text')==label)
                self.assertTrue(button.winfo_ismapped())
                self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),app.command.winfo_rooty())
                self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),app.winfo_rootx()+app.winfo_width())
        finally:app.quit_app()


if __name__ == '__main__': unittest.main()
