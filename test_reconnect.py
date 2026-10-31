import socket
import threading
import time
import unittest
from unittest.mock import patch
import queue
from app import App


class ReconnectTests(unittest.TestCase):
    def test_turning_off_auto_reconnect_cancels_a_waiting_retry(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            app.call.set('LZ3ZZ');app.auto_reconnect.set(True)
            with patch('app.Connection',FakeConnection):
                app.connect();app.connection.emit('status','Disconnected')
                app.after_cancel(app.poll_after);app.poll()
                self.assertIsNotNone(app.retry_after)
                checkbox=next(w for w in app.connect_btn.master.winfo_children()
                              if w.winfo_class()=='TCheckbutton' and w.cget('text')=='Auto reconnect')
                checkbox.invoke()
                self.assertFalse(app.auto_reconnect.get());self.assertIsNone(app.retry_after)
                self.assertFalse(app.want_connection);self.assertEqual(app.connect_btn.cget('text'),'Connect')
                self.assertEqual(app.status.get(),'Disconnected')
        finally:app.quit_app()

    def test_manual_command_send_confirmation_over_local_tcp(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        port=listener.getsockname()[1];release=threading.Event();received=[];errors=[]
        def serve():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(6);sock.sendall(b'login: ');received.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    received.extend(stream.readline() for _ in range(3));release.wait(6)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=serve,daemon=True);worker.start()
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            app.host.set('127.0.0.1');app.port.set(str(port));app.call.set('LZ3ZZ');app.auto_reconnect.set(False);app.connect()
            deadline=time.monotonic()+6
            while app.status.get()!='Connected' and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertEqual(app.status.get(),'Connected')
            app.command_text.set('sh/dx/10');app.send()
            deadline=time.monotonic()+6
            while (len(received)<4 or app.pending_manual_commands) and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertEqual(received,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/dx/10\r\n'])
            self.assertFalse(app.pending_manual_commands);release.set()
            deadline=time.monotonic()+6
            while app.status.get()!='Disconnected' and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertEqual(app.status.get(),'Disconnected');self.assertFalse(app.manual_uncertain_notice)
        finally:
            release.set();app.quit_app();worker.join(3);listener.close()
        self.assertEqual(errors,[]);self.assertFalse(worker.is_alive())

    def test_unconfirmed_manual_command_is_reported_on_disconnect(self):
        class Node:
            def __init__(self):self.commands=[]
            def send(self,command):self.commands.append(command)
            def close(self):pass
        app=App();app.withdraw();node=Node();app.connection=node;app.status.set('Connected')
        try:
            app.command_text.set('sh/dx/10');app.send()
            app.command_text.set('sh/dx/10');app.send()
            self.assertEqual(len(app.pending_manual_commands),2)
            app.events.put((app.generation,'sent',node.commands[0]))
            app.after_cancel(app.poll_after);app.poll();self.assertEqual(len(app.pending_manual_commands),1)
            app.events.put((app.generation,'status','Disconnected'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertFalse(app.pending_manual_commands)
            self.assertIn('1 manually entered command had no send confirmation',app.transcript.get('1.0','end'))
            self.assertTrue(app.manual_uncertain_notice)
            self.assertIn('Command delivery unknown',app.spot_footer_hint.cget('text'))
            app.connection=Node();app.status.set('Connected');app.command_text.set('sh/filter');app.send()
            self.assertTrue(app.manual_uncertain_notice)
            app.show_spot_recovery_notice();app.notebook.select(app.console_page);app.unread_tab_changed()
            self.assertFalse(app.manual_uncertain_notice)
            self.assertIn('duplicates or leave gaps',app.spot_footer_hint.cget('text'))
            self.assertIn('delivery is unknown',app.transcript.get('1.0','end'))
        finally:app.quit_app()

    def test_confirmed_manual_command_does_not_report_uncertain_delivery(self):
        class Node:
            def __init__(self):self.commands=[]
            def send(self,command):self.commands.append(command)
            def close(self):pass
        app=App();app.withdraw();node=Node();app.connection=node;app.status.set('Connected')
        try:
            app.command_text.set('sh/dx/10');app.send()
            app.events.put((app.generation,'sent',node.commands[0]))
            app.after_cancel(app.poll_after);app.poll();app.disconnect()
            self.assertNotIn('no send confirmation',app.transcript.get('1.0','end'))
            self.assertFalse(app.manual_uncertain_notice)
        finally:app.quit_app()

    def test_command_recall_works_with_arrow_keys(self):
        class Node:
            def send(self,command):pass
            def close(self):pass
        app=App();app.deiconify();app.connection=Node();app.status.set('Connected')
        try:
            app.command_text.set('sh/dx/10');app.send()
            app.command_text.set('unfinished');app.command.focus_force();app.update()
            app.command.event_generate('<KeyPress-Up>');app.update()
            self.assertEqual(app.command_text.get(),'sh/dx/10')
            app.command.event_generate('<KeyPress-Down>');app.update()
            self.assertEqual(app.command_text.get(),'unfinished')
        finally:app.quit_app()

    def test_command_recall_preserves_draft_and_clears_on_disconnect(self):
        class Node:
            def __init__(self):self.commands=[]
            def send(self,command):self.commands.append(command)
            def close(self):pass
        app=App();app.withdraw();node=Node();app.connection=node;app.status.set('Connected')
        try:
            self.assertTrue(app.command.bind('<Up>'));self.assertTrue(app.command.bind('<Down>'))
            for command in ('sh/dx/10','sh/wwv'):
                app.command_text.set(command);app.send()
            app.command_text.set('unfinished')
            app.recall_command(-1);self.assertEqual(app.command_text.get(),'sh/wwv')
            app.recall_command(-1);self.assertEqual(app.command_text.get(),'sh/dx/10')
            app.recall_command(-1);self.assertEqual(app.command_text.get(),'sh/dx/10')
            app.recall_command(1);self.assertEqual(app.command_text.get(),'sh/wwv')
            app.recall_command(1);self.assertEqual(app.command_text.get(),'unfinished')
            app.disconnect();self.assertFalse(app.command_history)
            app.connection=Node();app.status.set('Connected')
            app.recall_command(-1);self.assertEqual(app.command_text.get(),'unfinished')
            with patch('app.messagebox.showerror') as error:app.send();error.assert_called_once()
            self.assertEqual(app.connection.commands,[])
        finally:app.quit_app()

    def test_failed_command_is_not_saved_for_recall(self):
        class Node:
            def send(self,command):raise OSError('Not sent')
            def close(self):pass
        app=App();app.withdraw();app.connection=Node();app.status.set('Connected')
        try:
            app.command_text.set('sh/dx/10')
            with patch('app.messagebox.showerror'):app.send()
            self.assertFalse(app.command_history);self.assertEqual(app.command_text.get(),'sh/dx/10')
        finally:app.quit_app()

    def test_unexpected_disconnect_clears_command_history(self):
        class Node:
            def send(self,command):pass
            def close(self):pass
        app=App();app.withdraw();app.connection=Node();app.status.set('Connected')
        try:
            app.command_text.set('sh/dx/10');app.send();self.assertTrue(app.command_history)
            app.events.put((app.generation,'status','Disconnected'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertFalse(app.command_history)
        finally:app.quit_app()

    def test_command_recall_is_bounded(self):
        class Node:
            def send(self,command):pass
            def close(self):pass
        app=App();app.withdraw();app.connection=Node();app.status.set('Connected')
        try:
            for n in range(55):app.command_text.set(f'sh/dx/{n+1}');app.send()
            self.assertEqual(len(app.command_history),50)
            self.assertEqual(app.command_history[0],'sh/dx/6')
        finally:app.quit_app()

    def test_failed_login_backoff_is_separate_for_each_node(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            app.call.set('LZ3ZZ');app.auto_reconnect.set(True)
            with patch('app.Connection',FakeConnection):
                app.connect()
                first=app.start_secondary_node('first.invalid',7300,'LZ3ZZ-1',auto_reconnect=True)
                second=app.start_secondary_node('second.invalid',7300,'LZ3ZZ-2',auto_reconnect=True)
                a=app.secondary_nodes[first];b=app.secondary_nodes[second]
                for expected in (2,4,8):
                    a['connection'].emit('status','Disconnected');app.poll_secondary_nodes()
                    self.assertEqual(a['status'],f'Reconnecting in {expected}s…')
                    app.after_cancel(a['retry_after']);app._start_secondary_attempt(first)
                self.assertEqual(b['retry_streak'],0)
                self.assertEqual(app.retry_streak,0)
                b['connection'].emit('status','Disconnected');app.poll_secondary_nodes()
                self.assertEqual(b['status'],'Reconnecting in 2s…')
                app.connection.emit('status','Disconnected');app.after_cancel(app.poll_after);app.poll()
                self.assertEqual(app.status.get(),'Reconnecting in 2 seconds…')
                self.assertEqual(a['retry_streak'],3)
        finally:app.quit_app()

    def test_short_lived_connections_back_off_independently_and_stable_sessions_reset(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            app.call.set('LZ3ZZ');app.auto_reconnect.set(True)
            with patch('app.Connection',FakeConnection):
                app.connect()
                ident=app.start_secondary_node('other.invalid',7300,'LZ3ZZ-1',auto_reconnect=True)
                item=app.secondary_nodes[ident]
                for delay in (2,4,8,16,32,60,60):
                    app.connection.emit('status','Connected');app.connection.emit('status','Disconnected')
                    app.after_cancel(app.poll_after);app.poll()
                    self.assertEqual(app.status.get(),f'Reconnecting in {delay} seconds…')
                    item['connection'].emit('status','Connected');item['connection'].emit('status','Disconnected');app.poll_secondary_nodes()
                    self.assertEqual(item['status'],f'Reconnecting in {delay}s…')
                    app.after_cancel(app.retry_after);app._retry_connection()
                    app.after_cancel(item['retry_after']);app._start_secondary_attempt(ident)
                app.connection.emit('status','Connected');app.after_cancel(app.poll_after);app.poll()
                item['connection'].emit('status','Connected');app.poll_secondary_nodes()
                app.connected_since=time.monotonic()-31;item['connected_since']=time.monotonic()-31
                app.connection.emit('status','Disconnected');app.after_cancel(app.poll_after);app.poll()
                item['connection'].emit('status','Disconnected');app.poll_secondary_nodes()
                self.assertEqual(app.status.get(),'Reconnecting in 2 seconds…')
                self.assertEqual(item['status'],'Reconnecting in 2s…')
                app.disconnect();self.assertEqual(app.retry_streak,0);self.assertIsNone(app.retry_after)
                app.stop_secondary_node(ident)
        finally:app.quit_app()

    def test_returning_to_an_earlier_main_node_warns_and_remembers_are_bounded(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            app.call.set('LZ3ZZ')
            with patch('app.Connection',FakeConnection):
                for host in ('first.invalid','second.invalid','first.invalid'):
                    app.host.set(host);app.connect();app.connection.emit('dialect','DXSpider');app.connection.emit('status','Connected')
                    app.after_cancel(app.poll_after);app.poll()
                    if host=='second.invalid':self.assertFalse(app.spot_recovery_notice)
                    app.disconnect()
                self.assertTrue(app.spot_recovery_notice)
                for i in range(60):app.remember_connected_source((f'node{i}.invalid',7300,'LZ3ZZ'))
                self.assertEqual(len(app.recent_connected_sources),50)
        finally:app.quit_app()

    def test_manual_secondary_reconnect_and_switch_to_main_warn_about_history(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            with patch('app.Connection',FakeConnection):
                ident=app.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=app.secondary_nodes[ident]
                item['connection'].emit('status','Connected');app.poll_secondary_nodes()
                self.assertFalse(app.spot_recovery_notice)
                app.stop_secondary_node(ident)
                ident=app.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=app.secondary_nodes[ident]
                item['connection'].emit('status','Connected');app.poll_secondary_nodes()
                self.assertTrue(app.spot_recovery_notice)
                self.assertIn('Reconnected. Recent spot history was requested',item['activity'])
                app.stop_secondary_node(ident);app.clear_spots()
                app.host.set('node.invalid');app.port.set('7300');app.call.set('LZ3ZZ');app.connect()
                app.connection.emit('dialect','DXSpider');app.connection.emit('status','Connected')
                app.after_cancel(app.poll_after);app.poll()
                self.assertTrue(app.spot_recovery_notice)
                self.assertIn('Reconnected. Recent spot history was requested',app.transcript.get('1.0','end'))
        finally:app.quit_app()

    def test_switch_from_main_to_additional_warns_about_history(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            with patch('app.Connection',FakeConnection):
                app.host.set('node.invalid');app.port.set('7300');app.call.set('LZ3ZZ');app.connect()
                app.connection.emit('dialect','DXSpider');app.connection.emit('status','Connected')
                app.after_cancel(app.poll_after);app.poll();app.disconnect()
                ident=app.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=app.secondary_nodes[ident]
                item['connection'].emit('status','Connected');app.poll_secondary_nodes()
                self.assertTrue(app.spot_recovery_notice)
                self.assertIn('may contain duplicates or leave gaps',item['activity'])
        finally:app.quit_app()

    def test_password_login_notice_appears_for_main_and_additional_nodes(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            app.call.set('LZ3ZZ')
            with patch('app.Connection',FakeConnection):
                app.connect();main=app.connection
                ident=app.start_secondary_node('other.invalid',7300,'LZ3ZZ-1');item=app.secondary_nodes[ident]
                text='Password-login output was hidden; spots in that response may be missing. Recent history was requested.'
                main.emit('notice',text);item['connection'].emit('notice',text)
                app.after_cancel(app.poll_after);app.poll();app.poll_secondary_nodes()
                self.assertIn(text,app.transcript.get('1.0','end'))
                self.assertIn(text,item['activity'])
        finally:app.quit_app()

    def test_manual_reconnect_warns_for_same_main_node_only(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.host.set('first.invalid');app.call.set('LZ3ZZ')
            with patch('app.Connection',FakeConnection):
                app.connect();app.connection.emit('dialect','DXSpider');app.connection.emit('status','Connected')
                app.after_cancel(app.poll_after);app.poll()
                self.assertFalse(app.spot_recovery_notice)
                app.disconnect();app.connect();app.connection.emit('dialect','DXSpider');app.connection.emit('status','Connected')
                app.after_cancel(app.poll_after);app.poll()
                self.assertTrue(app.spot_recovery_notice)
                self.assertIn('Reconnected. Recent spot history was requested',app.transcript.get('1.0','end'))
                app.disconnect();app.host.set('different.invalid');app.connect()
                app.connection.emit('dialect','DXSpider');app.connection.emit('status','Connected')
                app.after_cancel(app.poll_after);app.poll()
                self.assertFalse(app.spot_recovery_notice)
        finally:app.quit_app()

    def test_retry_reports_history_limit_for_main_and_additional_nodes(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.call.set('LZ3ZZ')
            with patch('app.Connection',FakeConnection):
                app.connect();main=app.connection;main.emit('dialect','DXSpider');main.emit('status','Connected')
                app.after_cancel(app.poll_after);app.poll()
                self.assertNotIn('Connected after retry',app.transcript.get('1.0','end'))
                main.emit('status','Disconnected');app.auto_reconnect.set(True)
                app.after_cancel(app.poll_after);app.poll();self.assertEqual(app.retry_attempt,1)
                app.after_cancel(app.retry_after);app._retry_connection();app.connection.emit('dialect','DXSpider');app.connection.emit('status','Connected')
                app.after_cancel(app.poll_after);app.poll()
                self.assertIn('may contain duplicates or leave gaps',app.transcript.get('1.0','end'))
                self.assertIn('duplicates or leave gaps',app.spot_footer_hint.cget('text'))
                self.assertEqual(app.spot_footer_hint.winfo_manager(),'pack')
                app.clear_spots()
                self.assertFalse(app.spot_footer_hint.winfo_manager())
                ident=app.start_secondary_node('other.invalid',7300,'LZ3ZZ-1',auto_reconnect=True)
                item=app.secondary_nodes[ident];item['connection'].emit('status','Disconnected');app.poll_secondary_nodes()
                app.after_cancel(item['retry_after']);app._start_secondary_attempt(ident)
                item['connection'].emit('status','Connected');app.poll_secondary_nodes()
                self.assertIn('may contain duplicates or leave gaps',item['activity'])
                self.assertIn('Additional node other.invalid: Connected after retry',app.transcript.get('1.0','end'))
                self.assertIn('duplicates or leave gaps',app.spot_footer_hint.cget('text'))
        finally:app.quit_app()

    def test_connection_fields_track_active_session_and_retry(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.call.set('LZ3ZZ')
            with patch('app.Connection',FakeConnection):
                self.assertTrue(all(str(e['state'])=='normal' for e in app.connection_entries))
                app.connect()
                self.assertTrue(all(str(e['state'])=='readonly' for e in app.connection_entries))
                app.auto_reconnect.set(True);app.connection.emit('status','Disconnected')
                app.after_cancel(app.poll_after);app.poll()
                self.assertIsNotNone(app.retry_after)
                self.assertTrue(all(str(e['state'])=='readonly' for e in app.connection_entries))
                app.disconnect()
                self.assertTrue(all(str(e['state'])=='normal' for e in app.connection_entries))
                app.auto_reconnect.set(False);app.connect();app.connection.emit('status','Disconnected')
                app.after_cancel(app.poll_after);app.poll()
                self.assertTrue(all(str(e['state'])=='normal' for e in app.connection_entries))
        finally:app.quit_app()

    def test_recovery_notice_remains_visible_at_compact_height(self):
        app=App();app._save_settings=lambda:None
        try:
            app.geometry('780x560');app.update();app.show_spot_recovery_notice();app.update()
            self.assertTrue(app.spot_source_label.winfo_ismapped())
            self.assertIn('duplicates or leave gaps',app.spot_source_label.cget('text'))
            app.geometry('1120x760');app.update()
            self.assertTrue(app.spot_footer_hint.winfo_ismapped())
            self.assertIn('duplicates or leave gaps',app.spot_footer_hint.cget('text'))
            app.clear_spots();app.geometry('780x560');app.update()
            self.assertEqual(str(app.spot_source_label.cget('textvariable')),str(app.spot_source_note))
            self.assertFalse(app.spot_recovery_notice)
        finally:app.quit_app()

    def test_single_connection_button_tracks_connect_cancel_and_retry(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit;self.closed=False
            def start(self,*args):pass
            def close(self):self.closed=True
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.call.set('LZ3ZZ')
            with patch('app.Connection',FakeConnection):
                self.assertEqual(app.connect_btn.cget('text'),'Connect')
                app.connect_btn.invoke();first=app.connection
                self.assertEqual(app.connect_btn.cget('text'),'Disconnect')
                app.connect_btn.invoke()
                self.assertTrue(first.closed);self.assertIsNone(app.connection)
                self.assertEqual(app.connect_btn.cget('text'),'Connect')
                app.connect_btn.invoke();second=app.connection
                second.emit('dialect','DXSpider');second.emit('status','Connected')
                app.after_cancel(app.poll_after);app.poll()
                self.assertEqual(app.connect_btn.cget('text'),'Disconnect')
                app.auto_reconnect.set(True);second.emit('status','Disconnected')
                app.after_cancel(app.poll_after);app.poll()
                self.assertIsNotNone(app.retry_after)
                self.assertEqual(app.connect_btn.cget('text'),'Disconnect')
                app.connect_btn.invoke()
                self.assertIsNone(app.retry_after);self.assertFalse(app.want_connection)
                self.assertEqual(app.connect_btn.cget('text'),'Connect')
                self.assertTrue(all(str(entry['state'])=='normal' for entry in app.connection_entries))
        finally:app.quit_app()

    def test_auto_reconnect_after_initial_connection_refused(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.settimeout(8)
        port=listener.getsockname()[1];seen=[];errors=[];release=threading.Event();worker=None
        def serve():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(6);sock.sendall(b'login: ');seen.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    seen.extend((stream.readline(),stream.readline()))
                    sock.sendall(b'CC11^14074^K1ABC^30-Oct-2026^1234Z^retry test^W1AW^226\r\n')
                    release.wait(8)
            except Exception as exc:errors.append(exc)
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            app.host.set('127.0.0.1');app.port.set(str(port));app.call.set('LZ3ZZ');app.auto_reconnect.set(True);app.connect()
            deadline=time.monotonic()+8
            while app.retry_after is None and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertIsNotNone(app.retry_after);self.assertIn('Reconnecting',app.status.get())
            self.assertEqual(app.connect_btn.cget('text'),'Disconnect')
            listener.listen();worker=threading.Thread(target=serve,daemon=True);worker.start()
            deadline=time.monotonic()+8
            while (app.status.get()!='Connected' or not app.rows) and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertEqual(app.status.get(),'Connected');self.assertTrue(any(spot.dx=='K1ABC' for _,spot in app.rows))
            self.assertTrue(any(app.spots.set(row,'dx')=='K1ABC' for row in app.spots.get_children()))
            self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])
            self.assertIn('Recent spot history was requested',app.transcript.get('1.0','end'))
        finally:
            release.set();app.quit_app();listener.close()
            if worker:worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_connection_button_opens_and_closes_real_local_session(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(5)
        port=listener.getsockname()[1];ready=threading.Event();closed=threading.Event();seen=[];errors=[]
        def serve():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(6);stream=sock.makefile('rb');sock.sendall(b'login: ')
                    seen.append(stream.readline());sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    seen.extend((stream.readline(),stream.readline()));ready.set()
                    if stream.read(1)!=b'':raise RuntimeError('Unexpected extra data before close')
                    closed.set()
            except Exception as exc:errors.append(exc);ready.set()
            finally:listener.close()
        worker=threading.Thread(target=serve,daemon=True);worker.start()
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.host.set('127.0.0.1');app.port.set(str(port));app.call.set('LZ3ZZ');app.auto_reconnect.set(False)
            app.connect_btn.invoke();self.assertEqual(app.connect_btn.cget('text'),'Disconnect')
            deadline=time.monotonic()+6
            while (not ready.is_set() or app.status.get()!='Connected') and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertEqual(errors,[]);self.assertEqual(app.status.get(),'Connected')
            self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])
            app.connect_btn.invoke();self.assertEqual(app.connect_btn.cget('text'),'Connect')
            self.assertTrue(closed.wait(5));self.assertIsNone(app.connection);self.assertFalse(app.want_connection)
        finally:app.quit_app();worker.join(2);listener.close()
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_connection_button_cancels_real_session_waiting_for_login(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(5)
        port=listener.getsockname()[1];accepted=threading.Event();closed=threading.Event();errors=[]
        def serve():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(6);accepted.set()
                    if sock.recv(1)!=b'':raise RuntimeError('Unexpected data before login prompt')
                    closed.set()
            except Exception as exc:errors.append(exc);accepted.set()
            finally:listener.close()
        worker=threading.Thread(target=serve,daemon=True);worker.start()
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.host.set('127.0.0.1');app.port.set(str(port));app.call.set('LZ3ZZ');app.auto_reconnect.set(False)
            app.connect_btn.invoke();deadline=time.monotonic()+6
            while not accepted.is_set() and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertEqual(errors,[]);self.assertTrue(accepted.is_set())
            self.assertEqual(app.connect_btn.cget('text'),'Disconnect');app.connect_btn.invoke()
            self.assertTrue(closed.wait(5));self.assertEqual(app.connect_btn.cget('text'),'Connect')
            self.assertIsNone(app.connection);self.assertFalse(app.want_connection)
        finally:app.quit_app();worker.join(2);listener.close()
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_command_draft_requires_edit_after_main_connection_changes(self):
        class FakeConnection:
            def __init__(self):self.commands=[]
            def send(self,value):self.commands.append(value)
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            old=FakeConnection();app.connection=old;app.status.set('Connected');app.command.insert(0,'sh/dx/5')
            app.disconnect();new=FakeConnection();app.connection=new;app.status.set('Connected')
            with patch('app.messagebox.showerror') as error:app.send();error.assert_called_once()
            self.assertEqual(new.commands,[]);self.assertEqual(app.command.get(),'sh/dx/5')
            app.command.insert('end',' ');app.send();self.assertEqual(new.commands,['sh/dx/5'])
        finally:app.quit_app()

    def test_manual_disconnect_reports_pending_mail_delivery_unknown(self):
        app=App(); app.withdraw()
        try:
            app.mail_compose_pending=True
            app.disconnect()
            self.assertIn('delivery may be unknown',app.transcript.get('1.0','end'))
            self.assertFalse(app.mail_compose_pending)
        finally: app.quit_app()

    def test_full_incoming_queue_closes_and_reports_connection(self):
        class FakeConnection:
            def __init__(self, emit): self.emit=emit; self.closed=False
            def start(self, *args): pass
            def close(self): self.closed=True
        app=App(); app.withdraw(); app._save_settings=lambda: None
        try:
            app.call.set('LZ3ZZ')
            app.events=queue.Queue(maxsize=1)
            with patch('app.Connection', FakeConnection): app.connect()
            connection=app.connection
            app.mail_compose_pending=True
            connection.emit('data','first'); connection.emit('data','overflow')
            self.assertTrue(connection.closed)
            app.after_cancel(app.poll_after); app.poll()
            self.assertIsNone(app.connection)
            self.assertIn('Connection error',app.status.get())
            self.assertIn('exceeded the application queue',app.transcript.get('1.0','end'))
            self.assertIn('delivery may be unknown',app.transcript.get('1.0','end'))
            self.assertFalse(app.mail_compose_pending)
            self.assertEqual(str(app.connect_btn['state']),'normal')
        finally: app.quit_app()

    def test_reconnects_after_eof_without_replaying_command(self):
        listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(); listener.settimeout(6)
        seen=[]; errors=[]; second_ready=threading.Event()
        def server():
            try:
                for attempt in range(2):
                    sock,_=listener.accept(); sock.settimeout(3)
                    with sock:
                        sock.sendall(b'login: '); stream=sock.makefile('rb')
                        seen.append(stream.readline()); sock.sendall(b'LZ3ZZ de TEST CCC >\r\n')
                        seen.append([stream.readline() for _ in range(3)])
                        if attempt == 0:
                            seen.append(stream.readline())
                        else:
                            second_ready.set(); sock.settimeout(.6)
                            try: seen.append(sock.recv(100))
                            except socket.timeout: seen.append(b'')
                        stream.close()
            except Exception as exc: errors.append(exc)
            finally: listener.close()
        worker=threading.Thread(target=server,daemon=True); worker.start()
        app=App(); app.withdraw(); app._save_settings=lambda: None
        try:
            app.host.set('127.0.0.1'); app.port.set(str(listener.getsockname()[1])); app.call.set('LZ3ZZ'); app.auto_reconnect.set(True); app.connect()
            deadline=time.monotonic()+5
            while app.status.get() != 'Connected' and time.monotonic()<deadline: app.update(); time.sleep(.01)
            self.assertEqual(app.status.get(),'Connected')
            app.connection.send('sh/dx/5')
            while not second_ready.is_set() and time.monotonic()<deadline: app.update(); time.sleep(.01)
            self.assertTrue(second_ready.is_set())
            worker.join(2)
            self.assertFalse(worker.is_alive()); self.assertEqual(errors,[])
            self.assertEqual(seen[2],b'sh/dx/5\r\n')
            self.assertEqual(seen[3],b'LZ3ZZ\r\n')
            self.assertEqual(seen[4],[b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/settings\r\n'])
            self.assertEqual(seen[5],b'')
        finally:
            app.quit_app(); worker.join(1)

    def test_manual_disconnect_cancels_pending_retry(self):
        app=App(); app.withdraw()
        try:
            app.want_connection=True; app.auto_reconnect.set(True); app.connection_target=('127.0.0.1',1,'LZ3ZZ')
            app._schedule_reconnect(); self.assertIsNotNone(app.retry_after)
            app.disconnect()
            self.assertIsNone(app.retry_after); self.assertFalse(app.want_connection)
            self.assertEqual(app.status.get(),'Disconnected')
        finally: app.quit_app()

if __name__ == '__main__': unittest.main()
