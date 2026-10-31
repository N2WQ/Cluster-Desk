import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import app


class FilterTransportTests(unittest.TestCase):
    def test_marker_filter_request_and_response_over_socket(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(8)
        port=listener.getsockname()[1];commands=[];errors=[];release=threading.Event()
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(8);sock.sendall(b'login: ');commands.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    commands.extend(stream.readline() for _ in range(3))
                    sock.sendall(b'Filter command received\r\nLZ3ZZ de NODE dxspider >\r\n');release.wait(8)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ');window.connect()
                deadline=time.monotonic()+8
                while window.status.get()!='Connected' and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(window.status.get(),'Connected')
                window.spider_filter_kind.set('Spotter callsign text');window.spider_expression.set('-#');window.change_filter('reject')
                deadline=time.monotonic()+8
                while 'Filter command received' not in window.transcript.get('1.0','end') and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertIn('Filter command received',window.transcript.get('1.0','end'))
                self.assertEqual(commands,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'reject/spots 1 by {\\-\\#}\r\n'])
            finally:release.set();window.quit_app();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])


    def test_cc_time_filter_request_and_response_reach_activity(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(8)
        port=listener.getsockname()[1];commands=[];errors=[];release=threading.Event()
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(8);sock.sendall(b'login: ');commands.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE CCC >\r\n')
                    commands.extend(stream.readline() for _ in range(4))
                    sock.sendall(b'BandTime Filter set OFF\r\nLZ3ZZ de NODE CCC >\r\n');release.wait(8)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ');window.connect_btn.invoke()
                deadline=time.monotonic()+8
                while window.status.get()!='Connected' and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(window.status.get(),'Connected');window.show_cc_bandtime();deadline=time.monotonic()+8
                while 'BandTime Filter set OFF' not in window.transcript.get('1.0','end') and time.monotonic()<deadline:
                    window.update();time.sleep(.01)
                self.assertIn('BandTime Filter set OFF',window.transcript.get('1.0','end'))
                self.assertEqual(window.filter_readback.state,'complete')
                self.assertIn('BandTime Filter set OFF',window.filter_readback_text.get('1.0','end'))
                self.assertEqual(commands,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/settings\r\n',b'sh/filter bandtime\r\n'])
            finally:release.set();window.quit_app();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_local_node_french_empty_filter_readback_with_away_prompt(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(8)
        port=listener.getsockname()[1];commands=[];errors=[];release=threading.Event()
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(8);sock.sendall(b'login: ');commands.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    commands.extend((stream.readline(),stream.readline(),stream.readline()))
                    sock.sendall('Pas de filtres d\u00e9finis pour LZ3ZZ\r\n(LZ3ZZ) de NODE dxspider >\r\n'.encode('cp1252'))
                    release.wait(8)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:None
            try:
                window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ');window.connect_btn.invoke()
                deadline=time.monotonic()+8
                while window.status.get()!='Connected' and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(window.status.get(),'Connected');window.show_filters();deadline=time.monotonic()+8
                while window.filter_readback.state!='complete' and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(window.filter_readback.state,'complete')
                self.assertEqual(window.filter_readback.text(),'Pas de filtres d\u00e9finis pour LZ3ZZ')
                self.assertEqual(commands,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/filter\r\n'])
            finally:
                release.set();window.quit_app();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required for the pinned DXSpider parser')
    def test_simple_text_filter_reaches_dxspider_parser_over_local_connection(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(8)
        port = listener.getsockname()[1]
        commands, matches, errors = [], [], []
        release = threading.Event()

        def server():
            try:
                sock, _ = listener.accept()
                with sock:
                    sock.settimeout(8)
                    with sock.makefile('rb') as stream:
                        sock.sendall(b'login: ')
                        commands.append(stream.readline())
                        sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                        commands.extend(stream.readline() for _ in range(3))
                        expression = commands[-1].decode('ascii').strip().removeprefix('reject/spots 1 ')
                        rows = []
                        for value in ('CQ DX', 'CQ  DX', '14.2', '14X2'):
                            row = [''] * 17
                            row[3] = value
                            rows.append(row)
                        result = subprocess.run(['perl', 'tools/check-dxspider-filter.pl'],
                            input=json.dumps({'expression': expression, 'rows': rows}),
                            capture_output=True, text=True, cwd=Path(__file__).resolve().parent, timeout=8)
                        if result.returncode:
                            raise RuntimeError(result.stderr)
                        matches.extend(json.loads(result.stdout)['matches'])
                        commands.append(stream.readline())
                        sock.sendall(b'LZ3ZZ: spots\r\n  1 reject info text\r\nLZ3ZZ de NODE dxspider >\r\n')
                        sock.sendall(b'CC11^14074.0^K1ABC^30-Oct-2026^1234Z^CQ^W3ABC^291\r\n')
                        release.wait(8)
            except Exception as exc:
                errors.append(exc)
            finally:
                listener.close()

        worker = threading.Thread(target=server, daemon=True)
        worker.start()
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'SETTINGS', Path(directory) / 'settings.json'):
            window = app.App()
            window.withdraw()
            window._save_settings = lambda: None
            try:
                window.host.set('127.0.0.1')
                window.port.set(str(port))
                window.call.set('LZ3ZZ')
                window.auto_reconnect.set(False)
                window.connect_btn.invoke()
                deadline = time.monotonic() + 8
                while window.status.get() != 'Connected' and time.monotonic() < deadline:
                    window.update()
                    time.sleep(.01)
                self.assertEqual(window.status.get(), 'Connected')
                window.spider_filter_kind.set('Spot comment text')
                window.spider_expression.set('CQ DX,14.2')
                window.change_filter('reject')
                window.show_filters()
                deadline = time.monotonic() + 8
                while (window.filter_readback.state != 'complete' or not window.rows) and time.monotonic() < deadline:
                    window.update()
                    time.sleep(.01)
                self.assertEqual(window.filter_readback.state, 'complete')
                self.assertEqual(window.rows[-1][1].dx, 'K1ABC')
                self.assertEqual(matches, [1, 0, 1, 0])
                self.assertEqual(commands, [b'LZ3ZZ\r\n', b'set/ve7cc\r\n', b'sh/mydx/100\r\n',
                    b'reject/spots 1 info {CQ\\ DX},{14\\.2}\r\n', b'sh/filter\r\n'])
            finally:
                release.set()
                window.quit_app()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
