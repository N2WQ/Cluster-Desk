import unittest
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
import socket
import threading
import time
from unittest.mock import patch
from tkinter import ttk

from app import App
from protocol import history_command, frequency_history_command, older_filtered_spots_command, spider_history_text_command, spider_history_country_command, cc_history_comment_command, cc_history_spotter_command, Spot


class FakeConnection:
    def __init__(self): self.commands=[]
    def send(self, command): self.commands.append(command)
    def close(self): pass


class SpotHistoryTests(unittest.TestCase):
    def test_history_explains_cc_alternatives_when_no_dxspider_is_connected(self):
        window=App();window.withdraw();window._save_settings=lambda:True
        node=FakeConnection();window.connection=node;window.status.set('Connected');window.dialect='CC Cluster'
        try:
            with patch('app.messagebox.showinfo') as info:window.open_older_spots()
            text=info.call_args.args[1]
            self.assertIn('CC Cluster',text);self.assertIn('Search',text);self.assertIn('Refresh spots',text)
            self.assertEqual(node.commands,[])
        finally:window.connection=None;window.quit_app()

    def test_country_history_only_accepts_numeric_node_identifiers(self):
        self.assertEqual(spider_history_country_command('DX country','291'),'sh/dx/100 call_dxcc 291')
        self.assertEqual(spider_history_country_command('Spotter country','0212'),'sh/dx/100 by_dxcc 212')
        for value in ('0','USA','1 2','1\nbye','123456','²'):
            with self.subTest(value=value),self.assertRaises(ValueError):spider_history_country_command('DX country',value)

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider parser')
    def test_country_history_matches_exact_node_country_in_pinned_source(self):
        cwd=Path(__file__).resolve().parent
        for field,index in (('DX country',5),('Spotter country',6)):
            with self.subTest(field=field):
                command=spider_history_country_command(field,'291')
                parsed=subprocess.run(['perl','tools/check-dxspider-history.pl','100',*command.split()[1:]],cwd=cwd,capture_output=True,text=True,timeout=10)
                self.assertEqual(parsed.returncode,0,parsed.stderr)
                rows=[]
                for value in (291,1291,29,0):
                    row=['']*17;row[index]=value;rows.append(row)
                result=subprocess.run(['perl','tools/check-dxspider-filter.pl'],cwd=cwd,capture_output=True,text=True,timeout=10,
                    input=json.dumps({'expression':json.loads(parsed.stdout)['filter'],'rows':rows}))
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout)['matches'],[1,0,0,0])

    def test_history_does_not_convert_non_ascii_to_a_different_callsign(self):
        for dialect in ('CC Cluster','DXSpider'):
            with self.subTest(dialect=dialect),self.assertRaises(ValueError):
                history_command(dialect,'ß1',count=100)

    def test_frequency_history_uses_bounded_nearby_range(self):
        self.assertEqual(frequency_history_command('CC Cluster','14074.05'),'sh/dx/100 14073-14075')
        self.assertEqual(frequency_history_command('DXSpider','14074.00'),'sh/dx/100 on 14073/14075')
        for value in ('14074.001','0.50','99999999','14\nbye','NaN'):
            with self.subTest(value=value),self.assertRaises(ValueError):frequency_history_command('DXSpider',value)

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider parser')
    def test_frequency_history_range_matches_pinned_node_boundaries(self):
        cwd=Path(__file__).resolve().parent
        command=frequency_history_command('DXSpider','14074.00')
        parsed=subprocess.run(['perl','tools/check-dxspider-history.pl','100',*command.split()[1:]],cwd=cwd,capture_output=True,text=True,timeout=10)
        self.assertEqual(parsed.returncode,0,parsed.stderr)
        rows=[]
        for frequency in (14072.99,14073,14073.995,14074,14075,14075.01):
            row=['']*17;row[0]=frequency;rows.append(row)
        result=subprocess.run(['perl','tools/check-dxspider-filter.pl'],cwd=cwd,capture_output=True,text=True,timeout=10,
                              input=json.dumps({'expression':json.loads(parsed.stdout)['filter'],'rows':rows}))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['matches'],[0,1,1,1,1,0])

    def test_cc_spotter_history_validates_the_command_argument(self):
        self.assertEqual(cc_history_spotter_command('w1aw-1'),'sh/dxby W1AW-1')
        for text in ('K1*', 'K1\nbye', 'café', 'W1AW '+('X'*40), 'WAW', '123'):
            with self.subTest(text=text),self.assertRaises(ValueError):cc_history_spotter_command(text)

    def test_cc_comment_history_uses_documented_literal_syntax(self):
        self.assertEqual(cc_history_comment_command('rtty'),"sh/dx 'RTTY'")
        for text in ('CQ FT8', "FT8'", 'é', 'A'*33):
            with self.subTest(text=text),self.assertRaises(ValueError):cc_history_comment_command(text)

    def test_combined_history_search_respects_node_command_length(self):
        with self.assertRaises(ValueError):spider_history_text_command('All fields','.'*50)
        self.assertLessEqual(len(spider_history_text_command('All fields','.'*20)),512)

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider parser')
    def test_history_text_cannot_inject_filter_operators(self):
        cwd=Path(__file__).resolve().parent;text='A} or by K1'
        command=spider_history_text_command('Comment',text)
        parsed=subprocess.run(['perl','tools/check-dxspider-history.pl','100',*command.split()[1:]],cwd=cwd,capture_output=True,text=True,timeout=10)
        self.assertEqual(parsed.returncode,0,parsed.stderr)
        rows=[]
        for comment,spotter in ((text,'W1AW'),('unrelated','K1ABC')):
            row=['']*17;row[3]=comment;row[4]=spotter;rows.append(row)
        result=subprocess.run(['perl','tools/check-dxspider-filter.pl'],cwd=cwd,capture_output=True,text=True,timeout=10,
                              input=json.dumps({'expression':json.loads(parsed.stdout)['filter'],'rows':rows}))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['matches'],[1,0])

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider parser')
    def test_all_fields_history_search_matches_each_text_field(self):
        cwd=Path(__file__).resolve().parent;command=spider_history_text_command('All fields','K1')
        parsed=subprocess.run(['perl','tools/check-dxspider-history.pl','100',*command.split()[1:]],cwd=cwd,capture_output=True,text=True,timeout=10)
        self.assertEqual(parsed.returncode,0,parsed.stderr)
        rows=[]
        for index,value in ((1,'K1ABC'),(4,'VK1ABC'),(3,'CQ K1'),(7,'K1NODE'),(1,'K2ABC')):
            row=['']*17;row[index]=value;rows.append(row)
        result=subprocess.run(['perl','tools/check-dxspider-filter.pl'],cwd=cwd,capture_output=True,text=True,timeout=10,
                              input=json.dumps({'expression':json.loads(parsed.stdout)['filter'],'rows':rows}))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['matches'],[1,1,1,1,0])

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider parser')
    def test_comment_phrase_and_punctuation_search_are_literal(self):
        cwd=Path(__file__).resolve().parent
        command=spider_history_text_command('Comment','CQ FT8.1')
        self.assertEqual(command,r'sh/dx/100 info {CQ\x20FT8\x2e1}')
        parsed=subprocess.run(['perl','tools/check-dxspider-history.pl','100',*command.split()[1:]],cwd=cwd,capture_output=True,text=True,timeout=10)
        self.assertEqual(parsed.returncode,0,parsed.stderr)
        rows=[]
        for comment in ('CQ FT8.1','CQ  FT8.1','CQ FT8X1'):
            row=['']*17;row[3]=comment;rows.append(row)
        result=subprocess.run(['perl','tools/check-dxspider-filter.pl'],cwd=cwd,capture_output=True,text=True,timeout=10,
                              input=json.dumps({'expression':json.loads(parsed.stdout)['filter'],'rows':rows}))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['matches'],[1,0,0])

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider parser')
    def test_comment_history_search_matches_literal_substrings(self):
        cwd=Path(__file__).resolve().parent
        parsed=subprocess.run(['perl','tools/check-dxspider-history.pl','100','info','{FT8}'],cwd=cwd,capture_output=True,text=True,timeout=10)
        self.assertEqual(parsed.returncode,0,parsed.stderr)
        expression=json.loads(parsed.stdout)['filter'];self.assertEqual(expression,'info {FT8}')
        rows=[]
        for comment in ('CQ FT8','ft8 signal','CW'):
            row=['']*17;row[3]=comment;rows.append(row)
        result=subprocess.run(['perl','tools/check-dxspider-filter.pl'],cwd=cwd,capture_output=True,text=True,timeout=10,
                              input=json.dumps({'expression':expression,'rows':rows}))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['matches'],[1,1,0])

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider parser')
    def test_spotter_history_search_matches_literal_substrings(self):
        cwd=Path(__file__).resolve().parent
        parsed=subprocess.run(['perl','tools/check-dxspider-history.pl','100','by','{K1}'],cwd=cwd,capture_output=True,text=True,timeout=10)
        self.assertEqual(parsed.returncode,0,parsed.stderr)
        expression=json.loads(parsed.stdout)['filter'];self.assertEqual(expression,'by {K1}')
        rows=[]
        for spotter in ('K1ABC','VK1ABC','K2ABC'):
            row=['']*17;row[4]=spotter;rows.append(row)
        result=subprocess.run(['perl','tools/check-dxspider-filter.pl'],cwd=cwd,capture_output=True,text=True,timeout=10,
                              input=json.dumps({'expression':expression,'rows':rows}))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['matches'],[1,1,0])

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider parser')
    def test_dx_call_history_search_matches_literal_substrings(self):
        cwd=Path(__file__).resolve().parent
        command=spider_history_text_command('DX call','K1')
        parsed=subprocess.run(['perl','tools/check-dxspider-history.pl','100',*command.split()[1:]],cwd=cwd,capture_output=True,text=True,timeout=10)
        self.assertEqual(parsed.returncode,0,parsed.stderr)
        rows=[]
        for call in ('K1ABC','VK1ABC','K2ABC'):
            row=['']*17;row[1]=call;rows.append(row)
        result=subprocess.run(['perl','tools/check-dxspider-filter.pl'],cwd=cwd,capture_output=True,text=True,timeout=10,
                              input=json.dumps({'expression':json.loads(parsed.stdout)['filter'],'rows':rows}))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['matches'],[1,1,0])

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to run the pinned DXSpider history search')
    def test_pinned_history_paging_shifts_when_new_spots_arrive(self):
        result=subprocess.run(['perl','tools/check-dxspider-history-paging.pl'],
                              cwd=Path(__file__).resolve().parent,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        pages=json.loads(result.stdout);first=pages['first'];second=pages['second']
        self.assertEqual(first,[f'old{i}' for i in range(100)])
        self.assertEqual(second,[f'old{i}' for i in range(95,195)])
        self.assertEqual(set(first)&set(second),{f'old{i}' for i in range(95,100)})

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to run the pinned DXSpider command parser')
    def test_days_back_reaches_pinned_dxspider_history_parser(self):
        for mode,value,expected in [('Callsign','W1AW','call '),('Band','20m','on 20m'),('Frequency range','14000-14350','on 14000/14350')]:
            with self.subTest(mode=mode):
                command=history_command('DXSpider',value,mode,count=30,days_ago=2)
                # The sh/dx/N alias passes N as the first argument to show/dx.
                args=['perl','tools/check-dxspider-history.pl','30',*command.split()[1:]]
                result=subprocess.run(args,cwd=Path(__file__).resolve().parent,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                parsed=json.loads(result.stdout)
                self.assertEqual((int(parsed['days_ago']),int(parsed['count'])),(2,30))
                self.assertIn(expected,parsed['filter'])

    def test_country_search_requests_node_history_and_filters_returned_spots(self):
        for field,wire_field,expected in (('DX country','call_dxcc','K1ABC'),('Spotter country','by_dxcc','K2ABC')):
            with self.subTest(field=field):
                listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
                port=listener.getsockname()[1];seen=[];errors=[];release=threading.Event()
                def serve():
                    try:
                        sock,_=listener.accept()
                        with sock,sock.makefile('rb') as stream:
                            sock.settimeout(5);sock.sendall(b'login: ');seen.append(stream.readline())
                            sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n');seen.extend(stream.readline() for _ in range(4))
                            sock.sendall(b'CC11^14074^K1ABC^30-Oct-2026^1234Z^one^W1AW^291^212\r\nCC11^7001^K2ABC^30-Oct-2026^1234Z^two^W1AW^227^291\r\n')
                            release.wait(5)
                    except Exception as exc:errors.append(exc)
                    finally:listener.close()
                worker=threading.Thread(target=serve,daemon=True);worker.start()
                with tempfile.TemporaryDirectory() as directory,patch('app.SETTINGS',Path(directory)/'settings.json'):
                    window=App();window.withdraw();window._save_settings=lambda:None
                    try:
                        window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ');window.connect()
                        deadline=time.monotonic()+5
                        while window.status.get()!='Connected' and time.monotonic()<deadline:window.update();time.sleep(.01)
                        self.assertEqual(window.status.get(),'Connected')
                        window.search_field.set(field);window.search.set('291');window.search_connected_nodes()
                        deadline=time.monotonic()+5
                        while len(window.rows)<2 and time.monotonic()<deadline:window.update();time.sleep(.01)
                        self.assertEqual(len(window.rows),2)
                        self.assertEqual([window.spots.item(row,'values')[3] for row in window.spots.get_children()],[expected])
                        self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',f'sh/dx/100 {wire_field} 291\r\n'.encode(),b'sh/mydx/100\r\n'])
                        self.assertIn('recent node-filtered',window.spot_search_note.get())
                    finally:release.set();window.quit_app();worker.join(2)
                self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_frequency_search_requests_range_and_filters_returned_spots(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        port=listener.getsockname()[1];seen=[];errors=[];release=threading.Event()
        def serve():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(5);stream=sock.makefile('rb');sock.sendall(b'login: ');seen.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    seen.extend(stream.readline() for _ in range(4))
                    sock.sendall(b'CC11^14074.05^K1ABC^30-Oct-2026^1234Z^Match^W1AW^1\r\nCC11^14075^K2ABC^30-Oct-2026^1234Z^Other^W1AW^1\r\n')
                    release.wait(5);stream.close()
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=serve,daemon=True);worker.start()
        with tempfile.TemporaryDirectory() as directory,patch('app.SETTINGS',Path(directory)/'settings.json'):
            window=App();window.withdraw();window._save_settings=lambda:None
            try:
                window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ');window.connect()
                deadline=time.monotonic()+5
                while window.status.get()!='Connected' and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(window.status.get(),'Connected')
                window.search_field.set('Frequency');window.search.set('14074.05');window.search_connected_nodes()
                deadline=time.monotonic()+5
                while len(window.rows)<2 and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(len(window.rows),2)
                self.assertEqual([window.spots.item(row,'values')[3] for row in window.spots.get_children()],['K1ABC'])
                self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/dx/100 on 14073/14075\r\n',b'sh/mydx/100\r\n'])
            finally:release.set();window.quit_app();worker.join(2)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_cc_spotter_search_sends_both_history_requests_over_socket(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        port=listener.getsockname()[1];seen=[];errors=[];release=threading.Event()
        def serve():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(5);stream=sock.makefile('rb')
                    sock.sendall(b'login: ');seen.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE CCC >\r\n')
                    seen.extend(stream.readline() for _ in range(5))
                    sock.sendall(b'CC11^14074.0^K1ABC^30-Oct-2026^1234Z^History^W1AW^1\r\n')
                    release.wait(5);stream.close()
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=serve,daemon=True);worker.start()
        with tempfile.TemporaryDirectory() as directory,patch('app.SETTINGS',Path(directory)/'settings.json'):
            window=App();window.withdraw();window._save_settings=lambda:None
            try:
                window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ');window.connect()
                deadline=time.monotonic()+5
                while window.status.get()!='Connected' and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(window.status.get(),'Connected')
                window.search_field.set('Spotter');window.search.set('W1AW');window.search_connected_nodes()
                deadline=time.monotonic()+5
                while not window.spots.get_children() and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual([window.spots.item(row,'values')[3] for row in window.spots.get_children()],['K1ABC'])
                self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/settings\r\n',b'sh/dxby W1AW\r\n',b'sh/mydx/100\r\n'])
            finally:release.set();window.quit_app();worker.join(2)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_cc_partial_call_search_displays_recent_result_over_socket(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        port=listener.getsockname()[1];seen=[];errors=[];release=threading.Event()
        def serve():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(5);stream=sock.makefile('rb')
                    sock.sendall(b'login: ');seen.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE CCC >\r\n')
                    seen.extend(stream.readline() for _ in range(5))
                    sock.sendall(b'CC11^14074.0^VK1ABC^30-Oct-2026^1234Z^Recent spot^W1AW^1\r\n')
                    release.wait(5);stream.close()
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=serve,daemon=True);worker.start()
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ');window.connect()
            deadline=time.monotonic()+5
            while window.status.get()!='Connected' and time.monotonic()<deadline:window.update();time.sleep(.01)
            self.assertEqual(window.status.get(),'Connected')
            window.search_field.set('DX call');window.search.set('K1');window.search_connected_nodes()
            deadline=time.monotonic()+5
            while not window.spots.get_children() and time.monotonic()<deadline:window.update();time.sleep(.01)
            self.assertEqual([window.spots.item(row,'values')[3] for row in window.spots.get_children()],['VK1ABC'])
            self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/settings\r\n',b'sh/dx/100 K1\r\n',b'sh/mydx/100\r\n'])
        finally:
            release.set();window.quit_app();worker.join(2)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_older_page_reaches_local_node_and_displays_its_response(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        seen=[];errors=[]
        def serve():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(5);stream=sock.makefile('rb')
                    sock.sendall(b'login: ');seen.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    seen.extend((stream.readline(),stream.readline(),stream.readline()))
                    sock.sendall(b'CC11^14074.0^K1ABC^30-Oct-2026^1234Z^Older spot^W1AW^291\r\nLZ3ZZ de NODE dxspider >\r\n')
                    time.sleep(.2);stream.close()
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=serve,daemon=True);worker.start()
        window=App();window.withdraw();window._save_settings=lambda:None
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            window.host.set('127.0.0.1');window.port.set(str(listener.getsockname()[1]));window.call.set('LZ3ZZ');window.connect()
            deadline=time.monotonic()+5
            while window.status.get()!='Connected' and time.monotonic()<deadline:window.update();time.sleep(.01)
            self.assertEqual(window.status.get(),'Connected')
            window.history_button.invoke();dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
            next(w for w in walk(dialog) if type(w) is ttk.Entry).delete(0,'end')
            next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Request spots').invoke()
            deadline=time.monotonic()+5
            while not window.rows and time.monotonic()<deadline:window.update();time.sleep(.01)
            worker.join(2)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/mydx 101-200\r\n'])
            self.assertEqual(window.rows[0][1].dx,'K1ABC')
            self.assertEqual(window.rows[0][1].comment,'Older spot')
        finally:window.quit_app();worker.join(1)

    def test_older_filtered_spot_ranges_are_bounded(self):
        self.assertEqual(older_filtered_spots_command(2), 'sh/mydx 101-200')
        self.assertEqual(older_filtered_spots_command(10), 'sh/mydx 901-1000')
        self.assertEqual(older_filtered_spots_command(2,'02'), 'sh/mydx/100 day 2')
        for day in ('0','100','-1','2\nbye','²',True):
            with self.subTest(day=day), self.assertRaises(ValueError):
                older_filtered_spots_command(2,day)
        for page in (0, 1, 11, True, '2'):
            with self.subTest(page=page), self.assertRaises(ValueError):
                older_filtered_spots_command(page)

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider parser')
    def test_older_filtered_day_reaches_pinned_history_parser(self):
        command=older_filtered_spots_command(2,'2')
        self.assertEqual(command,'sh/mydx/100 day 2')
        dispatch=subprocess.run(['perl','tools/check-dxspider-dispatch.pl',*command.split()],
                                cwd=Path(__file__).resolve().parent,capture_output=True,text=True,timeout=10)
        self.assertEqual(dispatch.returncode,0,dispatch.stderr)
        expanded=json.loads(dispatch.stdout)
        self.assertEqual(expanded,{'command':'show/dx','args':'filter 100 day 2'})
        with patch.dict('os.environ',{'DXSPIDER_TEST_SPOT_FILTER':'1'}):
            result=subprocess.run(['perl','tools/check-dxspider-history.pl',*expanded['args'].split()],
                                  cwd=Path(__file__).resolve().parent,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        parsed=json.loads(result.stdout)
        self.assertEqual((parsed['count'],parsed['days_ago']),('100','2'))
        self.assertTrue(parsed['node_filter_enabled'])

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider search')
    def test_older_day_search_uses_selected_day_and_node_filter(self):
        for mode in ('cache','files'):
            with self.subTest(mode=mode):
                result=subprocess.run(['perl','tools/check-dxspider-history-days.pl',mode],
                                      cwd=Path(__file__).resolve().parent,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout),['keep-start','keep-older'])

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider disk reader')
    def test_older_day_search_reads_real_temporary_history_files(self):
        result=subprocess.run(['perl','tools/check-dxspider-history-disk.pl'],
                              cwd=Path(__file__).resolve().parent,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),['K1AAA','K3CCC'])

    def test_older_spots_day_overrides_page(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='DXSpider'
            window.open_older_spots()
            dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
            widgets=list(walk(dialog));entry=next(w for w in widgets if type(w) is ttk.Entry)
            entry.delete(0,'end');entry.insert(0,'2')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Request spots').invoke()
            self.assertEqual(fake.commands,['sh/mydx/100 day 2'])
            self.assertIn('sort date or scroll',window.spot_search_note.get())
        finally:window.quit_app()

    def test_older_day_request_and_result_over_local_tcp(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        port=listener.getsockname()[1];seen=[];errors=[];release=threading.Event()
        def serve():
            try:
                sock,_=listener.accept()
                with sock,sock.makefile('rb') as stream:
                    sock.settimeout(5);sock.sendall(b'login: ');seen.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    seen.extend(stream.readline() for _ in range(3))
                    sock.sendall(b'CC11^14074^K1ABC^28-Oct-2026^1234Z^Older day^W1AW^291\r\n')
                    release.wait(5)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=serve,daemon=True);worker.start()
        window=App();window.withdraw();window._save_settings=lambda:None
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            window.host.set('127.0.0.1');window.port.set(str(port));window.call.set('LZ3ZZ');window.connect()
            deadline=time.monotonic()+5
            while window.status.get()!='Connected' and time.monotonic()<deadline:window.update();time.sleep(.01)
            self.assertEqual(window.status.get(),'Connected')
            window.history_button.invoke();dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
            widgets=list(walk(dialog));entry=next(w for w in widgets if type(w) is ttk.Entry);entry.delete(0,'end');entry.insert(0,'2')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Request spots').invoke()
            deadline=time.monotonic()+5
            while not window.rows and time.monotonic()<deadline:window.update();time.sleep(.01)
            self.assertEqual([spot.dx for _,spot in window.rows],['K1ABC'])
            self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/mydx/100 day 2\r\n'])
        finally:release.set();window.quit_app();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])

    def test_older_spots_defaults_to_selected_spots_dxspider_source(self):
        class Secondary(FakeConnection):
            def __init__(self, emit): super().__init__()
            def start(self, *args): pass
        window=App();window.withdraw();window._save_settings=lambda:None
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            window.connection=FakeConnection();window.status.set('Connected');window.dialect='DXSpider'
            with patch('app.Connection', Secondary):
                ident=window.start_secondary_node('other.invalid',7300,'LZ3ZZ-1')
                item=window.secondary_nodes[ident];item['status']='Connected';item['dialect']='DXSpider'
                window.add_spot(Spot('W1AW','14074','K1ABC','1234Z','CQ'),source=item['source'])
                window.render_spots();window.spots.selection_set(window.spots.get_children()[0])
                window.open_older_spots()
                dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
                node=next(w for w in walk(dialog) if isinstance(w,ttk.Combobox))
                self.assertEqual(node.get(),'other.invalid:7300 · LZ3ZZ-1')
                next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Request spots').invoke()
                self.assertEqual(item['connection'].commands,['sh/mydx/100 day 1'])
                self.assertEqual(window.connection.commands,[])
        finally:window.quit_app()

    def test_older_spots_activity_opens_selected_node_and_rechecks_connection(self):
        class Secondary(FakeConnection):
            def __init__(self, emit): super().__init__()
            def start(self, *args): pass
        window=App();window.withdraw();window._save_settings=lambda:None
        def walk(widget):
            for child in widget.winfo_children(): yield child; yield from walk(child)
        try:
            window.connection=FakeConnection();window.status.set('Connected');window.dialect='DXSpider'
            with patch('app.Connection',Secondary):
                ident=window.start_secondary_node('other.invalid',7300,'LZ3ZZ-1')
                item=window.secondary_nodes[ident];item['status']='Connected';item['dialect']='DXSpider'
                window.open_older_spots()
                dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
                widgets=list(walk(dialog));node=next(w for w in widgets if isinstance(w,ttk.Combobox))
                button=next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Open node activity')
                button.invoke();self.assertEqual(window.notebook.select(),str(window.console_page))
                node.set('other.invalid:7300 · LZ3ZZ-1')
                with patch.object(window,'open_secondary_activity') as show:
                    button.invoke();show.assert_called_once_with(ident)
                    item['epoch']+=1
                    with patch('app.messagebox.showerror') as error:button.invoke();error.assert_called_once()
                    show.assert_called_once_with(ident)
        finally:window.quit_app()

    def test_older_spots_dialog_sends_selected_page_and_rejects_stale_session(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='DXSpider'
            window.open_older_spots()
            dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
            widgets=list(walk(dialog));page=next(w for w in widgets if isinstance(w,ttk.Combobox) and 'spots 101' in str(w.cget('values')))
            self.assertEqual(str(page.cget('state')),'disabled')
            next(w for w in widgets if type(w) is ttk.Entry).delete(0,'end')
            self.assertEqual(str(page.cget('state')),'readonly')
            page.current(2)
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Request spots').invoke()
            self.assertEqual(fake.commands,['sh/mydx 301-400'])
            window.open_older_spots()
            dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
            window.generation+=1
            with patch('app.messagebox.showerror') as error:
                next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Request spots').invoke()
                error.assert_called_once()
            self.assertEqual(fake.commands,['sh/mydx 301-400'])
        finally: window.quit_app()

    def test_command_for_both_cluster_dialects(self):
        for dialect in ('CC Cluster', 'DXSpider'):
            self.assertEqual(history_command(dialect, ' w1aw/8 '), 'sh/dx/30 W1AW/8')
        self.assertEqual(history_command('CC Cluster','f*'), 'sh/dx/30 F*')
        self.assertEqual(history_command('DXSpider','*abc*'), 'sh/dx/30 *ABC*')
        self.assertEqual(history_command('DXSpider','w1?a*'), 'sh/dx/30 W1?A*')
        for text in ('', 'W1AW\nBYE', 'A B', '*', 'é', 'A'*33):
            with self.subTest(text=text), self.assertRaises(ValueError): history_command('CC Cluster', text)
        with self.assertRaises(ValueError): history_command('unknown', 'W1AW')
        for text in ('*ABC', 'W?AW', 'W**', 'A'*32+'*'):
            with self.subTest(text=text), self.assertRaises(ValueError): history_command('CC Cluster', text)
        for text in ('***', '?', 'W1*\rBYE'):
            with self.subTest(text=text), self.assertRaises(ValueError): history_command('DXSpider', text)

    def test_band_and_frequency_ranges_use_node_specific_syntax(self):
        self.assertEqual(history_command('CC Cluster','20m','Band'),'sh/dx/30 20')
        self.assertEqual(history_command('DXSpider','20','Band'),'sh/dx/30 on 20m')
        self.assertEqual(history_command('CC Cluster','4 m','Band'),'sh/dx/30 4')
        self.assertEqual(history_command('DXSpider','4m','Band'),'sh/dx/30 on 4m')
        for text,low,high in [('1.25m','220000','224000'),('220','220000','224000'),('70 cm','420000','450000'),('Microwave','500000','21000000')]:
            with self.subTest(band=text):
                self.assertEqual(history_command('CC Cluster',text,'Band'),f'sh/dx/30 {low}-{high}')
                self.assertEqual(history_command('DXSpider',text,'Band'),f'sh/dx/30 on {low}/{high}')
        self.assertEqual(history_command('CC Cluster','14000-14350','Frequency range'),'sh/dx/30 14000-14350')
        self.assertEqual(history_command('DXSpider','14000-14350','Frequency range'),'sh/dx/30 on 14000/14350')
        for mode,value in [('Band','999'),('Band','20m\nbye'),('Frequency range','14000-14000'),('Frequency range','14350-14000'),('Frequency range','1-2\nbye')]:
            with self.subTest(mode=mode,value=value),self.assertRaises(ValueError): history_command('DXSpider',value,mode)

    def test_history_count_is_validated_and_uses_selected_value(self):
        for dialect in ('CC Cluster','DXSpider'):
            self.assertEqual(history_command(dialect,'W1AW',count='100'),'sh/dx/100 W1AW')
            self.assertEqual(history_command(dialect,'20m','Band',count=10),
                             'sh/dx/10 '+('20' if dialect=='CC Cluster' else 'on 20m'))
            for value in ('0','101','-1','30\nBYE','',True):
                with self.subTest(dialect=dialect,value=value),self.assertRaises(ValueError):
                    history_command(dialect,'W1AW',count=value)

    def test_dxspider_history_can_start_days_ago(self):
        self.assertEqual(history_command('DXSpider','W1AW',count=50,days_ago='2'),'sh/dx/50 W1AW day 2')
        self.assertEqual(history_command('DXSpider','20m','Band',days_ago=14),'sh/dx/30 on 20m day 14')
        self.assertEqual(history_command('DXSpider','14000-14350','Frequency range',days_ago='99'),'sh/dx/30 on 14000/14350 day 99')
        self.assertEqual(history_command('CC Cluster','W1AW',days_ago=''),'sh/dx/30 W1AW')
        for value in ('0','100','-1','2\nBYE',True):
            with self.subTest(value=value),self.assertRaises(ValueError):history_command('DXSpider','W1AW',days_ago=value)
        with self.assertRaises(ValueError):history_command('CC Cluster','W1AW',days_ago='2')

    def test_history_dialog_sends_dxspider_days_back(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='DXSpider'
            window.open_spot_history();dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel');widgets=list(walk(dialog))
            entries=[w for w in widgets if type(w) is ttk.Entry];entries[0].insert(0,'W1AW');entries[1].insert(0,'2')
            self.assertEqual(fake.commands,[])
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Find spots').invoke()
            self.assertEqual(fake.commands,['sh/dx/30 W1AW day 2'])
        finally:window.quit_app()

    def test_history_days_field_is_disabled_for_cc_cluster(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            window.connection=FakeConnection();window.status.set('Connected');window.dialect='CC Cluster'
            window.open_spot_history();dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
            entries=[w for w in walk(dialog) if type(w) is ttk.Entry]
            self.assertEqual(str(entries[1]['state']),'disabled')
        finally:window.quit_app()

    def test_history_dialog_sends_selected_count(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='DXSpider'
            window.open_spot_history();dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
            widgets=list(walk(dialog));next(w for w in widgets if type(w) is ttk.Entry).insert(0,'W1AW')
            count=next(w for w in widgets if isinstance(w,ttk.Combobox) and tuple(w.cget('values'))==('10','30','50','100'))
            count.set('100')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Find spots').invoke()
            self.assertEqual(fake.commands,['sh/dx/100 W1AW'])
        finally:window.quit_app()

    def test_stale_request_cannot_reach_a_new_connection(self):
        window=App(); window.withdraw(); window._save_settings=lambda:None
        try:
            fake=FakeConnection(); window.connection=fake; window.status.set('Connected'); window.dialect='CC Cluster'
            generation=window.generation
            window.request_spot_history(generation, 'W1AW')
            self.assertEqual(fake.commands,['sh/dx/30 W1AW'])
            window.generation += 1
            with self.assertRaises(OSError): window.request_spot_history(generation,'W1AW')
            self.assertEqual(fake.commands,['sh/dx/30 W1AW'])
        finally: window.quit_app()

    def test_dialog_uses_selected_spot_and_does_not_send_until_submit(self):
        window=App(); window.withdraw(); window._save_settings=lambda:None
        try:
            fake=FakeConnection(); window.connection=fake; window.status.set('Connected'); window.dialect='CC Cluster'
            window.add_spot(Spot('S','14074','W1AW/8','1200Z',''))
            window.render_spots(); window.spots.selection_set(window.spots.get_children()[0])
            window.open_spot_history(); dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
            def walk(w):
                for child in w.winfo_children():
                    yield child
                    yield from walk(child)
            widgets=list(walk(dialog)); entry=next(w for w in widgets if type(w) is ttk.Entry)
            self.assertEqual(entry.get(),'W1AW/8'); self.assertEqual(fake.commands,[])
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Find spots').invoke()
            self.assertEqual(fake.commands,['sh/dx/30 W1AW/8'])
        finally: window.quit_app()

    def test_dialog_submits_band_to_the_selected_dialect(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='DXSpider'
            window.open_spot_history();dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
            def walk(w):
                for child in w.winfo_children():yield child;yield from walk(child)
            widgets=list(walk(dialog));selector=next(w for w in widgets if isinstance(w,ttk.Combobox));selector.set('Band')
            entry=next(w for w in widgets if type(w) is ttk.Entry);entry.insert(0,'20m')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Find spots').invoke()
            self.assertEqual(fake.commands,['sh/dx/30 on 20m'])
        finally:window.quit_app()

    def test_history_defaults_to_selected_secondary_and_rejects_reconnect(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def send(self,command):self.commands.append(command)
            def close(self):pass
        window=App();window.withdraw();window._save_settings=lambda:None
        def walk(w):
            for child in w.winfo_children():yield child;yield from walk(child)
        try:
            main=FakeConnection();window.connection=main;window.connection_target=('main.invalid',7300,'LZ3ZZ')
            window.spot_source=('main.invalid',7300,'LZ3ZZ');window.dialect='CC Cluster';window.status.set('Connected')
            with patch('app.Connection',Secondary):
                ident=window.start_secondary_node('other.invalid',7301,'LZ3ZZ')
                item=window.secondary_nodes[ident];old=item['connection'];old.emit('dialect','DXSpider');old.emit('status','Connected');window.poll_secondary_nodes()
                window.add_spot(Spot('S','14074','W1AW','1200Z',''),source=item['source']);window.render_spots()
                window.spots.selection_set(window.spots.get_children()[0]);window.open_spot_history()
                dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel');widgets=list(walk(dialog))
                selectors=[w for w in widgets if isinstance(w,ttk.Combobox)]
                self.assertIn('other.invalid:7301',selectors[1].get())
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Find spots').invoke()
                self.assertEqual(old.commands,['sh/dx/30 W1AW']);self.assertEqual(main.commands,[])
                window.open_spot_history();dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
                window._start_secondary_attempt(ident)
                with patch('app.messagebox.showerror') as error:
                    next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Find spots').invoke()
                    error.assert_called_once()
                self.assertEqual(item['connection'].commands,[]);self.assertEqual(main.commands,[])
        finally:window.quit_app()

    def test_history_can_use_secondary_while_main_is_disconnected(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def send(self,command):self.commands.append(command)
            def close(self):pass
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            with patch('app.Connection',Secondary):
                ident=window.start_secondary_node('other.invalid',7301,'LZ3ZZ');conn=window.secondary_nodes[ident]['connection']
                conn.emit('dialect','DXSpider');conn.emit('status','Connected');window.poll_secondary_nodes()
                window.open_spot_history();dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
                panel=dialog.winfo_children()[0];entry=next(w for w in panel.winfo_children() if type(w) is ttk.Entry);entry.insert(0,'W1AW')
                buttons=panel.winfo_children()[-1];next(w for w in buttons.winfo_children() if w.cget('text')=='Find spots').invoke()
                self.assertEqual(conn.commands,['sh/dx/30 W1AW'])
        finally:window.quit_app()


if __name__=='__main__': unittest.main()
