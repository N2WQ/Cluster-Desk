import socket
import time
import unittest
import queue
from unittest.mock import patch
from app import App
from logger_bridge import LoggerBridge, format_spot
from protocol import Spot, TelnetDecoder, parse_spot


def recv_until(sock, marker, timeout=3):
    sock.settimeout(.2); result=b''; deadline=time.monotonic()+timeout
    while marker not in result and time.monotonic()<deadline:
        try:
            data=sock.recv(4096)
            if not data: break
            result+=data
        except socket.timeout: pass
    return result


class LoggerBridgeTests(unittest.TestCase):
    def setUp(self):
        self.events=[]; self.bridge=LoggerBridge(0,lambda kind,value:self.events.append((kind,value)))
        self.bridge.start(); deadline=time.monotonic()+3
        while self.bridge.bound_port is None and time.monotonic()<deadline: time.sleep(.01)
        self.assertIsNotNone(self.bridge.bound_port)

    def tearDown(self):
        self.bridge.stop(); self.bridge.thread.join(2)
        self.assertFalse(self.bridge.thread.is_alive())

    def test_prompt_fragment_reaches_logged_in_client_without_newline(self):
        with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as sock:
            recv_until(sock,b'login:');sock.sendall(b'LZ3ZZ\r\n')
            self.assertIn(b'Connected to Cluster Desk by N2WQ.\r\n',recv_until(sock,b'Connected to Cluster Desk by N2WQ.\r\n'))
            self.bridge.publish_fragment('cmd:')
            self.assertEqual(recv_until(sock,b'cmd:'),b'cmd:')
            self.bridge.publish_text(' response')
            self.assertEqual(recv_until(sock,b'\r\n'),b' response\r\n')

    def test_logger_login_spot_command_and_bye(self):
        with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as sock:
            self.assertIn(b'login:',recv_until(sock,b'login:'))
            sock.sendall(b'\xff\xfb\x01LZ3ZZ\r\n')
            self.assertIn(b'Connected to Cluster Desk',recv_until(sock,b'Connected to Cluster Desk'))
            spot=Spot('EA5KCI','7074.0','HA1BF','2328Z','FT8 -10dB','29-Oct-2026')
            self.bridge.publish(spot)
            actual=recv_until(sock,b'2328Z')
            self.assertIn(b'DX de EA5KCI:',actual); self.assertIn(b'7074.0',actual); self.assertIn(b'HA1BF',actual)
            sock.sendall(b'sh/dx/5\r\n'); deadline=time.monotonic()+2
            while not any(k=='command' for k,v in self.events) and time.monotonic()<deadline: time.sleep(.01)
            commands=[v for k,v in self.events if k=='command']; self.assertEqual(len(commands),1)
            ident, command=commands[0]; self.assertEqual(command,'sh/dx/5')
            self.bridge.reply(ident,'sample response'); self.assertIn(b'sample response',recv_until(sock,b'sample response'))
            sock.sendall(b'BYE\r\nsh/dx/10\r\n'); self.assertIn(b'not sent',recv_until(sock,b'not sent'))
            sock.settimeout(2); self.assertEqual(sock.recv(4096),b'')
            self.assertEqual(len([v for k,v in self.events if k=='command']),1)

    def test_telnet_line_endings_and_fragmented_cr_nul(self):
        with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as sock:
            recv_until(sock,b'login:');sock.sendall(b'LZ3ZZ\r')
            self.assertIn(b'Connected to Cluster Desk',recv_until(sock,b'Connected to Cluster Desk'))
            sock.sendall(b'\x00sh/dx\r')
            deadline=time.monotonic()+2
            while len([1 for k,v in self.events if k=='command'])<1 and time.monotonic()<deadline:time.sleep(.01)
            sock.sendall(b'\nsh/wx\nsh/wwv\rsh/wcy\r\x00bad\x01command\r')
            self.assertIn(b'printable only',recv_until(sock,b'printable only'))
            commands=[v[1] for k,v in self.events if k=='command']
            self.assertEqual(commands,['sh/dx','sh/wx','sh/wwv','sh/wcy'])

    def test_ignore_bye_keeps_logger_connected_and_never_forwards_it(self):
        self.bridge.ignore_bye=True
        with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as sock:
            recv_until(sock,b'login:');sock.sendall(b'LZ3ZZ\r\n');recv_until(sock,b'Connected to Cluster Desk')
            sock.sendall(b'BYE\r\nsh/dx/5\r\n')
            self.assertIn(b'connection remains open',recv_until(sock,b'connection remains open'))
            deadline=time.monotonic()+2
            while not any(k=='command' for k,v in self.events) and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual([v[1] for k,v in self.events if k=='command'],['sh/dx/5'])
            self.bridge.publish(Spot('S','14074','K1ABC','1200Z','CQ'))
            self.assertIn(b'K1ABC',recv_until(sock,b'1200Z'))
            sock.sendall(b'B\r\n');self.assertIn(b'not sent',recv_until(sock,b'not sent'))
            sock.settimeout(2);self.assertEqual(sock.recv(1),b'')

    def test_spots_only_suppresses_node_text_but_keeps_spots_and_local_errors(self):
        self.bridge.spots_only=True
        with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as sock:
            recv_until(sock,b'login:');sock.sendall(b'LZ3ZZ\r\n');recv_until(sock,b'Connected to Cluster Desk')
            self.bridge.publish_text('node response not forwarded')
            self.bridge.publish(Spot('S','14074','K1ABC','1200Z','CQ'))
            output=recv_until(sock,b'1200Z\r\n')
            self.assertIn(b'K1ABC',output);self.assertNotIn(b'node response',output)
            sock.sendall(b'cmd\xff\xff\r\n')
            self.assertIn(b'Command must contain ASCII',recv_until(sock,b'Command must contain ASCII'))

    def test_node_text_categories_filter_without_suppressing_spots_or_local_replies(self):
        self.bridge.text_categories=frozenset(('wwv',))
        with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as sock:
            recv_until(sock,b'login:');sock.sendall(b'LZ3ZZ\r\n');recv_until(sock,b'Connected to Cluster Desk')
            self.bridge.publish_text('announcement hidden',category='announcements')
            self.bridge.publish_text('response hidden')
            self.bridge.publish_text('report visible',category='wwv')
            self.bridge.publish(Spot('S','14074','K1ABC','1200Z','CQ'))
            data=recv_until(sock,b'1200Z')
            self.assertIn(b'report visible',data);self.assertIn(b'K1ABC',data)
            self.assertNotIn(b'announcement hidden',data);self.assertNotIn(b'response hidden',data)
            sock.sendall(b'SET/VE7CC\r\n');self.assertIn(b'OK',recv_until(sock,b'OK'))

    def test_60m_selection_forwards_reference_lower_boundary(self):
        self.bridge.spot_bands=frozenset(('60 m',))
        with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as sock:
            recv_until(sock,b'login:');sock.sendall(b'LZ3ZZ\r\n');recv_until(sock,b'Connected to Cluster Desk')
            self.bridge.publish(Spot('S','5194.99','K1OUTSIDE','1200Z','CQ'))
            self.bridge.publish(Spot('S','5195','K1INSIDE','1201Z','CQ'))
            self.bridge.publish_text('end of test')
            data=recv_until(sock,b'end of test')
            self.assertIn(b'K1INSIDE',data);self.assertNotIn(b'K1OUTSIDE',data)

    def test_band_selection_filters_spots_but_not_node_text(self):
        self.bridge.spot_bands=frozenset(('20 m','Other'))
        with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as sock:
            recv_until(sock,b'login:');sock.sendall(b'LZ3ZZ\r\n');recv_until(sock,b'Connected to Cluster Desk')
            self.bridge.publish(Spot('S','7074','K1HIDDEN','1200Z','CQ'))
            self.bridge.publish(Spot('S','14074','K1VISIBLE','1201Z','CQ'))
            self.bridge.publish(Spot('S','1000','K1OTHER','1202Z','CQ'))
            self.bridge.publish_text('node response')
            data=recv_until(sock,b'node response')
            self.assertNotIn(b'K1HIDDEN',data);self.assertIn(b'K1VISIBLE',data);self.assertIn(b'K1OTHER',data)
        with self.assertRaises(ValueError):LoggerBridge(0,lambda *_:None,spot_bands={'invalid'})

    def test_bye_disconnects_only_that_logger(self):
        with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as first, socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as second:
            for sock in (first,second):
                recv_until(sock,b'login:');sock.sendall(b'LZ3ZZ\r\n');recv_until(sock,b'Connected to Cluster Desk')
            first.sendall(b'BYE\r\n');recv_until(first,b'not sent');first.settimeout(2);self.assertEqual(first.recv(4096),b'')
            self.bridge.publish(Spot('EA5KCI','7074.0','HA1BF','2328Z','FT8'))
            self.assertIn(b'HA1BF',recv_until(second,b'2328Z'))

    def test_logger_disconnect_aliases_are_local_to_each_client(self):
        for command in ('b', 'quit'):
            with self.subTest(command=command):
                with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as first, socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as second:
                    for sock in (first,second):
                        recv_until(sock,b'login:');sock.sendall(b'LZ3ZZ\r\n');recv_until(sock,b'Connected to Cluster Desk')
                    first.sendall(command.encode('ascii')+b'\r\nsh/dx\r\n')
                    self.assertIn(b'not sent',recv_until(first,b'not sent'))
                    first.settimeout(2);self.assertEqual(first.recv(4096),b'')
                    self.bridge.publish(Spot('S','14074','K1ABC','1200Z','CQ'))
                    self.assertIn(b'K1ABC',recv_until(second,b'1200Z'))
                self.assertFalse(any(k=='command' for k,v in self.events))

    def test_enhanced_format_is_per_logger_and_preserves_node_line(self):
        line = 'CC11^14074.05^K1ABC^29-Oct-2026^2300Z^CQ~DX^LZ3ZZ^291^^NODE^^^^^^^^^^^future^'
        spot = parse_spot(line, 'DXSpider')
        with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as first, socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as second:
            for sock in (first, second):
                recv_until(sock,b'login:'); sock.sendall(b'LZ3ZZ\r\n'); recv_until(sock,b'Connected to Cluster Desk')
            first.sendall(b'set/ve7cc\r\n'); self.assertIn(b'OK',recv_until(first,b'OK\r\n'))
            self.bridge.publish(spot)
            self.assertEqual(recv_until(first,b'future^\r\n'), (line+'\r\n').encode('ascii'))
            self.assertIn(b'DX de LZ3ZZ:',recv_until(second,b'2300Z\r\n'))
            self.bridge.publish(Spot('S','7000','K1ABC','1201Z','plain'))
            self.assertIn(b'DX de S:',recv_until(first,b'1201Z\r\n'))
            recv_until(second,b'1201Z\r\n')
            first.sendall(b'unset/ve7cc\r\n'); self.assertIn(b'OK',recv_until(first,b'OK\r\n'))
            self.bridge.publish(spot)
            self.assertIn(b'DX de LZ3ZZ:',recv_until(first,b'2300Z\r\n'))
            self.assertFalse(any(k == 'command' for k,v in self.events))

    def test_telnet_payload_escapes_iac_for_all_logger_output(self):
        line='CC11^14074.0^K1ABC^29-Oct-2026^2300Z^aÿb^LZ3ZZ^291^^NODE^^^^^^^^^^^'
        spot=parse_spot(line,'DXSpider')
        with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as sock:
            recv_until(sock,b'login:');sock.sendall(b'LZ3ZZ\r\n');recv_until(sock,b'Connected to Cluster Desk')
            self.bridge.publish(spot)
            wire=recv_until(sock,b'2300Z\r\n');self.assertIn(b'a\xff\xffb',wire)
            self.assertIn(b'a\xffb',TelnetDecoder().feed(wire)[0])
            sock.sendall(b'set/ve7cc\r\n');recv_until(sock,b'OK\r\n')
            self.bridge.publish(spot)
            wire=recv_until(sock,b'NODE^^^^^^^^^^^\r\n');self.assertIn(b'a\xff\xffb',wire)
            self.assertEqual(TelnetDecoder().feed(wire)[0],(line+'\r\n').encode('cp1252'))
            self.bridge.publish_text('reply ÿ text')
            wire=recv_until(sock,b'text\r\n');self.assertEqual(TelnetDecoder().feed(wire)[0],b'reply \xff text\r\n')
            ident=next(value for kind,value in self.events if kind=='clients')
            self.bridge.reply(ident,'answer ÿ')
            wire=recv_until(sock,b'\r\n');self.assertEqual(TelnetDecoder().feed(wire)[0],b'answer \xff\r\n')

    def test_long_input_is_disconnected_and_listener_stops(self):
        with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as sock:
            recv_until(sock,b'login:'); sock.sendall(b'A'*2100)
            self.assertEqual(recv_until(sock,b'never'),b'')
        port=self.bridge.bound_port; self.bridge.stop(); self.bridge.thread.join(2)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',port))

    def test_oversized_tail_after_complete_command_disconnects_without_more_input(self):
        with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as sock:
            recv_until(sock,b'login:');sock.sendall(b'LZ3ZZ\r\n');recv_until(sock,b'Connected to Cluster Desk')
            sock.sendall(b'SH/DX\r\n'+b'A'*2049)
            sock.settimeout(2)
            self.assertEqual(sock.recv(1),b'')
            commands=[v[1] for k,v in self.events if k=='command']
            self.assertEqual(commands,['SH/DX'])

    def test_spot_formatter_replaces_newlines_and_adds_z(self):
        spot=Spot('A','14074.0','B','2300','one\ntwo')
        result=format_spot(spot)
        self.assertEqual(result.count(b'\n'),1)
        self.assertIn(b'one two',result); self.assertTrue(result.endswith(b'2300Z\r\n'))

    def test_frequency_precision_is_preserved_for_logging_clients(self):
        with socket.create_connection(('127.0.0.1',self.bridge.bound_port),timeout=2) as sock:
            recv_until(sock,b'login:');sock.sendall(b'LZ3ZZ\r\n');recv_until(sock,b'Connected to Cluster Desk')
            self.bridge.publish(Spot('S','14074.05','K1ABC','1200Z','CQ'))
            self.assertIn(b'14074.05',recv_until(sock,b'1200Z\r\n'))
        self.assertIn(b'14074.0',format_spot(Spot('S','14074','K1ABC','1200Z','CQ')))
        self.assertIn(b'14074.125',format_spot(Spot('S','14074.125','K1ABC','1200Z','CQ')))
        with self.assertRaises(ValueError): format_spot(Spot('S','1..2','K1ABC','1200Z','CQ'))


