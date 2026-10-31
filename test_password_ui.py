import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

import app


class FakeConnection:
    def __init__(self): self.passwords=[]; self.closed=False
    def provide_password(self, value): self.passwords.append(value)
    def close(self): self.closed=True


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class PasswordUITests(unittest.TestCase):
    def test_submit_is_in_memory_only_and_cancel_disconnects(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'SETTINGS', Path(directory) / 'settings.json'):
            window=app.App(); window.withdraw()
            try:
                fake=FakeConnection(); window.connection=fake; window.connection_target=('node.example',7300,'LZ3ZZ')
                window.ask_password(); dialog=window.password_dialog
                widgets=list(descendants(dialog)); entry=next(w for w in widgets if isinstance(w, ttk.Entry))
                self.assertEqual(entry.cget('show'),'•')
                entry.insert(0,'test-secret'); next(w for w in widgets if isinstance(w, ttk.Button) and w.cget('text')=='Sign in').invoke()
                self.assertEqual(fake.passwords,['test-secret']); self.assertIsNone(window.password_dialog)
                window._save_settings(); self.assertNotIn('test-secret',json.dumps(json.loads(app.SETTINGS.read_text())))
                window.ask_password(); dialog=window.password_dialog; widgets=list(descendants(dialog))
                next(w for w in widgets if isinstance(w, ttk.Button) and w.cget('text')=='Cancel').invoke()
                self.assertTrue(fake.closed); self.assertIsNone(window.connection)
            finally: window.quit_app()


if __name__ == '__main__': unittest.main()
