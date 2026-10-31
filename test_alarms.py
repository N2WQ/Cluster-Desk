import tempfile
import time
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from alarms import compile_rules, matches
from protocol import Spot
from country_lookup import CountryIndex
from test_country_lookup import DATA


class AlarmTests(unittest.TestCase):
    def test_invalid_saved_alarm_is_explained_and_preserved_until_corrected(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            original={'calls':'ß1','enabled':True}
            app.SETTINGS.write_text(json.dumps({'alarm':original}),encoding='utf-8')
            window=app.App();window.withdraw()
            try:
                self.assertIsNone(window.alarm_rules)
                self.assertFalse(window.alarm_enabled.get())
                self.assertIn('review saved alarm',window.alarm_note.get())
                self.assertIn('Saved alarm disabled',window.transcript.get('1.0','end'))
                window.dark_theme.set(True);self.assertTrue(window._save_settings())
                self.assertEqual(json.loads(app.SETTINGS.read_text(encoding='utf-8'))['alarm'],original)
                window.alarm_calls.set('K1*');window.alarm_enabled.set(True);window.apply_alarm()
                self.assertEqual(window.alarm_note.get(),'Enabled.')
                self.assertEqual(json.loads(app.SETTINGS.read_text(encoding='utf-8'))['alarm']['calls'],'K1*')
            finally:window.quit_app()

    def test_alarm_inputs_do_not_convert_non_ascii_to_other_identifiers(self):
        for calls,states,ranges,zones in (('ß1','','',''),('','ß','',''),('','','١٠-٢٠',''),('','','','１')):
            with self.subTest(calls=calls,states=states,ranges=ranges,zones=zones), self.assertRaises(ValueError):
                compile_rules(calls,states,ranges,zones)
        rules=compile_rules('','','',countries='España')
        self.assertEqual(rules.countries,('españa',))
    def test_reapplying_stale_alarm_does_not_claim_other_windows_rule_was_saved(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            initial=app.App();initial.withdraw()
            try:
                initial.alarm_calls.set('K1*');initial.alarm_enabled.set(True);initial.apply_alarm()
            finally:initial.quit_app()
            first=app.App();first.withdraw();second=app.App();second.withdraw()
            try:
                first.alarm_calls.set('W1*');first.apply_alarm()
                with patch('app.messagebox.showwarning') as warning:second.apply_alarm();warning.assert_called_once()
                self.assertIn('Not saved',second.alarm_note.get())
                self.assertEqual(second.alarm_config['calls'],'K1*')
                self.assertTrue(second._save_settings())
                self.assertIn('Not saved',second.alarm_note.get())
                self.assertEqual(json.loads(app.SETTINGS.read_text(encoding='utf-8'))['alarm']['calls'],'W1*')
            finally:first.quit_app();second.quit_app()

    def test_alarm_save_failure_is_visible_until_later_save_succeeds(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw()
            try:
                window.alarm_calls.set('K1*');window.alarm_enabled.set(True)
                with patch('app.save_settings',side_effect=OSError('disk unavailable')),patch('app.messagebox.showwarning') as warning:
                    window.apply_alarm();warning.assert_called_once()
                self.assertIn('Not saved',window.alarm_note.get())
                window.add_spot(Spot('S','14074','K1ABC','1200Z',''))
                self.assertEqual(len(window.alarm_rows),1)
                self.assertIn('Not saved',window.alarm_note.get())
                self.assertTrue(window._save_settings())
                self.assertNotIn('Not saved',window.alarm_note.get())
                self.assertTrue(app.SETTINGS.exists())
            finally:window.quit_app()

    def test_country_prefix_alarm_is_opt_in_and_uses_country_identity(self):
        index=CountryIndex.from_text(DATA)
        old=compile_rules('*LZ','','')
        new=compile_rules('*LZ K1*','','',country_prefix_mode=True)
        self.assertTrue(matches(old,Spot('S','14074','K1LZ','1200Z','')))
        self.assertFalse(matches(old,Spot('S','14074','LZ3ZZ','1200Z','')))
        self.assertTrue(matches(new,Spot('S','14074','LZ3ZZ','1200Z',''),index))
        self.assertTrue(matches(new,Spot('S','14074','LZ0ABC','1200Z',''),index))
        self.assertTrue(matches(new,Spot('S','14074','K1ABC','1200Z',''),None))
        self.assertFalse(matches(new,Spot('S','14074','LZ3ZZ','1200Z',''),None))
        self.assertFalse(matches(new,Spot('S','14074','OA/LZ3ZZ','1200Z',''),index))
        self.assertFalse(matches(new,Spot('S','14074','LZ3ZZ/MM','1200Z',''),index))
        self.assertFalse(matches(compile_rules('*LZBAD','','',country_prefix_mode=True),Spot('S','14074','LZ3ZZ','1200Z',''),index))

    def test_country_prefix_alarm_requires_known_file_and_warns_after_restart(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw()
            try:
                window.alarm_calls.set('*LZ');window.alarm_country_prefix_mode.set(True);window.alarm_enabled.set(True)
                with patch('app.messagebox.showerror') as error:window.apply_alarm();error.assert_called_once()
                self.assertIsNone(window.alarm_rules)
                window.country_index=CountryIndex.from_text(DATA);window.apply_alarm()
                window.add_spot(Spot('S','14074','LZ3ZZ','1200Z',''))
                self.assertEqual(len(window.alarm_rows),1)
            finally:window.quit_app()
            restored=app.App();restored.withdraw()
            try:
                self.assertTrue(restored.alarm_country_prefix_mode.get())
                self.assertIn('Country file required',restored.alarm_note.get())
                restored.country_index=CountryIndex.from_text(DATA);restored.update_alarm_note()
                self.assertEqual(restored.alarm_note.get(),'Enabled.')
                restored.add_spot(Spot('S','14074','LZ3ZZ','1200Z',''))
                self.assertEqual(len(restored.alarm_rows),1)
            finally:restored.quit_app()

    def test_country_prefix_alarm_reactivates_after_country_file_load(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            country_file=Path(directory)/'cty.dat';country_file.write_text(DATA,encoding='utf-8')
            window=app.App();window.withdraw()
            try:
                window.country_file=str(country_file);window.country_index=CountryIndex.from_file(country_file)
                window.alarm_calls.set('*LZ');window.alarm_country_prefix_mode.set(True);window.alarm_enabled.set(True);window.apply_alarm()
            finally:window.quit_app()
            restored=app.App();restored.withdraw()
            try:
                deadline=time.monotonic()+5
                while restored.country_index is None and time.monotonic()<deadline:restored.update();time.sleep(.01)
                self.assertIsNotNone(restored.country_index)
                self.assertEqual(restored.alarm_note.get(),'Enabled.')
                restored.add_spot(Spot('S','14074','LZ3ZZ','1200Z',''))
                self.assertEqual(len(restored.alarm_rows),1)
            finally:restored.quit_app()

    def test_clearing_country_file_disables_only_country_matches_until_reload(self):
        import tkinter as tk
        from tkinter import ttk
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            country_file=Path(directory)/'cty.dat';country_file.write_text(DATA,encoding='utf-8')
            window=app.App();window.withdraw()
            try:
                window.country_file=str(country_file);window.country_index=CountryIndex.from_file(country_file)
                window.alarm_calls.set('*LZ,K1*');window.alarm_country_prefix_mode.set(True);window.alarm_enabled.set(True);window.apply_alarm()
                window.open_country_file();dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                def walk(widget):
                    for child in widget.winfo_children():yield child;yield from walk(child)
                next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Clear').invoke()
                self.assertIn('Country file required',window.alarm_note.get())
                window.add_spot(Spot('S','14074','LZ3ZZ','1200Z',''))
                window.add_spot(Spot('S','14074','K1ABC','1200Z',''))
                self.assertEqual(len(window.alarm_rows),1)
                window.start_country_load(str(country_file),persist=True)
                deadline=time.monotonic()+5
                while window.country_index is None and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertIsNotNone(window.country_index)
                window.add_spot(Spot('S','14074','LZ3ZZ','1200Z',''))
                self.assertEqual(len(window.alarm_rows),2)
                self.assertNotIn('Country file required',window.alarm_note.get())
            finally:window.quit_app()

    def test_alarm_band_picker_keeps_custom_ranges_and_applies_only_on_request(self):
        import tkinter as tk
        from tkinter import ttk
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw()
            def walk(widget):
                for child in widget.winfo_children():yield child;yield from walk(child)
            try:
                window.alarm_ranges.set('14000-14350, 7100-7120')
                window.open_alarm_band_picker();dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                widgets=list(walk(dialog))
                next(w for w in widgets if isinstance(w,ttk.Checkbutton) and w.cget('text')=='20 m').invoke()
                next(w for w in widgets if isinstance(w,ttk.Checkbutton) and w.cget('text')=='70 cm').invoke()
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
                self.assertEqual(window.alarm_ranges.get(),'7100-7120, 420000-450000')
                self.assertIsNone(window.alarm_rules)
                window.alarm_enabled.set(True);window.apply_alarm()
                self.assertTrue(matches(window.alarm_rules,Spot('S','432100','K1ABC','1200Z','')))
                self.assertFalse(matches(window.alarm_rules,Spot('S','14074','K1ABC','1200Z','')))
                window.open_alarm_band_picker();dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                widgets=list(walk(dialog));next(w for w in widgets if isinstance(w,ttk.Checkbutton) and w.cget('text')=='20 m').invoke()
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Cancel').invoke()
                self.assertEqual(window.alarm_ranges.get(),'7100-7120, 420000-450000')
            finally:window.quit_app()

    def test_criteria_and_boundaries(self):
        rules = compile_rules('K1*, W1AW', 'MA, CT', '14000-14350,7000-7300')
        self.assertTrue(matches(rules, Spot('S', '14000', 'K1ABC', '1200Z', '', dx_state='MA')))
        self.assertTrue(matches(rules, Spot('S', '14350', 'W1AW', '1200Z', '', dx_state='CT')))
        self.assertFalse(matches(rules, Spot('S', '14350.1', 'W1AW', '1200Z', '', dx_state='CT')))
        self.assertFalse(matches(rules, Spot('S', '14001', 'W1AW', '1200Z', '', dx_state='')))
        self.assertFalse(matches(rules, Spot('S', '14001', 'N1ABC', '1200Z', '', dx_state='MA')))

    def test_invalid_rules_rejected(self):
        for args in [('', '', ''), ('K1\nBAD!', '', ''), ('', 'INVALID', ''), ('', '', '3-2'), ('', '', 'NaN-4'), ('', '', '0-100')]:
            with self.subTest(args=args):
                with self.assertRaises(ValueError): compile_rules(*args)

    def test_cq_zone_matches_spotted_station_and_requires_known_zone(self):
        rules=compile_rules('K1*', '', '14000-14350', '5, 14')
        self.assertTrue(matches(rules, Spot('S', '14074', 'K1ABC', '1200Z', '', dx_cq='05')))
        self.assertFalse(matches(rules, Spot('S', '14074', 'K1ABC', '1200Z', '', dx_cq='')))
        self.assertFalse(matches(rules, Spot('S', '14074', 'K1ABC', '1200Z', '', dx_cq='3')))
        for invalid in ('0', '41', '5.5', 'x'):
            with self.subTest(value=invalid), self.assertRaises(ValueError): compile_rules('', '', '', invalid)

    def test_country_values_match_node_label_or_id_exactly(self):
        rules=compile_rules('', '', '', '', ' k, 168, United States ')
        self.assertTrue(matches(rules, Spot('S','14074','K1ABC','1200Z','', dx_country='K')))
        self.assertTrue(matches(rules, Spot('S','14074','TI5VK','1200Z','', dx_dxcc='168', dx_country='Alajuela-TI')))
        self.assertTrue(matches(rules, Spot('S','14074','K1ABC','1200Z','', dx_country='united states')))
        self.assertFalse(matches(rules, Spot('S','14074','K1ABC','1200Z','', dx_country='KA')))
        self.assertFalse(matches(rules, Spot('S','14074','K1ABC','1200Z','')))
        with self.assertRaises(ValueError): compile_rules('', '', '', '', 'K\nVE')

    def test_alarm_applied_saved_restored_and_view_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            with patch.object(app, 'SETTINGS', path):
                window = app.App(); window.withdraw()
                try:
                    window.alarm_calls.set('K1*'); window.alarm_ranges.set('14000-14350'); window.alarm_cq.set('5'); window.alarm_countries.set('K')
                    window.alarm_enabled.set(True); window.apply_alarm()
                    self.assertTrue(path.exists())
                    for i in range(202): window.add_spot(Spot('S', '14074', 'K1ABC', '1200Z', str(i), dx_cq='5', dx_country='K'))
                    self.assertEqual(len(window.alarm_tree.get_children()), 200)
                    self.assertEqual(window.alarm_tree.item(window.alarm_tree.get_children()[0], 'values')[-1], '201')
                    window.clear_alarms(); self.assertEqual(len(window.alarm_tree.get_children()), 0)
                finally: window.quit_app()
                second = app.App(); second.withdraw()
                try:
                    self.assertTrue(second.alarm_enabled.get())
                    self.assertEqual(second.alarm_cq.get(), '5')
                    self.assertEqual(second.alarm_countries.get(), 'K')
                    second.add_spot(Spot('S', '14074', 'K1ABC', '1200Z', 'restored', dx_cq='5', dx_country='K'))
                    self.assertEqual(len(second.alarm_tree.get_children()), 1)
                    with patch('app.messagebox.showerror') as error:
                        second.alarm_ranges.set('bad'); second.apply_alarm()
                        error.assert_called_once()
                    second.add_spot(Spot('S', '14074', 'K1ABC', '1200Z', 'still active', dx_cq='5', dx_country='K'))
                    self.assertEqual(len(second.alarm_tree.get_children()), 2)
                finally: second.quit_app()

    def test_match_details_preserve_source_and_show_original_spot(self):
        window=app.App();window.withdraw();window._save_settings=lambda:None
        try:
            window.alarm_calls.set('K1*');window.alarm_enabled.set(True);window.apply_alarm()
            source=('node.example',7301,'LZ3ZZ-1')
            spot=Spot('S','14074','K1ABC','1200','CQ test',dx_country='United States',dx_cq='5')
            window.add_spot(spot,source=source);window.render_spots()
            row=window.alarm_tree.get_children()[0];window.alarm_tree.selection_set(row)
            self.assertEqual(window.alarm_tree.item(row,'values')[0:3],('12:00Z','K1ABC','14074.00'))
            window.open_alarm_detail();dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
            text=next(w for w in dialog.winfo_children()[0].winfo_children() if w.winfo_class()=='Text').get('1.0','end')
            self.assertIn('node.example:7301 as LZ3ZZ-1',text);self.assertIn('United States',text);self.assertIn('CQ test',text)
            self.assertIn('14074.00 kHz',text);self.assertIn('12:00Z',text)
            window.show_alarm_spot();self.assertEqual(window.spots.selection(),('spot-1',));self.assertEqual(window.notebook.index('current'),0)
            window.search.set('no match')
            with patch('app.messagebox.showinfo') as notice:window.show_alarm_spot();notice.assert_called_once()
            window.clear_spots();self.assertEqual(len(window.alarm_details),1)
            window.clear_alarms();self.assertEqual(window.alarm_details,{})
        finally:window.quit_app()

    def test_clearing_selected_band_preserves_alarm_details(self):
        window=app.App();window.withdraw();window._save_settings=lambda:None
        try:
            window.alarm_calls.set('K1*');window.alarm_enabled.set(True);window.apply_alarm()
            window.add_spot(Spot('S','14074','K1ABC','1200Z','CQ'),source=('node',7300,'LZ3ZZ'))
            window.render_spots();row=window.alarm_tree.get_children()[0];window.alarm_tree.selection_set(row)
            window.selected_bands={'20 m'};window.clear_selected_band_spots()
            self.assertFalse(window.rows);self.assertEqual(len(window.alarm_details),1)
            with patch('app.messagebox.showinfo') as notice:window.show_alarm_spot()
            self.assertIn('no longer in the spot list',notice.call_args.args[1])
            window.open_alarm_detail()
            dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
            text=next(w for w in dialog.winfo_children()[0].winfo_children() if w.winfo_class()=='Text').get('1.0','end')
            self.assertIn('K1ABC',text);self.assertIn('node:7300 as LZ3ZZ',text)
        finally:window.quit_app()

    def test_alarm_detail_storage_evicts_with_rows(self):
        window=app.App();window.withdraw();window._save_settings=lambda:None
        try:
            window.alarm_calls.set('K1*');window.alarm_enabled.set(True);window.apply_alarm()
            for i in range(201):window.add_spot(Spot('S','14074','K1ABC','1200Z',str(i)),source=('n',7300,'LZ3ZZ'))
            self.assertEqual(len(window.alarm_rows),200);self.assertEqual(len(window.alarm_details),200)
            self.assertEqual(set(window.alarm_details),set(window.alarm_rows))
            self.assertNotIn('spot-1',[item[0] for item in window.alarm_details.values()])
        finally:window.quit_app()

    def test_sound_is_rate_limited(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'SETTINGS', Path(directory) / 'settings.json'):
            window=app.App(); window.withdraw()
            try:
                window.alarm_calls.set('K1*'); window.alarm_enabled.set(True); window.alarm_sound.set(True); window.apply_alarm()
                with patch.object(window, 'bell') as bell, patch('app.time.monotonic', side_effect=[10, 11, 15]):
                    for _ in range(3): window.add_spot(Spot('S', '14074', 'K1ABC', '1200Z', ''))
                self.assertEqual(bell.call_count, 2)
            finally: window.quit_app()

    def test_auto_open_is_opt_in_applied_and_restored(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'SETTINGS', Path(directory) / 'settings.json'):
            window=app.App();window.withdraw()
            try:
                window.alarm_calls.set('K1*');window.alarm_enabled.set(True);window.apply_alarm()
                window.notebook.select(0);window.add_spot(Spot('S','14074','K1ABC','1200Z',''))
                self.assertEqual(window.notebook.index('current'),0)
                window.alarm_auto_open.set(True);window.apply_alarm()
                window.add_spot(Spot('S','14074','W1AW','1200Z',''))
                self.assertEqual(window.notebook.index('current'),0)
                window.add_spot(Spot('S','14074','K1ABC','1200Z',''))
                self.assertEqual(window.notebook.index('current'),8)
            finally:window.quit_app()
            restored=app.App();restored.withdraw()
            try:
                self.assertTrue(restored.alarm_auto_open.get());restored.notebook.select(0)
                restored.add_spot(Spot('S','14074','K1ABC','1200Z',''))
                self.assertEqual(restored.notebook.index('current'),8)
            finally:restored.quit_app()

    def test_alarm_view_remains_usable_at_minimum_window_size(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'SETTINGS', Path(directory) / 'settings.json'):
            window=app.App()
            try:
                window.geometry('780x560'); window.update(); window.notebook.select(8); window.update()
                self.assertTrue(window.alarm_tree.winfo_ismapped())
                self.assertGreater(window.alarm_tree.winfo_height(), 50)
                self.assertTrue(window.command.winfo_ismapped())
                self.assertLessEqual(window.alarm_tree.winfo_rooty() + window.alarm_tree.winfo_height(), window.command.winfo_rooty())
            finally: window.quit_app()


if __name__ == '__main__': unittest.main()
