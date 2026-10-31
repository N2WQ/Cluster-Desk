import socket
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class SpotStreamTransportTests(unittest.TestCase):
    def test_line_limit_excludes_crlf_even_when_split_across_reads(self):
        class Node:
            def __init__(self, emit): self.emit = emit
            def start(self, *_): pass
            def close(self): pass

        def record(call, size):
            start=f'CC11^14074^{call}^30-Oct-2026^1234Z^'
            end='^W1AW^226'
            return start + 'X' * (size - len(start) - len(end)) + end

        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'SETTINGS', Path(directory)/'settings.json'), patch('app.Connection', Node):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                ident=window.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                good=record('K1ABC',8192);too_long=record('K2ABC',8193)
                window.after_cancel(window.poll_after)
                window.events.put((window.generation,'data',good+'\r'))
                item['connection'].emit('data',good+'\r')
                window.poll()
                self.assertEqual(len(window.rows),0)
                window.after_cancel(window.poll_after)
                window.events.put((window.generation,'data','\n'+too_long+'\r\n'))
                item['connection'].emit('data','\n'+too_long+'\r\n')
                window.poll()
                self.assertEqual([spot.dx for _,spot in window.rows],['K1ABC','K1ABC'])
                self.assertIn('overlong cluster line',window.transcript.get('1.0','end'))
                self.assertIn('overlong cluster line',item['activity'])
            finally:window.quit_app()

    def test_main_and_additional_backlogs_both_yield_and_drain(self):
        class Node:
            def __init__(self,emit):self.emit=emit
            def start(self,*_):pass
            def close(self):pass
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'),patch('app.Connection',Node):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                ident=window.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                item['connection'].emit('dialect','DXSpider');item['connection'].emit('status','Connected');window.poll_secondary_nodes()
                line='CC11^14074^K1ABC^30-Oct-2026^1234Z^CQ^W1AW^226\r\n'
                for _ in range(100):
                    window.events.put((window.generation,'data',line*60))
                    item['connection'].emit('data',line*60)
                observed=[];window.after(0,lambda:observed.append(window.next_spot_id))
                window.after_cancel(window.poll_after);window.poll()
                self.assertEqual(window.next_spot_id,2400)
                self.assertEqual((window.events.qsize(),window.secondary_events.qsize()),(80,80))
                deadline=time.monotonic()+15
                while window.next_spot_id<12000 and time.monotonic()<deadline:window.update();time.sleep(.001)
                self.assertEqual(observed,[2400]);self.assertEqual(window.next_spot_id,12000)
                self.assertEqual((window.events.qsize(),window.secondary_events.qsize()),(0,0))
            finally:window.quit_app()

    def test_large_backlog_yields_to_ui_and_drains_without_losing_spots(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                for block in range(100):
                    records=''.join(f'CC11^14074^K{block*60+i:04}AA^30-Oct-2026^1234Z^CQ^W1AW^226\r\n' for i in range(60))
                    window.events.put((window.generation,'data',records))
                observed=[];window.after(0,lambda:observed.append(window.next_spot_id))
                window.after_cancel(window.poll_after);window.poll()
                self.assertEqual(window.next_spot_id,1200);self.assertEqual(window.events.qsize(),80)
                deadline=time.monotonic()+10
                while window.next_spot_id<6000 and time.monotonic()<deadline:window.update();time.sleep(.001)
                self.assertEqual(observed,[1200])
                self.assertEqual(window.next_spot_id,6000);self.assertEqual(len(window.rows),1000)
                self.assertEqual({spot.dx for _,spot in window.rows},{f'K{i:04}AA' for i in range(5000,6000)})
            finally:window.quit_app()

    def test_rate_limited_search_retries_and_displays_result_from_local_node(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(8)
        port=listener.getsockname()[1];commands=[];errors=[];release=threading.Event()
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(8);sock.sendall(b'login: ');commands.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n');commands.extend((stream.readline(),stream.readline()))
                    commands.append(stream.readline())
                    sock.sendall(b'CC11^14074^K1ABC^30-Oct-2026^1234Z^CQ^W1AW^226\r\n')
                    release.wait(8)
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
                with window.connection.lock:
                    window.connection.command_times.clear()
                    window.connection.command_times.extend([time.monotonic()-20.5]*20)
                window.search_field.set('DX call');window.search.set('K1ABC');window.search_connected_nodes()
                self.assertIn('1 waiting',window.spot_search_note.get())
                deadline=time.monotonic()+5
                while not window.rows and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual([spot.dx for _,spot in window.rows],['K1ABC'])
                self.assertFalse(window.pending_spot_searches)
                self.assertEqual(commands[-1],b'sh/dx/100 call {K1ABC}\r\n')
                self.assertIn('Delayed search queued',window.transcript.get('1.0','end'))
            finally:release.set();window.quit_app();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_received_warc_and_vhf_uhf_spots_follow_independent_band_buttons(self):
        bands=[('30 m','10136','K1AAA'),('17 m','18100','K2BBB'),('12 m','24915','K3CCC'),
               ('2 m','145500','K4DDD'),('70 cm','432100','K5EEE')]
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(8)
        port=listener.getsockname()[1];errors=[];release=threading.Event()
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(8);sock.sendall(b'login: ');stream.readline()
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n');stream.readline();stream.readline()
                    for _,frequency,call in bands:
                        sock.sendall(f'CC11^{frequency}^{call}^30-Oct-2026^1234Z^CQ^W1AW^226\r\n'.encode('ascii'))
                    release.wait(8)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ');window.connect()
                deadline=time.monotonic()+8
                while len(window.rows)<len(bands) and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(len(window.rows),len(bands))
                for band,_,_ in bands:self.assertEqual(window.band_shortcut_counts[band].cget('text'),'1')
                window.band_shortcut_buttons['30 m'].invoke();window.band_shortcut_buttons['70 cm'].invoke()
                self.assertEqual(window.selected_bands,{'30 m','70 cm'})
                self.assertEqual({window.spots.item(row,'values')[3] for row in window.spots.get_children()},{'K1AAA','K5EEE'})
                window.band_shortcut_buttons['30 m'].invoke()
                self.assertEqual(window.selected_bands,{'70 cm'})
                self.assertEqual([window.spots.item(row,'values')[3] for row in window.spots.get_children()],['K5EEE'])
            finally:release.set();window.quit_app();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_country_prefix_alarm_matches_spots_received_from_node(self):
        from country_lookup import CountryIndex
        from test_country_lookup import DATA
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(8)
        port=listener.getsockname()[1];errors=[];release=threading.Event()
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(8);sock.sendall(b'login: ');stream.readline()
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n');stream.readline();stream.readline()
                    sock.sendall(b'CC11^14074^LZ3ZZ^30-Oct-2026^1234Z^match^W1AW^1\r\nCC11^14074^OA4ABC^30-Oct-2026^1234Z^other^W1AW^1\r\n')
                    release.wait(8)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                window.country_index=CountryIndex.from_text(DATA)
                window.alarm_calls.set('*LZ');window.alarm_country_prefix_mode.set(True);window.alarm_enabled.set(True);window.apply_alarm()
                window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ');window.connect()
                deadline=time.monotonic()+8
                while len(window.rows)<2 and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(len(window.rows),2)
                self.assertEqual([window.alarm_tree.item(row,'values')[1] for row in window.alarm_tree.get_children()],['LZ3ZZ'])
            finally:release.set();window.quit_app();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_secondary_cc_precision_request_keeps_spot_source(self):
        import tkinter as tk
        from tkinter import ttk
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(8)
        port=listener.getsockname()[1];commands=[];errors=[];release=threading.Event()
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(8);sock.sendall(b'login: ');commands.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE CCC >\r\n')
                    commands.extend(stream.readline() for _ in range(4))
                    sock.sendall(b'DX Spot resolution set to 2 digits.\r\nCC11^14074.05^K1ABC^30-Oct-2026^1234Z^CQ^W1AW^291\r\n');release.wait(8)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                ident=window.start_secondary_node('127.0.0.1',port,'LZ3ZZ');item=window.secondary_nodes[ident]
                deadline=time.monotonic()+8
                while item['status']!='Connected' and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(item['status'],'Connected');window.open_secondary_precision(ident)
                dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel) and w.title()=='Spot frequency precision')
                def walk(widget):
                    for child in widget.winfo_children():yield child;yield from walk(child)
                next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='2 decimals').invoke()
                deadline=time.monotonic()+8
                while not window.rows and time.monotonic()<deadline:window.update();time.sleep(.01)
                row,spot=window.rows[0];self.assertEqual(spot.frequency,'14074.05')
                self.assertEqual(window.spot_sources[row],item['source'])
                self.assertIn('DX Spot resolution set to 2 digits.',item['activity'])
                self.assertEqual(commands,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/settings\r\n',b'set/res 2\r\n'])
            finally:release.set();window.quit_app();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_cc_precision_request_and_two_decimal_spot_reach_display(self):
        import tkinter as tk
        from tkinter import ttk
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(8)
        port=listener.getsockname()[1];commands=[];errors=[];release=threading.Event()
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(8);sock.sendall(b'login: ');commands.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE CCC >\r\n')
                    commands.extend(stream.readline() for _ in range(4))
                    sock.sendall(b'DX Spot resolution set to 2 digits.\r\nCC11^14074.05^K1ABC^30-Oct-2026^1234Z^CQ^W1AW^291\r\n');release.wait(8)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ');window.connect_btn.invoke()
                deadline=time.monotonic()+8
                while window.status.get()!='Connected' and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(window.status.get(),'Connected');window.open_node_profile()
                dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel) and w.title()=='My node profile')
                def walk(widget):
                    for child in widget.winfo_children():yield child;yield from walk(child)
                next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='2 decimals').invoke()
                deadline=time.monotonic()+8
                while not window.rows and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(window.rows[0][1].frequency,'14074.05')
                self.assertEqual(window.spots.item(window.spots.get_children()[0],'values')[1],'14074.05')
                self.assertIn('DX Spot resolution set to 2 digits.',window.transcript.get('1.0','end'))
                self.assertEqual(commands,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/settings\r\n',b'set/res 2\r\n'])
            finally:release.set();window.quit_app();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_cc_comment_search_reaches_node_and_result_appears(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(8)
        port=listener.getsockname()[1];commands=[];errors=[];release=threading.Event()
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(8);sock.sendall(b'login: ');commands.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE CCC >\r\n')
                    commands.extend(stream.readline() for _ in range(4))
                    sock.sendall(b'CC11^14074.0^K1ABC^30-Oct-2026^1234Z^CQ FT8^W1AW^291\r\n');release.wait(8)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ');window.connect_btn.invoke()
                deadline=time.monotonic()+8
                while window.status.get()!='Connected' and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(window.status.get(),'Connected')
                window.search_field.set('Comment');window.search.set('FT8');window.search_connected_nodes()
                while not window.spots.get_children() and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(len(window.spots.get_children()),1)
                self.assertEqual(window.spots.item(window.spots.get_children()[0],'values')[3],'K1ABC')
                self.assertEqual(commands,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/settings\r\n',b"sh/dx 'FT8'\r\n"])
            finally:release.set();window.quit_app();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_all_fields_search_reaches_node_and_matching_history_appears(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(8)
        port=listener.getsockname()[1];commands=[];errors=[];release=threading.Event()
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(8);sock.sendall(b'login: ');commands.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    commands.extend((stream.readline(),stream.readline(),stream.readline(),stream.readline()))
                    sock.sendall(b'CC11^14074.0^K1ABC^30-Oct-2026^1234Z^CQ FT8^W1AW^291\r\n')
                    release.wait(8)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ');window.connect_btn.invoke()
                deadline=time.monotonic()+8
                while window.status.get()!='Connected' and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(window.status.get(),'Connected')
                window.search.set('FT8');window.search_connected_nodes()
                while not window.spots.get_children() and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(len(window.spots.get_children()),1)
                self.assertEqual(window.spots.item(window.spots.get_children()[0],'values')[3],'K1ABC')
                self.assertEqual(commands,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',
                    b'sh/dx/100 ( call {FT8} or by {FT8} or info {FT8} or origin {FT8} )\r\n',b'sh/mydx/100\r\n'])
            finally:release.set();window.quit_app();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required for the pinned DXSpider formatter')
    def test_plain_history_spot_from_pinned_formatter_reaches_display(self):
        record={'width':80,'frequency':14074,'dx':'K1ABC','comment':'CQ FT8','spotter':'W1AW'}
        result=subprocess.run(['perl','tools/check-dxspider-history-spot.pl'],input=json.dumps(record),
            text=True,capture_output=True,cwd=Path(__file__).resolve().parent,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        line=json.loads(result.stdout)['line'].encode('ascii')+b'\r\n'
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(8)
        port=listener.getsockname()[1];commands=[];errors=[];release=threading.Event()
        def server():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(8);sock.sendall(b'login: ');commands.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    commands.extend((stream.readline(),stream.readline()))
                    sock.sendall(line);release.wait(8)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:None
            try:
                window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ')
                window.connect_btn.invoke();deadline=time.monotonic()+8
                while not window.rows and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(len(window.rows),1)
                spot=window.rows[0][1]
                self.assertEqual((spot.dx,spot.spotter,spot.comment,spot.date),('K1ABC','W1AW','CQ FT8','30-Oct-2026'))
                self.assertEqual(len(window.spots.get_children()),1)
                self.assertEqual(window.spots.item(window.spots.get_children()[0],'values')[3],'K1ABC')
                self.assertEqual(commands,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])
            finally:
                release.set();window.quit_app();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_reconnect_retains_spots_and_reports_duplicate_history(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(12)
        port=listener.getsockname()[1];commands=[];errors=[];drop=threading.Event();release=threading.Event()
        first=b'CC11^14074.0^K1ABC^30-Oct-2026^1234Z^CQ^W1AW^226\r\n'
        second=b'CC11^7074.0^K2ABC^30-Oct-2026^1235Z^CQ^W1AW^226\r\n'
        def server():
            try:
                for attempt in range(2):
                    sock,_=listener.accept()
                    with sock, sock.makefile('rb') as stream:
                        sock.settimeout(10);sock.sendall(b'login: ');commands.append(stream.readline())
                        sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                        commands.extend((stream.readline(),stream.readline()))
                        if attempt==0:
                            sock.sendall(first);drop.wait(10)
                        else:
                            sock.sendall(first+second);release.wait(10)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:None
            try:
                window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ')
                window.auto_reconnect.set(True);window.connect_btn.invoke()
                deadline=time.monotonic()+8
                while len(window.rows)<1 and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual([spot.dx for _,spot in window.rows],['K1ABC'])
                drop.set();deadline=time.monotonic()+10
                while (len(window.rows)<3 or not window.spot_recovery_notice) and time.monotonic()<deadline:
                    window.update();time.sleep(.01)
                self.assertEqual(window.status.get(),'Connected')
                self.assertEqual([spot.dx for _,spot in window.rows],['K1ABC','K1ABC','K2ABC'])
                self.assertTrue(window.spot_recovery_notice)
                self.assertIn('duplicates or leave gaps',window.spot_footer_hint.cget('text'))
                self.assertEqual(commands,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n']*2)
            finally:
                drop.set();release.set();window.quit_app();worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors,[])

    def test_fragmented_burst_keeps_latest_spots_and_filters_by_band(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(10)
        port=listener.getsockname()[1];commands=[];errors=[];release=threading.Event()

        def server():
            try:
                sock,_=listener.accept()
                with sock, sock.makefile('rb') as stream:
                    sock.settimeout(10);sock.sendall(b'login: ');commands.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    commands.extend((stream.readline(),stream.readline()))
                    records=[]
                    for index in range(1200):
                        frequency='14074.0' if index%2==0 else '7074.0'
                        records.append(f'CC11^{frequency}^K{index:04}AA^30-Oct-2026^1234Z^CQ^W1AW^226\r\n')
                    wire=''.join(records).encode('ascii')
                    for start in range(0,len(wire),83):sock.sendall(wire[start:start+83])
                    release.wait(15)
            except Exception as exc:errors.append(exc)
            finally:listener.close()

        worker=threading.Thread(target=server,daemon=True);worker.start()
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:None
            try:
                window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ')
                window.connect_btn.invoke()
                deadline=time.monotonic()+15
                while window.next_spot_id<1200 and time.monotonic()<deadline:
                    window.update();time.sleep(.01)
                window.update()
                self.assertEqual(window.status.get(),'Connected')
                self.assertEqual(window.next_spot_id,1200)
                self.assertEqual(len(window.rows),1000)
                self.assertEqual(window.spot_count.get(),'1000 shown · list full')
                self.assertEqual({spot.dx for _,spot in window.rows},{f'K{i:04}AA' for i in range(200,1200)})
                self.assertEqual(window.band_shortcut_counts['20 m'].cget('text'),'500')
                self.assertEqual(window.band_shortcut_counts['40 m'].cget('text'),'500')
                window.band_shortcut_buttons['20 m'].invoke()
                self.assertEqual(len(window.spots.get_children()),500)
                self.assertEqual({window.spots.item(row,'values')[2] for row in window.spots.get_children()},{'20 m'})
                window.band_shortcut_buttons['40 m'].invoke()
                self.assertEqual(len(window.spots.get_children()),1000)
                self.assertEqual({window.spots.item(row,'values')[2] for row in window.spots.get_children()},{'20 m','40 m'})
                window.band_shortcut_buttons['20 m'].invoke()
                self.assertEqual(len(window.spots.get_children()),500)
                self.assertEqual({window.spots.item(row,'values')[2] for row in window.spots.get_children()},{'40 m'})
                self.assertEqual(window.spot_count.get(),'500 shown · list full')
                self.assertEqual(commands,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])
            finally:
                release.set();window.quit_app();worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors,[])
