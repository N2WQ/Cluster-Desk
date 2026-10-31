import socket
import threading
import time
import unittest
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from app import App


def local_node(call, dx, frequency='14074.0'):
    listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen();listener.settimeout(5)
    ready=threading.Event(); release=threading.Event(); seen=[];errors=[]
    def run():
        try:
            sock,_=listener.accept()
            with sock:
                sock.settimeout(5); stream=sock.makefile('rb');sock.sendall(b'login: ');seen.append(stream.readline())
                sock.sendall((call+' de NODE dxspider >\r\n').encode('ascii'))
                seen.extend([stream.readline(),stream.readline()])
                sock.sendall(('CC11^'+frequency+'^'+dx+'^30-Oct-2026^1234Z^TEST^'+call+'^291^212^NODE^^^^^^^^^^^\r\n').encode('ascii'))
                ready.set();release.wait(8)
        except Exception as exc:errors.append(exc)
        finally:listener.close()
    thread=threading.Thread(target=run,daemon=True);thread.start()
    return listener.getsockname()[1],ready,release,seen,errors,thread


class MultiNodeTests(unittest.TestCase):
    def test_additional_manual_command_confirmation_over_tcp(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        port=listener.getsockname()[1];release=threading.Event();received=[];errors=[]
        def serve():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(6);sock.sendall(b'login: ');received.append(stream.readline())
                    sock.sendall(b'LZ3ZZ-1 de NODE dxspider >\r\n')
                    received.extend(stream.readline() for _ in range(3));release.wait(6)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=serve,daemon=True);worker.start()
        with TemporaryDirectory() as directory,patch('app.SETTINGS',Path(directory)/'settings.json'):
            app=App();app.withdraw();app._save_settings=lambda:True
            try:
                ident=app.start_secondary_node('127.0.0.1',port,'LZ3ZZ-1');item=app.secondary_nodes[ident]
                deadline=time.monotonic()+6
                while item['status']!='Connected' and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(item['status'],'Connected');app.open_secondary_activity(ident)
                def walk(widget):
                    for child in widget.winfo_children():yield child;yield from walk(child)
                view=item['view'];entry=next(w for w in walk(view['window']) if type(w) is ttk.Entry)
                entry.insert(0,'sh/dx/10');view['send'].invoke()
                deadline=time.monotonic()+6
                while (len(received)<4 or item['pending_manual_commands']) and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(received,[b'LZ3ZZ-1\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/dx/10\r\n'])
                self.assertFalse(item['pending_manual_commands']);release.set()
                deadline=time.monotonic()+6
                while item['status']!='Disconnected' and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(item['status'],'Disconnected');self.assertFalse(app.manual_uncertain_notice)
            finally:
                release.set();app.quit_app();worker.join(3);listener.close()
        self.assertEqual(errors,[]);self.assertFalse(worker.is_alive())

    def test_additional_node_manual_command_reports_uncertain_delivery(self):
        class Node:
            def __init__(self,emit):self.emit=emit;self.sent=[]
            def start(self,*args):pass
            def close(self):pass
            def send(self,value):self.sent.append(value)
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            with patch('app.Connection',Node):
                first=app.start_secondary_node('one.invalid',7300,'LZ3ZZ-1')
                second=app.start_secondary_node('two.invalid',7300,'LZ3ZZ-2')
                one=app.secondary_nodes[first];two=app.secondary_nodes[second]
                for item in (one,two):item['connection'].emit('status','Connected')
                app.poll_secondary_nodes()
                def walk(widget):
                    for child in widget.winfo_children():yield child;yield from walk(child)
                for ident,item in ((first,one),(second,two)):
                    app.open_secondary_activity(ident);view=item['view']
                    entry=next(w for w in walk(view['window']) if type(w) is ttk.Entry)
                    entry.insert(0,'sh/dx/10');view['send'].invoke()
                    self.assertEqual(len(item['pending_manual_commands']),1)
                one['connection'].emit('sent',one['connection'].sent[0])
                two['connection'].emit('status','Disconnected');app.poll_secondary_nodes()
                self.assertFalse(one['pending_manual_commands']);self.assertFalse(two['pending_manual_commands'])
                self.assertNotIn('delivery is unknown',one['activity'])
                self.assertIn('delivery is unknown',two['activity'])
                self.assertIn('Additional node two.invalid',app.transcript.get('1.0','end'))
                self.assertTrue(app.manual_uncertain_notice)
        finally:app.quit_app()

    def test_cc_filter_change_and_readback_over_local_tcp(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        port=listener.getsockname()[1];release=threading.Event();errors=[];seen=[]
        def serve():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(6);sock.sendall(b'login: ');seen.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE CCC >\r\n')
                    seen.extend(stream.readline() for _ in range(5))
                    sock.sendall(b'DX Spot Orig Cty Filter set OFF\r\nKeyword Filter set to Reject: ZZQXTEST\r\nLZ3ZZ de NODE CCC >\r\n')
                    release.wait(6)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        with TemporaryDirectory() as directory,patch('app.SETTINGS',Path(directory)/'settings.json'):
            app=App();app.withdraw();app._save_settings=lambda:True
            try:
                app.host.set('127.0.0.1');app.port.set(str(port));app.call.set('LZ3ZZ');app.connect()
                deadline=time.monotonic()+6
                while app.status.get()!='Connected' and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(app.status.get(),'Connected')
                app.keyword_text.set('ZZQXTEST');app.change_filter('reject');app.show_filters()
                deadline=time.monotonic()+6
                while app.filter_readback.state not in ('complete','partial') and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(app.filter_readback.state,'complete')
                self.assertIn('Keyword Filter set to Reject: ZZQXTEST',app.filter_readback.text())
                self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/settings\r\n',b'set/filter keyword/rej ZZQXTEST\r\n',b'sh/filter\r\n'])
            finally:
                release.set();app.quit_app();thread.join(3);listener.close()
        self.assertEqual(errors,[]);self.assertFalse(thread.is_alive())

    def test_band_search_across_cc_and_dxspider_over_tcp(self):
        release=threading.Event();errors=[];seen={};threads=[];listeners=[]
        def start(label,call,prompt,band_command,dx):
            listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(8);listeners.append(listener)
            port=listener.getsockname()[1]
            def serve():
                try:
                    sock,_=listener.accept()
                    with sock,sock.makefile('rb') as stream:
                        sock.settimeout(8);sock.sendall(b'login: ');seen[label]=[stream.readline()]
                        sock.sendall((call+' de NODE '+prompt+' >\r\n').encode('ascii'))
                        while True:
                            line=stream.readline();seen[label].append(line)
                            if line==band_command:break
                            if not line:raise EOFError('Connection closed before the band request')
                        sock.sendall(('CC11^14074^'+dx+'^30-Oct-2026^1201Z^CQ^W1AW\r\nCC11^7074^K9OTHER^30-Oct-2026^1202Z^CQ^W1AW\r\n').encode('ascii'))
                        release.wait(8)
                except Exception as exc:errors.append(exc)
                finally:listener.close()
            thread=threading.Thread(target=serve,daemon=True);thread.start();threads.append(thread);return port
        cc=start('cc','LZ3ZZ','CCC',b'sh/dx/100 20\r\n','K1AAA')
        spider=start('spider','LZ3ZZ-1','dxspider',b'sh/dx/100 on 20m\r\n','K1BBB')
        with TemporaryDirectory() as directory,patch('app.SETTINGS',Path(directory)/'settings.json'):
            app=App();app.withdraw();app._save_settings=lambda:True
            try:
                app.host.set('127.0.0.1');app.port.set(str(cc));app.call.set('LZ3ZZ');app.connect()
                ident=app.start_secondary_node('127.0.0.1',spider,'LZ3ZZ-1');item=app.secondary_nodes[ident]
                deadline=time.monotonic()+8
                while (app.status.get()!='Connected' or item['status']!='Connected') and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual((app.status.get(),item['status']),('Connected','Connected'))
                app.search.set('20m');app.search_connected_nodes()
                deadline=time.monotonic()+8
                while len(app.rows)<4 and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(len(app.rows),4)
                self.assertEqual({app.spots.item(row,'values')[3] for row in app.spots.get_children()},{'K1AAA','K1BBB'})
                self.assertEqual({app.spot_sources[ident] for ident,spot in app.rows if spot.dx in ('K1AAA','K1BBB')},{app.spot_source,item['source']})
                self.assertIn(b'sh/dx/100 20\r\n',seen['cc']);self.assertIn(b'sh/dx/100 on 20m\r\n',seen['spider'])
            finally:
                release.set();app.quit_app()
                for thread in threads:thread.join(3)
                for listener in listeners:listener.close()
        self.assertEqual(errors,[]);self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_filter_change_and_readback_over_local_tcp(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        port=listener.getsockname()[1];release=threading.Event();errors=[];seen=[]
        def serve():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(6);sock.sendall(b'login: ');seen.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    seen.extend([stream.readline(),stream.readline(),stream.readline(),stream.readline()])
                    sock.sendall(b'LZ3ZZ: spots filter\r\naccept 1 on 20m\r\nLZ3ZZ de NODE dxspider >\r\n')
                    release.wait(6)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        with TemporaryDirectory() as directory,patch('app.SETTINGS',Path(directory)/'settings.json'):
            app=App();app.withdraw();app._save_settings=lambda:True
            try:
                app.host.set('127.0.0.1');app.port.set(str(port));app.call.set('LZ3ZZ');app.connect()
                deadline=time.monotonic()+6
                while app.status.get()!='Connected' and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(app.status.get(),'Connected')
                app.spider_expression.set('20m');app.change_filter('accept');app.show_filters()
                deadline=time.monotonic()+6
                while app.filter_readback.state not in ('complete','partial') and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(app.filter_readback.state,'complete')
                self.assertIn('accept 1 on 20m',app.filter_readback.text())
                self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'accept/spots 1 on 20m\r\n',b'sh/filter\r\n'])
            finally:
                release.set();app.quit_app();thread.join(3);listener.close()
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])

    def test_additional_node_recovers_from_long_line_over_tcp(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        port=listener.getsockname()[1];release=threading.Event();errors=[];seen=[]
        def serve():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(6);sock.sendall(b'login: ');seen.append(stream.readline())
                    sock.sendall(b'LZ3ZZ-1 de NODE dxspider >\r\n')
                    seen.extend([stream.readline(),stream.readline()])
                    sock.sendall(b'CC11^7074^K1ABC^30-Oct-2026^1201Z^CQ^W1AW\r\n'+b'X'*9000+b'\r\n'+b'CC11^14074^K2ABC^30-Oct-2026^1202Z^CQ^W1AW\r\n')
                    release.wait(6)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        with TemporaryDirectory() as directory,patch('app.SETTINGS',Path(directory)/'settings.json'):
            app=App();app.withdraw();app._save_settings=lambda:True
            try:
                ident=app.start_secondary_node('127.0.0.1',port,'LZ3ZZ-1',auto_reconnect=True)
                item=app.secondary_nodes[ident];deadline=time.monotonic()+6
                while len(app.rows)<2 and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual([spot.dx for _,spot in app.rows],['K1ABC','K2ABC'])
                self.assertTrue(all(app.spot_sources[row]==item['source'] for row,_ in app.rows))
                self.assertEqual(item['status'],'Connected');self.assertIsNone(item['retry_after'])
                self.assertIn('overlong cluster line',item['activity'])
            finally:
                release.set();app.quit_app();thread.join(3);listener.close()
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assertEqual(seen,[b'LZ3ZZ-1\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])

    def test_all_sources_search_retries_only_rate_limited_node_over_sockets(self):
        listeners=[];threads=[];errors=[];received={};release=threading.Event()
        def start(label,call):
            listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(8);listeners.append(listener)
            def serve():
                try:
                    sock,_=listener.accept()
                    with sock,sock.makefile('rb',buffering=0) as stream:
                        sock.settimeout(8);sock.sendall(b'login: ');received[label]=[stream.readline()]
                        sock.sendall((call+' de NODE dxspider >\r\n').encode('ascii'))
                        received[label].extend((stream.readline(),stream.readline(),stream.readline()))
                        sock.sendall(f'CC11^14074^K1ABC^30-Oct-2026^1234Z^{label}^W1AW^226\r\n'.encode('ascii'))
                        release.wait(8);sock.settimeout(.2)
                        try:received[label].append(stream.readline())
                        except socket.timeout:received[label].append(b'')
                except Exception as exc:errors.append(exc)
                finally:listener.close()
            thread=threading.Thread(target=serve,daemon=True);thread.start();threads.append(thread)
            return listener.getsockname()[1]
        first=start('main','LZ3ZZ');second=start('secondary','LZ3ZZ-1')
        with TemporaryDirectory() as directory,patch('app.SETTINGS',Path(directory)/'settings.json'):
            window=App();window.withdraw();window._save_settings=lambda:True
            try:
                window.host.set('127.0.0.1');window.port.set(str(first));window.call.set('LZ3ZZ');window.connect()
                ident=window.start_secondary_node('127.0.0.1',second,'LZ3ZZ-1');item=window.secondary_nodes[ident]
                deadline=time.monotonic()+8
                while (window.status.get()!='Connected' or item['status']!='Connected') and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual((window.status.get(),item['status']),('Connected','Connected'))
                with item['connection'].lock:
                    item['connection'].command_times.clear();item['connection'].command_times.extend([time.monotonic()-20.5]*20)
                window.search_field.set('DX call');window.search.set('K1ABC');window.search_connected_nodes()
                self.assertIn('1 waiting',window.spot_search_note.get())
                deadline=time.monotonic()+6
                while len(window.rows)<2 and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual({spot.comment for _,spot in window.rows},{'main','secondary'})
                self.assertEqual({window.spot_sources[row] for row,_ in window.rows},{window.spot_source,item['source']})
                self.assertFalse(window.pending_spot_searches);release.set()
                for thread in threads:thread.join(3)
                self.assertEqual(received['main'],[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/dx/100 call {K1ABC}\r\n',b''])
                self.assertEqual(received['secondary'],[b'LZ3ZZ-1\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/dx/100 call {K1ABC}\r\n',b''])
            finally:
                release.set();window.quit_app()
                for thread in threads:thread.join(2)
                for listener in listeners:listener.close()
        self.assertEqual(errors,[]);self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_selected_source_search_sends_only_to_that_node(self):
        listeners=[];threads=[];errors=[];received={};release=threading.Event();done=threading.Event()
        def start(label,call,dx):
            listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6);listeners.append(listener)
            port=listener.getsockname()[1]
            def serve():
                try:
                    sock,_=listener.accept()
                    with sock,sock.makefile('rb') as stream:
                        sock.settimeout(6);sock.sendall(b'login: ');received[label]=[stream.readline()]
                        sock.sendall((call+' de NODE dxspider >\r\n').encode('ascii'))
                        received[label].extend((stream.readline(),stream.readline()))
                        sock.sendall(('CC11^14074^'+dx+'^30-Oct-2026^1234Z^initial^W1AW^226\r\n').encode('ascii'))
                        if label=='secondary':
                            received[label].append(stream.readline())
                            sock.sendall(b'CC11^14074^K1CCC^30-Oct-2026^1235Z^history^W1AW^226\r\n');release.wait(6)
                        else:
                            release.wait(6);sock.settimeout(.2)
                            try:received[label].append(sock.recv(1024))
                            except socket.timeout:received[label].append(b'')
                            done.set()
                except Exception as exc:errors.append(exc);done.set()
                finally:listener.close()
            thread=threading.Thread(target=serve,daemon=True);thread.start();threads.append(thread)
            return port
        first=start('main','LZ3ZZ','K1AAA');second=start('secondary','LZ3ZZ-1','K1BBB')
        with TemporaryDirectory() as directory,patch('app.SETTINGS',Path(directory)/'settings.json'):
            app=App();app.withdraw();app._save_settings=lambda:None
            try:
                app.host.set('127.0.0.1');app.port.set(str(first));app.call.set('LZ3ZZ');app.connect()
                ident=app.start_secondary_node('127.0.0.1',second,'LZ3ZZ-1');item=app.secondary_nodes[ident]
                deadline=time.monotonic()+6
                while (len(app.rows)<2 or item['status']!='Connected') and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(len(app.rows),2)
                app.spot_source_filter.set(app.source_label(('127.0.0.1',second,'LZ3ZZ-1')))
                app.search_field.set('Spotter');app.search.set('W1AW');app.search_connected_nodes()
                deadline=time.monotonic()+6
                while not any(spot.dx=='K1CCC' for _,spot in app.rows) and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertTrue(any(spot.dx=='K1CCC' and app.spot_sources[row]==item['source'] for row,spot in app.rows))
                release.set();self.assertTrue(done.wait(3))
                self.assertEqual(received['main'],[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b''])
                self.assertEqual(received['secondary'],[b'LZ3ZZ-1\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/dx/100 by {W1AW}\r\n'])
            finally:
                release.set();app.quit_app()
                for thread in threads:thread.join(2)
                for listener in listeners:listener.close()
        self.assertEqual(errors,[]);self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_band_selection_combines_with_live_source_selection(self):
        p1,r1,end1,seen1,errors1,t1=local_node('LZ3ZZ','K1AAA','14074.0')
        p2,r2,end2,seen2,errors2,t2=local_node('LZ3ZZ-1','K1BBB','7074.0')
        with TemporaryDirectory() as directory,patch('app.SETTINGS',Path(directory)/'settings.json'):
            app=App();app.deiconify();app._save_settings=lambda:None
            try:
                app.host.set('127.0.0.1');app.port.set(str(p1));app.call.set('LZ3ZZ');app.connect()
                ident=app.start_secondary_node('127.0.0.1',p2,'LZ3ZZ-1')
                deadline=time.monotonic()+6
                while len(app.rows)<2 and time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual({spot.dx for _,spot in app.rows},{'K1AAA','K1BBB'})
                app.band_shortcut_buttons['20 m'].invoke();app.band_shortcut_buttons['40 m'].invoke()
                self.assertEqual({app.spots.item(row,'values')[3] for row in app.spots.get_children()},{'K1AAA','K1BBB'})
                source=app.source_label(('127.0.0.1',p2,'LZ3ZZ-1'))
                app.spot_source_box.current(list(app.spot_source_box.cget('values')).index(source))
                app.spot_source_box.event_generate('<<ComboboxSelected>>');app.update()
                self.assertEqual([app.spots.item(row,'values')[3] for row in app.spots.get_children()],['K1BBB'])
                app.band_shortcut_buttons['20 m'].invoke()
                self.assertEqual(app.selected_bands,{'40 m'})
                self.assertEqual([app.spots.item(row,'values')[3] for row in app.spots.get_children()],['K1BBB'])
                app.spot_source_box.current(0);app.spot_source_box.event_generate('<<ComboboxSelected>>');app.update()
                self.assertEqual([app.spots.item(row,'values')[3] for row in app.spots.get_children()],['K1BBB'])
            finally:
                end1.set();end2.set();app.quit_app();t1.join(3);t2.join(3)
        self.assertFalse(t1.is_alive() or t2.is_alive());self.assertEqual(errors1+errors2,[])

    def test_secondary_filter_readback_uses_only_selected_real_local_connection(self):
        listeners=[];threads=[];errors=[];received={};release=threading.Event();done=threading.Event()
        def create_node(label,call,dx):
            listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6);listeners.append(listener)
            def serve():
                try:
                    sock,_=listener.accept()
                    with sock:
                        sock.settimeout(6);stream=sock.makefile('rb');sock.sendall(b'login: ')
                        received[label]=[stream.readline()];sock.sendall((call+' de NODE dxspider >\r\n').encode('ascii'))
                        received[label].extend((stream.readline(),stream.readline()))
                        sock.sendall(('CC11^14074^'+dx+'^30-Oct-2026^1234Z^TEST^W1AW^226\r\n').encode('ascii'))
                        if label=='secondary':
                            received[label].append(stream.readline())
                            sock.sendall((f'No filters defined for {call}\r\n{call} de NODE dxspider >\r\n').encode('ascii'))
                            release.wait(6)
                        else:
                            release.wait(6);sock.settimeout(.2)
                            try:received[label].append(sock.recv(1024))
                            except socket.timeout:received[label].append(b'')
                            done.set()
                except Exception as exc:errors.append(exc);done.set()
                finally:listener.close()
            thread=threading.Thread(target=serve,daemon=True);thread.start();threads.append(thread)
            return listener.getsockname()[1]
        first=create_node('main','LZ3ZZ','K1AAA');second=create_node('secondary','LZ3ZZ-1','K1BBB')
        app=App();app.withdraw();app._save_settings=lambda:None
        def walk(w):
            for child in w.winfo_children():yield child;yield from walk(child)
        try:
            app.host.set('127.0.0.1');app.port.set(str(first));app.call.set('LZ3ZZ');app.auto_reconnect.set(False);app.connect_btn.invoke()
            ident=app.start_secondary_node('127.0.0.1',second,'LZ3ZZ-1');item=app.secondary_nodes[ident]
            deadline=time.monotonic()+6
            while (len(app.rows)<2 or item['status']!='Connected') and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertEqual({spot.dx for _,spot in app.rows},{'K1AAA','K1BBB'})
            app.open_secondary_filters(ident)
            dialog=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel) and w.title()=='Additional node filters')
            next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Show node filters').invoke()
            deadline=time.monotonic()+6
            while item['filter_capture'].state not in ('complete','partial') and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertEqual(item['filter_capture'].state,'complete')
            self.assertIn('No filters defined for LZ3ZZ-1',item['filter_capture'].text())
            self.assertEqual(app.filter_readback.state,'idle')
            release.set();self.assertTrue(done.wait(3))
            self.assertEqual(received['main'],[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b''])
            self.assertEqual(received['secondary'],[b'LZ3ZZ-1\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/filter\r\n'])
        finally:
            release.set();app.quit_app()
            for thread in threads:thread.join(2)
            for listener in listeners:listener.close()
        self.assertEqual(errors,[]);self.assertTrue(all(not t.is_alive() for t in threads))

    def test_two_nodes_merge_spots_and_preserve_independent_source(self):
        p1,r1,end1,seen1,errors1,t1=local_node('LZ3ZZ','K1AAA')
        p2,r2,end2,seen2,errors2,t2=local_node('LZ3ZZ-1','K1BBB')
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.host.set('127.0.0.1');app.port.set(str(p1));app.call.set('LZ3ZZ');app.connect()
            ident=app.start_secondary_node('127.0.0.1',p2,'LZ3ZZ-1')
            deadline=time.monotonic()+6
            while len(app.rows)<2 and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertTrue(r1.is_set() and r2.is_set());self.assertEqual({s.dx for _,s in app.rows},{'K1AAA','K1BBB'})
            sources={spot.dx:app.spot_sources[ident] for ident,spot in app.rows}
            self.assertEqual(sources['K1AAA'],('127.0.0.1',p1,'LZ3ZZ'))
            self.assertEqual(sources['K1BBB'],('127.0.0.1',p2,'LZ3ZZ-1'))
            self.assertIn('multiple connections',app.spot_source_note.get())
            app.stop_secondary_node(ident);self.assertEqual(app.status.get(),'Connected')
            self.assertEqual(seen1[:3],[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])
            self.assertEqual(seen2[:3],[b'LZ3ZZ-1\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n'])
        finally:
            app.quit_app();end1.set();end2.set();t1.join(3);t2.join(3)
        self.assertEqual(errors1,[]);self.assertEqual(errors2,[])

    def test_additional_messages_and_reports_keep_source_and_allow_reply(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                app.connection=FakeConnection(lambda *_:None);app.connection_target=('main.invalid',7300,'LZ3ZZ')
                app.spot_source=('main.invalid',7300,'LZ3ZZ');app.dialect='DXSpider';app.status.set('Connected')
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ')
                conn=app.secondary_nodes[ident]['connection'];conn.emit('dialect','DXSpider');conn.emit('status','Connected')
                conn.emit('data','WX de K1ABC: rain\r\nLZ3ZZ de K1ABC: hello\r\nWWV de W0MU <0000>: SFI=89\r\n')
                app.poll_secondary_nodes()
                self.assertEqual(len(app.ann_tree.get_children()),2)
                app.message_kind.set('Weather');app.render_announcements()
                weather=app.ann_tree.item(app.ann_tree.get_children()[0],'values')
                self.assertEqual(weather,('WX de K1ABC: rain','other.invalid:7301 · LZ3ZZ'))
                app.message_kind.set('All messages');app.render_announcements()
                talk=next(i for i in app.ann_tree.get_children() if app.ann_tree.item(i,'values')[0]=='LZ3ZZ de K1ABC: hello')
                app.ann_tree.selection_set(talk);app.show_announcement();self.assertEqual(str(app.reply_button['state']),'normal')
                report=app.report_tree.item(app.report_tree.get_children()[0],'values')
                self.assertEqual(report,('WWV','WWV de W0MU <0000>: SFI=89','other.invalid:7301 · LZ3ZZ'))
                self.assertNotIn('WX de K1ABC: rain',app.transcript.get('1.0','end'))
        finally:app.quit_app()

    def test_secondary_group_messages_follow_each_connection_membership(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                first=app.start_secondary_node('first.invalid',7300,'LZ3ZZ')
                second=app.start_secondary_node('second.invalid',7300,'LZ3ZZ')
                one=app.secondary_nodes[first]['connection'];two=app.secondary_nodes[second]['connection']
                for conn in (one,two):conn.emit('dialect','DXSpider');conn.emit('status','Connected')
                one.emit('data','joining group DXCHAT\r\nDXCHAT de K1ABC: first\r\n')
                two.emit('data','DXCHAT de K1ABC: not joined\r\n');app.poll_secondary_nodes()
                self.assertEqual([entry[0] for entry in app.announcements],['DXCHAT de K1ABC: first'])
                self.assertEqual(app.announcements[-1][1],('first.invalid',7300,'LZ3ZZ'))
                one.emit('data','leaving group DXCHAT\r\nDXCHAT de K1ABC: after leave\r\n');app.poll_secondary_nodes()
                self.assertEqual(len(app.announcements),1)
                one.emit('data','joining group DXCHAT\r\n');app.poll_secondary_nodes()
                app._start_secondary_attempt(first);new=app.secondary_nodes[first]['connection']
                one.emit('data','DXCHAT de K1ABC: old session\r\n')
                new.emit('dialect','DXSpider');new.emit('status','Connected')
                new.emit('data','DXCHAT de K1ABC: before rejoin\r\n');app.poll_secondary_nodes()
                self.assertEqual(len(app.announcements),1)
                new.emit('data','joining group DXCHAT\r\nDXCHAT de K1ABC: after rejoin\r\n');app.poll_secondary_nodes()
                self.assertEqual(app.announcements[-1][0],'DXCHAT de K1ABC: after rejoin')
        finally:app.quit_app()

    def test_chat_form_targets_secondary_and_rejects_changed_session(self):
        from tkinter import ttk
        class FakeConnection:
            def __init__(self,emit):self.emit=emit;self.sent=[]
            def start(self,*args):pass
            def close(self):pass
            def send(self,value):self.sent.append(value)
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ')
                conn=app.secondary_nodes[ident]['connection'];conn.emit('dialect','DXSpider');conn.emit('status','Connected');app.poll_secondary_nodes()
                app.open_chat();widgets=list(walk(app.chat_window))
                self.assertIn('other.invalid:7301',next(w for w in widgets if isinstance(w,ttk.Combobox)).get())
                entries=[w for w in widgets if type(w) is ttk.Entry];entries[0].insert(0,'FOC');entries[1].insert(0,'hello')
                buttons={w.cget('text'):w for w in widgets if isinstance(w,ttk.Button)}
                self.assertEqual(str(buttons['Refresh groups']['state']),'normal')
                buttons['Join'].invoke();buttons['Send'].invoke()
                self.assertEqual(conn.sent,['JOIN FOC','CHAT FOC hello'])
                conn.emit('data','joining group FOC\r\nFOC de K1ABC: message\r\n');app.poll_secondary_nodes()
                self.assertEqual(app.announcements[-1][0],'FOC de K1ABC: message')
                app._start_secondary_attempt(ident)
                with patch('app.messagebox.showerror') as error:buttons['Leave'].invoke();error.assert_called_once()
                self.assertEqual(conn.sent,['JOIN FOC','CHAT FOC hello'])
                self.assertEqual(app.secondary_nodes[ident]['connection'].sent,[])
        finally:app.quit_app()

    def test_secondary_refresh_recovers_groups_and_discards_incomplete_readback(self):
        from tkinter import ttk
        import time
        class FakeConnection:
            def __init__(self,emit):self.emit=emit;self.sent=[]
            def start(self,*args):pass
            def close(self):pass
            def send(self,value):self.sent.append(value)
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');item=app.secondary_nodes[ident];conn=item['connection']
                conn.emit('dialect','DXSpider');conn.emit('status','Connected');app.poll_secondary_nodes();app.open_chat()
                refresh=next(w for w in walk(app.chat_window) if isinstance(w,ttk.Button) and w.cget('text')=='Refresh groups')
                refresh.invoke();self.assertEqual(conn.sent,['stat/user']);self.assertEqual(item['chat_capture'].state,'queued')
                conn.emit('data','Callsign: LZ3ZZ\r\nGroup: WRONG\r\nLZ3ZZ de NODE dxspider >\r\n');app.poll_secondary_nodes()
                self.assertEqual(item['chat_capture'].state,'queued');self.assertEqual(item['chat_groups'],set())
                conn.emit('sent','stat/user');conn.emit('data','Callsign: LZ3ZZ\r\nGroup: FOC, LOCAL\r\nLZ3ZZ de NODE dxspider >')
                conn.emit('data','\r\nFOC de K1ABC: hello\r\n');app.poll_secondary_nodes()
                self.assertEqual(item['chat_groups'],{'FOC','LOCAL'});self.assertEqual(app.announcements[-1][0],'FOC de K1ABC: hello')
                self.assertIn('FOC',app.chat_note.get())
                refresh.invoke();conn.emit('sent','stat/user');conn.emit('sent','sh/dx');conn.emit('data','Callsign: LZ3ZZ\r\nGroup: WRONG\r\nLZ3ZZ de NODE dxspider >\r\n');app.poll_secondary_nodes()
                self.assertEqual(item['chat_capture'].state,'partial');self.assertEqual(item['chat_groups'],{'FOC','LOCAL'})
                refresh.invoke();conn.emit('sent','stat/user');conn.emit('data','Callsign: OTHER\r\nGroup: WRONG\r\nLZ3ZZ de NODE dxspider >\r\n');app.poll_secondary_nodes()
                self.assertEqual(item['chat_capture'].state,'partial');self.assertEqual(item['chat_groups'],{'FOC','LOCAL'})
                refresh.invoke();conn.emit('sent','stat/user');conn.emit('data','Callsign: LZ3ZZ\r\nGroup: WRONG\r\n');app.poll_secondary_nodes()
                item['chat_deadline']=time.monotonic()-1;app.poll_secondary_nodes()
                self.assertEqual(item['chat_capture'].state,'partial');self.assertEqual(item['chat_groups'],{'FOC','LOCAL'})
                refresh.invoke();conn.emit('sent','stat/user');conn.emit('data','Callsign: LZ3ZZ\r\nGroup: FOC\r\njoining group NEW\r\nLZ3ZZ de NODE dxspider >\r\n');app.poll_secondary_nodes()
                self.assertEqual(item['chat_capture'].state,'partial');self.assertEqual(item['chat_groups'],{'FOC','LOCAL','NEW'})
                refresh.invoke();app._start_secondary_attempt(ident)
                self.assertIsNone(item['chat_capture']);self.assertEqual(item['chat_groups'],set())
                conn.emit('sent','stat/user');conn.emit('data','Callsign: LZ3ZZ\r\nGroup: OLD\r\nLZ3ZZ de NODE dxspider >\r\n');app.poll_secondary_nodes()
                self.assertEqual(item['chat_groups'],set())
        finally:app.quit_app()

    def test_additional_node_can_use_saved_address_without_connecting(self):
        from tkinter import ttk
        class FakeConnection:
            def __init__(self,emit):self.emit=emit;self.started=None
            def start(self,*args):self.started=args
            def close(self):pass
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.saved_nodes=[{'name':'Local test','host':'one.invalid','port':7301}];app.call.set('LZ3ZZ')
            with patch('app.Connection',FakeConnection):
                app.open_secondary_nodes();dialog=app.secondary_window;widgets=list(walk(dialog))
                selector=next(w for w in widgets if isinstance(w,ttk.Combobox))
                entries=[w for w in widgets if type(w) is ttk.Entry]
                selector.set('Local test');selector.event_generate('<<ComboboxSelected>>');dialog.update()
                self.assertEqual([w.get() for w in entries],['one.invalid','7301','LZ3ZZ'])
                self.assertEqual(len(app.secondary_nodes),0)
                app.saved_nodes[0]={'name':'Local test','host':'changed.invalid','port':7302}
                selector.event_generate('<FocusIn>');selector.event_generate('<<ComboboxSelected>>');dialog.update()
                self.assertEqual([w.get() for w in entries],['changed.invalid','7302','LZ3ZZ'])
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Connect').invoke()
                self.assertEqual(next(iter(app.secondary_nodes.values()))['connection'].started,('changed.invalid',7302,'LZ3ZZ'))
        finally:app.quit_app()

    def test_reply_uses_source_and_rejects_a_changed_connection(self):
        from tkinter import ttk
        class FakeConnection:
            def __init__(self, emit): self.emit=emit; self.sent=[]
            def start(self, *args): pass
            def close(self): pass
            def send(self, command): self.sent.append(command)
            def send_batch(self, commands): self.sent.extend(commands)
        app=App(); app.withdraw(); app._save_settings=lambda:None
        try:
            with patch('app.Connection', FakeConnection):
                main=FakeConnection(None); app.connection=main
                app.connection_target=('main.invalid',7300,'LZ3ZZ'); app.dialect='DXSpider'; app.status.set('Connected')
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ')
                conn=app.secondary_nodes[ident]['connection']
                conn.emit('dialect','DXSpider'); conn.emit('status','Connected')
                conn.emit('data','LZ3ZZ de K1ABC: hello\r\n'); app.poll_secondary_nodes()
                app.ann_tree.selection_set(app.ann_tree.get_children()[0]); app.show_announcement()
                app.status.set('Disconnected')
                self.assertEqual(str(app.reply_button['state']),'normal')
                app.reply_to_talk()
                dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
                panel=dialog.winfo_children()[0]
                entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry]
                self.assertEqual(entries[0].get(),'K1ABC'); entries[1].insert(0,'hello back')
                self.assertEqual(conn.sent,[])
                buttons=panel.winfo_children()[-1].winfo_children()
                next(b for b in buttons if b.cget('text')=='Send message').invoke()
                self.assertEqual(conn.sent,['TALK K1ABC hello back']); self.assertEqual(main.sent,[])
                app.reply_to_talk()
                dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
                panel=dialog.winfo_children()[0]
                entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry]; entries[1].insert(0,'stale')
                app._start_secondary_attempt(ident)
                with patch('app.messagebox.showerror') as error:
                    next(b for b in panel.winfo_children()[-1].winfo_children() if b.cget('text')=='Send message').invoke()
                    error.assert_called_once()
                self.assertEqual(conn.sent,['TALK K1ABC hello back'])
                self.assertEqual(app.secondary_nodes[ident]['connection'].sent,[])
                self.assertEqual(str(app.reply_button['state']),'disabled')
        finally: app.quit_app()

    def test_cc_cluster_talk_from_additional_node_keeps_its_source(self):
        from tkinter import ttk
        class FakeConnection:
            def __init__(self,emit):self.emit=emit;self.sent=[]
            def start(self,*args):pass
            def close(self):pass
            def send(self,command):self.sent.append(command)
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=app.start_secondary_node('cc.invalid',7300,'LZ3ZZ');item=app.secondary_nodes[ident];conn=item['connection']
                conn.emit('dialect','CC Cluster');conn.emit('status','Connected')
                conn.emit('data','LZ3ZZ de LZ3ZZ: CLUSTERDESKSELFTEST\r\n');app.poll_secondary_nodes()
                row=app.ann_tree.get_children()[0];app.ann_tree.selection_set(row);app.show_announcement()
                self.assertEqual(app.selected_talk_target(),('LZ3ZZ',ident))
                app.reply_to_talk();self.assertEqual(conn.sent,[])
                dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
                panel=dialog.winfo_children()[0];entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry]
                self.assertEqual(entries[0].get(),'LZ3ZZ');entries[1].insert(0,'test reply')
                next(w for w in panel.winfo_children()[-1].winfo_children() if w.cget('text')=='Send message').invoke()
                self.assertEqual(conn.sent,['TALK LZ3ZZ test reply'])
                app.reply_to_talk();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
                panel=dialog.winfo_children()[0];entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry]
                entries[1].insert(0,'stale');app._start_secondary_attempt(ident)
                with patch('app.messagebox.showerror') as error:
                    next(w for w in panel.winfo_children()[-1].winfo_children() if w.cget('text')=='Send message').invoke();error.assert_called_once()
                self.assertEqual(conn.sent,['TALK LZ3ZZ test reply']);self.assertEqual(item['connection'].sent,[])
        finally:app.quit_app()

    def test_compose_can_target_secondary_and_rejects_changed_connection(self):
        from tkinter import ttk
        class FakeConnection:
            def __init__(self,emit):self.emit=emit;self.sent=[]
            def start(self,*args):pass
            def close(self):pass
            def send(self,value):self.sent.append(value)
            def send_batch(self,values):self.sent.extend(values)
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                main=FakeConnection(None);app.connection=main;app.connection_target=('main.invalid',7300,'LZ3ZZ');app.dialect='DXSpider';app.status.set('Connected')
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');item=app.secondary_nodes[ident];old=item['connection']
                old.emit('dialect','CC Cluster');old.emit('status','Connected');app.poll_secondary_nodes()
                app.open_message();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');panel=dialog.winfo_children()[0]
                combos=[w for w in panel.winfo_children() if isinstance(w,ttk.Combobox)]
                combos[0].set('Local announcement');combos[1].set('other.invalid:7301 · LZ3ZZ')
                entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry];entries[1].insert(0,'example')
                self.assertEqual(old.sent,[]);self.assertEqual(main.sent,[])
                next(w for w in panel.winfo_children()[-1].winfo_children() if w.cget('text')=='Send message').invoke()
                self.assertEqual(old.sent,['ANNOUNCE example']);self.assertEqual(main.sent,[])
                app.open_message();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');panel=dialog.winfo_children()[0]
                combos=[w for w in panel.winfo_children() if isinstance(w,ttk.Combobox)];combos[1].set('other.invalid:7301 · LZ3ZZ')
                entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry];entries[0].insert(0,'K1ABC');entries[1].insert(0,'hello')
                app._start_secondary_attempt(ident)
                with patch('app.messagebox.showerror') as error:
                    next(w for w in panel.winfo_children()[-1].winfo_children() if w.cget('text')=='Send message').invoke();error.assert_called_once()
                self.assertEqual(item['connection'].sent,[]);self.assertEqual(main.sent,[])
        finally:app.quit_app()

    def test_activity_is_per_node_and_commands_target_only_selected_connection(self):
        from tkinter import ttk
        class FakeConnection:
            def __init__(self,emit):self.emit=emit;self.sent=[];self.closed=False
            def start(self,*args):pass
            def close(self):self.closed=True
            def send(self,value):self.sent.append(value)
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                first=app.start_secondary_node('first.invalid',7300,'LZ3ZZ')
                second=app.start_secondary_node('second.invalid',7300,'LZ3ZZ')
                one=app.secondary_nodes[first]['connection'];two=app.secondary_nodes[second]['connection']
                one.emit('status','Connected');two.emit('status','Connected')
                one.emit('data','FIRST RESPONSE\r\n');two.emit('data','SECOND RESPONSE\r\n');app.poll_secondary_nodes()
                app.open_secondary_activity(first);view=app.secondary_nodes[first]['view']
                self.assertIn('FIRST RESPONSE',view['text'].get('1.0','end'))
                self.assertNotIn('SECOND RESPONSE',view['text'].get('1.0','end'))
                def descendants(w):
                    for child in w.winfo_children():yield child;yield from descendants(child)
                entry=next(w for w in descendants(view['window']) if type(w) is ttk.Entry)
                entry.insert(0,'sh/filter');view['send'].invoke()
                self.assertEqual(one.sent,['sh/filter']);self.assertEqual(two.sent,[])
                one.emit('status','Disconnected');app.poll_secondary_nodes()
                self.assertEqual(str(view['send']['state']),'disabled')
                app.stop_secondary_node(first);self.assertFalse(view['window'].winfo_exists())
        finally:app.quit_app()

    def test_additional_activity_draft_requires_edit_after_reconnect(self):
        from tkinter import ttk
        class FakeConnection:
            def __init__(self,emit):self.emit=emit;self.sent=[]
            def start(self,*args):pass
            def close(self):pass
            def send(self,value):self.sent.append(value)
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');item=app.secondary_nodes[ident];old=item['connection']
                old.emit('status','Connected');app.poll_secondary_nodes();app.open_secondary_activity(ident);view=item['view']
                def descendants(w):
                    for child in w.winfo_children():yield child;yield from descendants(child)
                entry=next(w for w in descendants(view['window']) if type(w) is ttk.Entry);entry.insert(0,'sh/dx/5')
                app._start_secondary_attempt(ident);new=item['connection'];new.emit('status','Connected');app.poll_secondary_nodes()
                with patch('app.messagebox.showerror') as error:view['send'].invoke();error.assert_called_once()
                self.assertEqual(new.sent,[]);self.assertEqual(entry.get(),'sh/dx/5')
                entry.insert('end',' ');view['send'].invoke();self.assertEqual(new.sent,['sh/dx/5'])
        finally:app.quit_app()

    def test_additional_activity_is_bounded(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            item={'activity':''}
            app.append_secondary_activity(item,'x'*200010)
            self.assertEqual(len(item['activity']),200000)
        finally:app.quit_app()

    def test_additional_node_reconnects_independently_and_ignores_old_attempt(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(8)
        received=[]; errors=[];ready=threading.Event();release=threading.Event()
        def server():
            try:
                for number in range(2):
                    sock,_=listener.accept()
                    with sock:
                        sock.settimeout(4);f=sock.makefile('rb');sock.sendall(b'login: ');received.append(f.readline())
                        sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n');received.extend([f.readline(),f.readline()]);f.close()
                        if number==1:
                            sock.sendall(b'CC11^14074^K1AAA^30-Oct-2026^1234Z^RECONNECTED^LZ3ZZ^\r\n')
                            ready.set();release.wait(4)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        port=listener.getsockname()[1];worker=threading.Thread(target=server,daemon=True);worker.start()
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            ident=app.start_secondary_node('127.0.0.1',port,'LZ3ZZ',auto_reconnect=True)
            old=app.secondary_nodes[ident]['connection'];deadline=time.monotonic()+7
            while (not ready.is_set() or not app.rows) and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertTrue(ready.is_set());self.assertEqual(len(app.rows),1)
            old.emit('data','CC11^14075^K1OLD^30-Oct-2026^1234Z^OLD^LZ3ZZ^\r\n')
            app.poll_secondary_nodes();self.assertEqual([s.dx for _,s in app.rows],['K1AAA'])
            self.assertEqual(received,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n']*2)
            self.assertEqual(app.secondary_nodes[ident]['retry_attempt'],0)
        finally:
            app.quit_app();release.set();worker.join(3)
        self.assertEqual(errors,[])

    def test_removing_node_cancels_pending_retry(self):
        class FakeConnection:
            instances=[]
            def __init__(self,emit):self.emit=emit;self.closed=False;self.instances.append(self)
            def start(self,*args):pass
            def close(self):self.closed=True
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=app.start_secondary_node('example.invalid',7300,'LZ3ZZ',auto_reconnect=True)
                FakeConnection.instances[-1].emit('status','Disconnected');app.poll_secondary_nodes()
                self.assertIsNotNone(app.secondary_nodes[ident]['retry_after'])
                app.stop_secondary_node(ident)
                deadline=time.monotonic()+2.2
                while time.monotonic()<deadline:app.update();time.sleep(.01)
                self.assertEqual(len(FakeConnection.instances),1)
        finally:app.quit_app()

    def test_overlong_secondary_line_is_skipped_and_later_spots_survive(self):
        from filter_readback import FilterReadback
        class FakeConnection:
            def __init__(self,emit):self.emit=emit;self.closed=False
            def start(self,*args):pass
            def close(self):self.closed=True
        for with_newline in (False,True):
            with self.subTest(with_newline=with_newline):
                app=App();app.withdraw();app._save_settings=lambda:None
                try:
                    with patch('app.Connection',FakeConnection):
                        ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ',auto_reconnect=True)
                        item=app.secondary_nodes[ident];conn=item['connection']
                        conn.emit('dialect','DXSpider');conn.emit('status','Connected');app.poll_secondary_nodes()
                        capture=FilterReadback();capture.queued('DXSpider','LZ3ZZ');capture.sent();item['filter_capture']=capture
                        item['chat_groups'].add('LOCAL')
                        conn.emit('data','X'*8193);app.poll_secondary_nodes()
                        self.assertEqual(capture.state,'partial');self.assertTrue(item['filter_unresolved'])
                        self.assertIn('LOCAL',item['chat_groups'])
                        conn.emit('data','Y'*1001+('\r\n' if with_newline else ''))
                        if not with_newline:conn.emit('data','Z'*10+'\r\n')
                        conn.emit('data','CC11^14074^W1AW^30-Oct-2026^1200Z^CQ^LZ3ZZ^25\r\n')
                        app.poll_secondary_nodes()
                        self.assertFalse(conn.closed);self.assertEqual(len(app.rows),1)
                        self.assertEqual(app.rows[0][1].dx,'W1AW')
                        self.assertIsNone(item['retry_after']);self.assertEqual(item['status'],'Connected')
                        self.assertIn('overlong cluster line',app.transcript.get('1.0','end'))
                        conn.emit('data','CC11^7074^K1ABC^30-Oct-2026^1201Z^CQ^LZ3ZZ^25\r\n'+'X'*8193+'\r\n'+'CC11^14074^K2ABC^30-Oct-2026^1202Z^CQ^LZ3ZZ^25\r\n')
                        app.poll_secondary_nodes()
                        self.assertEqual([spot.dx for _,spot in app.rows],['W1AW','K1ABC','K2ABC'])
                finally:app.quit_app()

    def test_overflow_discards_old_events_and_schedules_retry(self):
        import queue
        class FakeConnection:
            def __init__(self,emit):self.emit=emit;self.closed=False
            def start(self,*args):pass
            def close(self):self.closed=True
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.secondary_events=queue.Queue(maxsize=1)
            with patch('app.Connection',FakeConnection):
                ident=app.start_secondary_node('example.invalid',7300,'LZ3ZZ',auto_reconnect=True)
                conn=app.secondary_nodes[ident]['connection']
                conn.emit('data','CC11^14074^K1OLD^30-Oct-2026^1234Z^OLD^LZ3ZZ^\r\n')
                conn.emit('status','Disconnected');self.assertTrue(conn.closed)
                app.poll_secondary_nodes()
                self.assertEqual(len(app.rows),0)
                self.assertIsNotNone(app.secondary_nodes[ident]['retry_after'])
                self.assertIn('event queue full',app.transcript.get('1.0','end'))
        finally:app.quit_app()

    def test_stale_password_prompt_cannot_affect_new_attempt(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit;self.passwords=[];self.closed=False
            def start(self,*args):pass
            def close(self):self.closed=True
            def provide_password(self,value):self.passwords.append(value)
        for answer in ('secret',None):
            with self.subTest(answer=answer):
                app=App();app.withdraw();app._save_settings=lambda:None
                try:
                    with patch('app.Connection',FakeConnection):
                        ident=app.start_secondary_node('other.invalid',7300,'LZ3ZZ')
                        old=app.secondary_nodes[ident]['connection'];old.emit('password_required',None)
                        def replace(*args,**kwargs):app._start_secondary_attempt(ident);return answer
                        with patch('app.simpledialog.askstring',side_effect=replace):app.poll_secondary_nodes()
                        new=app.secondary_nodes[ident]['connection']
                        self.assertIsNot(new,old);self.assertFalse(new.closed)
                        self.assertEqual(old.passwords,[]);self.assertEqual(new.passwords,[])
                finally:app.quit_app()

    def test_password_error_dialog_cannot_remove_new_attempt(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit;self.closed=False
            def start(self,*args):pass
            def close(self):self.closed=True
            def provide_password(self,value):raise OSError('request expired')
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=app.start_secondary_node('other.invalid',7300,'LZ3ZZ')
                old=app.secondary_nodes[ident]['connection'];old.emit('password_required',None)
                with patch('app.simpledialog.askstring',return_value='secret'), patch('app.messagebox.showerror',side_effect=lambda *a,**k:app._start_secondary_attempt(ident)):
                    app.poll_secondary_nodes()
                new=app.secondary_nodes[ident]['connection']
                self.assertIsNot(new,old);self.assertFalse(new.closed)
        finally:app.quit_app()

    def test_additional_node_password_and_cancel_are_isolated(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit;self.closed=False;self.passwords=[]
            def start(self,*args):pass
            def close(self):self.closed=True
            def provide_password(self,value):self.passwords.append(value)
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                first=app.start_secondary_node('first.invalid',7300,'LZ3ZZ');second=app.start_secondary_node('second.invalid',7300,'LZ3ZZ')
                one=app.secondary_nodes[first]['connection'];two=app.secondary_nodes[second]['connection']
                one.emit('password_required',None)
                with patch('app.simpledialog.askstring',return_value='secret'):app.poll_secondary_nodes()
                self.assertEqual(one.passwords,['secret']);self.assertEqual(two.passwords,[])
                two.emit('password_required',None)
                with patch('app.simpledialog.askstring',return_value=None):app.poll_secondary_nodes()
                self.assertTrue(two.closed);self.assertIn(first,app.secondary_nodes);self.assertNotIn(second,app.secondary_nodes)
        finally:app.quit_app()

    def test_invalid_main_target_and_duplicate_do_not_disconnect_current_session(self):
        class FakeConnection:
            def __init__(self,emit=None):self.emit=emit;self.closed=False
            def start(self,*args):pass
            def close(self):self.closed=True
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                current=FakeConnection();app.connection=current;app.status.set('Connected')
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ')
                item=app.secondary_nodes[ident];item['connection'].emit('status','Connected');app.poll_secondary_nodes()
                for host,port,call in [('bad host','7300','LZ3ZZ'),('x'*254,'7300','LZ3ZZ'),('node.invalid','+7300','LZ3ZZ'),('node.invalid','7300','A'*33),('node.invalid','7300','---'),('node.invalid','7300','ß1'),('OTHER.INVALID','7301','lz3zz')]:
                    with self.subTest(host=host,port=port,call=call):
                        app.host.set(host);app.port.set(port);app.call.set(call)
                        with patch('app.messagebox.showerror') as error:app.connect();error.assert_called_once()
                        self.assertIs(app.connection,current);self.assertFalse(current.closed)
        finally:app.quit_app()

    def test_disconnected_secondary_does_not_block_main_connection_to_same_source(self):
        class FakeConnection:
            def __init__(self,emit=None):self.emit=emit;self.closed=False
            def start(self,*args):pass
            def close(self):self.closed=True
        with TemporaryDirectory() as directory,patch('app.SETTINGS',Path(directory)/'settings.json'),patch('app.Connection',FakeConnection):
            app=App();app.withdraw()
            try:
                ident=app.start_secondary_node('example.invalid',7300,'LZ3ZZ')
                secondary=app.secondary_nodes[ident]['connection']
                secondary.emit('status','Disconnected');app.poll_secondary_nodes()
                self.assertEqual(app.secondary_nodes[ident]['status'],'Disconnected')
                app.host.set('EXAMPLE.INVALID');app.port.set('7300');app.call.set('lz3zz');app.connect()
                self.assertIsNotNone(app.connection);self.assertTrue(app.want_connection)
                with self.assertRaises(ValueError):app.start_secondary_node('example.invalid',7300,'LZ3ZZ')
                self.assertIs(app.secondary_nodes[ident]['connection'],secondary)
            finally:app.quit_app()

    def test_duplicate_and_limit_are_rejected_and_stale_events_ignored(self):
        class FakeConnection:
            def __init__(self,emit):self.emit=emit;self.closed=False
            def start(self,*args):pass
            def close(self):self.closed=True
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=app.start_secondary_node('example.invalid',7300,'LZ3ZZ')
                conn=app.secondary_nodes[ident]['connection']
                with self.assertRaises(ValueError):app.start_secondary_node('EXAMPLE.invalid',7300,'LZ3ZZ')
                app.stop_secondary_node(ident);self.assertTrue(conn.closed)
                conn.emit('data','CC11^14074^K1ABC^30-Oct-2026^1234Z^CQ^LZ3ZZ^\r\n')
                app.poll_secondary_nodes();self.assertEqual(len(app.rows),0)
                for n in range(3):app.start_secondary_node(f'node{n}.invalid',7300,'LZ3ZZ')
                with self.assertRaises(ValueError):app.start_secondary_node('extra.invalid',7300,'LZ3ZZ')
        finally:app.quit_app()

if __name__=='__main__':unittest.main()
