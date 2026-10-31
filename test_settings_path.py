import json
import tempfile
import unittest
import subprocess
import sys
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import app
from settings_store import save_settings, SettingsConflict, settings_lock


class SettingsPathTests(unittest.TestCase):
    def test_frozen_mac_settings_use_application_support(self):
        with patch.object(app.sys, 'platform', 'darwin'):
            self.assertEqual(app.settings_path(frozen=True), Path.home() / 'Library' / 'Application Support' / 'ClusterDesk' / 'settings.json')

    def test_settings_warning_keeps_core_controls_visible_at_minimum_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json';path.write_text('{broken',encoding='utf8')
            with patch.object(app,'SETTINGS',path):
                window=app.App();window.geometry('780x560');window.deiconify();window.update()
                try:
                    for control in (window.connect_btn,window.search_box,window.spots,window.clear_spots_button,window.spot_lock_button,window.command):
                        with self.subTest(control=str(control)):
                            self.assertTrue(control.winfo_ismapped())
                            self.assertLessEqual(control.winfo_rooty()+control.winfo_height(),window.winfo_rooty()+window.winfo_height())
                finally:window.quit_app()

    def test_invalid_settings_are_visible_and_preserved_at_startup(self):
        for name,content in (('broken','{broken'),('wrong root','[]'),('deep nesting','{"nested":'+'['*2000+'0'+']'*2000+'}')):
            with self.subTest(content=name),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'settings.json';path.write_text(content,encoding='utf8')
                with patch.object(app,'SETTINGS',path):
                    window=app.App();window.withdraw()
                    try:
                        self.assertTrue(window.settings_load_problem)
                        self.assertIn('Defaults are in use',window.transcript.get('1.0','end'))
                        def walk(widget):
                            for child in widget.winfo_children():yield child;yield from walk(child)
                        self.assertTrue(any(isinstance(w,ttk.Label) and 'Settings could not be loaded' in str(w.cget('text')) for w in walk(window)))
                        self.assertFalse(window._save_settings());self.assertEqual(path.read_text(encoding='utf8'),content)
                    finally:window.quit_app()
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'missing.json'):
            window=app.App();window.withdraw()
            try:self.assertEqual(window.settings_load_problem,'')
            finally:window.quit_app()

    def test_display_apply_reports_stale_choices_from_another_window(self):
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            initial=app.App();initial.withdraw();initial._save_settings();initial.quit_app()
            first=app.App();first.withdraw();second=app.App();second.withdraw()
            try:
                first.open_display_bands();dialog=next(w for w in first.winfo_children() if isinstance(w,tk.Toplevel));widgets=list(walk(dialog))
                next(w for w in widgets if isinstance(w,ttk.Checkbutton) and w.cget('text')=='20 m').invoke()
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke()
                second.open_display_bands();dialog=next(w for w in second.winfo_children() if isinstance(w,tk.Toplevel));widgets=list(walk(dialog))
                with patch('app.messagebox.showwarning') as warning:
                    next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke();warning.assert_called_once()
                self.assertIn('not saved',second.hidden_bands_note.get())
                self.assertEqual(second.hidden_bands,set())
                self.assertTrue(second._save_settings());self.assertIn('not saved',second.hidden_bands_note.get())
                self.assertEqual(json.loads(app.SETTINGS.read_text(encoding='utf-8'))['hidden_bands'],['20 m'])
            finally:first.quit_app();second.quit_app()

    def test_display_save_failure_remains_visible_until_saved(self):
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw()
            try:
                window.open_display_bands();dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel));widgets=list(walk(dialog))
                next(w for w in widgets if isinstance(w,ttk.Checkbutton) and w.cget('text')=='20 m').invoke()
                with patch('app.save_settings',side_effect=OSError('disk unavailable')),patch('app.messagebox.showwarning') as warning:
                    next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke();warning.assert_called_once()
                self.assertEqual(window.hidden_bands,{'20 m'});self.assertIn('not saved',window.hidden_bands_note.get())
                self.assertTrue(window._save_settings());self.assertNotIn('not saved',window.hidden_bands_note.get())
            finally:window.quit_app()

    def test_new_profile_requires_own_callsign_and_preserves_saved_call(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw()
            try:
                self.assertEqual(window.call.get(),'')
                self.assertFalse(window.auto_start.get())
                window.call.set('K1ABC');self.assertTrue(window._save_settings())
            finally:window.quit_app()
            restored=app.App();restored.withdraw();restored._save_settings=lambda:None
            try:self.assertEqual(restored.call.get(),'K1ABC')
            finally:restored.quit_app()

    def test_settings_lock_blocks_another_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            code=('from settings_store import settings_lock\n'
                  'import sys\n'
                  'try:\n'
                  '  with settings_lock(sys.argv[1],timeout=.2): print("acquired")\n'
                  'except OSError: print("busy")\n')
            with settings_lock(path):
                result=subprocess.run([sys.executable,'-c',code,str(path)],capture_output=True,text=True,timeout=5,
                                      creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.assertEqual(result.returncode,0);self.assertEqual(result.stdout.strip(),'busy')

    def test_cooperating_writers_preserve_independent_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            original={'host':'node','scripts':[],'unknown':'keep'}
            path.write_text(json.dumps(original),encoding='utf8')
            local={'host':'node','scripts':[]}
            def write(change):return save_settings(path,dict(local,**change),local,original)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(write,{'host':'new-node'}),pool.submit(write,{'scripts':[{'name':'home'}]})]
                for future in futures:future.result()
            saved=json.loads(path.read_text(encoding='utf8'))
            self.assertEqual(saved,{'host':'new-node','scripts':[{'name':'home'}],'unknown':'keep'})
            with self.assertRaises(SettingsConflict):save_settings(path,dict(local,host='different'),local,original)
            self.assertEqual(json.loads(path.read_text(encoding='utf8')),saved)

    def test_remote_change_remains_protected_after_unrelated_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json';path.write_text('{}',encoding='utf8')
            with patch.object(app,'SETTINGS',path):
                window=app.App();window.withdraw()
                try:
                    path.write_text(json.dumps({'host':'other-instance'}),encoding='utf8')
                    window.local_grid.set('KN12');self.assertTrue(window._save_settings())
                    self.assertEqual(json.loads(path.read_text(encoding='utf8'))['host'],'other-instance')
                    window.host.set('my-new-host');self.assertFalse(window._save_settings())
                    self.assertEqual(json.loads(path.read_text(encoding='utf8'))['host'],'other-instance')
                    self.assertIn('Another copy changed: host',window.transcript.get('1.0','end'))
                finally:window.quit_app()

    def test_invalid_settings_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json';path.write_text('{broken',encoding='utf8')
            with self.assertRaises(OSError):save_settings(path,{'host':'new'},{'host':'old'}, {})
            self.assertEqual(path.read_text(encoding='utf8'),'{broken')

    def test_deleted_empty_settings_file_is_not_silently_recreated(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json';path.write_text('{}',encoding='utf8')
            with patch.object(app,'SETTINGS',path):
                window=app.App();window.withdraw()
                try:
                    path.unlink();window.host.set('new-node')
                    self.assertFalse(window._save_settings());self.assertFalse(path.exists())
                finally:window.quit_app()

    def test_legacy_category_migration_preserves_other_serial_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            path.write_text(json.dumps({'logger_text_categories':['other'],'serial_text_categories':['reports']}),encoding='utf8')
            with patch.object(app,'SETTINGS',path):
                window=app.App();window.withdraw()
                try:
                    window.logger_text_categories={'talk'};self.assertTrue(window._save_settings())
                    window.serial_text_categories={'wcy'};self.assertTrue(window._save_settings())
                finally:window.quit_app()
                restored=app.App();restored.withdraw();restored._save_settings=lambda:None
                try:
                    self.assertEqual(restored.logger_text_categories,{'talk'})
                    self.assertEqual(restored.serial_text_categories,{'wcy'})
                finally:restored.quit_app()

    def test_frozen_app_uses_local_app_data(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict('os.environ', {'LOCALAPPDATA': directory}):
                self.assertEqual(app.settings_path(True), Path(directory) / 'ClusterDesk' / 'settings.json')

    def test_source_mode_keeps_existing_settings_location(self):
        self.assertEqual(app.settings_path(False), app.ROOT / 'settings.json')

    def test_save_creates_parent_and_loads_saved_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nested' / 'settings.json'
            with patch.object(app, 'SETTINGS', path):
                window = app.App(); window.withdraw()
                try:
                    window.host.set('example.invalid'); window.port.set('1234'); window.call.set('K1ABC'); window.logger_ignore_bye.set(True); window.logger_spots_only.set(True); window.logger_port.set('17300'); window.serial_port.set('COM8'); window.serial_baud.set('19200');window.serial_ignore_bye.set(True);window.serial_spots_only.set(True);window.serial_local_echo.set(True);window.auto_open_talk.set(True)
                    window.logger_text_categories={'wwv'};window.serial_text_categories={'announcements'};window.logger_spot_bands={'20 m','Other'};window.serial_spot_bands={'40 m'};window._save_settings()
                    self.assertTrue(path.exists())
                finally: window.quit_app()
                second = app.App(); second.withdraw()
                try:
                    self.assertEqual((second.host.get(), second.port.get(), second.call.get()), ('example.invalid', '1234', 'K1ABC'))
                    self.assertTrue(second.logger_ignore_bye.get())
                    self.assertTrue(second.logger_spots_only.get())
                    self.assertEqual(second.logger_text_categories,{'wwv'})
                    self.assertEqual(second.serial_text_categories,{'announcements'})
                    self.assertEqual(second.logger_spot_bands,{'20 m','Other'});self.assertEqual(second.serial_spot_bands,{'40 m'})
                    self.assertEqual(second.logger_port.get(),'17300')
                    self.assertEqual((second.serial_port.get(),second.serial_baud.get()),('COM8','19200'))
                    self.assertTrue(second.serial_ignore_bye.get());self.assertTrue(second.serial_spots_only.get());self.assertTrue(second.serial_local_echo.get());self.assertTrue(second.auto_open_talk.get())
                finally: second.quit_app()

    def test_legacy_text_categories_preserve_forwarding_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            path.write_text(json.dumps({'logger_text_categories':['announcements','other'], 'serial_text_categories':['reports']}),encoding='utf8')
            with patch.object(app,'SETTINGS',path):
                window=app.App();window.withdraw();window._save_settings=lambda:None
                try:
                    self.assertEqual(window.logger_text_categories,{'announcements','weather','talk','chat','other'})
                    self.assertEqual(window.serial_text_categories,{'wwv','wcy'})
                finally:window.quit_app()

    def test_invalid_saved_logger_port_uses_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            for value in (0, 65536, 'x', '９９', True, '9'*5000):
                with self.subTest(value=str(value)[:20]):
                    path.write_text(json.dumps({'logger_port':value}),encoding='utf8')
                    with patch.object(app,'SETTINGS',path):
                        window=app.App();window.withdraw();window._save_settings=lambda:None
                        try:self.assertEqual(window.logger_port.get(),'7300')
                        finally:window.quit_app()


if __name__ == '__main__': unittest.main()
