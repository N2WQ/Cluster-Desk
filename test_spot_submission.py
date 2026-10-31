import socket
import threading
import time
import unittest
from unittest.mock import patch
from tkinter import ttk
from app import App
from protocol import spot_command


class SpotTests(unittest.TestCase):
    def test_main_node_unconfirmed_spot_is_reported(self):
        class Node:
            def __init__(self):self.commands=[]
            def send(self,value):self.commands.append(value)
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:True;node=Node()
        app.connection=node;app.connection_target=('node.invalid',7300,'LZ3ZZ');app.spot_source=app.connection_target
        app.dialect='CC Cluster';app.status.set('Connected');app.refresh_report_targets()
        try:
            app.open_spot();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');panel=dialog.winfo_children()[0]
            entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry]
            entries[0].insert(0,'14074');entries[1].insert(0,'K1ABC')
            next(w for w in panel.winfo_children()[-1].winfo_children() if w.cget('text')=='Send spot').invoke()
            self.assertEqual(len(app.pending_spot_submissions),1)
            app.events.put((app.generation,'status','Disconnected'));app.after_cancel(app.poll_after);app.poll()
            self.assertFalse(app.pending_spot_submissions);self.assertTrue(app.spot_uncertain_notice)
            self.assertIn('spot submission had no send confirmation',app.transcript.get('1.0','end'))
        finally:app.quit_app()

    def test_unconfirmed_spot_is_reported_without_replay(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def send(self,value):self.commands.append(value)
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            with patch('app.Connection',Secondary):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');item=app.secondary_nodes[ident];old=item['connection']
                old.emit('dialect','CC Cluster');old.emit('status','Connected');app.poll_secondary_nodes();app.open_spot()
                dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');panel=dialog.winfo_children()[0]
                entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry]
                entries[0].insert(0,'14074');entries[1].insert(0,'K1ABC')
                next(w for w in panel.winfo_children()[-1].winfo_children() if w.cget('text')=='Send spot').invoke()
                self.assertEqual(len(item['pending_spot_submissions']),1)
                old.emit('status','Disconnected');app.poll_secondary_nodes()
                self.assertFalse(item['pending_spot_submissions']);self.assertTrue(app.spot_uncertain_notice)
                self.assertIn('spot submission had no send confirmation',item['activity'])
                app._start_secondary_attempt(ident);self.assertEqual(item['connection'].commands,[])
        finally:app.quit_app()

    def test_confirmed_spot_is_not_reported_uncertain(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def send(self,value):self.commands.append(value)
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            with patch('app.Connection',Secondary):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');item=app.secondary_nodes[ident];node=item['connection']
                node.emit('dialect','DXSpider');node.emit('status','Connected');app.poll_secondary_nodes();app.open_spot()
                dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');panel=dialog.winfo_children()[0]
                entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry]
                entries[0].insert(0,'14074');entries[1].insert(0,'K1ABC')
                next(w for w in panel.winfo_children()[-1].winfo_children() if w.cget('text')=='Send spot').invoke()
                node.emit('sent',node.commands[0]);node.emit('status','Disconnected');app.poll_secondary_nodes()
                self.assertFalse(item['pending_spot_submissions']);self.assertFalse(app.spot_uncertain_notice)
        finally:app.quit_app()

    def test_enter_sends_spot_to_simulated_node_over_tcp(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        port=listener.getsockname()[1];received=[];errors=[];release=threading.Event()
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(6);sock.sendall(b'login: ');received.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de TEST CCC >\r\n')
                    received.extend(stream.readline() for _ in range(4));release.wait(6)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        app=App();app.deiconify();app._save_settings=lambda:True
        try:
            app.host.set('127.0.0.1');app.port.set(str(port));app.call.set('LZ3ZZ');app.connect()
            deadline=time.monotonic()+6
            while app.status.get()!='Connected' and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertEqual(app.status.get(),'Connected');app.open_spot()
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');panel=dialog.winfo_children()[0]
            entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry]
            entries[0].insert(0,'14074.0');entries[1].insert(0,'K1ABC');entries[2].insert(0,'local test')
            entries[2].focus_force();app.update();entries[2].event_generate('<Return>')
            deadline=time.monotonic()+6
            while (len(received)<5 or app.pending_spot_submissions) and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertEqual(received,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/settings\r\n',b'DX 14074.0 K1ABC local test\r\n'])
            self.assertFalse(dialog.winfo_exists());self.assertFalse(app.pending_spot_submissions)
        finally:
            release.set();app.quit_app();worker.join(3);listener.close()
        self.assertEqual(errors,[]);self.assertFalse(worker.is_alive())

    def test_enter_rejects_a_spot_form_from_an_earlier_connection(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def send(self,value):self.commands.append(value)
            def close(self):pass
        app=App();app.deiconify();app._save_settings=lambda:True
        try:
            with patch('app.Connection',Secondary):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');item=app.secondary_nodes[ident];old=item['connection']
                old.emit('dialect','DXSpider');old.emit('status','Connected');app.poll_secondary_nodes();app.open_spot()
                dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');panel=dialog.winfo_children()[0]
                entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry]
                entries[0].insert(0,'14074');entries[1].insert(0,'K1ABC');app._start_secondary_attempt(ident)
                entries[1].focus_force();app.update()
                with patch('app.messagebox.showerror') as error:
                    entries[1].event_generate('<Return>');app.update();error.assert_called_once()
                self.assertEqual(old.commands,[]);self.assertEqual(item['connection'].commands,[])
                self.assertTrue(dialog.winfo_exists())
        finally:app.quit_app()

    def test_enter_in_spot_fields_validates_and_queues_once(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def send(self,value):self.commands.append(value)
            def close(self):pass
        app=App();app.deiconify();app._save_settings=lambda:True
        try:
            with patch('app.Connection',Secondary):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');item=app.secondary_nodes[ident];node=item['connection']
                node.emit('dialect','DXSpider');node.emit('status','Connected');app.poll_secondary_nodes();app.open_spot()
                dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');panel=dialog.winfo_children()[0]
                entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry]
                entries[0].insert(0,'14074');entries[0].focus_force();app.update()
                with patch('app.messagebox.showerror') as error:
                    entries[0].event_generate('<Return>');app.update();error.assert_called_once()
                self.assertEqual(node.commands,[]);self.assertTrue(dialog.winfo_exists())
                entries[1].insert(0,'K1ABC');entries[2].insert(0,'local test');entries[2].focus_force();app.update()
                entries[2].event_generate('<Return>');app.update()
                self.assertEqual(node.commands,['DX 14074 K1ABC local test']);self.assertFalse(dialog.winfo_exists())
        finally:app.quit_app()

    def test_valid_spot_and_empty_comment(self):
        self.assertEqual(spot_command('14074.0',' k1abc/p ','CQ DX'), 'DX 14074.0 K1ABC/P CQ DX')
        self.assertEqual(spot_command('7001','W1AW',''), 'DX 7001 W1AW')
        self.assertEqual(spot_command('14074.0','K1ABC','test',test=True), 'DXTEST K1ABC 14074.0 test')

    def test_invalid_fields_cannot_insert_commands(self):
        for args in [('0','K1ABC',''), ('1e3','K1ABC',''), ('14074','K1ABC\nDX',''), ('14074','K1ABC','x\r\nBYE'), ('14074','', ''), ('14074','K1ABC','x'*401)]:
            with self.subTest(args=args), self.assertRaises(ValueError): spot_command(*args)

    def test_submission_reaches_only_local_test_server_and_rejects_stale_form(self):
        listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(); listener.settimeout(5)
        seen=[]; errors=[]
        def server():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(3); sock.sendall(b'login: '); stream=sock.makefile('rb')
                    seen.append(stream.readline()); sock.sendall(b'LZ3ZZ de TEST CCC >\r\n')
                    seen.extend(stream.readline() for _ in range(3))
                    seen.append(stream.readline()); seen.append(stream.readline()); stream.close()
            except Exception as exc: errors.append(exc)
            finally: listener.close()
        worker=threading.Thread(target=server,daemon=True); worker.start()
        app=App(); app.withdraw(); app._save_settings=lambda: None
        try:
            app.host.set('127.0.0.1'); app.port.set(str(listener.getsockname()[1])); app.call.set('LZ3ZZ'); app.connect()
            deadline=time.monotonic()+4
            while app.status.get() != 'Connected' and time.monotonic()<deadline: app.update(); time.sleep(.01)
            self.assertEqual(app.status.get(),'Connected')
            with self.assertRaises(OSError): app.queue_spot(app.generation-1,'14074','K1ABC','stale')
            app.dialect='DXSpider'
            with self.assertRaises(ValueError): app.queue_spot(app.generation,'14074','K1ABC','test',True)
            app.dialect='CC Cluster'
            app.queue_spot(app.generation,'14074.0','K1ABC','test only')
            app.queue_spot(app.generation,'14074.0','K1ABC','local test',True)
            worker.join(3); self.assertFalse(worker.is_alive()); self.assertEqual(errors,[])
            self.assertEqual(seen[-2:], [b'DX 14074.0 K1ABC test only\r\n', b'DXTEST K1ABC 14074.0 local test\r\n'])
        finally: app.quit_app(); worker.join(1)

    def test_form_targets_secondary_and_rejects_stale_session(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def send(self,value):self.commands.append(value)
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',Secondary):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');item=app.secondary_nodes[ident];old=item['connection']
                old.emit('dialect','CC Cluster');old.emit('status','Connected');app.poll_secondary_nodes()
                self.assertEqual(str(app.spot_button['state']),'normal')
                app.spot_button.invoke();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');panel=dialog.winfo_children()[0]
                entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry]
                entries[0].insert(0,'14074');entries[1].insert(0,'K1ABC');entries[2].insert(0,'test')
                next(w for w in panel.winfo_children() if isinstance(w,ttk.Checkbutton)).invoke()
                self.assertEqual(old.commands,[])
                next(w for w in panel.winfo_children()[-1].winfo_children() if w.cget('text')=='Send test spot').invoke()
                self.assertEqual(old.commands,['DXTEST K1ABC 14074 test'])
                app.open_spot();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');panel=dialog.winfo_children()[0]
                entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry];entries[0].insert(0,'14074');entries[1].insert(0,'K1ABC')
                app._start_secondary_attempt(ident)
                with patch('app.messagebox.showerror') as error:
                    next(w for w in panel.winfo_children()[-1].winfo_children() if w.cget('text')=='Send spot').invoke();error.assert_called_once()
                self.assertEqual(item['connection'].commands,[])
        finally:app.quit_app()

    def test_test_spot_option_is_disabled_for_dxspider(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',Secondary):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');conn=app.secondary_nodes[ident]['connection']
                conn.emit('dialect','DXSpider');conn.emit('status','Connected');app.poll_secondary_nodes();app.open_spot()
                dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');panel=dialog.winfo_children()[0]
                self.assertEqual(str(next(w for w in panel.winfo_children() if isinstance(w,ttk.Checkbutton))['state']),'disabled')
        finally:app.quit_app()

if __name__ == '__main__': unittest.main()
