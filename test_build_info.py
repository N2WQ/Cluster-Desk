import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

from app import App
from build_info import executable_sha256


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class BuildInfoTests(unittest.TestCase):
    def test_closing_about_cancels_fingerprint_callback(self):
        started=threading.Event();release=threading.Event()
        def slow_hash(_path):started.set();release.wait(3);return 'TEST'
        window=App();window.withdraw();window._save_settings=lambda:True
        try:
            before=set(window.tk.call('after','info'))
            with patch('app.packaged_executable',return_value=Path('test.exe')),patch('app.executable_sha256',side_effect=slow_hash):
                window.open_build_info();self.assertTrue(started.wait(2))
                dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
                pending=set(window.tk.call('after','info'))-before;self.assertEqual(len(pending),1)
                dialog.destroy();self.assertFalse(pending & set(window.tk.call('after','info')))
                release.set();window.update()
        finally:release.set();window.quit_app()

    def test_packaged_fingerprint_is_copyable_and_does_not_include_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'app.exe';path.write_bytes(b'test build')
            self.assertEqual(executable_sha256(path),hashlib.sha256(b'test build').hexdigest().upper())
            window=App();window.withdraw();window._save_settings=lambda:None
            try:
                with patch('app.packaged_executable',return_value=path):window.open_build_info()
                dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel');widgets=list(descendants(dialog))
                entry=next(w for w in widgets if type(w) is ttk.Entry);button=next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Copy fingerprint')
                deadline=time.monotonic()+4
                while 'disabled' in button.state() and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(entry.get(),hashlib.sha256(b'test build').hexdigest().upper())
                with patch.object(window,'clipboard_clear') as clear,patch.object(window,'clipboard_append') as append:
                    button.invoke();clear.assert_called_once();append.assert_called_once_with(entry.get())
            finally:window.quit_app()

    def test_source_mode_has_no_misleading_file_hash(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            with patch('app.packaged_executable',return_value=None):window.open_build_info()
            dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel');widgets=list(descendants(dialog))
            self.assertEqual(next(w for w in widgets if type(w) is ttk.Entry).get(),'Source mode: no packaged executable')
            self.assertIn('disabled',next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Copy fingerprint').state())
        finally:window.quit_app()

    def test_about_button_fits_settings_at_minimum_window_size(self):
        window=App();window._save_settings=lambda:None
        try:
            window.geometry('780x560');window.notebook.select(2);window.update()
            page=window.nametowidget(window.notebook.tabs()[2])
            button=next(w for w in descendants(page) if isinstance(w,ttk.Button) and w.cget('text')=='About this build…')
            self.assertTrue(button.winfo_ismapped())
            self.assertGreater(button.winfo_width(),50)
            self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),window.winfo_rootx()+window.winfo_width())
        finally:window.quit_app()
