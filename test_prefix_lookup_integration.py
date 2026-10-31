import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
from app import App
from test_secondary_filters import FakeConnection, children

class PrefixLookupIntegrationTests(unittest.TestCase):
    def test_main_filter_lookup_sends_to_main_and_opens_activity(self):
        app=App(); app._save_settings=lambda:None
        try:
            conn=FakeConnection(None); app.connection=conn; app.status.set('Connected'); app.dialect='DXSpider'
            app.update_filter_view(); app.geometry('780x560'); app.notebook.select(3); app.update()
            button=next(w for w in children(app.spider_filter) if isinstance(w,ttk.Button) and w.cget('text').startswith('Find node country'))
            self.assertTrue(button.winfo_ismapped())
            self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),app.command.winfo_rooty())
            with patch('app.simpledialog.askstring',return_value='K'): button.invoke()
            self.assertEqual(conn.commands,['show/prefix K'])
            self.assertEqual(app.notebook.select(),str(app.console_page))
        finally: app.quit_app()

    def test_secondary_lookup_uses_selected_node_and_rechecks_epoch(self):
        app=App(); app.withdraw(); app._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                first=app.start_secondary_node('first.invalid',7300,'LZ3ZZ')
                second=app.start_secondary_node('second.invalid',7300,'LZ3ZZ')
                for ident in (first,second):
                    conn=app.secondary_nodes[ident]['connection'];conn.emit('dialect','DXSpider');conn.emit('status','Connected')
                app.poll_secondary_nodes(); item=app.secondary_nodes[second]; conn=item['connection']
                app.open_secondary_filters(second)
                dialog=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel))
                button=next(w for w in children(dialog) if isinstance(w,ttk.Button) and w.cget('text').startswith('Find node country'))
                def changed(*args,**kwargs): item['epoch']+=1;return 'K'
                with patch('app.simpledialog.askstring',side_effect=changed),patch('app.messagebox.showerror'): button.invoke()
                self.assertEqual(conn.commands,[]);item['epoch']-=1
                with patch('app.simpledialog.askstring',return_value='VE'): button.invoke()
                self.assertEqual(conn.commands,['show/prefix VE'])
                self.assertEqual(app.secondary_nodes[first]['connection'].commands,[])
                self.assertIsNotNone(item['view'])
        finally: app.quit_app()

if __name__=='__main__': unittest.main()
