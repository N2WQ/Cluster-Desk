import tempfile
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

import app as app_module
from app import App
from talk_lists import load_talk_lists, validate_talk_list


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class FakeConnection:
    def __init__(self): self.commands=[]
    def send(self, command): self.commands.append(command)
    def send_batch(self, commands): self.commands.extend(commands)
    def close(self): pass


class TalkListTests(unittest.TestCase):
    def test_validation_and_bounded_loading(self):
        self.assertEqual(validate_talk_list(' Friends ', 'k1abc, lz3zz-1'), {'name':'Friends','recipients':'K1ABC,LZ3ZZ-1'})
        for name, recipients in [('', 'K1ABC'), ('A'*41,'K1ABC'), ('x',''), ('x','K1ABC,k1abc'), ('x','K1ABC,\nBYE')]:
            with self.subTest(name=name,recipients=recipients),self.assertRaises(ValueError):validate_talk_list(name,recipients)
        data=[{'name':'A','recipients':'K1ABC'}, {'name':'a','recipients':'W1AW'}, {'name':'bad','recipients':'A\nBYE'}, None]
        self.assertEqual(load_talk_lists(data),[{'name':'A','recipients':'K1ABC'}])
        self.assertEqual(len(load_talk_lists([{'name':str(i),'recipients':'K1ABC'} for i in range(30)])),20)

    def test_saved_list_can_be_selected_edited_and_used_without_automatic_send(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app_module,'SETTINGS',Path(directory)/'settings.json'):
            app=App();app.withdraw()
            try:
                fake=FakeConnection();app.connection=fake;app.connection_target=('node',7300,'LZ3ZZ');app.dialect='CC Cluster';app.status.set('Connected')
                app.open_message();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');dialog.update()
                panel=dialog.winfo_children()[0];entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry]
                entries[0].insert(0,'K1ABC,LZ3ZZ-1')
                buttons={w.cget('text'):w for w in descendants(dialog) if isinstance(w,ttk.Button)}
                with patch('app.simpledialog.askstring',return_value='Friends'):buttons['Save list…'].invoke()
                self.assertEqual(fake.commands,[]);self.assertEqual(app.talk_lists,[{'name':'Friends','recipients':'K1ABC,LZ3ZZ-1'}])
                entries[0].delete(0,'end')
                box=next(w for w in descendants(dialog) if isinstance(w,ttk.Combobox) and 'Friends' in w.cget('values'))
                box.set('Friends');box.event_generate('<<ComboboxSelected>>');dialog.update()
                self.assertEqual(entries[0].get(),'K1ABC,LZ3ZZ-1');self.assertEqual(fake.commands,[])
                entries[1].insert(0,'hello');buttons['Send message'].invoke()
                self.assertEqual(fake.commands,['TALK K1ABC hello','TALK LZ3ZZ-1 hello'])
                restored=App();restored.withdraw();restored._save_settings=lambda:None
                try:self.assertEqual(restored.talk_lists,[{'name':'Friends','recipients':'K1ABC,LZ3ZZ-1'}])
                finally:restored.quit_app()
            finally:app.quit_app()

    def test_replace_and_delete_require_confirmation(self):
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            app.talk_lists=[{'name':'Friends','recipients':'K1ABC'}]
            fake=FakeConnection();app.connection=fake;app.connection_target=('node',7300,'LZ3ZZ');app.dialect='DXSpider';app.status.set('Connected')
            app.open_message();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');dialog.update()
            panel=dialog.winfo_children()[0];entry=next(w for w in panel.winfo_children() if type(w) is ttk.Entry);entry.insert(0,'W1AW')
            buttons={w.cget('text'):w for w in descendants(dialog) if isinstance(w,ttk.Button)}
            with patch('app.simpledialog.askstring',return_value='friends'),patch('app.messagebox.askyesno',return_value=False):buttons['Save list…'].invoke()
            self.assertEqual(app.talk_lists[0]['recipients'],'K1ABC')
            with patch('app.simpledialog.askstring',return_value='friends'),patch('app.messagebox.askyesno',return_value=True):buttons['Save list…'].invoke()
            self.assertEqual(app.talk_lists[0]['recipients'],'W1AW')
            with patch('app.messagebox.askyesno',return_value=False):buttons['Delete list…'].invoke()
            self.assertEqual(len(app.talk_lists),1)
            with patch('app.messagebox.askyesno',return_value=True):buttons['Delete list…'].invoke()
            self.assertEqual(app.talk_lists,[]);self.assertEqual(fake.commands,[])
        finally:app.quit_app()


if __name__=='__main__':unittest.main()
