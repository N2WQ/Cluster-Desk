import socket
import threading
import time
import unittest
import queue
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch
from protocol import Connection, Spot, TelnetDecoder, PROMPT, parse_setting, parse_spider_setting, parse_spot, setting_command
from app import band_for, spot_order


class ProtocolTests(unittest.TestCase):
    def test_callsign_and_please_enter_password_prompts(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        seen=[];errors=[];events=[]
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(5);sock.sendall(b'Callsign: ');seen.append(stream.readline())
                    sock.sendall(b'Please enter your pass');time.sleep(.03);sock.sendall(b'word: ')
                    seen.append(stream.readline());sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    seen.extend((stream.readline(),stream.readline()))
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        connection=Connection(lambda kind,value:events.append((kind,value)))
        try:
            connection.start('127.0.0.1',listener.getsockname()[1],'LZ3ZZ')
            deadline=time.monotonic()+5
            while ('password_required',None) not in events and time.monotonic()<deadline:time.sleep(.01)
            self.assertIn(('password_required',None),events);connection.provide_password('secret')
            worker.join(7);self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(seen,[b'LZ3ZZ\r\n',b'secret\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])
            self.assertIn(('status','Connected'),events)
            self.assertNotIn('secret',''.join(str(value) for _,value in events))
        finally:
            connection.close()
            if connection.thread:connection.thread.join(2)

    def test_password_mentioned_in_banner_does_not_block_login(self):
        from protocol import PASSWORD
        for prompt in ('password: ', 'Password:', 'Enter password: ', 'Enter your password: '):
            self.assertIsNotNone(PASSWORD.search(prompt))
        self.assertIsNone(PASSWORD.search('To reset your password: contact the operator.'))
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        seen=[];errors=[]
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(5);sock.sendall(b'login: ');seen.append(stream.readline())
                    sock.sendall(b'To reset your password: contact the operator.\r\nLZ3ZZ de NODE dxspider >\r\n')
                    seen.extend((stream.readline(),stream.readline()))
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start();events=[]
        connection=Connection(lambda kind,value:events.append((kind,value)))
        try:
            connection.start('127.0.0.1',listener.getsockname()[1],'LZ3ZZ');worker.join(7)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])
            self.assertNotIn(('password_required',None),events);self.assertIn(('status','Connected'),events)
        finally:
            connection.close()
            if connection.thread:connection.thread.join(2)

    def test_last_login_banner_does_not_trigger_login(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        seen=[];errors=[]
        def server():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.sendall(b'Last login: ');sock.settimeout(.5)
                    try:seen.append(sock.recv(100))
                    except socket.timeout:seen.append(b'')
                    sock.settimeout(4);sock.sendall(b'31-Oct-2026\r\nPlease enter your ca');time.sleep(.03);sock.sendall(b'll: ')
                    with sock.makefile('rb') as stream:
                        seen.append(stream.readline());sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                        seen.extend((stream.readline(),stream.readline()))
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start();events=[]
        connection=Connection(lambda kind,value:events.append((kind,value)))
        try:
            connection.start('127.0.0.1',listener.getsockname()[1],'LZ3ZZ');worker.join(7)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(seen,[b'',b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])
            self.assertIn(('status','Connected'),events)
        finally:
            connection.close()
            if connection.thread:connection.thread.join(2)

    def test_cc_history_preserves_angle_brackets_in_comments(self):
        # Observed in the supplied CC node's 2 m history; <TR> belongs to
        # the comment, while the final brackets identify the spotter.
        line = ('144174.0  F1NZC       31-Oct-2026 1503Z  '
                '<TR> FT8 -21 dB 2341 Hz C <SM6VTZ>')
        spot = parse_spot(line, 'CC Cluster')
        self.assertIsNotNone(spot)
        self.assertEqual((spot.frequency, spot.dx, spot.date, spot.time,
                          spot.comment, spot.spotter),
                         ('144174.0', 'F1NZC', '31-Oct-2026', '1503Z',
                          '<TR> FT8 -21 dB 2341 Hz C', 'SM6VTZ'))

    def test_password_login_hides_same_read_spot_but_delivers_later_spot(self):
        class Socket:
            def __init__(self):
                self.reads=iter((b'login: ',b'password: ',
                    b'secret\r\nLZ3ZZ de NODE dxspider >\r\nCC11^14074^K1ABC^30-Oct-2026^1234Z^CQ^W1AW^226\r\n',
                    b'CC11^7074^K2ABC^30-Oct-2026^1235Z^CQ^W1AW^226\r\n',b''))
                self.sent=[]
            def settimeout(self,value):pass
            def recv(self,size):return next(self.reads)
            def sendall(self,data):self.sent.append(data)
            def close(self):pass
        events=[]
        def emit(kind,value):
            events.append((kind,value))
            if kind=='password_required':connection.provide_password('secret')
        sock=Socket();connection=Connection(emit)
        with patch('protocol.socket.create_connection',return_value=sock):connection._run('node.invalid',7300,'LZ3ZZ')
        text=''.join(value for kind,value in events if kind=='data')
        self.assertNotIn('secret',text);self.assertNotIn('K1ABC',text);self.assertIn('K2ABC',text)
        self.assertTrue(any(kind=='notice' and 'spots in that response may be missing' in value for kind,value in events))
        self.assertIn(b'sh/mydx/100\r\n',sock.sent)

    def test_node_closing_during_login_reports_a_useful_error(self):
        class Socket:
            def settimeout(self,value):pass
            def recv(self,size):return b''
            def close(self):pass
        events=[];connection=Connection(lambda kind,value:events.append((kind,value)))
        with patch('protocol.socket.create_connection',return_value=Socket()):connection._run('node.invalid',7300,'LZ3ZZ')
        self.assertIn(('error','The node closed the connection before sign-in completed.'),events)
        self.assertEqual(events[-1],('status','Disconnected'))

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required for the pinned DXSpider formatter')
    def test_plain_history_from_pinned_dxspider_formatter(self):
        for width,comment,expected in ((80,'CQ FT8','CQ FT8'),(80,'',''),(50,'CQ TEST LONG COMMENT','C')):
            with self.subTest(width=width,comment=comment):
                data={'width':width,'frequency':14074,'dx':'K1ABC','comment':comment,'spotter':'W1AW'}
                result=subprocess.run(['perl','tools/check-dxspider-history-spot.pl'],input=json.dumps(data),
                    text=True,capture_output=True,cwd=Path(__file__).resolve().parent,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                spot=parse_spot(json.loads(result.stdout)['line'],'DXSpider')
                self.assertIsNotNone(spot)
                self.assertEqual((spot.frequency,spot.dx,spot.date,spot.time,spot.comment,spot.spotter),
                                 ('14074.0','K1ABC','30-Oct-2026','1234Z',expected,'W1AW'))

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required for the pinned DXSpider formatter')
    def test_cc11_spot_from_pinned_dxspider_formatter(self):
        fields=['CQ FT8','LZ3ZZ','291','212','NODE^ONE','8','5','28','20','MA','TX']
        result=subprocess.run(['perl','tools/check-dxspider-ve7cc-spot.pl'],
            input=json.dumps({'frequency':14074.0,'dx':'K1ABC','fields':fields}),
            text=True,capture_output=True,cwd=Path(__file__).resolve().parent,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        line=json.loads(result.stdout)['line']
        spot=parse_spot(line,'DXSpider')
        self.assertIsNotNone(spot)
        self.assertEqual((spot.frequency,spot.dx,spot.date,spot.time,spot.comment,spot.spotter),
                         ('14074.0','K1ABC','30-Oct-2026','1234Z','CQ FT8','LZ3ZZ'))
        self.assertEqual((spot.dx_dxcc,spot.spotter_dxcc,spot.origin,spot.dx_itu,spot.dx_cq,
                          spot.spotter_itu,spot.spotter_cq,spot.dx_state,spot.spotter_state),
                         ('291','212','NODE~ONE','8','5','28','20','MA','TX'))
        self.assertEqual((spot.dx_country,spot.spotter_country),('United States','Bulgaria'))

    def test_dialect_precedes_spot_in_same_login_read(self):
        class Socket:
            def __init__(self):
                self.reads=iter((b'login: ',b'LZ3ZZ de NODE dxspider >\r\nCC11^14074^K1ABC^30-Oct-2026^1234Z^CQ^W1AW^291\r\n',b''))
                self.sent=[]
            def settimeout(self,value):pass
            def recv(self,size):return next(self.reads)
            def sendall(self,data):self.sent.append(data)
            def close(self):pass
        sock=Socket();events=[];connection=Connection(lambda kind,value:events.append((kind,value)))
        with patch('protocol.socket.create_connection',return_value=sock):connection._run('node.invalid',7300,'LZ3ZZ')
        dialect_index=events.index(('dialect','DXSpider'))
        spot_index=next(i for i,(kind,value) in enumerate(events) if kind=='data' and 'CC11^' in value)
        self.assertLess(dialect_index,spot_index)
        self.assertEqual(sum(kind=='dialect' for kind,_ in events),1)
        spot=parse_spot(events[spot_index][1].splitlines()[-1],events[dialect_index][1])
        self.assertEqual(spot.dx_dxcc,'291')
        self.assertEqual(sock.sent,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])

    def test_batch_queue_admission_is_all_or_none(self):
        connection=Connection(lambda *_:None); connection.sock=object(); connection.ready=True; connection.commands=queue.Queue(maxsize=1)
        with self.assertRaises(ValueError): connection.send_batch(['TALK A hi','bad\ncommand'])
        self.assertTrue(connection.commands.empty())
        connection.send_batch(['TALK A hi','TALK B hi'])
        with self.assertRaises(OSError): connection.send_batch(['TALK C hi','TALK D hi'])
        self.assertEqual(connection.commands.get_nowait(),('TALK A hi','TALK B hi'))

    def test_dxspider_budget_includes_startup_sent_and_queued_commands(self):
        connection=Connection(lambda *_:None);connection.sock=object();connection.ready=True;connection.dialect='DXSpider'
        with patch('protocol.time.monotonic',return_value=100):
            connection._command_sent(queued=False);connection._command_sent(queued=False)
            connection.send_batch(['sh/dx']*10);connection.send_batch(['sh/dx']*8)
            with self.assertRaisesRegex(OSError,'Wait before trying again'):connection.send('sh/dx')

            self.assertEqual(connection.pending_commands,18)
            for _ in range(10):connection._command_sent(queued=True)
        with patch('protocol.time.monotonic',return_value=122):
            connection.send_batch(['sh/dx']*10)
            with self.assertRaises(OSError):connection.send_batch(['sh/dx']*3)
            self.assertEqual(connection.pending_commands,18)

    def test_cc_commands_are_not_subject_to_dxspider_budget(self):
        connection=Connection(lambda *_:None);connection.sock=object();connection.ready=True;connection.dialect='CC Cluster'
        for _ in range(3):connection.send_batch(['sh/dx']*10)
        self.assertEqual(connection.pending_commands,30)

    def test_dxspider_counts_embedded_command_separator_and_mail(self):
        connection=Connection(lambda *_:None);connection.sock=object();connection.ready=True;connection.dialect='DXSpider'
        connection.send_batch(['sh/dx']*10)
        connection.send_batch(['sh/dx']*8)
        with self.assertRaisesRegex(OSError,'command limit'):
            connection.send(r'sh/dx\nsh/dx\nsh/dx')
        connection.send(r'sh/dx\nsh/dx')
        self.assertEqual(connection.pending_commands,20)
        with self.assertRaisesRegex(OSError,'command limit'):
            connection.send_mail('K1ABC','subject','body')
        self.assertFalse(connection.mail_busy)

    def test_dxspider_burst_limit_keeps_receiving_spots(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(5)
        port=listener.getsockname()[1];received=[];errors=[];release=threading.Event()
        def serve():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(5);sock.sendall(b'login: ');stream.readline()
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    received.extend(stream.readline() for _ in range(20))
                    sock.sendall(b'CC11^14074^K1ABC^30-Oct-2026^1234Z^CQ^W1AW^226\r\n')
                    release.wait(5)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=serve,daemon=True);worker.start()
        events=[];connection=Connection(lambda kind,value:events.append((kind,value)))
        try:
            connection.start('127.0.0.1',port,'LZ3ZZ');deadline=time.monotonic()+5
            while ('status','Connected') not in events and time.monotonic()<deadline:time.sleep(.01)
            self.assertIn(('status','Connected'),events)
            connection.send_batch(['sh/dx']*10);connection.send_batch(['sh/dx']*8)
            with self.assertRaisesRegex(OSError,'command limit'):connection.send('sh/dx')
            deadline=time.monotonic()+5
            while not any(k=='data' and 'K1ABC' in v for k,v in events) and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(any(k=='data' and 'K1ABC' in v for k,v in events))
            self.assertEqual(received,[b'set/ve7cc\r\n',b'sh/mydx/100\r\n']+[b'sh/dx\r\n']*18)
        finally:
            release.set();connection.close();connection.thread.join(3);worker.join(3)
        self.assertEqual(errors,[]);self.assertFalse(worker.is_alive())

    def test_batch_reaches_only_the_local_test_node(self):
        listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(); seen=[]; errors=[]
        def server():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(4); f=sock.makefile('rb');sock.sendall(b'login: ');f.readline()
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    f.readline();f.readline()
                    seen.extend([f.readline(),f.readline()])
            except Exception as exc: errors.append(exc)
            finally: listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        events=[]; connection=Connection(lambda kind,value:events.append((kind,value)))
        connection.start('127.0.0.1',listener.getsockname()[1],'LZ3ZZ'); deadline=time.monotonic()+4
        while ('status','Connected') not in events and time.monotonic()<deadline:time.sleep(.01)
        self.assertIn(('status','Connected'),events)
        connection.send_batch(['TALK K1ABC hello','TALK W1AW hello'])
        worker.join(4);connection.close();connection.thread.join(2)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
        self.assertEqual(seen,[b'TALK K1ABC hello\r\n',b'TALK W1AW hello\r\n'])
    def test_negotiation_can_cross_reads(self):
        decoder=TelnetDecoder()
        self.assertEqual(decoder.feed(b'hello\xff\xfb'), (b'hello',b''))
        self.assertEqual(decoder.feed(b'\x01!'), (b'!',b'\xff\xfe\x01'))

    def test_subnegotiation_and_literal_iac(self):
        decoder=TelnetDecoder()
        self.assertEqual(decoder.feed(b'a\xff\xfa\x01x\xff'), (b'a',b''))
        self.assertEqual(decoder.feed(b'\xf0b\xff\xff'), (b'b\xff',b''))

    def test_telnet_cr_nul_and_commands_across_reads(self):
        decoder=TelnetDecoder()
        self.assertEqual(decoder.feed(b'one\r'),(b'one\r',b''))
        self.assertEqual(decoder.feed(b'\xff\xfb\x01\x00two\r\nthree\x00'),(b'two\r\nthree\x00',b'\xff\xfe\x01'))
        self.assertEqual(decoder.feed(b'\r\xff\xff\x00'),(b'\r\xff\x00',b''))

    def test_password_escapes_telnet_iac_on_the_wire(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();seen=[];errors=[]
        def server():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(4);stream=sock.makefile('rb');sock.sendall(b'login: ');seen.append(stream.readline())
                    sock.sendall(b'password: ');seen.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n');seen.extend([stream.readline(),stream.readline()])
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        events=[];connection=Connection(lambda kind,value:events.append((kind,value)))
        connection.start('127.0.0.1',listener.getsockname()[1],'LZ3ZZ')
        deadline=time.monotonic()+4
        while ('password_required',None) not in events and time.monotonic()<deadline:time.sleep(.01)
        self.assertIn(('password_required',None),events);connection.provide_password('aÿb')
        worker.join(4);connection.close();connection.thread.join(2)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
        self.assertEqual(seen,[b'LZ3ZZ\r\n',b'a\xff\xffb\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])

    def test_observed_spot_formats(self):
        expected=Spot('LZ3ZZ','14074.0','K1ABC','2300Z','CQ','29-Oct-2026')
        short = parse_spot('CC11^14074.0^K1ABC^29-Oct-2026^2300Z^CQ^LZ3ZZ^25', 'DXSpider')
        self.assertEqual((short.spotter, short.frequency, short.dx, short.date, short.dx_dxcc), ('LZ3ZZ','14074.0','K1ABC','29-Oct-2026','25'))
        extended = parse_spot('CC11^14290.0^TI5VK^ 1-Oct-2026^2332Z^CQ~DX^RA9V^168^182^W3LPL^11^7^31^18^^^Alajuela-TI^Kemerovskaya-UA9^^', 'DXSpider')
        self.assertEqual((extended.dx_dxcc, extended.spotter_dxcc, extended.origin, extended.dx_country, extended.spotter_country), ('168','182','W3LPL','Alajuela-TI','Kemerovskaya-UA9'))
        self.assertEqual(parse_spot('  14074.0  K1ABC  29-Oct-2026 2300Z  CQ  <LZ3ZZ>'), expected)
        self.assertEqual(parse_spot('login: DX de LZ3ZZ:  14074.0  K1ABC  CQ 2300Z'), Spot('LZ3ZZ','14074.0','K1ABC','2300Z','CQ'))
        self.assertIsNone(parse_spot('CC11^not-a-frequency^K1ABC^date^time^comment^LZ3ZZ'))
        self.assertIsNone(parse_spot('CC11^NaN^K1ABC^date^time^comment^LZ3ZZ'))
        self.assertIsNone(parse_spot('not a spot'))
        self.assertIsNone(parse_spot('DX de LZ3ZZ: 1..2 K1ABC CQ 2300Z'))

    def test_dxspider_plain_spot_with_node_beep_is_recognized(self):
        line='DX de LZ3ZZ:  14074.0  K1ABC        FT8 signal                     2300Z\a\a'
        spot=parse_spot(line,'DXSpider')
        self.assertEqual((spot.spotter,spot.frequency,spot.dx,spot.time,spot.comment.strip()),('LZ3ZZ','14074.0','K1ABC','2300Z','FT8 signal'))
        self.assertIsNone(parse_spot('DX de LZ3ZZ: 14074.0 K1ABC CQ 2300Zbroken','DXSpider'))

    def test_plain_spot_can_have_no_comment(self):
        for line in ('DX de W1AW: 14074 K1ABC 1234Z',
                     'DX de W1AW: 14074 K1ABC   1234z\a'):
            with self.subTest(line=line):
                spot=parse_spot(line,'DXSpider')
                self.assertEqual((spot.spotter,spot.frequency,spot.dx,spot.time.upper(),spot.comment),
                                 ('W1AW','14074','K1ABC','1234Z',''))
        self.assertIsNone(parse_spot('DX de W1AW: 14074 K1ABC 2460Z'))

    def test_plain_spot_comment_can_contain_an_earlier_utc_time(self):
        spot=parse_spot('DX de W1AW: 14074.0 K1ABC QRT 1234Z then QRV 1430Z FN31','DXSpider')
        self.assertEqual((spot.time,spot.comment),('1430Z','QRT 1234Z then QRV'))
        self.assertIsNone(parse_spot('DX de W1AW: 14074.0 K1ABC QRV 1234Z then 2460Z'))

    def test_spot_times_require_real_utc_hours_and_minutes(self):
        formats = (
            'CC11^14074^K1ABC^29-Oct-2026^{time}^CQ^LZ3ZZ^25',
            'PC11^14074^K1ABC^29-Oct-2026^{time}^CQ^LZ3ZZ^NODE^',
            'DX de LZ3ZZ: 14074 K1ABC CQ {time}',
            '14074 K1ABC 29-Oct-2026 {time} CQ <LZ3ZZ>',
        )
        for template in formats:
            for value in ('0000Z', '2359Z'):
                with self.subTest(format=template, value=value):
                    self.assertIsNotNone(parse_spot(template.format(time=value)))
            for value in ('2400Z', '1260Z', '9999Z'):
                with self.subTest(format=template, value=value):
                    self.assertIsNone(parse_spot(template.format(time=value)))
        self.assertIsNotNone(parse_spot('CC11^14074^K1ABC^29-Oct-2026^2359^CQ^LZ3ZZ^25'))
        self.assertIsNotNone(parse_spot('PC11^14074^K1ABC^29-Oct-2026^2359^CQ^LZ3ZZ^NODE^'))

    def test_cc11_is_not_extracted_from_message_text(self):
        packet='CC11^14074.0^K1ABC^29-Oct-2026^2300Z^CQ^LZ3ZZ^25'
        self.assertEqual(parse_spot('  '+packet+'\r\n','DXSpider').dx,'K1ABC')
        for line in ('To ALL de K1ABC: Example '+packet, 'LZ3ZZ de K1ABC: '+packet,
                     'other text '+packet, 'To ALL de K1ABC: DX de LZ3ZZ: 14074 K1ABC CQ 2300Z'):
            with self.subTest(line=line):self.assertIsNone(parse_spot(line,'DXSpider'))

    def test_pc11_and_pc61_wire_spots_preserve_fields(self):
        pc11='PC11^14074.05^K1ABC^30-Oct-2026^1234Z^CQ~DX^LZ3ZZ^NODE^H50^~'
        spot=parse_spot(pc11,'DXSpider')
        self.assertEqual((spot.frequency,spot.dx,spot.date,spot.time,spot.comment,spot.spotter,spot.origin),('14074.05','K1ABC','30-Oct-2026','1234Z','CQ~DX','LZ3ZZ','NODE'))
        self.assertEqual(spot.extra_fields,('H50','~'))
        pc61='PC61^7074.0^W1AW^30-Oct-2026^1235^FT8^K1ABC^NODE^192.0.2.1^H50^~'
        spot=parse_spot(pc61)
        self.assertEqual((spot.time,spot.origin,spot.extra_fields),('1235Z','NODE',('192.0.2.1','H50','~')))
        self.assertIsNotNone(parse_spot('PC11^14074^K1ABC^30-Oct-2026^1234Z^CQ^LZ3ZZ^NODE^'))
        for line in ('PC11^bad^K1ABC^30-Oct-2026^1234Z^CQ^LZ3ZZ^NODE^', 'PC61^7074^K1ABC^30-Oct-2026^1234Z^CQ^LZ3ZZ^NODE^', 'PC11^7074^K1ABC^bad^1234Z^CQ^LZ3ZZ^NODE^', 'PC11^7074^K1ABC^30-Oct-2026^1234Z^CQ^LZ3ZZ^NODE'):
            with self.subTest(line=line):self.assertIsNone(parse_spot(line))

    def test_pc26_merged_spot_preserves_node_field_without_claiming_origin(self):
        line='PC26^14074.0^K1ABC^30-Oct-2026^1234Z^CQ~DX^LZ3ZZ^REQUESTNODE^ ^~'
        spot=parse_spot(line,'DXSpider')
        self.assertEqual((spot.frequency,spot.dx,spot.date,spot.time,spot.comment,spot.spotter),('14074.0','K1ABC','30-Oct-2026','1234Z','CQ~DX','LZ3ZZ'))
        self.assertEqual(spot.origin,'');self.assertEqual(spot.extra_fields,('REQUESTNODE',' ','~'))
        for invalid in (line.replace('^14074.0^','^bad^'),line.replace('^1234Z^','^bad^'),line[:-2], 'To ALL de A: '+line):
            with self.subTest(invalid=invalid):self.assertIsNone(parse_spot(invalid,'DXSpider'))

    def test_dxspider_extended_spot_fields(self):
        line = 'CC11^14074.0^K1ABC^29-Oct-2026^2300Z^CQ~test^LZ3ZZ^291^212^NODE^8^5^28^20^MA^^United States^Bulgaria^FN42^KN12^future'
        spot = parse_spot(line, 'DXSpider')
        self.assertEqual((spot.dx_state, spot.spotter_state, spot.dx_country, spot.spotter_country), ('MA', '', 'United States', 'Bulgaria'))
        self.assertEqual((spot.dx_locator, spot.spotter_locator, spot.extra_fields), ('FN42', 'KN12', ('future',)))

    def test_cc_cluster_does_not_mislabel_different_fields_as_dxcc(self):
        line = 'CC11^14058.1^K6DF^29-Oct-2026^2350Z^CW 27 dB^K5EM-#^1^^SC-CW-RTTY^6^3^6^3^CA^WA^K^K^^CN87^24^^'
        spot = parse_spot(line, 'CC Cluster')
        self.assertEqual((spot.dx_dxcc, spot.spotter_dxcc), ('', ''))
        self.assertEqual(spot.metadata_fields[:3], ('1', '', 'SC-CW-RTTY'))
        self.assertEqual((spot.dx_country, spot.dx_state, spot.spotter_locator), ('K', 'CA', 'CN87'))

    def test_band_selection(self):
        self.assertEqual(band_for('14074.0'), '20 m')
        self.assertEqual(band_for('18100'), '17 m')
        for freq, expected in [('220000','1.25 m'),('224000','1.25 m'),('420000','70 cm'),('450000','70 cm'),('500000','Microwave'),('21000000','Microwave'),('450001',''),('21000001','')]:
            with self.subTest(frequency=freq): self.assertEqual(band_for(freq),expected)
        self.assertEqual(band_for('garbage'), '')

    def test_settings_readback_accepts_only_known_settings(self):
        self.assertEqual(parse_setting('Req FT8   Spots: No'), ('Req FT8   Spots', 'No'))
        self.assertEqual(parse_setting('      Home Node: '), ('Home Node', ''))
        self.assertIsNone(parse_setting('DX de SOMEONE: message'))

    def test_only_verified_cc_settings_have_commands(self):
        self.assertEqual(setting_command('CC Cluster','Req DX Spots',False),'set/nodx')
        self.assertEqual(setting_command('CC Cluster','Req SkimmerSpot',True),'set/skimmer')
        self.assertEqual(setting_command('CC Cluster','Req Beacon Spot',True),'set/beacon')
        self.assertEqual(setting_command('CC Cluster','Req Beacon Spot',False),'set/nobeacon')
        self.assertEqual(setting_command('CC Cluster','Req FT8   Spots',True),'set/ft8')
        self.assertEqual(setting_command('CC Cluster','Req WX',False),'set/nowx')
        for name, on, off in [('Req BoB Spots','set/bob','set/nobob'), ('Req Self Spots','set/self','set/noself'), ('Req Skimmer Own','set/own','set/noown'), ('Req Beep','set/beep','set/nobeep'), ('Login Info Req','set/login','set/nologin')]:
            with self.subTest(name=name):
                self.assertEqual(setting_command('CC Cluster',name,True),on)
                self.assertEqual(setting_command('CC Cluster',name,False),off)
        self.assertEqual(setting_command('DXSpider','Req DX Spots',True),'set/dx')
        self.assertEqual(setting_command('DXSpider','Req DX Spots',False),'unset/dx')
        self.assertEqual(setting_command('DXSpider','Req WWV',False),'unset/wwv')
        with self.assertRaises(ValueError): setting_command('DXSpider','Req FT8   Spots',True)
        with self.assertRaises(ValueError): setting_command('CC Cluster','Req Echo',True)

    def test_dxspider_setting_response_requires_exact_login(self):
        self.assertEqual(parse_spider_setting('DX Spots enabled for LZ3ZZ','lz3zz'),('Req DX Spots','Yes'))
        self.assertEqual(parse_spider_setting('WWV disabled LZ3ZZ','LZ3ZZ'),('Req WWV','No'))
        for name, token in [('Req DX Spots','dx'),('Req Announce','announce'),('Req Talk','talk'),('Req WWV','wwv'),('Req WCY','wcy'),('Req WX','wx')]:
            with self.subTest(name=name):
                self.assertEqual(setting_command('DXSpider',name,True),'set/'+token)
                self.assertEqual(setting_command('DXSpider',name,False),'unset/'+token)
        self.assertIsNone(parse_spider_setting('Talk enabled for LZ3ZZ-1','LZ3ZZ'))
        self.assertIsNone(parse_spider_setting('To ALL de K1ABC: Talk enabled for LZ3ZZ','LZ3ZZ'))

    def test_dxspider_spot_display_settings_and_responses(self):
        cases = [('Spider CQ display','dxcq','DX CQ Zones'), ('Spider ITU display','dxitu','DX ITU Zones'),
                 ('Spider grid display','dxgrid','DX Grid'), ('Spider US state display','usstate','US State display')]
        for name, token, response in cases:
            with self.subTest(name=name):
                self.assertEqual(setting_command('DXSpider', name, True), 'set/'+token)
                self.assertEqual(setting_command('DXSpider', name, False), 'unset/'+token)
                self.assertEqual(parse_spider_setting(response+' enabled for LZ3ZZ', 'LZ3ZZ'), (name,'Yes'))
                self.assertEqual(parse_spider_setting(response+' disabled for LZ3ZZ', 'LZ3ZZ'), (name,'No'))
                self.assertIsNone(parse_spider_setting(response+' enabled for OTHER', 'LZ3ZZ'))

    def test_spots_sort_by_date_and_time_across_midnight(self):
        old = Spot('A','14000','B','2359Z','','28-Oct-2026')
        new = Spot('A','14000','B','0001Z','','29-Oct-2026')
        self.assertGreater(spot_order(new), spot_order(old))

    def test_login_prompt_recognition_requires_a_complete_prompt_line(self):
        for line in ('To ALL de K1ABC: try dxspider > later', 'This node runs dxspider > today', 'LZ3ZZ de NODE dxspider > more text'):
            with self.subTest(line=line):self.assertIsNone(PROMPT.search(line))
        for line,dialect in [('LZ3ZZ de NODE 30-Oct-2026 1234Z dxspider >','dxspider'),
                             ('(LZ3ZZ) de NODE 30-Oct-2026 1234Z dxspider >','dxspider'),
                             ('NODE CCC >','CCC'),('login: LZ3ZZ de NODE CCC >','CCC')]:
            with self.subTest(line=line):
                match=PROMPT.search(line);self.assertIsNotNone(match);self.assertEqual(match.group(1).lower(),dialect.lower())

        self.assertIsNone(PROMPT.search('(LZ3ZZ) de NODE CCC >'))

    def test_login_accepts_split_dxspider_away_prompt(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        port=listener.getsockname()[1];seen=[];errors=[]
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(5);sock.sendall(b'login: ');seen.append(stream.readline())
                    sock.sendall(b'(LZ3ZZ) de NODE 30-Oct-2026 1234Z dxspi');time.sleep(.03)
                    sock.sendall(b'der >\r\n');seen.extend((stream.readline(),stream.readline()))
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start();events=[]
        connection=Connection(lambda kind,value:events.append((kind,value)))
        connection.start('127.0.0.1',port,'LZ3ZZ');worker.join(6);connection.thread.join(6)
        self.assertFalse(worker.is_alive());self.assertFalse(connection.thread.is_alive());self.assertEqual(errors,[])
        self.assertIn(('dialect','DXSpider'),events);self.assertIn(('status','Connected'),events)
        self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])

    def test_banner_prompt_words_do_not_start_session(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();seen=[];errors=[]
        def server():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(3);stream=sock.makefile('rb');sock.sendall(b'login: ');seen.append(stream.readline())
                    sock.sendall(b'To ALL de K1ABC: try CCC > later\r\nThis node runs dxspider > today\r\n')
                    sock.settimeout(.5)
                    try:seen.append(sock.recv(100))
                    except socket.timeout:seen.append(b'')
                    sock.settimeout(3);sock.sendall(b'LZ3ZZ de NODE dxspi');time.sleep(.03);sock.sendall(b'der >\r\n')
                    seen.extend([stream.readline(),stream.readline()])
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start();events=[]
        connection=Connection(lambda kind,value:events.append((kind,value)))
        try:
            connection.start('127.0.0.1',listener.getsockname()[1],'LZ3ZZ');worker.join(5)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(seen,[b'LZ3ZZ\r\n',b'',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])
            self.assertIn(('dialect','DXSpider'),events)
        finally:
            connection.close()
            if connection.thread:connection.thread.join(2)

    def test_split_login_and_startup_commands(self):
        listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen()
        seen=[]; errors=[]
        def server():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(3)
                    sock.sendall(b'Welcome\r\nlo'); time.sleep(.03); sock.sendall(b'gin: ')
                    f=sock.makefile('rb')
                    seen.append(f.readline())
                    sock.sendall(b'Hello\r\nLZ3ZZ de N2WQ 29-Oct-2026 2333Z C')
                    time.sleep(.03); sock.sendall(b'CC >\r\n')
                    for _ in range(3): seen.append(f.readline())
                    sock.sendall(b'CC11^14074.0^K1ABC^29-Oct-2026^2300Z^CQ^LZ3ZZ^25\r\n')
                    seen.append(f.readline())
            except Exception as exc: errors.append(exc)
            finally: listener.close()
        thread=threading.Thread(target=server,daemon=True); thread.start()
        events=[]; connection=Connection(lambda kind,value:events.append((kind,value)))
        connection.start('127.0.0.1',listener.getsockname()[1],'LZ3ZZ')
        deadline=time.monotonic()+4
        while ('status','Connected') not in events and time.monotonic()<deadline: time.sleep(.01)
        self.assertIn(('status','Connected'),events)
        connection.send('sh/dx/5'); thread.join(4); connection.close(); connection.thread.join(2)
        self.assertFalse(thread.is_alive()); self.assertEqual(errors,[])
        self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/settings\r\n',b'sh/dx/5\r\n'])
        self.assertIn('CC11^', ''.join(v for k,v in events if k=='data'))
        self.assertEqual(events[-1], ('status','Disconnected'))

    def test_dxspider_does_not_request_unsupported_settings(self):
        listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen()
        seen=[]
        def server():
            sock,_=listener.accept()
            with sock:
                sock.settimeout(3); sock.sendall(b'login: '); f=sock.makefile('rb')
                seen.append(f.readline()); sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                seen.extend([f.readline(), f.readline()]); sock.settimeout(.4)
                try: seen.append(sock.recv(100))
                except socket.timeout: pass
            listener.close()
        thread=threading.Thread(target=server,daemon=True); thread.start()
        events=[]; connection=Connection(lambda kind,value:events.append((kind,value)))
        connection.start('127.0.0.1',listener.getsockname()[1],'LZ3ZZ')
        thread.join(4); connection.close(); connection.thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])
        self.assertIn(('dialect', 'DXSpider'), events)

    def test_password_prompt_and_echo_are_not_exposed_to_activity(self):
        listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen()
        seen=[]; errors=[]
        def server():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(4); f=sock.makefile('rb')
                    sock.sendall(b'login: '); seen.append(f.readline())
                    sock.sendall(b'pass'); time.sleep(.04); sock.sendall(b'word: ')
                    seen.append(f.readline())
                    # A poorly behaved node might echo the password in fragments.
                    sock.sendall(b'se'); time.sleep(.04); sock.sendall(b'cret\r\nLZ3ZZ de NODE dxspider >\r\n')
                    seen.extend([f.readline(), f.readline()])
            except Exception as exc: errors.append(exc)
            finally: listener.close()
        thread=threading.Thread(target=server,daemon=True); thread.start()
        events=[]; connection=Connection(lambda kind,value:events.append((kind,value)))
        connection.start('127.0.0.1',listener.getsockname()[1],'LZ3ZZ')
        deadline=time.monotonic()+4
        while ('password_required',None) not in events and time.monotonic()<deadline: time.sleep(.01)
        self.assertIn(('password_required',None), events)
        with self.assertRaises(ValueError): connection.provide_password('secret\nbye')
        connection.provide_password('secret')
        thread.join(4); connection.close(); connection.thread.join(2)
        self.assertFalse(thread.is_alive()); self.assertEqual(errors,[])
        self.assertEqual(seen,[b'LZ3ZZ\r\n',b'secret\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])
        self.assertNotIn('secret',''.join(str(v) for _,v in events))
        self.assertIn(('status','Connected'),events)
        self.assertIn(('notice','Password-login output was hidden; spots in that response may be missing. Recent history was requested.'),events)

if __name__ == '__main__': unittest.main()
