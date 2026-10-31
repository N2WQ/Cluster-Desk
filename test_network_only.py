import json
import tempfile
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

import app


class NetworkOnlyTests(unittest.TestCase):
    def test_legacy_tnc_settings_do_not_create_controls_or_get_deleted(self):
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        with tempfile.TemporaryDirectory() as directory:
            settings=Path(directory)/'settings.json'
            original={'tnc':{'port':'COM8'},'tnc_scripts':[{'name':'old','text':'saved'}]}
            settings.write_text(json.dumps(original),encoding='utf8')
            with patch.object(app,'SETTINGS',settings):
                window=app.App()
                try:
                    self.assertFalse(hasattr(window,'open_tnc'))
                    labels=[str(widget.cget('text')) for widget in walk(window)
                            if isinstance(widget,(ttk.Button,ttk.Label))]
                    self.assertFalse(any('tnc' in label.casefold() for label in labels))
                    window.dark_theme.set(not window.dark_theme.get())
                    self.assertTrue(window._save_settings())
                    saved=json.loads(settings.read_text(encoding='utf8'))
                    self.assertEqual(saved['tnc'],original['tnc'])
                    self.assertEqual(saved['tnc_scripts'],original['tnc_scripts'])
                finally:window.quit_app()


if __name__=='__main__':unittest.main()