class LoggerViewTests(unittest.TestCase):
    def test_unterminated_node_prompt_reaches_connected_logger_once(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        bridge=LoggerBridge(0,lambda *_:None);bridge.start();app.logger=bridge
        try:
            deadline=time.monotonic()+3
            while bridge.bound_port is None and time.monotonic()<deadline: time.sleep(.01)
            self.assertIsNotNone(bridge.bound_port)
            with socket.create_connection(('127.0.0.1',bridge.bound_port),timeout=2) as sock:
                recv_until(sock,b'login:');sock.sendall(b'LZ3ZZ\r\n');recv_until(sock,b'Connected to Cluster Desk')
                app.events.put((app.generation,'data','Node response\r\nLZ3ZZ de NODE dxspider >'))
                app.after_cancel(app.poll_after);app.poll()
                received=recv_until(sock,b'dxspider >\r\n')
                self.assertIn(b'Node response\r\n',received);self.assertEqual(received.count(b'dxspider >'),1)
                app.after_cancel(app.poll_after);app.poll()
                sock.settimeout(.3)
                with self.assertRaises(socket.timeout): sock.recv(4096)
        finally: app.quit_app()

    def test_node_text_options_apply_and_cannot_change_running_listener(self):
        from tkinter import ttk
        app=App();app.withdraw();app._save_settings=lambda:None
        def descendants(w):
            for child in w.winfo_children():yield child;yield from descendants(child)
        try:
            app.open_logger_text_options();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            checks={w.cget('text'):w for w in descendants(dialog) if isinstance(w,ttk.Checkbutton)}
            
            for label,check in checks.items():
                if label != 'WWV reports':check.invoke()
            next(w for w in descendants(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke()
            self.assertEqual(app.logger_text_categories,{'wwv'})
            app.logger=object()
            with patch('app.messagebox.showerror') as error:app.open_logger_text_options();error.assert_called_once()
            app.logger=None
        finally:app.quit_app()

    def test_logger_band_options_are_independent_and_session_bound(self):
        from tkinter import ttk
        app=App();app.withdraw();app._save_settings=lambda:None
        def descendants(w):
            for child in w.winfo_children():yield child;yield from descendants(child)
        try:
            app.serial_spot_bands={'40 m'};app.open_logger_band_options();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            checks={w.cget('text'):w for w in descendants(dialog) if isinstance(w,ttk.Checkbutton)}
            for label,check in checks.items():
                if label!='20 m':check.invoke()
            next(w for w in descendants(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke()
            self.assertEqual(app.logger_spot_bands,{'20 m'});self.assertEqual(app.serial_spot_bands,{'40 m'})
            app.logger=object()
            with patch('app.messagebox.showerror') as error:app.open_logger_band_options();error.assert_called_once()
            app.logger=None
        finally:app.quit_app()

    def test_queued_commands_do_not_cross_cluster_connections(self):
        class FakeThread:
            def is_alive(self): return False
            def join(self,timeout=None): pass
        class FakeBridge:
            def __init__(self,port,emit,ignore_bye=False,spots_only=False,text_categories=None,spot_bands=None): self.emit=emit;self.thread=FakeThread();self.replies=[];self.bound_port=port
            def start(self): pass
            def stop(self): pass
            def reply(self,ident,text):self.replies.append((ident,text))
        class FakeConnection:
            def __init__(self):self.sent=[]
            def send(self,command):self.sent.append(command)
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.LoggerBridge',FakeBridge):app.start_logger()
            bridge=app.logger
            bridge.emit('command',(1,'sh/dx/1'))
            old=FakeConnection();app.connection=old;app.status.set('Connected')
            bridge.emit('command',(1,'sh/dx/2'))
            app.disconnect()
            new=FakeConnection();app.connection=new;app.status.set('Connected')
            bridge.emit('command',(1,'sh/dx/3'))
            app.poll_logger()
            self.assertEqual(old.sent,[]);self.assertEqual(new.sent,['sh/dx/3'])
            self.assertEqual(len(bridge.replies),2)
            self.assertTrue(all('command not sent' in text for _,text in bridge.replies))
        finally:app.quit_app()

    def test_full_event_queue_reports_and_recovers_stopped_listener(self):
        class FakeThread:
            def is_alive(self): return False
        class FakeBridge:
            def __init__(self, port, emit, ignore_bye=False, spots_only=False, text_categories=None, spot_bands=None): self.emit=emit; self.thread=FakeThread(); self.stopped=False
            def start(self): pass
            def stop(self): self.stopped=True
        app=App(); app.withdraw()
        try:
            app.logger_events=queue.Queue(maxsize=1)
            with patch('app.LoggerBridge', FakeBridge): app.start_logger()
            bridge=app.logger
            self.assertEqual(str(app.logger_port_entry['state']),'disabled')
            bridge.emit('clients', 1); bridge.emit('clients', 2)
            self.assertTrue(bridge.stopped)
            app.poll_logger()
            self.assertIsNone(app.logger)
            self.assertIn('event queue full',app.logger_status.get())
            self.assertEqual(str(app.logger_start['state']),'normal')
            self.assertEqual(str(app.logger_port_entry['state']),'normal')
        finally: app.quit_app()

if __name__ == '__main__': unittest.main()
