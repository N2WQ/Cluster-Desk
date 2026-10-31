import json
import tempfile
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

import app
from nodes import load_startup_nodes


class AutoStartTests(unittest.TestCase):
    def test_skipped_saved_nodes_are_reported_without_overwriting_them(self):
        import tkinter as tk
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            saved=[{'host':'node.invalid','port':0,'call':'LZ3ZZ'}, {'host':'node.invalid','port':7300,'call':'---'}]
            app.SETTINGS.write_text(json.dumps({'startup_secondary_nodes':saved}),encoding='utf-8')
            window=app.App();window.withdraw()
            def walk(widget):
                for child in widget.winfo_children():yield child;yield from walk(child)
            try:
                self.assertEqual(window.startup_secondary_nodes,[])
                self.assertIn('2 saved additional node(s) were not loaded',window.transcript.get('1.0','end'))
                window.geometry('780x560');window.deiconify();window.update()
                window.open_secondary_nodes();dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                self.assertTrue(any(isinstance(w,ttk.Label) and '2 saved node(s) were not loaded' in w.cget('text') for w in walk(dialog)))
                dialog.update()
                self.assertEqual(int(window.secondary_tree.cget('height')),3)
                self.assertLessEqual(dialog.winfo_height(),560)
                for label in ('Connect','Disconnect / remove','Close'):
                    button=next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')==label)
                    self.assertTrue(button.winfo_ismapped())
                    self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),dialog.winfo_rooty()+dialog.winfo_height())
                self.assertTrue(window._save_settings())
                self.assertEqual(json.loads(app.SETTINGS.read_text(encoding='utf-8'))['startup_secondary_nodes'],saved)
            finally:window.quit_app()

    def test_invalid_saved_call_does_not_start_or_change_on_save(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            app.SETTINGS.write_text(json.dumps({'host':'example.invalid','port':7300,'call':'ß1','auto_start':True}),encoding='utf-8')
            with patch('app.Connection') as connection,patch('app.messagebox.showerror') as error:
                window=app.App();window.withdraw()
                try:
                    window.update()
                    error.assert_called_once();connection.assert_not_called()
                    self.assertFalse(window.want_connection)
                    window._save_settings()
                    self.assertEqual(json.loads(app.SETTINGS.read_text(encoding='utf-8'))['call'],'ß1')
                finally:window.quit_app()

    def test_invalid_or_duplicate_saved_nodes_are_not_started(self):
        entries=[{'host':'example.invalid','port':7300,'call':'lz3zz','auto_reconnect':True},
                 {'host':'EXAMPLE.invalid','port':'7300','call':'LZ3ZZ'},
                 {'host':'invalid host','port':7300,'call':'LZ3ZZ'}]
        self.assertEqual(load_startup_nodes(entries),[{'host':'example.invalid','port':7300,'call':'LZ3ZZ','auto_reconnect':True}])
        self.assertEqual(load_startup_nodes({'host':'bad'}),[])

    def test_manage_startup_removes_only_selected_entry_and_keeps_live_connection(self):
        class Connection:
            def __init__(self,emit): self.closed=False
            def start(self,*args): pass
            def close(self): self.closed=True
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'SETTINGS',Path(directory)/'settings.json'), patch.object(app,'Connection',Connection):
            window=app.App();window.withdraw()
            try:
                ident=window.start_secondary_node('one.invalid',7300,'LZ3ZZ')
                connection=window.secondary_nodes[ident]['connection']
                window.startup_secondary_nodes=load_startup_nodes([
                    {'host':'one.invalid','port':7300,'call':'LZ3ZZ'},
                    {'host':'two.invalid','port':7300,'call':'LZ3ZZ-1','auto_reconnect':True}])
                window.open_startup_secondary_nodes();dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
                tree=next(w for w in walk(dialog) if isinstance(w,ttk.Treeview))
                self.assertEqual(len(tree.get_children()),2)
                tree.selection_set('1');next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Remove selected').invoke()
                self.assertEqual([x['host'] for x in window.startup_secondary_nodes],['one.invalid'])
                self.assertEqual(len(json.loads(app.SETTINGS.read_text())['startup_secondary_nodes']),1)
                self.assertFalse(connection.closed);self.assertIn(ident,window.secondary_nodes)
            finally:window.quit_app()

    def test_additional_startup_nodes_are_opt_in_and_removed_by_user(self):
        class Connection:
            started=[]
            def __init__(self,emit): pass
            def start(self,host,port,call): self.started.append((host,port,call))
            def close(self): pass
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'SETTINGS',Path(directory)/'settings.json'), patch.object(app,'Connection',Connection):
            window=app.App();window.withdraw()
            try:
                window.call.set('LZ3ZZ')
                window.open_secondary_nodes();dialog=window.secondary_window
                widgets=list(walk(dialog));entries=[w for w in widgets if w.winfo_class()=='TEntry']
                entries[0].insert(0,'example.invalid')
                next(w for w in widgets if isinstance(w,ttk.Checkbutton) and 'when the app starts' in w.cget('text')).invoke()
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Connect').invoke()
                self.assertEqual(len(window.startup_secondary_nodes),1)
                self.assertEqual(Connection.started,[('example.invalid',7300,'LZ3ZZ')])
            finally:window.quit_app()
            Connection.started=[]
            restored=app.App();restored.withdraw()
            try:
                restored.update();self.assertEqual(Connection.started,[('example.invalid',7300,'LZ3ZZ')])
                restored.open_secondary_nodes();restored.secondary_tree.selection_set(restored.secondary_tree.get_children()[0])
                next(w for w in walk(restored.secondary_window) if isinstance(w,ttk.Button) and w.cget('text')=='Disconnect / remove').invoke()
                self.assertEqual(restored.startup_secondary_nodes,[])
                self.assertEqual(json.loads(app.SETTINGS.read_text())['startup_secondary_nodes'],[])
            finally:restored.quit_app()

    def test_opt_in_is_saved_and_only_runs_on_a_later_start(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'SETTINGS', Path(directory) / 'settings.json'):
            window = app.App(); window.withdraw()
            try:
                window.open_nodes()
                dialog = next(child for child in window.winfo_children() if child.winfo_class() == 'Toplevel')
                def walk(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from walk(child)
                control = next(child for child in walk(dialog) if isinstance(child, ttk.Checkbutton) and 'when the app starts' in child.cget('text'))
                with patch.object(window, 'connect') as connect:
                    control.invoke(); window.update()
                    connect.assert_not_called()
                self.assertTrue(json.loads(app.SETTINGS.read_text())['auto_start'])
            finally: window.quit_app()
            with patch.object(app.App, 'connect') as connect:
                restored = app.App(); restored.withdraw()
                try:
                    restored.update()
                    connect.assert_called_once_with()
                finally: restored.quit_app()

    def test_disconnect_cancels_queued_start_and_default_is_off(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'SETTINGS', Path(directory) / 'settings.json'):
            with patch.object(app.App, 'connect') as connect:
                window = app.App(); window.withdraw()
                try:
                    window.update(); connect.assert_not_called()
                finally: window.quit_app()
            app.SETTINGS.write_text(json.dumps({'auto_start': True}))
            with patch.object(app.App, 'connect') as connect:
                window = app.App(); window.withdraw()
                try:
                    window.disconnect(); window.update(); connect.assert_not_called()
                finally: window.quit_app()


if __name__ == '__main__': unittest.main()
