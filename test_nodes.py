import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

import app
from nodes import load_nodes, load_startup_nodes, validate_node, validate_login_call


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class NodeTests(unittest.TestCase):
    def test_login_calls_are_validated_before_normalization(self):
        self.assertEqual(validate_login_call(' lz3zz-1 '),'LZ3ZZ-1')
        self.assertEqual(validate_login_call('ea8/lz3zz'),'EA8/LZ3ZZ')
        for value in ('---','/K1ABC','ß1','K1 ABC','', 'A'*33):
            with self.subTest(value=value),self.assertRaises(ValueError):validate_login_call(value)
        self.assertEqual(load_startup_nodes([{'host':'node','port':7300,'call':'ß1'}]),[])

    def test_invalid_main_call_does_not_start_connection_or_change_saved_value(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw()
            try:
                window.call.set('ß1')
                self.assertEqual(window._settings_snapshot()['call'],'ß1')
                with patch('app.messagebox.showerror') as error,patch('app.Connection') as connection:
                    window.connect();error.assert_called_once();connection.assert_not_called()
                with self.assertRaises(ValueError):window.start_secondary_node('node',7300,'---')
            finally:window.quit_app()

    def test_validation_and_loading(self):
        self.assertEqual(validate_node(' My node ', ' example.org ', '7300'), {'name': 'My node', 'host': 'example.org', 'port': 7300})
        for args in [('', 'x', 7300), ('x', 'bad host', 7300), ('x', 'x', 0), ('x', 'x', 65536), ('x', 'x', 1.5), ('x', 'x', True)]:
            with self.subTest(args=args):
                with self.assertRaises(ValueError): validate_node(*args)
        self.assertEqual(load_nodes([]), [])
        self.assertEqual(len(load_nodes(None)), 2)
        self.assertEqual(len(load_nodes([{'name':'A','host':'x','port':1},{'name':'a','host':'y','port':2},{}])), 1)

    def test_dialog_selects_and_edits_without_connecting(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'SETTINGS', Path(directory) / 'settings.json'):
            window=app.App(); window.withdraw()
            try:
                window.open_nodes(); dialog = next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
                widgets=list(descendants(dialog)); tree=next(w for w in widgets if isinstance(w, ttk.Treeview))
                buttons={w.cget('text'):w for w in widgets if isinstance(w, ttk.Button)}
                entries=[w for w in widgets if isinstance(w, ttk.Entry)]
                tree.selection_set('1'); tree.event_generate('<<TreeviewSelect>>'); dialog.update()
                self.assertEqual(entries[1].get(), 'dxs.n2wq.com')
                entries[0].delete(0,'end'); entries[0].insert(0,'My node')
                entries[1].delete(0,'end'); entries[1].insert(0,'example.invalid')
                buttons['Save node'].invoke()
                self.assertEqual(len(json.loads(app.SETTINGS.read_text())['nodes']),3)
                tree.selection_set('2'); buttons['Use selected'].invoke()
                self.assertEqual((window.host.get(), window.port.get()), ('example.invalid','7300'))
                self.assertIsNone(window.connection)
            finally: window.quit_app()

    def test_selection_does_not_change_an_active_session(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'SETTINGS', Path(directory) / 'settings.json'):
            window=app.App(); window.withdraw(); window._save_settings=lambda:True
            try:
                window.open_nodes(); dialog=next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
                widgets=list(descendants(dialog)); tree=next(w for w in widgets if isinstance(w, ttk.Treeview))
                button=next(w for w in widgets if isinstance(w, ttk.Button) and w.cget('text') == 'Use selected')
                tree.selection_set('0'); window.want_connection=True; before=(window.host.get(), window.port.get())
                with patch('app.messagebox.showerror') as error: button.invoke(); error.assert_called_once()
                self.assertEqual((window.host.get(),window.port.get()),before)
                window.want_connection=False; dialog.destroy()
            finally: window.quit_app()


if __name__ == '__main__': unittest.main()
