import unittest
import time
from collections import deque
import tempfile
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import patch
import app as app_module
from app import App, spot_order
from protocol import Spot, Connection, CommandRateLimitError


class FakeConnection:
    def __init__(self, emit): self.emit=emit
    def start(self, *args): pass
    def close(self): pass


class SpotViewTests(unittest.TestCase):
    def test_same_node_with_different_calls_has_distinct_spot_sources(self):
        a=self.app
        first=('cluster.example',7300,'LZ3ZZ-1')
        second=('cluster.example',7300,'LZ3ZZ-2')
        a.add_spot(Spot('W1AW','14074','K1ABC','1200Z','first'),source=first)
        a.add_spot(Spot('W1AW','7074','K2ABC','1201Z','second'),source=second)
        a.render_spots()
        self.assertNotEqual(a.source_label(first),a.source_label(second))
        for source,call in ((first,'K1ABC'),(second,'K2ABC')):
            with self.subTest(source=source):
                a.spot_source_filter.set(a.source_label(source));a.render_spots()
                rows=a.spots.get_children()
                self.assertEqual([a.spots.item(row,'values')[3] for row in rows],[call])
                self.assertEqual(a.spot_sources[rows[0]],source)

    def test_invalid_incoming_call_does_not_inherit_another_stations_qsl_status(self):
        a=self.app
        a.lotw_index={'SS1':'2026-10-30T12:00'}
        a.eqsl_index=SimpleNamespace(calls={'SS1':'2026-10-30'},list_time='2026-10-30')
        a.add_spot(Spot('W1AW','14074','ß1','1200Z','CQ'))
        a.render_spots();row=a.spots.get_children()[0]
        self.assertEqual(a.spots.item(row,'values')[3],'ß1')
        a.spots.selection_set(row);a.show_spot_detail()
        detail=a.spot_detail.get('1.0','end')
        self.assertNotIn('LoTW activity',detail)
        self.assertNotIn('eQSL AG:',detail)

    def test_all_sources_status_tracks_the_connection_for_the_displayed_source(self):
        a=self.app;source=('node.invalid',7300,'LZ3ZZ')
        a.spot_source=source;a.connection_target=None;a.status.set('Disconnected')
        a.secondary_nodes[1]={'source':source,'status':'Connected','connection':object()}
        try:
            a.update_spot_source();self.assertIn(' · connected',a.spot_source_note.get())
            a.secondary_nodes[1]['status']='Disconnected'
            a.update_spot_source();self.assertIn(' · not connected',a.spot_source_note.get())
            a.connection=object();a.connection_target=('other.invalid',7300,'LZ3ZZ');a.status.set('Connected')
            a.update_spot_source();self.assertIn(' · not connected',a.spot_source_note.get())
        finally:
            a.connection=None;a.connection_target=None;a.secondary_nodes.clear();a.status.set('Disconnected')

    def test_login_read_with_first_spot_preserves_node_source_and_dialect(self):
        class Socket:
            def __init__(self):
                self.reads=iter((b'login: ',b'LZ3ZZ de NODE dxspider >\r\nCC11^14074^K1ABC^30-Oct-2026^1234Z^CQ^W1AW^226\r\n',b''))
            def settimeout(self,value):pass
            def recv(self,size):return next(self.reads)
            def sendall(self,data):pass
            def close(self):pass
        a=self.app;source=('node.invalid',7300,'LZ3ZZ');a.spot_source=source;a.connection_target=source
        connection=Connection(lambda kind,value:a.events.put((a.generation,kind,value)));a.connection=connection
        try:
            with patch('protocol.socket.create_connection',return_value=Socket()):connection._run(*source)
            a.after_cancel(a.poll_after);a.poll()
            self.assertEqual(len(a.rows),1)
            ident,spot=a.rows[0]
            self.assertEqual(a.spot_sources[ident],source);self.assertEqual(spot.dx_dxcc,'226')
            a.spot_source_filter.set(a.source_label(source));a.render_spots()
            self.assertEqual(len(a.spots.get_children()),1)
        finally:a.connection=None;a.status.set('Disconnected')

    def test_cc_login_read_with_first_spot_does_not_treat_cc_fields_as_dxspider_country(self):
        class Socket:
            def __init__(self):
                self.reads=iter((b'login: ',b'LZ3ZZ de NODE CCC >\r\nCC11^14074^K1ABC^30-Oct-2026^1234Z^CQ^W1AW^226\r\n',b''))
            def settimeout(self,value):pass
            def recv(self,size):return next(self.reads)
            def sendall(self,data):pass
            def close(self):pass
        a=self.app;source=('cc.invalid',7300,'LZ3ZZ');a.spot_source=source;a.connection_target=source
        connection=Connection(lambda kind,value:a.events.put((a.generation,kind,value)));a.connection=connection
        try:
            with patch('protocol.socket.create_connection',return_value=Socket()):connection._run(*source)
            a.after_cancel(a.poll_after);a.poll();self.assertEqual(len(a.rows),1)
            ident,spot=a.rows[0];self.assertEqual(a.spot_sources[ident],source)
            self.assertEqual(spot.dx_dxcc,'');self.assertEqual(spot.metadata_fields[0],'226')
        finally:a.connection=None;a.status.set('Disconnected')

    def test_hidden_text_is_local_persisted_and_explained(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app_module,'SETTINGS',Path(directory)/'settings.json'):
            a=App();a.withdraw()
            try:
                a.add_spot(Spot('W1AW','14074','K1ABC','1200Z','CQ FT8'))
                a.add_spot(Spot('K2AAA','7074','K2ABC','1201Z','CW'))
                def walk(widget):
                    for child in widget.winfo_children():yield child;yield from walk(child)
                def edit_text():
                    a.open_display_bands();outer=next(w for w in a.winfo_children() if isinstance(w,tk.Toplevel))
                    next(w for w in walk(outer) if isinstance(w,ttk.Button) and w.cget('text')=='Hide text…').invoke()
                    dialog=next(w for w in outer.winfo_children() if isinstance(w,tk.Toplevel))
                    widgets=list(walk(dialog));entries=[w for w in widgets if isinstance(w,ttk.Entry)]
                    entries[1].insert(0,'w1aw');entries[2].insert(0,'cw')
                    next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Use text').invoke()
                    return outer
                outer=edit_text()
                self.assertEqual(a.spot_hidden_text,{'dx':'','spotter':'','comment':''})
                next(w for w in walk(outer) if isinstance(w,ttk.Button) and w.cget('text')=='Cancel').invoke()
                self.assertEqual(a.spot_hidden_text,{'dx':'','spotter':'','comment':''})
                outer=edit_text()
                next(w for w in walk(outer) if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke()
                self.assertEqual(len(a.rows),2);self.assertFalse(a.spots.get_children())
                self.assertIn('hidden text',a.spot_empty_hint.get());self.assertIn('text hidden',a.hidden_bands_note.get())
                self.assertTrue(a.empty_display_options_button.winfo_manager())
                a.reset_spot_filters();self.assertFalse(a.spots.get_children())
            finally:a.quit_app()
            b=App();b.withdraw()
            try:
                self.assertEqual(b.spot_hidden_text,{'dx':'','spotter':'w1aw','comment':'cw'})
                b.add_spot(Spot('W1AW','14074','K1ABC','1200Z','CQ FT8'))
                b.add_spot(Spot('W2AW','14074','K2ABC','1201Z','CQ'))
                b.render_spots();self.assertEqual([b.spots.item(row,'values')[3] for row in b.spots.get_children()],['K2ABC'])
                b.open_display_bands();outer=next(w for w in b.winfo_children() if isinstance(w,tk.Toplevel))
                next(w for w in walk(outer) if isinstance(w,ttk.Button) and w.cget('text')=='Hide text…').invoke()
                dialog=next(w for w in outer.winfo_children() if isinstance(w,tk.Toplevel))
                next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Clear fields').invoke()
                next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Use text').invoke()
                self.assertEqual(b.spot_hidden_text['spotter'],'w1aw')
                next(w for w in walk(outer) if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke()
                self.assertEqual(len(b.spots.get_children()),2)
                self.assertNotIn('text hidden',b.hidden_bands_note.get())
            finally:b.quit_app()

    def setUp(self):
        self.app=App(); self.app.withdraw(); self.app._save_settings=lambda:True

    def tearDown(self): self.app.quit_app()

    def test_spot_copy_uses_raw_values_and_right_click_selects_row(self):
        a=self.app
        a.add_spot(Spot('W1AW','14074.050','K1ABC','1200Z','CQ'),source=('archive',0,'saved.csv'))
        a.lotw_index={'K1ABC':'2026-10-30T12:00'};a.render_spots();row=a.spots.get_children()[0]
        self.assertIn('[saved]',a.spots.item(row,'values')[3])
        with patch.object(a.spots,'identify_row',return_value=row),patch.object(a.spot_copy_menu,'tk_popup') as popup,patch.object(a.spot_copy_menu,'grab_release'):
            self.assertEqual(a.open_spot_copy_menu(SimpleNamespace(y=10,x_root=20,y_root=30)),'break')
            popup.assert_called_once_with(20,30)
        self.assertEqual(a.spots.selection(),(row,))
        with patch.object(a,'clipboard_clear') as clear,patch.object(a,'clipboard_append') as append:
            for field,value in [('dx','K1ABC'),('frequency','14074.050'),('spotter','W1AW')]:
                self.assertEqual(a.copy_selected_spot(field),'break');append.assert_called_with(value)
            self.assertEqual(clear.call_count,3)
            a.spots.selection_remove(row);a.copy_selected_spot('dx');self.assertEqual(clear.call_count,3)

    def test_right_click_empty_spot_area_does_not_open_menu(self):
        a=self.app
        with patch.object(a.spots,'identify_row',return_value=''),patch.object(a.spot_copy_menu,'tk_popup') as popup:
            self.assertEqual(a.open_spot_copy_menu(SimpleNamespace(y=10,x_root=20,y_root=30)),'break')
            popup.assert_not_called()

    def test_commentless_plain_spot_appears_in_live_list(self):
        a=self.app
        a.events.put((a.generation,'data','DX de W1AW: 14074 K1ABC 1234Z\r\n'))
        a.after_cancel(a.poll_after);a.poll()
        self.assertEqual(len(a.rows),1)
        self.assertEqual((a.rows[0][1].dx,a.rows[0][1].comment),('K1ABC',''))
        self.assertEqual(a.spots.item(a.spots.get_children()[0],'values')[3],'K1ABC')

    def test_other_band_selects_only_unclassified_spots(self):
        a=self.app
        a.add_spot(Spot('W1AW','14074','K1ABC','1200Z','CQ'))
        a.add_spot(Spot('W1AW','1000','K2ABC','1200Z','CQ'))
        self.assertIn('Other',a.band_box.cget('values'))
        a.band.set('Other')
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K2ABC'])
        a.search.set('K1ABC');self.assertFalse(a.spots.get_children())
        a.band.set('All bands');self.assertEqual(len(a.spots.get_children()),1)

    def test_search_filters_unclassified_spots_in_each_field(self):
        a=self.app
        a.add_spot(Spot('W1AW','1000','K2ABC','1200Z','CQ'))
        a.add_spot(Spot('W2AW','14074','K1ABC','1201Z','FT8'))
        for field in ('All fields','DX call','Spotter','Comment','Frequency'):
            with self.subTest(field=field):
                a.search_field.set(field);a.search.set('not-present');a.render_spots()
                self.assertFalse(a.spots.get_children())
        a.search_field.set('All fields');a.search.set('20m');a.render_spots()
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K1ABC'])
        a.search.set('Other');a.render_spots()
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K2ABC'])

    def test_empty_view_reset_preserves_saved_hidden_bands(self):
        a=self.app
        a.add_spot(Spot('W1AW','14074','K1ABC','1200Z','CQ'),source=('node',7300,'LZ3ZZ'))
        a.add_spot(Spot('W1AW','7074','K2ABC','1200Z','CQ'),source=('other',7300,'LZ3ZZ'))
        a.hidden_bands={'40 m'};a.search.set('missing');a.band.set('20 m');a.spot_source_filter.set('node:7300 · LZ3ZZ');a.render_spots()
        self.assertFalse(a.spots.get_children());self.assertIn('search',a.spot_empty_hint.get())
        a.reset_spot_filters_button.invoke()
        self.assertEqual((a.search.get(),a.search_field.get(),a.band.get(),a.spot_source_filter.get()),('','All fields','All bands','All sources'))
        self.assertEqual(a.hidden_bands,{'40 m'})
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K1ABC'])

    def test_empty_band_explains_active_display_filters(self):
        a=self.app
        a.add_spot(Spot('W1AW','14074','K1ABC','1200Z','CQ'),source=('node',7300,'LZ3ZZ'))
        a.band.set('20 m');a.spot_marker_filter='Marked -# only';a.search.set('missing');a.render_spots()
        self.assertFalse(a.spots.get_children())
        self.assertEqual(a.band_shortcut_counts['20 m'].cget('text'),'1')
        self.assertIn('Band counts include all retained spots',a.spot_empty_hint.get())
        self.assertIn('search',a.spot_empty_hint.get())
        self.assertIn('bands',a.spot_empty_hint.get())
        self.assertIn('spotter marker',a.spot_empty_hint.get())
        self.assertTrue(a.empty_display_options_button.winfo_manager())
        a.reset_spot_filters_button.invoke()
        self.assertIn('spotter marker',a.spot_empty_hint.get())
        self.assertTrue(a.empty_display_options_button.winfo_manager())
        a.empty_display_options_button.invoke()
        dialog=next(w for w in a.winfo_children() if isinstance(w,tk.Toplevel))
        self.assertEqual(dialog.title(),'Spot display options')
        dialog.destroy()

    def test_search_enter_requests_selected_nodes_without_sending_while_typing(self):
        class Node:
            def __init__(self): self.commands=[]
            def send_batch(self,commands): self.commands.extend(commands)
        a=self.app; main=Node(); other=Node()
        a.connection=main;a.status.set('Connected');a.dialect='CC Cluster';a.spot_source=('main',7300,'LZ3ZZ')
        a.secondary_nodes[1]={'connection':other,'status':'Connected','dialect':'DXSpider','source':('other',7300,'LZ3ZZ')}
        try:
            a.search.set('K1ABC')
            self.assertEqual(main.commands,[]);self.assertEqual(other.commands,[])
            a.search_connected_nodes()
            self.assertEqual(main.commands,["sh/dx/100 K1ABC","sh/dx 'K1ABC'",'sh/dxby K1ABC','sh/mydx/100'])
            self.assertEqual(other.commands,['sh/dx/100 ( call {K1ABC} or by {K1ABC} or info {K1ABC} or origin {K1ABC} )','sh/mydx/100'])
            a.search_field.set('Comment');a.spot_source_filter.set(a.source_label(('other',7300,'LZ3ZZ')))
            a.search.set('CQ TEST');a.search_connected_nodes()
            self.assertEqual(main.commands,["sh/dx/100 K1ABC","sh/dx 'K1ABC'",'sh/dxby K1ABC','sh/mydx/100'])
            self.assertEqual(other.commands[-1],r'sh/dx/100 info {CQ\x20TEST}')
            a.spot_source_filter.set('All sources');a.search_field.set('Spotter');a.search.set('K1');a.search_connected_nodes()
            self.assertEqual(main.commands[-1],'sh/mydx/100')
            self.assertEqual(main.commands[-2],'sh/dxby K1')
            self.assertEqual(other.commands[-1],'sh/dx/100 by {K1}')
            a.search.set('K1/2');a.search_connected_nodes();self.assertEqual(other.commands[-1],r'sh/dx/100 by {K1\x2f2}')
            a.search_field.set('Comment');a.search.set('FT8');a.search_connected_nodes()
            self.assertEqual(main.commands[-1],"sh/dx 'FT8'");self.assertEqual(other.commands[-1],'sh/dx/100 info {FT8}')
            a.search.set('CQ FT8');a.search_connected_nodes();self.assertEqual(other.commands[-1],r'sh/dx/100 info {CQ\x20FT8}')
            a.search.set('café');a.search_connected_nodes();self.assertEqual(other.commands[-1],'sh/mydx/100')
            self.assertIn('some matches limited to recent node-filtered spots',a.spot_search_note.get())
            a.search_field.set('Frequency');a.search.set('14074.05');a.search_connected_nodes()
            self.assertEqual(main.commands[-2:],['sh/dx/100 14073-14075','sh/mydx/100'])
            self.assertEqual(other.commands[-2:],['sh/dx/100 on 14073/14075','sh/mydx/100'])
            a.search_field.set('All fields');a.search_connected_nodes()
            self.assertEqual(main.commands[-2:],['sh/dx/100 14073-14075','sh/mydx/100'])
            self.assertEqual(other.commands[-2:],['sh/dx/100 on 14073/14075','sh/mydx/100'])
            before=len(other.commands);a.search.set('291');a.search_connected_nodes()
            self.assertEqual(other.commands[before:],['sh/dx/100 ( call {291} or by {291} or info {291} or origin {291} )',
                'sh/dx/100 call_dxcc 291','sh/dx/100 by_dxcc 291','sh/dx/100 on 290/292','sh/mydx/100'])
            a.search_field.set('Frequency')
            a.search.set('74.05x');a.search_connected_nodes();self.assertEqual(other.commands[-1],'sh/mydx/100')
            a.search_field.set('Origin');a.search.set('GB7');a.search_connected_nodes()
            self.assertEqual(other.commands[-1],'sh/dx/100 origin {GB7}')
            a.search_field.set('DX country');a.search.set('291');a.search_connected_nodes()
            self.assertEqual(main.commands[-1],'sh/mydx/100')
            self.assertEqual(other.commands[-2:],['sh/dx/100 call_dxcc 291','sh/mydx/100'])
            a.search_field.set('Spotter country');a.search_connected_nodes()
            self.assertEqual(other.commands[-2:],['sh/dx/100 by_dxcc 291','sh/mydx/100'])
            a.search_field.set('Origin')
            a.search.set('café');a.search_connected_nodes();self.assertEqual(other.commands[-1],'sh/mydx/100')
            a.search.set('next');self.assertIn('press Enter',a.spot_search_note.get())
            a.search_field.set('DX call');a.search.set('K1');a.search_connected_nodes()
            self.assertEqual(main.commands[-2:],['sh/dx/100 K1','sh/mydx/100'])
            self.assertIn('recent node-filtered spots',a.spot_search_note.get())
            a.search_field.set('DX call');a.search.set('K1*');a.search_connected_nodes()
            self.assertEqual(other.commands[-1],r'sh/dx/100 call {K1\x2a}')
            self.assertEqual(main.commands[-1],'sh/mydx/100')
            self.assertIn('recent node-filtered spots',a.spot_search_note.get())
            before=len(main.commands)
            a.search.set('ß1');a.search_connected_nodes()
            self.assertEqual(main.commands[before:],['sh/mydx/100'])
            self.assertIn('recent node-filtered spots',a.spot_search_note.get())
        finally:
            a.connection=None;a.secondary_nodes.clear();a.status.set('Disconnected')

    def test_band_search_requests_history_from_each_selected_node(self):
        class Node:
            def __init__(self):self.commands=[]
            def send_batch(self,commands):self.commands.extend(commands)
        a=self.app;main=Node();other=Node()
        a.connection=main;a.status.set('Connected');a.dialect='CC Cluster';a.spot_source=('main',7300,'LZ3ZZ')
        a.secondary_nodes[1]={'connection':other,'status':'Connected','dialect':'DXSpider','source':('other',7300,'LZ3ZZ')}
        try:
            a.search.set('20 m');a.search_connected_nodes()
            self.assertIn('sh/dx/100 20',main.commands)
            self.assertIn('sh/dx/100 on 20m',other.commands)
            before=len(other.commands);a.search.set('20m');a.search_connected_nodes()
            self.assertIn('sh/dx/100 on 20m',other.commands[before:])
            before=len(other.commands);a.search.set('20');a.search_connected_nodes()
            self.assertNotIn('sh/dx/100 on 20m',other.commands[before:])
            a.search.set('70 cm');a.spot_source_filter.set(a.source_label(('other',7300,'LZ3ZZ')))
            main_before=len(main.commands);a.search_connected_nodes()
            self.assertEqual(len(main.commands),main_before)
            self.assertIn('sh/dx/100 on 420000/450000',other.commands)
            before=len(other.commands);a.search.set('70cm');a.search_connected_nodes()
            self.assertIn('sh/dx/100 on 420000/450000',other.commands[before:])
            a.search.set('20 m');a.spot_source_filter.set('All sources')
            a.add_spot(Spot('W1AW','14074.0','K1ABC','1234Z','CQ',date='30-Oct-2026'));a.render_spots()
            self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K1ABC'])
        finally:
            a.connection=None;a.secondary_nodes.clear();a.status.set('Disconnected')

    def test_search_result_arriving_from_node_is_visible(self):
        class Node:
            def __init__(self): self.commands=[]
            def send_batch(self,commands): self.commands.extend(commands)
        a=self.app;node=Node();a.connection=node;a.status.set('Connected');a.dialect='DXSpider'
        a.spot_source=('node',7300,'LZ3ZZ')
        try:
            a.search_field.set('DX call');a.search.set('K1ABC');a.search_connected_nodes()
            self.assertEqual(node.commands,['sh/dx/100 call {K1ABC}'])
            a.events.put((a.generation,'data','CC11^14074.0^K1ABC^30-Oct-2026^1234Z^Older spot^W1AW^291\r\n'))
            a.after_cancel(a.poll_after);a.poll()
            self.assertEqual(len(a.spots.get_children()),1)
            self.assertEqual(a.spots.item(a.spots.get_children()[0],'values')[3],'K1ABC')
        finally:
            a.connection=None;a.status.set('Disconnected')

    def test_search_reports_a_rejected_batch_without_queuing_part(self):
        class Node:
            def __init__(self): self.commands=[]
            def send_batch(self,commands): raise OSError('Command queue is full')
        a=self.app;node=Node();a.connection=node;a.status.set('Connected');a.dialect='DXSpider'
        a.spot_source=('node',7300,'LZ3ZZ')
        try:
            a.search.set('K1ABC');a.search_connected_nodes()
            self.assertEqual(node.commands,[])
            self.assertIn('0 node(s) queued',a.spot_search_note.get())
            self.assertIn('some searches failed',a.spot_search_note.get())
        finally:
            a.connection=None;a.status.set('Disconnected')

    def test_search_retries_rate_limited_node_without_resending_other_node(self):
        class Node:
            def __init__(self,limited):self.limited=limited;self.commands=[]
            def send_batch(self,commands):
                if self.limited:raise CommandRateLimitError('DXSpider command limit reached. Wait before trying again.')
                self.commands.append(tuple(commands))
        a=self.app;node=Node(True);other=Node(False);a.connection=node;a.status.set('Connected');a.dialect='DXSpider'
        a.spot_source=('node',7300,'LZ3ZZ')
        a.secondary_nodes[1]={'connection':other,'status':'Connected','dialect':'DXSpider','source':('other',7300,'LZ3ZZ')}
        try:
            a.search.set('K1ABC');a.search_connected_nodes()
            self.assertIn('1 node(s) queued',a.spot_search_note.get())
            self.assertIn('1 waiting',a.spot_search_note.get())
            self.assertEqual(len(other.commands),1);self.assertEqual(node.commands,[])
            request=a.pending_spot_searches[a.spot_source];a.after_cancel(request['after']);node.limited=False
            a.retry_spot_search(a.spot_source,request)
            self.assertEqual(len(node.commands),1);self.assertEqual(len(other.commands),1)
            self.assertFalse(a.pending_spot_searches);self.assertIn('2 node(s) queued',a.spot_search_note.get())
        finally:
            a.connection=None;a.secondary_nodes.clear();a.status.set('Disconnected')

    def test_search_edit_or_connection_change_cancels_delayed_request(self):
        class Node:
            def __init__(self):self.limited=True;self.commands=[]
            def send_batch(self,commands):
                if self.limited:raise CommandRateLimitError('limit')
                self.commands.append(tuple(commands))
        a=self.app;node=Node();a.connection=node;a.status.set('Connected');a.dialect='DXSpider';a.spot_source=('node',7300,'LZ3ZZ')
        try:
            a.search.set('K1');a.search_connected_nodes();request=a.pending_spot_searches[a.spot_source]
            a.search.set('K2');node.limited=False;a.retry_spot_search(a.spot_source,request)
            self.assertEqual(node.commands,[]);self.assertFalse(a.pending_spot_searches)
            node.limited=True;a.search_connected_nodes();request=a.pending_spot_searches[a.spot_source]
            a.after_cancel(request['after']);a.connection=Node();node.limited=False;a.retry_spot_search(a.spot_source,request)
            self.assertEqual(node.commands,[]);self.assertIn('some searches failed',a.spot_search_note.get())
        finally:a.connection=None;a.status.set('Disconnected')

    def test_search_retry_runs_from_timer_and_expires_if_limit_persists(self):
        class Node:
            def __init__(self):self.limited=True;self.commands=[]
            def send_batch(self,commands):
                if self.limited:raise CommandRateLimitError('limit')
                self.commands.append(tuple(commands))
        a=self.app;node=Node();a.connection=node;a.status.set('Connected');a.dialect='DXSpider';a.spot_source=('node',7300,'LZ3ZZ')
        try:
            a.search.set('K1');a.search_connected_nodes();node.limited=False
            deadline=time.monotonic()+3
            while not node.commands and time.monotonic()<deadline:a.update();time.sleep(.01)
            self.assertEqual(len(node.commands),1);self.assertFalse(a.pending_spot_searches)
            node.limited=True;a.search_connected_nodes();request=a.pending_spot_searches[a.spot_source]
            a.after_cancel(request['after']);request['deadline']=0;a.retry_spot_search(a.spot_source,request)
            self.assertFalse(a.pending_spot_searches);self.assertIn('some searches failed',a.spot_search_note.get())
            self.assertIn('did not clear in time',a.transcript.get('1.0','end'))
        finally:a.connection=None;a.status.set('Disconnected')

    def test_empty_filter_actions_fit_in_minimum_height_spot_table(self):
        a=self.app;a.geometry('780x560');a.deiconify()
        a.add_spot(Spot('W1AW','14074','K1ABC','1200Z','CQ'))
        a.hidden_bands={'20 m'};a.spot_marker_filter='Marked -# only';a.search.set('missing');a.render_spots();a.update()
        for _ in range(2):
            for button in (a.reset_spot_filters_button,a.empty_display_options_button):
                self.assertTrue(button.winfo_ismapped())
                self.assertGreaterEqual(button.winfo_rooty(),a.spots.winfo_rooty())
                self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),a.spots.winfo_rooty()+a.spots.winfo_height())
            a.switch_theme();a.update()

    def test_band_shortcuts_match_dropdown_and_preserve_other_filters(self):
        a=self.app
        a.add_spot(Spot('W1AW','14074','K1ABC','1200Z','CQ'))
        a.add_spot(Spot('W1AW','7074','K2ABC','1200Z','CQ'))
        a.add_spot(Spot('W1AW','18100','K3ABC','1200Z','CQ'))
        a.search.set('CQ')
        a.band_shortcut_buttons['20 m'].invoke()
        self.assertEqual(a.band.get(),'20 m')
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K1ABC'])
        self.assertEqual(a.band_shortcut_buttons['20 m'].cget('style'),'BandSelected.TButton')
        a.band.set('17 m')
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K3ABC'])
        self.assertEqual(a.band_shortcut_buttons['17 m'].cget('style'),'BandSelected.TButton')
        a.hidden_bands.add('40 m');a.band_shortcut_buttons['40 m'].invoke()
        self.assertEqual({a.spots.item(row,'values')[3] for row in a.spots.get_children()},{'K2ABC','K3ABC'})
        a.band_shortcut_buttons['All bands'].invoke()
        self.assertEqual(len(a.spots.get_children()),2)

    def test_band_buttons_toggle_independently_and_all_restores_preferences(self):
        a=self.app;a.hidden_bands={'40 m'}
        for freq,call in [('14074','K1ABC'),('7074','K2ABC'),('18100','K3ABC')]:
            a.add_spot(Spot('W1AW',freq,call,'1200Z','CQ'))
        a.band_shortcut_buttons['20 m'].invoke();a.band_shortcut_buttons['40 m'].invoke()
        self.assertEqual(a.selected_bands,{'20 m','40 m'})
        self.assertEqual({a.spots.item(row,'values')[3] for row in a.spots.get_children()},{'K1ABC','K2ABC'})
        self.assertEqual(a.band_shortcut_buttons['20 m'].cget('style'),'BandSelected.TButton')
        self.assertEqual(a.band_shortcut_buttons['40 m'].cget('style'),'BandSelected.TButton')
        a.band_shortcut_buttons['20 m'].invoke()
        self.assertEqual(a.selected_bands,{'40 m'})
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K2ABC'])
        a.band_shortcut_buttons['40 m'].invoke()
        self.assertEqual(a.selected_bands,set());self.assertFalse(a.spots.get_children())
        a.band_shortcut_buttons['All bands'].invoke()
        self.assertIsNone(a.selected_bands)
        self.assertEqual({a.spots.item(row,'values')[3] for row in a.spots.get_children()},{'K1ABC','K3ABC'})
        self.assertEqual(a.hidden_bands,{'40 m'})

    def test_focused_band_buttons_toggle_with_space(self):
        a=self.app;a.deiconify();a.geometry('900x700');a.update()
        for name in ('20 m','40 m','20 m'):
            button=a.band_shortcut_buttons[name];button.focus_force();a.update()
            button.event_generate('<KeyPress-space>');button.event_generate('<KeyRelease-space>');a.update()
        self.assertEqual(a.selected_bands,{'40 m'})

    def test_control_f_opens_spot_view_and_focuses_search(self):
        a=self.app;a.deiconify();a.geometry('780x560');a.update()
        a.search.set('CQ');a.notebook.select(a.console_page);a.command.focus_force();a.update()
        a.command.event_generate('<Control-f>');a.update()
        self.assertEqual(a.notebook.index(a.notebook.select()),0)
        self.assertEqual(a.focus_get(),a.search_box)
        self.assertEqual(a.search.get(),'CQ')

    def test_multiple_band_selection_combines_with_source_and_search(self):
        a=self.app;first=('first.invalid',7300,'LZ3ZZ');second=('second.invalid',7300,'LZ3ZZ')
        for freq,call,comment,source in [('14074','K1ABC','CQ',first),('7074','K2ABC','CQ',first),
                                         ('18100','K3ABC','CQ',first),('14075','K4ABC','CQ',second),
                                         ('7075','K5ABC','OTHER',first)]:
            a.add_spot(Spot('W1AW',freq,call,'1200Z',comment),source=source)
        a.band_shortcut_buttons['20 m'].invoke();a.band_shortcut_buttons['40 m'].invoke()
        a.search.set('CQ');a.spot_source_filter.set(a.source_label(first));a.render_spots()
        self.assertEqual({a.spots.item(row,'values')[3] for row in a.spots.get_children()},{'K1ABC','K2ABC'})
        a.band_shortcut_buttons['20 m'].invoke()
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K2ABC'])
        a.band_shortcut_buttons['17 m'].invoke()
        self.assertEqual({a.spots.item(row,'values')[3] for row in a.spots.get_children()},{'K2ABC','K3ABC'})
        self.assertEqual(a.selected_bands,{'40 m','17 m'})
        self.assertEqual(a.spot_source_filter.get(),a.source_label(first));self.assertEqual(a.search.get(),'CQ')

    def test_compact_band_picker_selects_multiple_and_cancel_preserves_selection(self):
        a=self.app;a.deiconify();a.geometry('780x560');a.update()
        for freq,call in [('14074','K1ABC'),('7074','K2ABC'),('18100','K3ABC')]:
            a.add_spot(Spot('W1AW',freq,call,'1200Z','CQ'))
        a.band.set('Choose bands…');a.band_box.event_generate('<<ComboboxSelected>>');a.update()
        dialog=next(w for w in a.winfo_children() if isinstance(w,tk.Toplevel))
        self.assertEqual(a.grab_current(),dialog)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets=list(descendants(dialog))
        next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Clear selection').invoke()
        for name in ('20 m','40 m'):
            next(w for w in widgets if isinstance(w,ttk.Checkbutton) and w.cget('text')==name).invoke()
        next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke()
        self.assertIsNone(a.grab_current())
        self.assertEqual(a.selected_bands,{'20 m','40 m'})
        self.assertEqual({a.spots.item(row,'values')[3] for row in a.spots.get_children()},{'K1ABC','K2ABC'})
        a.band.set('Choose bands…');a.band_box.event_generate('<<ComboboxSelected>>');a.update()
        dialog=next(w for w in a.winfo_children() if isinstance(w,tk.Toplevel))
        next(w for w in descendants(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Cancel').invoke()
        self.assertIsNone(a.grab_current())
        self.assertEqual(a.selected_bands,{'20 m','40 m'});self.assertEqual(a.band.get(),'2 bands')
        a.band.set('Choose bands…');a.band_box.event_generate('<<ComboboxSelected>>');a.update()
        dialog=next(w for w in a.winfo_children() if isinstance(w,tk.Toplevel))
        next(w for w in descendants(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='All bands').invoke()
        self.assertIsNone(a.selected_bands);self.assertEqual(a.band.get(),'All bands')

    def test_band_picker_reflects_visible_bands_and_unchanged_apply_keeps_all_mode(self):
        a=self.app;a.hidden_bands={'40 m'};a.band.set('All bands');a.open_band_picker();a.update()
        dialog=next(w for w in a.winfo_children() if isinstance(w,tk.Toplevel))
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets=list(descendants(dialog))
        boxes={w.cget('text'):w for w in widgets if isinstance(w,ttk.Checkbutton)}
        self.assertTrue(a.getboolean(a.getvar(boxes['20 m'].cget('variable'))))
        self.assertFalse(a.getboolean(a.getvar(boxes['40 m'].cget('variable'))))
        self.assertTrue(a.getboolean(a.getvar(boxes['Other'].cget('variable'))))
        next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke()
        self.assertIsNone(a.selected_bands)
        self.assertEqual(a.hidden_bands,{'40 m'})

    def test_band_filter_does_not_claim_all_bands_are_visible(self):
        a=self.app;a.hidden_bands=set()
        a.band_shortcut_buttons['40 m'].invoke();a.update()
        self.assertEqual(a.selected_bands,{'40 m'})
        self.assertEqual(a.hidden_bands_note.get(),'No bands hidden by options')

    def test_explicit_hidden_band_is_shown_without_changing_saved_choices(self):
        a=self.app;a.deiconify();a.geometry('900x700');a.update()
        a.hidden_bands={'15 m','20 m','40 m'};a.spot_marker_filter='All spots';a.hide_duplicate_spots=False
        for freq,call in [('21074','K1AAA'),('14074','K2BBB'),('7001','K3CCC'),('3501','K4DDD')]:
            a.add_spot(Spot('S',freq,call,'1200Z','CQ'))
        a.band.set('All bands');a.render_spots();a.update()
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K4DDD'])
        for band,call in [('15 m','K1AAA'),('20 m','K2BBB'),('40 m','K3CCC')]:
            with self.subTest(band=band):
                a.band_shortcut_buttons['All bands'].invoke()
                a.band_shortcut_buttons[band].invoke()
                self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],[call])
                self.assertIn(f'Showing {band}',a.hidden_bands_note.get())
                self.assertEqual(a.hidden_bands,{'15 m','20 m','40 m'})
        a.band_shortcut_buttons['All bands'].invoke();a.band_shortcut_counts['20 m'].event_generate('<Button-1>');a.update()
        self.assertEqual(a.band.get(),'20 m')
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K2BBB'])
        a.search.set('NO MATCH');self.assertFalse(a.spots.get_children())
        self.assertIn('search',a.spot_empty_hint.get())
        a.search.set('');a.band.set('All bands')
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K4DDD'])
        self.assertEqual(a.hidden_bands,{'15 m','20 m','40 m'})

    def test_requested_band_shortcuts_and_accent_counts(self):
        a=self.app;a.deiconify();a.geometry('780x760');a.update()
        from tkinter import font
        band_font=font.Font(root=a,font=('Segoe UI Semibold',9))
        for name in ('2 m','1.25 m','70 cm'):
            button=a.band_shortcut_buttons[name]
            self.assertEqual(button.cget('text'),name)
            self.assertGreaterEqual(button.winfo_width(),band_font.measure(name)+12)
        self.assertEqual(len({button.winfo_width() for button in a.band_shortcut_buttons.values()}),1)
        cases=[('30 m','10136'),('17 m','18100'),('12 m','24915'),('2 m','145500'),('70 cm','432100')]
        for index,(band,freq) in enumerate(cases):a.add_spot(Spot('S',freq,f'K{index}ABC','1200Z','CQ'))
        a.render_spots();a.update()
        for band,_ in cases:
            with self.subTest(band=band):
                self.assertEqual(a.band_shortcut_counts[band].cget('text'),'1')
                a.band_shortcut_buttons['All bands'].invoke()
                a.band_shortcut_buttons[band].invoke();self.assertEqual(a.band.get(),band)
                self.assertEqual(len(a.spots.get_children()),1)
                count=a.band_shortcut_counts[band]
                self.assertLessEqual(count.winfo_rootx()+count.winfo_width(),a.winfo_rootx()+a.winfo_width())
        a.band_shortcut_buttons['All bands'].invoke();a.band_shortcut_counts['30 m'].event_generate('<Button-1>');a.update();self.assertEqual(a.band.get(),'30 m')
        style=ttk.Style(a)
        for _ in range(2):
            self.assertNotEqual(style.lookup('BandCount.TLabel','foreground'),style.lookup('Band.TButton','foreground'))
            self.assertNotEqual(style.lookup('BandCount.TLabel','foreground'),style.lookup('BandSelected.TButton','foreground'))
            a.switch_theme();a.update()

    def test_full_list_band_counts_fit_in_minimum_wide_layout(self):
        from tkinter import font
        a=self.app;a.deiconify();a.geometry('780x760');a.update()
        for index in range(1000):
            a.add_spot(Spot('W1AW','14074',f'K{index}X','1200Z','CQ'))
        a.render_spots();a.update()
        count_font=font.Font(root=a,font=('Segoe UI Semibold',9))
        for name in ('All bands','20 m'):
            label=a.band_shortcut_counts[name]
            self.assertEqual(label.cget('text'),'1000')
            self.assertGreaterEqual(label.winfo_width(),count_font.measure('1000'))
            self.assertTrue(label.winfo_ismapped())

    def test_band_shortcuts_hide_and_return_with_window_height(self):
        a=self.app;a.deiconify()
        for size,visible in [('1120x760',True),('780x760',True),('780x560',False),('1120x760',True)]:
            a.geometry(size);a.update()
            self.assertEqual(bool(a.band_shortcuts.winfo_ismapped()),visible)
            self.assertTrue(a.band_box.winfo_ismapped())
            if visible:
                last=a.band_shortcut_buttons['6 m']
                self.assertGreater(last.winfo_width(),25)
                self.assertLessEqual(last.winfo_rootx()+last.winfo_width(),a.winfo_rootx()+a.winfo_width())

    def test_selected_spot_inspector_moves_when_resized_and_keeps_selection(self):
        a=self.app;a.deiconify()
        a.add_spot(Spot('W3ABC','14074','K1ABC','1200Z','Selected spot'))
        a.render_spots();row=a.spots.get_children()[0];a.spots.selection_set(row);a.show_spot_detail()
        for size,side in [('1440x900',True),('780x560',False),('1440x900',True)]:
            a.geometry(size);a.update()
            self.assertEqual(a._spot_side_layout,side)
            self.assertTrue(a.spot_detail.winfo_ismapped())
            self.assertEqual(a.spots.selection(),(row,))
            self.assertIn('Selected spot',a.spot_detail.get('1.0','end'))
            if side:
                self.assertGreater(a.spot_detail.winfo_height(),100)
                self.assertGreater(a.spot_detail.winfo_rootx(),a.spots.winfo_rootx())
                self.assertGreater(a.spots.winfo_width(),500)
            else:self.assertGreater(a.spot_detail.winfo_rooty(),a.spots.winfo_rooty())

    def test_spot_inspector_keeps_scroll_when_live_spots_arrive(self):
        a=self.app;a.deiconify();a.geometry('1440x900');a.update()
        a.add_spot(Spot('W3ABC','14074','K1ABC','1200Z','long comment ' * 120))
        a.render_spots();row=a.spots.get_children()[0];a.spots.selection_set(row);a.show_spot_detail();a.update()
        self.assertEqual(tuple(map(str,a.spot_detail.tag_ranges('callsign'))),('1.0','1.5'))
        a.spot_detail.yview_moveto(.5);a.update();position=a.spot_detail.yview()[0]
        self.assertGreater(position,0)
        a.add_spot(Spot('W3ABC','7000','K2ABC','1201Z','new'))
        a.render_spots(preserve_anchor=True);a.update()
        self.assertEqual(a.spots.selection(),(row,))
        self.assertAlmostEqual(a.spot_detail.yview()[0],position,places=2)
        new_row=next(item for item in a.spots.get_children() if item!=row)
        a.spots.selection_set(new_row);a.show_spot_detail();a.update()
        self.assertEqual(a.spot_detail.yview()[0],0.0)

    def test_empty_view_tracks_additional_node_connection(self):
        a=self.app
        with patch('app.Connection', FakeConnection):
            ident=a.start_secondary_node('other.invalid',7301,'LZ3ZZ')
            conn=a.secondary_nodes[ident]['connection']
            conn.emit('status','Connected'); a.poll_secondary_nodes()
            self.assertEqual(a.spot_empty_hint.get(),'Waiting for spots. If none arrive, check node filters and spot settings.')
            self.assertEqual(a.spot_source_note.get(),'Waiting for spots from connected nodes.')
            a.render_spots()
            self.assertEqual(a.spot_empty_hint.get(),'Waiting for spots. If none arrive, check node filters and spot settings.')
            a.stop_secondary_node(ident)
            self.assertEqual(a.spot_empty_hint.get(),'Connect to a cluster to start receiving spots.')
            self.assertEqual(a.spot_source_note.get(),'Connect to a node to receive spots.')

    def test_refresh_spots_chooses_source_and_rejects_stale_connection(self):
        class Connection:
            def __init__(self,emit=None):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def send(self,text):self.commands.append(text)
            def close(self):pass
        a=self.app;main=Connection();a.connection=main;a.connection_target=('main.invalid',7300,'LZ3ZZ')
        a.spot_source=('main.invalid',7300,'LZ3ZZ');a.status.set('Connected')
        with patch('app.Connection',Connection):
            ident=a.start_secondary_node('other.invalid',7301,'LZ3ZZ');item=a.secondary_nodes[ident];old=item['connection']
            old.emit('status','Connected');a.poll_secondary_nodes()
            a.add_spot(Spot('S','14074','W1AW','1200Z',''),source=item['source']);a.render_spots();a.spots.selection_set(a.spots.get_children()[0])
            a.refresh_spots();dialog=next(w for w in a.winfo_children() if w.winfo_class()=='Toplevel');panel=dialog.winfo_children()[0]
            selector=next(w for w in panel.winfo_children() if isinstance(w,ttk.Combobox))
            self.assertIn('other.invalid:7301',selector.get());self.assertEqual(main.commands,[]);self.assertEqual(old.commands,[])
            next(w for w in panel.winfo_children()[-1].winfo_children() if w.cget('text')=='Request spots').invoke()
            self.assertEqual(old.commands,['sh/mydx/100']);self.assertEqual(main.commands,[])
            a.refresh_spots();dialog=next(w for w in a.winfo_children() if w.winfo_class()=='Toplevel');panel=dialog.winfo_children()[0]
            a._start_secondary_attempt(ident)
            with patch('app.messagebox.showerror') as error:
                next(w for w in panel.winfo_children()[-1].winfo_children() if w.cget('text')=='Request spots').invoke();error.assert_called_once()
            self.assertEqual(item['connection'].commands,[]);self.assertEqual(main.commands,[])

    def test_refresh_spots_uses_secondary_when_it_is_only_connection(self):
        class Connection:
            def __init__(self,emit):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def send(self,text):self.commands.append(text)
            def close(self):pass
        a=self.app
        with patch('app.Connection',Connection):
            ident=a.start_secondary_node('other.invalid',7301,'LZ3ZZ');conn=a.secondary_nodes[ident]['connection']
            conn.emit('status','Connected');a.poll_secondary_nodes()
            self.assertEqual(str(a.refresh_spots_button['state']),'normal');a.refresh_spots_button.invoke()
            self.assertEqual(conn.commands,['sh/mydx/100'])

    def test_compact_layout_keeps_spot_and_message_lists_visible(self):
        a=self.app;a.deiconify();a.geometry('780x560');a.update()
        for page,widget in ((0,a.spots),(5,a.ann_tree)):
            a.notebook.select(page);a.update()
            self.assertTrue(widget.winfo_ismapped())
            self.assertGreaterEqual(widget.winfo_height(),55)
        self.assertTrue(a.command.winfo_ismapped());self.assertTrue(a.spot_detail.winfo_exists())
        a.geometry('1120x760');a.update()
        self.assertFalse(a._compact_layout)
        self.assertEqual(int(a.spot_detail.cget('height')),2);self.assertEqual(int(a.ann_detail.cget('height')),3)
        self.assertTrue(a.command_label.winfo_ismapped())

    def test_pc_spots_reach_display_from_node_stream(self):
        a=self.app
        a.events.put((a.generation,'data','PC11^14074.05^K1ABC^30-Oct-2026^1234Z^CQ^LZ3ZZ^NODE^H50^~\r\n'))
        a.after_cancel(a.poll_after);a.poll()
        self.assertEqual(len(a.rows),1);self.assertEqual(a.rows[0][1].dx,'K1ABC')
        self.assertEqual(a.spots.item(a.spots.get_children()[0],'values')[3],'K1ABC')

    def test_pc26_merged_spot_reaches_display(self):
        a=self.app
        a.events.put((a.generation,'data','PC26^14074.0^K1ABC^30-Oct-2026^1234Z^CQ^LZ3ZZ^REQUESTNODE^ ^~\r\n'))
        a.after_cancel(a.poll_after);a.poll()
        self.assertEqual(len(a.rows),1);self.assertEqual(a.rows[0][1].dx,'K1ABC')
        self.assertEqual(a.spots.item(a.spots.get_children()[0],'values')[3],'K1ABC')
        self.assertEqual(a.rows[0][1].origin,'')

    def test_plain_spot_with_node_beep_reaches_display(self):
        a=self.app;a.dialect='DXSpider'
        a.events.put((a.generation,'data','DX de LZ3ZZ:  14074.0  K1ABC        FT8 signal                     2300Z\a\a\r\n'))
        a.after_cancel(a.poll_after);a.poll()
        self.assertEqual(len(a.rows),1);self.assertEqual(a.rows[0][1].dx,'K1ABC')
        self.assertEqual(a.spots.item(a.spots.get_children()[0],'values')[3],'K1ABC')

    def test_announcement_with_packet_example_does_not_create_spot(self):
        a=self.app
        line='To ALL de K1ABC: Example CC11^14074^W1AW^30-Oct-2026^1200Z^CQ^K1ABC^25'
        a.events.put((a.generation,'data',line+'\r\n'))
        a.after_cancel(a.poll_after);a.poll()
        self.assertEqual(len(a.rows),0)
        self.assertEqual(a.announcements[-1][0],line)

    def test_overlong_lines_are_not_reinterpreted_as_spots(self):
        a=self.app
        spot='CC11^14074^W1AW^30-Oct-2026^1200Z^CQ^K1ABC^25'
        a.events.put((a.generation,'data','X'*9000))
        a.after_cancel(a.poll_after);a.poll()
        self.assertEqual(a.partial,'');self.assertTrue(a.discarding_long_line)
        a.events.put((a.generation,'data',spot+'\r\n'+spot+'\r\n'))
        a.after_cancel(a.poll_after);a.poll()
        self.assertEqual(len(a.rows),1);self.assertFalse(a.discarding_long_line)
        self.assertIn('Skipped 1 overlong cluster line',a.transcript.get('1.0','end'))
        a.clear_spots()
        a.events.put((a.generation,'data','X'*9000+spot+'\r\n'))
        a.after_cancel(a.poll_after);a.poll()
        self.assertEqual(len(a.rows),0)

    def test_overlong_line_interrupts_readbacks_before_following_prompt(self):
        from cluster_mail import MailRead
        from user_settings import SettingsReadback
        a=self.app
        a.dialect='DXSpider';a.mail_read=MailRead('42');a.mail_read.sent()
        a.settings_readback=SettingsReadback('LZ3ZZ');a.settings_readback.sent()
        a.events.put((a.generation,'data','Msg: 42 From: K1ABC Date: 29-Oct 1200Z Subj: hello\r\nCallsign: LZ3ZZ\r\nReq DX Spots: Yes\r\n'+'X'*9000+'\r\nLZ3ZZ de NODE dxspider >\r\n'))
        a.after_cancel(a.poll_after);a.poll()
        self.assertEqual(a.mail_read.state,'partial');self.assertIsNone(a.mail_read.reply_draft())
        self.assertEqual(a.settings_readback.state,'partial')
        self.assertNotEqual(a.node_settings.get('Req DX Spots'),'Yes')

    def test_undated_spot_order_is_fixed_at_receipt_and_evicted_with_row(self):
        from collections import deque
        a=self.app; a.rows=deque(maxlen=2)
        first=Spot('S','14074','K1ABC','2359Z','')
        second=Spot('S','14075','W1AW','0001Z','')
        self.assertEqual(spot_order(first, datetime(2026,10,30,0,2)), datetime(2026,10,29,23,59))
        with patch('app.spot_order', side_effect=[datetime(2026,10,29,23,59), datetime(2026,10,30,0,1), datetime(2026,10,30,0,2)]) as estimate:
            a.add_spot(first); a.add_spot(second); a.render_spots(); a.render_spots()
            self.assertEqual(estimate.call_count,2)
            self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['W1AW','K1ABC'])
            a.add_spot(Spot('S','14076','N1ABC','0002Z',''))
            self.assertEqual(set(a.spot_times), {'spot-2','spot-3'})
            a.clear_spots(); self.assertEqual(a.spot_times,{})

    def test_each_spot_keeps_its_receiving_source_and_evicts_it_with_row(self):
        from collections import deque
        from unittest.mock import Mock
        a=self.app;a.rows=deque(maxlen=2);log=Mock();a.spot_log=log
        first=('first.invalid',7300,'LZ3ZZ');second=('second.invalid',7301,'LZ3ZZ-1')
        a.spot_source=first
        a.add_spot(Spot('S','14074','K1ABC','1200Z','first'))
        a.add_spot(Spot('S','7001','W1AW','1201Z','second'),source=second)
        self.assertEqual(log.publish.call_args_list[1].args[1],second)
        a.render_spots();row=next(i for i in a.spots.get_children() if a.spots.item(i,'values')[3]=='W1AW')
        a.spots.selection_set(row);a.show_spot_detail()
        self.assertIn('Received from: second.invalid:7301 as LZ3ZZ-1',a.spot_detail.get('1.0','end'))
        a.add_spot(Spot('S','7002','N1ABC','1202Z','third'),source=second)
        self.assertEqual(set(a.spot_sources),{'spot-2','spot-3'})
        a.clear_spots();self.assertEqual(a.spot_sources,{})
        a.spot_log=None

    def test_common_window_shows_at_least_five_spot_rows(self):
        a=self.app; a.deiconify(); a.geometry('1200x720')
        for i in range(20): a.add_spot(Spot('S','14074',f'K{i}ABC','1200Z','CQ'))
        a.render_spots(); a.update()
        visible=sum(bool(a.spots.bbox(row)) for row in a.spots.get_children())
        self.assertGreaterEqual(visible,5)

    def test_new_spots_preserve_scrolled_row_and_follow_the_top(self):
        a=self.app;a.deiconify();a.geometry('900x700');a.update()
        for i in range(80):a.add_spot(Spot('S','14074',f'K{i}ABC','1200Z',f'spot {i}'))
        a.render_spots();a.update();a.spots.yview_moveto(.4);a.update()
        def top_row():
            return next((a.spots.identify_row(y) for y in range(min(a.spots.winfo_height(),120)) if a.spots.identify_row(y)), '')
        before=top_row();self.assertTrue(before)
        for i in range(80,100):a.add_spot(Spot('S','14074',f'K{i}ABC','1200Z',f'spot {i}'))
        a.render_spots(preserve_anchor=True);a.update();self.assertEqual(top_row(),before)
        a.spots.yview_moveto(0);a.update();before=top_row()
        a.add_spot(Spot('S','14074','K100ABC','1200Z','newest'))
        a.render_spots(preserve_anchor=True);a.update()
        self.assertNotEqual(top_row(),before);self.assertEqual(top_row(),a.spots.get_children()[0])

    def test_lock_and_spot_actions_fit_compact_window(self):
        a=self.app;a.deiconify();a.geometry('780x560')
        a.spot_count.set('1000 shown · list full')
        a.spot_marker_filter='Marked -# only';a.hidden_bands={'20 m','40 m'}
        a.hidden_bands_note.set(a.hidden_bands_label());a.update()
        controls=[a.spot_lock_button,a.refresh_spots_button]
        controls += [w for w in a.spot_lock_button.master.winfo_children() if isinstance(w,ttk.Button)]
        for control in controls:
            with self.subTest(control=control.cget('text')):
                self.assertTrue(control.winfo_ismapped())
                self.assertGreaterEqual(control.winfo_width(),control.winfo_reqwidth())
                self.assertLessEqual(control.winfo_rootx()+control.winfo_width(),a.winfo_rootx()+a.winfo_width())
                self.assertGreaterEqual(control.winfo_rooty(),a.spots.winfo_rooty()+a.spots.winfo_height())
                self.assertLessEqual(abs((control.winfo_rooty()+control.winfo_height()/2)-(a.display_options_button.winfo_rooty()+a.display_options_button.winfo_height()/2)),1)

    def test_lock_scroll_keeps_top_spot_while_new_spots_arrive(self):
        a=self.app;a.deiconify();a.geometry('900x700');a.update()
        for i in range(50):a.add_spot(Spot('S','14074',f'K{i}ABC','1200Z','CQ'))
        a.render_spots();a.update();a.spots.yview_moveto(0);a.update()
        def top_row():
            return next((a.spots.identify_row(y) for y in range(min(a.spots.winfo_height(),120)) if a.spots.identify_row(y)), '')
        before=top_row();self.assertTrue(before);a.spot_view_locked.set(True)
        for i in range(50,60):a.add_spot(Spot('S','14074',f'K{i}ABC','1200Z','CQ'))
        a.render_spots(preserve_anchor=True);a.update()
        self.assertEqual(top_row(),before);self.assertEqual(len(a.rows),60)
        a.spot_view_locked.set(False);a.spots.yview_moveto(0);a.update()
        a.add_spot(Spot('S','14074','K60ABC','1200Z','CQ'));a.render_spots(preserve_anchor=True);a.update()
        self.assertEqual(top_row(),a.spots.get_children()[0]);self.assertNotEqual(top_row(),before)

    def test_locked_scroll_survives_updates_while_another_tab_is_open(self):
        a=self.app;a.deiconify();a.geometry('900x700');a.update()
        for i in range(50):a.add_spot(Spot('S','14074',f'K{i}ABC','1200Z','CQ'))
        a.render_spots();a.update();a.spots.yview_moveto(0);a.update()
        def top_row():
            return next((a.spots.identify_row(y) for y in range(min(a.spots.winfo_height(),120)) if a.spots.identify_row(y)), '')
        before=top_row();self.assertTrue(before);a.spot_view_locked.set(True)
        spot_tab=a.notebook.select();a.notebook.select(a.console_page);a.update()
        self.assertFalse(a.spots.winfo_ismapped())
        for start in (50,60):
            for i in range(start,start+10):a.add_spot(Spot('S','14074',f'K{i}ABC','1200Z','CQ'))
            a.render_spots(preserve_anchor=True);a.update()
        a.notebook.select(spot_tab);a.update();self.assertEqual(top_row(),before)

    def test_scrolled_view_survives_updates_while_another_tab_is_open(self):
        a=self.app;a.deiconify();a.geometry('900x700');a.update();a.rows=deque(maxlen=80)
        for i in range(80):a.add_spot(Spot('S','14074',f'K{i}ABC','1200Z','CQ'))
        a.render_spots();a.update();a.spots.yview_moveto(.4);a.update()
        def top_row():
            return next((a.spots.identify_row(y) for y in range(min(a.spots.winfo_height(),120)) if a.spots.identify_row(y)), '')
        before=top_row();self.assertTrue(before);spot_tab=a.notebook.select()
        a.notebook.select(a.console_page);a.update()
        for start in (80,90):
            for i in range(start,start+10):a.add_spot(Spot('S','14074',f'K{i}ABC','1200Z','CQ'))
            a.render_spots(preserve_anchor=True);a.update()
        a.notebook.select(spot_tab);a.update();self.assertEqual(top_row(),before)

    def test_live_reconciliation_matches_full_render_with_eviction_and_filters(self):
        a=self.app
        a.rows=deque(maxlen=5)
        a.color_bands=True;a.hide_duplicate_spots=True
        for freq,dx,comment in [('7001','K1ABC','one'),('14074','K2ABC','two'),('14074','K2ABC','two'),('21074','K3ABC','three'),('14074','K4ABC','four')]:
            a.add_spot(Spot('S',freq,dx,'1200Z',comment))
        a.render_spots();row=a.spots.get_children()[0];a.spots.selection_set(row)
        a.add_spot(Spot('S','14075','K5ABC','1200Z','five'))
        a.render_spots(preserve_anchor=True)
        def snapshot():return [(i,a.spots.item(i,'values'),a.spots.item(i,'tags')) for i in a.spots.get_children()]
        incremental=snapshot();selection=a.spots.selection()
        a.render_spots();self.assertEqual(snapshot(),incremental);self.assertEqual(a.spots.selection(),selection)
        a.band.set('20 m');a.render_spots()
        a.add_spot(Spot('S','14076','K6ABC','1200Z','six'));a.render_spots(preserve_anchor=True)
        incremental=snapshot();a.render_spots();self.assertEqual(snapshot(),incremental)

    def test_marker_filter_affects_only_display_and_identifies_exact_suffix(self):
        a=self.app
        for spotter,dx in [('W1AW-#','K1AAA'),('W2AW','K2BBB'),('W3AW#','K3CCC')]:
            a.add_spot(Spot(spotter,'14074',dx,'1200Z','CQ'))
        a.spot_marker_filter='Marked -# only';a.render_spots()
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K1AAA'])
        self.assertEqual(len(a.rows),3)
        self.assertIn('Marked -# only',a.hidden_bands_label())
        a.spot_marker_filter='Unmarked only';a.render_spots()
        self.assertEqual({a.spots.item(row,'values')[3] for row in a.spots.get_children()},{'K2BBB','K3CCC'})
        a.spot_marker_filter='All spots';a.render_spots();self.assertEqual(len(a.spots.get_children()),3)

    def test_show_all_bands_does_not_change_marker_choice(self):
        a=self.app;a.hidden_bands={'20 m','40 m'};a.spot_marker_filter='Marked -# only'
        a.open_display_bands();dialog=next(w for w in a.winfo_children() if isinstance(w,tk.Toplevel))
        def walk(widget):
            for child in widget.winfo_children(): yield child; yield from walk(child)
        widgets=list(walk(dialog));self.assertEqual(dialog.title(),'Spot display options')
        next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Show all bands').invoke()
        next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke()
        self.assertEqual(a.hidden_bands,set());self.assertEqual(a.spot_marker_filter,'Marked -# only')

    def test_marker_filter_can_be_set_and_saved_in_display_dialog(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app_module,'SETTINGS',Path(directory)/'settings.json'):
            a=App();a.withdraw()
            try:
                a.open_display_bands();dialog=next(w for w in a.winfo_children() if isinstance(w,tk.Toplevel))
                def walk(widget):
                    for child in widget.winfo_children(): yield child; yield from walk(child)
                widgets=list(walk(dialog));choice=next(w for w in widgets if isinstance(w,ttk.Combobox))
                choice.set('Marked -# only')
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke()
                self.assertEqual(a.spot_marker_filter,'Marked -# only')
                b=App();b.withdraw()
                try:self.assertEqual(b.spot_marker_filter,'Marked -# only')
                finally:b.quit_app()
            finally:a.quit_app()

    def test_spot_table_shows_reported_day_and_marks_unknown_dates(self):
        a=self.app
        a.add_spot(Spot('S','14074','K1ABC','2359Z','',date='29-Oct-2026'))
        a.add_spot(Spot('S','14074','K2ABC','0001','',date='30-Oct-2026'))
        a.add_spot(Spot('S','14074','K3ABC','0002z','',date=''))
        a.add_spot(Spot('S','14074','K4ABC','0003Z','',date='31-Feb-2026'))
        a.render_spots()
        by_call={a.spots.item(row,'values')[3]:a.spots.item(row,'values')[0] for row in a.spots.get_children()}
        self.assertEqual(by_call['K1ABC'],'Oct 29 23:59Z')
        self.assertEqual(by_call['K2ABC'],'Oct 30 00:01Z')
        self.assertEqual(by_call['K3ABC'],'-- 00:02Z')
        self.assertEqual(by_call['K4ABC'],'-- 00:03Z')
        row=next(row for row in a.spots.get_children() if a.spots.item(row,'values')[3]=='K2ABC')
        a.spots.selection_set(row);a.show_spot_detail()
        self.assertIn('00:01Z',a.spot_detail.get('1.0','end'))
        self.assertEqual(next(spot.time for _,spot in a.rows if spot.dx=='K2ABC'),'0001')
        dated=[a.spots.item(row,'values')[3] for row in a.spots.get_children() if a.spots.item(row,'values')[3] in ('K1ABC','K2ABC')]
        self.assertEqual(dated,['K2ABC','K1ABC'])

    def test_all_fields_search_includes_displayed_date_time_and_band(self):
        a=self.app
        a.add_spot(Spot('W1AW','14074','K1ABC','1234Z','CQ',date='30-Oct-2026'))
        a.add_spot(Spot('W2AW','7074','K2ABC','2359Z','CQ',date='29-Oct-2026'))
        a.add_spot(Spot('W3AW','1000','K3ABC','0002Z','CQ'))
        for query,expected in [('Oct 30',['K1ABC']),('12:34Z',['K1ABC']),('20 m',['K1ABC']),('20m',['K1ABC']),('40M',['K2ABC']),('Other',['K3ABC']),('29-Oct-2026',['K2ABC'])]:
            with self.subTest(query=query):
                a.search.set(query)
                self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],expected)
        a.search_field.set('Comment');a.search.set('20 m')
        self.assertFalse(a.spots.get_children())

    def test_clear_selected_bands_preserves_other_spots_and_metadata(self):
        a=self.app
        a.add_spot(Spot('W1AW','14074','K1ABC','1200Z','CQ'),source=('one',7300,'LZ3ZZ'))
        a.add_spot(Spot('W2AW','7074','K2ABC','1201Z','CW'),source=('two',7300,'LZ3ZZ'))
        a.add_spot(Spot('W3AW','1000','K3ABC','1202Z','CQ'),source=('one',7300,'LZ3ZZ'))
        before={ident for ident,_ in a.rows}
        a.clear_selected_band_spots();self.assertEqual(len(a.rows),3)
        a.selected_bands={'20 m','Other'};a.search.set('no matches');a.clear_selected_band_spots()
        self.assertEqual([spot.dx for _,spot in a.rows],['K2ABC'])
        retained=a.rows[0][0]
        self.assertEqual(set(a.spot_times),{retained});self.assertEqual(set(a.spot_sources),{retained})
        self.assertEqual(a.spot_sources[retained],('two',7300,'LZ3ZZ'))
        self.assertEqual(len(before-{retained}),2);self.assertEqual(a.selected_bands,{'20 m','Other'})
        a.search.set('');a.clear_spots();self.assertFalse(a.rows)

    def test_clear_band_menu_is_available_to_mouse_and_keyboard(self):
        a=self.app
        with patch.object(a.clear_spots_menu,'tk_popup') as popup:
            a.selected_bands=None
            a.open_clear_spots_menu(SimpleNamespace(num=3,x_root=120,y_root=140))
            popup.assert_called_with(120,140)
            self.assertEqual(a.clear_spots_menu.entrycget(0,'state'),'disabled')
            a.selected_bands={'20 m'}
            a.open_clear_spots_menu(SimpleNamespace(num=None))
            self.assertEqual(a.clear_spots_menu.entrycget(0,'state'),'normal')
            self.assertEqual(popup.call_count,2)

    def test_band_shortcut_counts_cover_retained_spots_before_display_filters(self):
        a=self.app; a.rows=deque(maxlen=3)
        a.add_spot(Spot('S','14074','K1ABC','1200Z',''))
        a.add_spot(Spot('S','14074','K1ABC','1200Z',''))
        a.add_spot(Spot('S','7001','K2ABC','1200Z',''))
        a.hide_duplicate_spots=True; a.band.set('40 m'); a.render_spots()
        self.assertEqual(a.band_shortcut_counts['All bands'].cget('text'),'3')
        self.assertEqual(a.band_shortcut_counts['20 m'].cget('text'),'2')
        self.assertEqual(a.band_shortcut_counts['40 m'].cget('text'),'1')
        a.add_spot(Spot('S','21074','K3ABC','1200Z','')); a.render_spots(preserve_anchor=True)
        self.assertEqual(a.band_shortcut_counts['20 m'].cget('text'),'1')
        self.assertEqual(a.band_shortcut_counts['15 m'].cget('text'),'1')
        a.clear_spots()
        self.assertEqual(a.band_shortcut_counts['All bands'].cget('text'),'0')
        self.assertEqual(a.band_shortcut_counts['20 m'].cget('text'),'0')

    def test_selected_source_reports_connection_and_retained_disconnected_spots(self):
        a=self.app
        with patch('app.Connection',FakeConnection):
            ident=a.start_secondary_node('other.invalid',7300,'LZ3ZZ-1')
            item=a.secondary_nodes[ident];item['status']='Connected';item['dialect']='DXSpider'
            source=item['source'];a.add_spot(Spot('S','14074','K1ABC','1200Z','CQ'),source=source);a.render_spots()
            a.spot_source_filter.set(a.source_label(source))
            self.assertIn('· connected',a.spot_source_note.get())
            self.assertEqual(len(a.spots.get_children()),1)
            a.stop_secondary_node(ident)
            self.assertIn('· not connected',a.spot_source_note.get())
            self.assertEqual(len(a.rows),1)
            self.assertEqual(len(a.spots.get_children()),1)
            a.spot_source_filter.set('All sources')
            self.assertNotIn('Showing ',a.spot_source_note.get())

    def test_source_filter_distinguishes_connections_and_deduplicates_within_selection(self):
        a=self.app;a.deiconify();a.geometry('780x560');a.update()
        first=('one.example',7300,'LZ3ZZ');second=('two.example',7300,'LZ3ZZ-1')
        spot=Spot('S','14074','K1ABC','1200Z','same')
        a.add_spot(spot,source=first);a.add_spot(spot,source=second)
        a.hide_duplicate_spots=True;a.render_spots();a.update()
        self.assertEqual(len(a.spots.get_children()),1)
        selected=a.spots.get_children()[0];a.spots.selection_set(selected)
        selected_source=a.spot_sources[selected];other=first if selected_source==second else second
        label=f'{other[0]}:{other[1]} · {other[2]}'
        self.assertIn(label,tuple(a.spot_source_box.cget('values')))
        a.spot_source_filter.set(label);a.spot_source_box.event_generate('<<ComboboxSelected>>');a.update()
        rows=a.spots.get_children();self.assertEqual(len(rows),1);self.assertEqual(a.spot_sources[rows[0]],other)
        self.assertEqual(len(a.rows),2)
        a.clear_spots();self.assertIn(label,tuple(a.spot_source_box.cget('values')))
        self.assertEqual(a.spot_source_filter.get(),label)
        a.spot_source_filter.set('All sources');a.spot_source_box.event_generate('<<ComboboxSelected>>');a.update()
        self.assertNotIn(label,tuple(a.spot_source_box.cget('values')))
        self.assertTrue(a.spot_source_box.winfo_ismapped())
        self.assertLessEqual(a.spot_source_box.winfo_rootx()+a.spot_source_box.winfo_width(),a.winfo_rootx()+a.winfo_width())

    def test_selection_and_details_survive_new_spots_and_clear_with_filter(self):
        a=self.app
        a.add_spot(Spot('LZ3ZZ','14074.0','K1ABC','1200Z','a long comment','30-Oct-2026'))
        a.render_spots(); row=a.spots.get_children()[0]; a.spots.selection_set(row); a.show_spot_detail()
        a.add_spot(Spot('N1ABC','7001.0','W1AW','1201Z','new spot','30-Oct-2026')); a.render_spots()
        self.assertEqual(a.spots.selection(),(row,)); self.assertIn('a long comment',a.spot_detail.get('1.0','end'))
        a.search.set('W1AW'); self.assertEqual(a.spots.selection(),()); self.assertEqual(a.spot_detail.get('1.0','end-1c'),'Select a spot to see its details.')

    def test_unselected_spot_details_are_explained_in_both_themes(self):
        a=self.app
        for _ in range(2):
            self.assertEqual(a.spot_detail.get('1.0','end-1c'),'Select a spot to see its details.')
            self.assertEqual(str(a.spot_detail.tag_cget('placeholder','foreground')),a.theme_muted)
            a.switch_theme()

    def test_frequency_sort_is_numeric_and_preserves_selection(self):
        a=self.app
        a.add_spot(Spot('S','14074.05','K1ABC','1200Z','one'))
        a.add_spot(Spot('S','7001','W1AW','1201Z','two'))
        a.add_spot(Spot('S','14074.01','N1ABC','1202Z','three'))
        a.render_spots();selected=next(row for row in a.spots.get_children() if a.spots.item(row,'values')[3]=='K1ABC')
        a.spots.selection_set(selected);a.sort_spots('frequency')
        self.assertEqual([a.spots.item(row,'values')[1] for row in a.spots.get_children()],['7001.00','14074.01','14074.05'])
        self.assertEqual(a.spots.selection(),(selected,))
        a.sort_spots('frequency')
        self.assertEqual([a.spots.item(row,'values')[1] for row in a.spots.get_children()],['14074.05','14074.01','7001.00'])
        a.search.set('K1ABC');self.assertEqual(a.spots.selection(),(selected,))
        self.assertEqual(len(a.spots.get_children()),1)

    def test_frequencies_display_two_decimals_without_changing_received_values(self):
        a=self.app
        self.assertEqual(str(a.spots.column('frequency','anchor')),'e')
        self.assertEqual(str(a.spots.heading('frequency','anchor')),'e')
        self.assertEqual(str(a.alarm_tree.column('frequency','anchor')),'e')
        for frequency,call in [('14074','K1ABC'),('14074.1','K2ABC'),('14074.05','K3ABC'),('14074.056','K4ABC')]:
            a.add_spot(Spot('S',frequency,call,'1200','CQ'))
        a.render_spots()
        values={a.spots.item(row,'values')[3]:a.spots.item(row,'values')[1] for row in a.spots.get_children()}
        self.assertEqual(values,{'K1ABC':'14074.00','K2ABC':'14074.10','K3ABC':'14074.05','K4ABC':'14074.06'})
        self.assertEqual([spot.frequency for _,spot in a.rows],['14074','14074.1','14074.05','14074.056'])
        a.search_field.set('Frequency');a.search.set('14074.00')
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K1ABC'])

    def test_unmapped_frequency_shows_other_band(self):
        a=self.app
        a.add_spot(Spot('S','1000','K1ABC','1200Z','CQ'))
        a.band.set('Other')
        rows=a.spots.get_children()
        self.assertEqual(len(rows),1)
        self.assertEqual(a.spots.item(rows[0],'values')[2],'Other')
        a.add_spot(Spot('S','14074','W1AW','1201Z','CQ'))
        a.band.set('All bands');a.sort_spots('band')
        self.assertEqual([a.spots.item(row,'values')[2] for row in a.spots.get_children()],['20 m','Other'])

    def test_60m_display_includes_the_pinned_dxspider_lower_range(self):
        a=self.app
        for frequency,call in [('5194.99','K1AAA'),('5195','K1BBB'),('5249','K1CCC'),('5450','K1DDD'),('5450.01','K1EEE')]:
            a.add_spot(Spot('W1AW',frequency,call,'1200Z',''))
        a.band.set('60 m')
        self.assertEqual({a.spots.item(row,'values')[3] for row in a.spots.get_children()},{'K1BBB','K1CCC','K1DDD'})
        self.assertEqual(a.band_shortcut_counts['60 m'].cget('text'),'3')

    def test_field_specific_search_only_filters_display(self):
        a=self.app
        a.add_spot(Spot('K1ABC','14074','W1AW','1200Z','CQ K1ABC'))
        a.add_spot(Spot('OTHER','7001','K1ABC','1201Z','CQ'))
        a.render_spots();a.search.set('k1abc')
        self.assertEqual(len(a.spots.get_children()),2)
        for field, expected in [('DX call','K1ABC'),('Spotter','W1AW'),('Comment','W1AW')]:
            with self.subTest(field=field):
                a.search_field.set(field)
                self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],[expected])
        a.search_field.set('All fields');self.assertEqual(len(a.spots.get_children()),2)
        self.assertEqual(len(a.rows),2)

    def test_all_fields_search_does_not_join_unrelated_fields(self):
        a=self.app
        a.add_spot(Spot('W1AW','14074','K1ABC','1200Z','CQ DX'))
        a.add_spot(Spot('W2AW','7001','K2ABC','1201Z','K1ABC W1AW'))
        a.search.set('K1ABC W1AW')
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['K2ABC'])

    def test_search_can_target_received_spot_metadata(self):
        a=self.app
        a.add_spot(Spot('W1AW','14074','K1ABC','1200Z','CQ',dx_country='France',dx_dxcc='227',
                        spotter_country='Bulgaria',spotter_dxcc='212',dx_locator='JN18',spotter_locator='KN12',origin='NODE1'))
        a.add_spot(Spot('W2AW','7001','K2ABC','1201Z','France',dx_country='Bulgaria',spotter_country='France',dx_locator='KN12'))
        for field,query,expected in [('Frequency','14074','K1ABC'),('DX country','France','K1ABC'),
                                     ('DX country','227','K1ABC'),('Spotter country','France','K2ABC'),
                                     ('Spotter country','212','K1ABC'),('DX grid','JN18','K1ABC'),
                                     ('Spotter grid','KN12','K1ABC'),('Origin','NODE1','K1ABC')]:
            with self.subTest(field=field,query=query):
                self.assertIn(field,a.search_field_box.cget('values'))
                a.search_field.set(field);a.search.set(query)
                self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],[expected])
        self.assertEqual(len(a.rows),2)

    def test_spot_detail_labels_node_country_numbers_without_arrl_claim(self):
        a=self.app
        a.add_spot(Spot('W1AW','14074','K1ABC','1200Z','CQ',dx_country='United States',dx_dxcc='226'))
        a.render_spots();a.spots.selection_set(a.spots.get_children()[0]);a.show_spot_detail()
        detail=a.spot_detail.get('1.0','end')
        self.assertIn('Node country 226',detail)
        self.assertNotIn('DXCC 226',detail)

    def test_new_source_clears_old_rows_but_same_source_retains_them(self):
        a=self.app
        with patch('app.Connection',FakeConnection):
            a.host.set('example.invalid');a.port.set('7300');a.call.set('LZ3ZZ');a.connect()
            a.add_spot(Spot('S','14074','D','1200Z',''));a.render_spots();a.disconnect()
            self.assertIn('not connected',a.spot_source_note.get());a.host.set('EXAMPLE.INVALID');a.connect()
            self.assertEqual(len(a.rows),1);a.disconnect();a.host.set('other.invalid');a.connect()
            self.assertEqual(len(a.rows),0);self.assertIn('other.invalid',a.spot_source_note.get())

    def test_extended_details_and_search(self):
        a=self.app
        a.add_spot(Spot('LZ3ZZ','14074','K1ABC','1200Z','CQ', dx_country='United States', dx_locator='FN42', dx_cq='5', spotter_country='Bulgaria', origin='NODE'))
        a.render_spots(); row=a.spots.get_children()[0]; a.spots.selection_set(row); a.show_spot_detail()
        detail=a.spot_detail.get('1.0','end')
        for value in ('United States', 'FN42', 'CQ 5', 'Bulgaria', 'Origin: NODE'): self.assertIn(value,detail)
        a.search.set('FN42'); self.assertEqual(a.spots.get_children(),(row,))
        a.search.set('unmatched'); self.assertEqual(a.spots.get_children(),())

    def test_empty_state_and_alternating_rows_follow_spot_view(self):
        a=self.app; a.deiconify(); a.update()
        self.assertTrue(a.spot_empty.winfo_ismapped())
        a.status.set('Connected'); self.assertIn('Waiting', a.spot_empty_hint.get())
        a.add_spot(Spot('S','14074','K1ABC','1200Z','one'))
        a.add_spot(Spot('S','14075','W1AW','1201Z','two'))
        a.render_spots(); a.update()
        self.assertFalse(a.spot_empty.winfo_ismapped())
        rows=a.spots.get_children()
        self.assertEqual(a.spots.item(rows[0], 'tags'), '')
        self.assertEqual(a.spots.item(rows[1], 'tags'), ('alternate',))
        a.search.set('not found'); a.update()
        self.assertTrue(a.spot_empty.winfo_ismapped()); self.assertEqual(a.spot_empty_title.get(), 'No matching spots')

    def test_spot_detail_and_command_visible_at_minimum_size(self):
        a=self.app;a.geometry('780x560');a.deiconify();a.update()
        self.assertTrue(a.spot_detail.winfo_ismapped());self.assertGreater(a.spot_detail.winfo_height(),25)
        self.assertTrue(a.command.winfo_ismapped());self.assertLessEqual(a.spot_detail.winfo_rooty()+a.spot_detail.winfo_height(),a.command.winfo_rooty())
        self.assertTrue(a.spot_lock_button.winfo_ismapped());self.assertTrue(a.clear_spots_button.winfo_ismapped())
        self.assertEqual(a.spot_lock_button.winfo_parent(),a.clear_spots_button.winfo_parent())
        lock, clear = a.spot_lock_button, a.clear_spots_button
        self.assertLessEqual(abs((lock.winfo_rooty() + lock.winfo_height() / 2) -
                                 (clear.winfo_rooty() + clear.winfo_height() / 2)), 1)
        self.assertLessEqual(lock.winfo_rootx() + lock.winfo_width(), clear.winfo_rootx())
        self.assertLessEqual(clear.winfo_rootx() - (lock.winfo_rootx() + lock.winfo_width()), 12)

    def test_search_limit_warning_remains_readable_with_long_wwv_summary(self):
        a=self.app;a.geometry('780x560');a.deiconify()
        a.search.set('test')
        self.assertIn('Rapid repeats may disconnect',a.spot_search_note.get())
        a.spot_wwv_summary.set('Last received WWV · node.example:7300 · '+('WWWW '*30))
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        label=next(w for w in walk(a) if isinstance(w,ttk.Label) and str(w.cget('textvariable'))==str(a.spot_search_note))
        a.update()
        self.assertTrue(label.winfo_ismapped())
        self.assertGreaterEqual(label.winfo_width(),label.winfo_reqwidth())
        a.spot_search_note.set('2 node(s) queued · some matches limited to recent node-filtered spots.');a.update()
        self.assertGreaterEqual(label.winfo_width(),label.winfo_reqwidth())
        a.spot_search_note.set('2 node(s) queued · 1 waiting for node limit. See Activity.');a.update()
        self.assertGreaterEqual(label.winfo_width(),label.winfo_reqwidth())

    def test_hidden_bands_filter_only_display_and_restore(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app_module, 'SETTINGS', Path(directory)/'settings.json'):
            a=App();a.withdraw()
            try:
                a.add_spot(Spot('S','14074','K1ABC','1200Z',''))
                a.add_spot(Spot('S','7001','W1AW','1201Z',''))
                a.add_spot(Spot('S','1000','UNKNOWN','1202Z',''))
                a.open_display_bands();dialog=next(w for w in a.winfo_children() if isinstance(w,tk.Toplevel))
                def walk(w):
                    for child in w.winfo_children():yield child;yield from walk(child)
                widgets=list(walk(dialog))
                next(w for w in widgets if isinstance(w,ttk.Checkbutton) and w.cget('text')=='20 m').invoke()
                next(w for w in widgets if isinstance(w,ttk.Checkbutton) and w.cget('text')=='40 m').invoke()
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke()
                self.assertEqual(len(a.rows),3);self.assertEqual(len(a.spots.get_children()),1)
                self.assertEqual(a.spots.item(a.spots.get_children()[0],'values')[3],'UNKNOWN')
                self.assertEqual(a.hidden_bands_note.get(),'2 bands hidden')
            finally:a.quit_app()
            restored=App();restored.withdraw()
            try:self.assertEqual(restored.hidden_bands,{'20 m','40 m'})
            finally:restored.quit_app()

    def test_higher_frequency_bands_can_be_hidden_without_losing_spots(self):
        a=self.app
        for frequency,call in [('222100','A1'),('432100','B1'),('1296000','C1')]:
            a.add_spot(Spot('S',frequency,call,'1200Z',''))
        a.render_spots()
        self.assertEqual({a.spots.item(row,'values')[2] for row in a.spots.get_children()},{'1.25 m','70 cm','Microwave'})
        a.hidden_bands={'1.25 m','70 cm','Microwave'};a.render_spots()
        self.assertEqual(len(a.spots.get_children()),0);self.assertEqual(len(a.rows),3)
        a.hidden_bands=set();a.render_spots();self.assertEqual(len(a.spots.get_children()),3)

    def test_four_metre_spots_are_grouped_and_can_be_hidden(self):
        a=self.app
        for frequency,call in [('69887','A1'),('70150','B1'),('71500','C1'),('71501','D1')]:
            a.add_spot(Spot('S',frequency,call,'1200Z',''))
        a.render_spots()
        bands={a.spots.item(row,'values')[3]:a.spots.item(row,'values')[2] for row in a.spots.get_children()}
        self.assertEqual(bands,{'A1':'4 m','B1':'4 m','C1':'4 m','D1':'Other'})
        a.hidden_bands={'4 m'};a.render_spots()
        self.assertEqual([a.spots.item(row,'values')[3] for row in a.spots.get_children()],['D1'])
        self.assertEqual(len(a.rows),4)

    def test_optional_band_colors_are_applied_and_restored(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app_module,'SETTINGS',Path(directory)/'settings.json'):
            a=App();a.withdraw()
            try:
                a.add_spot(Spot('S','14074','K1ABC','1200Z',''))
                a.add_spot(Spot('S','7001','W1AW','1201Z',''))
                a.render_spots();a.open_display_bands()
                dialog=next(w for w in a.winfo_children() if isinstance(w,tk.Toplevel))
                def walk(w):
                    for child in w.winfo_children():yield child;yield from walk(child)
                widgets=list(walk(dialog))
                next(w for w in widgets if isinstance(w,ttk.Checkbutton) and w.cget('text')=='Use light band colors in the spot list').invoke()
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke()
                tags={a.spots.item(row,'values')[3]:a.spots.item(row,'tags') for row in a.spots.get_children()}
                self.assertEqual(tags['K1ABC'],('band-20 m',));self.assertEqual(tags['W1AW'],('band-40 m',))
            finally:a.quit_app()
            restored=App();restored.withdraw()
            try:self.assertTrue(restored.color_bands)
            finally:restored.quit_app()

    def test_identical_spot_hiding_is_display_only_and_restored(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app_module,'SETTINGS',Path(directory)/'settings.json'):
            a=App();a.withdraw()
            try:
                a.alarm_calls.set('K1*');a.alarm_enabled.set(True);a.apply_alarm()
                spot=Spot('S','14074','K1ABC','1200Z','CQ',dx_country='K')
                a.add_spot(spot);a.render_spots();selected=a.spots.get_children()[0];a.spots.selection_set(selected)
                a.add_spot(spot);a.add_spot(Spot('S','14074','K1ABC','1200Z','CQ',dx_country='different'))
                a.open_display_bands();dialog=next(w for w in a.winfo_children() if isinstance(w,tk.Toplevel))
                def walk(w):
                    for child in w.winfo_children():yield child;yield from walk(child)
                widgets=list(walk(dialog))
                next(w for w in widgets if isinstance(w,ttk.Checkbutton) and w.cget('text')=='Hide identical spots in the spot list').invoke()
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke()
                self.assertEqual(len(a.rows),3);self.assertEqual(len(a.alarm_rows),3)
                self.assertEqual(len(a.spots.get_children()),2);self.assertEqual(a.spots.selection(),(selected,))
            finally:a.quit_app()
            restored=App();restored.withdraw()
            try:self.assertTrue(restored.hide_duplicate_spots)
            finally:restored.quit_app()

if __name__ == '__main__':unittest.main()
