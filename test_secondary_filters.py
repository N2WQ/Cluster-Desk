import tkinter as tk
from tkinter import ttk
import socket
import threading
import time
import unittest
from unittest.mock import patch

from app import App
from protocol import TrackedCommand
from country_lookup import CountryIndex
from test_country_lookup import DATA


def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)


class FakeConnection:
    def __init__(self,emit):self.emit=emit;self.commands=[]
    def start(self,*args):pass
    def send(self,command):self.commands.append(command)
    def send_tracked(self,command):
        command=TrackedCommand(command);self.send(command);return command
    def close(self):pass


class SecondaryFilterTests(unittest.TestCase):
    def test_state_lookup_shows_latest_additional_activity(self):
        window=App();window._save_settings=lambda:True
        try:
            with patch('app.Connection',FakeConnection):
                ident=window.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                conn=item['connection'];conn.emit('dialect','DXSpider');conn.emit('status','Connected');window.poll_secondary_nodes()
                window.append_secondary_activity(item,'\n'.join('Earlier activity '+str(i) for i in range(100)))
                window.open_secondary_activity(ident);window.update();output=item['view']['text']
                output.yview_moveto(0);window.update();self.assertLess(output.yview()[1],.99)
                window.open_secondary_filters(ident);window.update()
                editor=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel) and w.title()=='Additional node filters')
                button=next(w for w in children(editor) if isinstance(w,ttk.Button) and w.cget('text')=='Look up node state…')
                with patch('app.ask_node_state_call',return_value='W1AW'):button.invoke()
                window.update();self.assertGreaterEqual(output.yview()[1],.999)
                window.append_secondary_activity(item,'\nNode response\n');window.render_secondary_activity(item);window.update()
                self.assertGreaterEqual(output.yview()[1],.999)
        finally:window.quit_app()

    def test_state_lookup_targets_additional_node(self):
        window=App();window.withdraw();window._save_settings=lambda:True
        try:
            with patch('app.Connection',FakeConnection):
                ident=window.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                conn=item['connection'];conn.emit('dialect','DXSpider');conn.emit('status','Connected');window.poll_secondary_nodes()
                window.open_secondary_filters(ident)
                editor=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                button=next(w for w in children(editor) if isinstance(w,ttk.Button) and w.cget('text')=='Look up node state…')
                with patch('app.ask_node_state_call',return_value='W1AW'),patch.object(window,'open_secondary_activity') as activity:
                    button.invoke();activity.assert_called_once_with(ident)
                self.assertEqual(conn.commands,['sh/usdb W1AW'])
        finally:window.quit_app()

    def test_state_picker_rejects_changed_additional_connection(self):
        window=App();window.withdraw();window._save_settings=lambda:True
        try:
            with patch('app.Connection',FakeConnection):
                ident=window.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                conn=item['connection'];conn.emit('dialect','CC Cluster');conn.emit('status','Connected');window.poll_secondary_nodes()
                window.open_secondary_filters(ident)
                editor=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                category=next(w for w in children(editor) if isinstance(w,ttk.Combobox) and 'DX state' in w.cget('values'))
                category.set('DX state');category.event_generate('<<ComboboxSelected>>');window.update()
                next(w for w in children(editor) if isinstance(w,ttk.Button) and w.cget('text')=='Choose states / provinces…').invoke()
                dialog=next(w for w in editor.winfo_children() if isinstance(w,tk.Toplevel))
                next(w for w in children(dialog) if isinstance(w,ttk.Checkbutton) and w.cget('text')=='Texas (TX)').invoke()
                item['epoch']+=1
                with patch('app.messagebox.showerror') as error:
                    next(w for w in children(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
                self.assertEqual(next(w for w in children(editor) if type(w) is ttk.Entry).get(),'')
                self.assertEqual(conn.commands,[]);error.assert_called_once()
        finally:window.quit_app()

    def test_state_picker_fills_additional_node_form_without_sending(self):
        for dialect in ('CC Cluster','DXSpider'):
            with self.subTest(dialect=dialect):
                window=App();window.withdraw();window._save_settings=lambda:True
                try:
                    with patch('app.Connection',FakeConnection):
                        ident=window.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                        conn=item['connection'];conn.emit('dialect',dialect);conn.emit('status','Connected');window.poll_secondary_nodes()
                        window.open_secondary_filters(ident)
                        editor=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                        category=next(w for w in children(editor) if isinstance(w,ttk.Combobox) and 'DX state' in w.cget('values'))
                        category.set('DX state');category.event_generate('<<ComboboxSelected>>');window.update()
                        choose=next(w for w in children(editor) if isinstance(w,ttk.Button) and w.cget('text') in ('Choose states / provinces…','Choose…'))
                        choose.invoke();dialog=next(w for w in editor.winfo_children() if isinstance(w,tk.Toplevel))
                        next(w for w in children(dialog) if isinstance(w,ttk.Checkbutton) and w.cget('text')=='Texas (TX)').invoke()
                        next(w for w in children(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
                        self.assertEqual(conn.commands,[])
                        entry=next(w for w in children(editor) if type(w) is ttk.Entry);self.assertEqual(entry.get(),'TX')
                        next(w for w in children(editor) if isinstance(w,ttk.Button) and w.cget('text')=='Reject matching').invoke()
                        self.assertEqual(conn.commands,['set/filter DXSTATE/reject TX' if dialect=='CC Cluster' else 'reject/spots 0 call_state TX'])
                finally:window.quit_app()

    def test_country_picker_fills_additional_node_filter_without_sending(self):
        for dialect in ('CC Cluster','DXSpider'):
            with self.subTest(dialect=dialect):
                window=App();window.withdraw();window._save_settings=lambda:True
                window.country_index=CountryIndex.from_text(DATA)
                try:
                    with patch('app.Connection',FakeConnection):
                        ident=window.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                        conn=item['connection'];conn.emit('dialect',dialect);conn.emit('status','Connected');window.poll_secondary_nodes()
                        window.open_secondary_filters(ident)
                        editor=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                        boxes=[w for w in children(editor) if isinstance(w,ttk.Combobox)]
                        category=next(w for w in boxes if 'DX country' in w.cget('values'))
                        category.set('DX country');category.event_generate('<<ComboboxSelected>>');window.update()
                        choose=next(w for w in children(editor) if isinstance(w,ttk.Button) and w.cget('text') in ('Choose countries…','Choose…'))
                        choose.invoke();dialog=next(w for w in editor.winfo_children() if isinstance(w,tk.Toplevel))
                        listing=next(w for w in children(dialog) if isinstance(w,tk.Listbox));listing.selection_set(0)
                        next(w for w in children(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
                        self.assertEqual(conn.commands,[])
                        entry=next(w for w in children(editor) if type(w) is ttk.Entry)
                        self.assertEqual(entry.get(),'LZ')
                        next(w for w in children(editor) if isinstance(w,ttk.Button) and w.cget('text')=='Reject matching').invoke()
                        self.assertEqual(conn.commands,['set/filter DXCTY/reject LZ' if dialect=='CC Cluster' else 'reject/spots 0 call_dxcc LZ'])
                finally:window.quit_app()

    def test_cc_band_filter_targets_only_selected_additional_node(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=window.start_secondary_node('cc.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                conn=item['connection'];conn.emit('dialect','CC Cluster');conn.emit('status','Connected');window.poll_secondary_nodes()
                window.open_secondary_filters(ident)
                dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                widgets=list(children(dialog));boxes=[w for w in widgets if isinstance(w,ttk.Combobox)]
                category=next(w for w in boxes if 'DX country' in str(w.cget('values')))
                band=next(w for w in boxes if '20 m' in str(w.cget('values')))
                category.set('DX country');band.set('20 m')
                next(w for w in widgets if type(w) is ttk.Entry).insert(0,'K')
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Pass matching').invoke()
                self.assertEqual(conn.commands,['set/filter/20 DXCTY/pass K'])
                category.set('Keywords');self.assertEqual(band.get(),'All bands');self.assertIn('disabled',band.state())
        finally:window.quit_app()

    def test_filter_editors_do_not_expose_time_filter_controls(self):
        window=App();window.withdraw();window._save_settings=lambda:True
        try:
            self.assertFalse(any(isinstance(w,ttk.Button) and 'time filter' in w.cget('text').lower() for w in children(window.cc_filter)))
            with patch('app.Connection',FakeConnection):
                ident=window.start_secondary_node('cc.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                item['connection'].emit('dialect','CC Cluster');item['connection'].emit('status','Connected');window.poll_secondary_nodes()
                window.open_secondary_filters(ident)
                editor=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                labels=[w.cget('text') for w in children(editor) if isinstance(w,ttk.Button)]
                self.assertIn('Show node filters',labels)
                self.assertFalse(any('time filter' in label.lower() for label in labels))
                self.assertEqual(item['connection'].commands,[])
        finally:window.quit_app()

    def test_rejected_additional_node_filter_does_not_leave_requested_status(self):
        window=App();window.withdraw();window._save_settings=lambda:True
        try:
            with patch('app.Connection',FakeConnection):
                ident=window.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                conn=item['connection'];conn.emit('dialect','DXSpider');conn.emit('status','Connected');window.poll_secondary_nodes()
                window.open_secondary_filters(ident);dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                widgets=list(children(dialog));entry=next(w for w in widgets if type(w) is ttk.Entry)
                entry.insert(0,'20m');button=next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Accept matching')
                button.invoke();self.assertEqual(conn.commands,['accept/spots 0 on 20m'])
                with patch.object(conn,'send',side_effect=OSError('Command limit reached')),patch('app.messagebox.showerror') as error:
                    button.invoke();error.assert_called_once()
                notes=[dialog.getvar(w.cget('textvariable')) for w in widgets if isinstance(w,ttk.Label) and w.cget('textvariable')]
                self.assertTrue(any('Change not sent' in note for note in notes))
                self.assertEqual(entry.get(),'20m');self.assertEqual(len(conn.commands),1)
        finally:window.quit_app()

    def test_rejected_additional_clear_and_reset_report_not_sent(self):
        window=App();window.withdraw();window._save_settings=lambda:True
        try:
            with patch('app.Connection',FakeConnection):
                for host,dialect,button_text,expected in (
                    ('spider.invalid','DXSpider','Clear all…','Clear not sent'),
                    ('cc.invalid','CC Cluster','Reset all node filters…','Reset not sent')):
                    ident=window.start_secondary_node(host,7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                    conn=item['connection'];conn.emit('dialect',dialect);conn.emit('status','Connected');window.poll_secondary_nodes()
                    window.open_secondary_filters(ident);dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel) and w.title()=='Additional node filters')
                    widgets=list(children(dialog));button=next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')==button_text)
                    with patch.object(conn,'send',side_effect=OSError('Command limit reached')),patch('app.messagebox.askyesno',return_value=True),patch('app.messagebox.showerror'):
                        button.invoke()
                    notes=[dialog.getvar(w.cget('textvariable')) for w in widgets if isinstance(w,ttk.Label) and w.cget('textvariable')]
                    self.assertTrue(any(expected in note for note in notes));self.assertEqual(conn.commands,[])
                    dialog.destroy()
        finally:window.quit_app()

    def test_secondary_precision_targets_only_selected_cc_node_and_rejects_stale_session(self):
        window=App();window.withdraw();window._save_settings=lambda:True
        try:
            with patch('app.Connection',FakeConnection):
                first=window.start_secondary_node('first.invalid',7300,'LZ3ZZ')
                second=window.start_secondary_node('second.invalid',7301,'LZ3ZZ-1')
                for ident in (first,second):
                    item=window.secondary_nodes[ident];item['connection'].emit('dialect','CC Cluster');item['connection'].emit('status','Connected')
                window.poll_secondary_nodes();target=window.secondary_nodes[second]
                window.open_secondary_nodes();window.secondary_tree.selection_set(str(second))
                next(w for w in children(window.secondary_window) if isinstance(w,ttk.Button) and w.cget('text')=='Frequency precision…').invoke()
                dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel) and w.title()=='Spot frequency precision')
                widgets=list(children(dialog));self.assertEqual(target['connection'].commands,[])
                for label,command in (('Show current','sh/res'),('2 decimals','set/res 2'),('1 decimal','set/res 1')):
                    next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')==label).invoke()
                    self.assertEqual(target['connection'].commands[-1],command)
                self.assertEqual(window.secondary_nodes[first]['connection'].commands,[])
                target['epoch']+=1
                with patch('app.messagebox.showerror') as error:
                    next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='2 decimals').invoke();error.assert_called_once()
                self.assertEqual(len(target['connection'].commands),3)
        finally:window.quit_app()



    def test_secondary_band_picker_keeps_the_selected_connection(self):
        window=App();window.withdraw();window._save_settings=lambda:True
        try:
            with patch('app.Connection',FakeConnection):
                ident=window.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                item['connection'].emit('dialect','DXSpider');item['connection'].emit('status','Connected');window.poll_secondary_nodes()
                window.open_secondary_filters(ident);editor=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                notes=[editor.getvar(w.cget('textvariable')) for w in children(editor) if isinstance(w,ttk.Label) and w.cget('textvariable')]
                self.assertTrue(any('Example: 20m or 14000/14350' in note for note in notes))
                self.assertFalse(any('Example: on 20m' in note for note in notes))
                next(w for w in children(editor) if isinstance(w,ttk.Button) and w.cget('text')=='Choose…').invoke()
                picker=next(w for w in editor.winfo_children() if isinstance(w,tk.Toplevel))
                next(w for w in children(picker) if isinstance(w,ttk.Checkbutton) and w.cget('text')=='20m').invoke()
                item['epoch']+=1
                with patch('app.messagebox.showerror') as error:
                    next(w for w in children(picker) if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
                    error.assert_called_once()
                self.assertEqual(item['connection'].commands,[])
                picker.destroy()
        finally:window.quit_app()

    def test_secondary_dxspider_state_filter_discloses_usdb_dependency(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=window.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                item['connection'].emit('dialect','DXSpider');item['connection'].emit('status','Connected');window.poll_secondary_nodes()
                window.open_secondary_filters(ident);dialog=next(child for child in window.winfo_children() if isinstance(child,tk.Toplevel))
                widgets=list(children(dialog));combos=[w for w in widgets if isinstance(w,ttk.Combobox)];combos[-1].set('DX state')
                messages=[dialog.getvar(w.cget('textvariable')) for w in widgets if isinstance(w,ttk.Label) and w.cget('textvariable')]
                self.assertTrue(any('USDB' in message for message in messages))
                combos[-1].set('DX country')
                messages=[dialog.getvar(w.cget('textvariable')) for w in widgets if isinstance(w,ttk.Label) and w.cget('textvariable')]
                self.assertTrue(any('not necessarily ARRL DXCC' in message for message in messages))
        finally:window.quit_app()
    def test_cc_reset_targets_selected_node_and_rechecks_after_confirmation(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=window.start_secondary_node('cc.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                conn=item['connection'];conn.emit('dialect','CC Cluster');conn.emit('status','Connected');window.poll_secondary_nodes()
                window.open_secondary_filters(ident);dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                button=next(w for w in children(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Reset all node filters…')
                with patch('app.messagebox.askyesno',return_value=False):button.invoke()
                self.assertEqual(conn.commands,[])
                def changed(*args,**kwargs):item['epoch']+=1;return True
                with patch('app.messagebox.askyesno',side_effect=changed),patch('app.messagebox.showerror') as error:
                    button.invoke();error.assert_called_once()
                self.assertEqual(conn.commands,[])
                item['epoch']-=1
                with patch('app.messagebox.askyesno',return_value=True):button.invoke()
                self.assertEqual(conn.commands,['set/nofilter'])
        finally:window.quit_app()

    def test_additional_dxspider_rbn_strength_filter(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=window.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                item['connection'].emit('dialect','DXSpider');item['connection'].emit('status','Connected');window.poll_secondary_nodes()
                window.open_secondary_filters(ident)
                dialog=next(child for child in window.winfo_children() if isinstance(child,tk.Toplevel));widgets=list(children(dialog))
                traffic,line,category=[w for w in widgets if isinstance(w,ttk.Combobox)]
                traffic.set('RBN spots');line.set('3');category.set('Minimum RBN strength')
                entry=next(w for w in widgets if isinstance(w,ttk.Entry) and not isinstance(w,ttk.Combobox));entry.insert(0,'15')
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Accept matching').invoke()
                self.assertEqual(item['connection'].commands,['accept/rbn 3 db 15+'])
        finally:window.quit_app()

    def test_cc_band_picker_targets_selected_connection_and_rechecks_it(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=window.start_secondary_node('cc.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                item['connection'].emit('dialect','CC Cluster');item['connection'].emit('status','Connected');window.poll_secondary_nodes()
                window.open_secondary_filters(ident)
                editor=next(child for child in window.winfo_children() if isinstance(child,tk.Toplevel))
                widgets=list(children(editor));category=next(w for w in widgets if isinstance(w,ttk.Combobox))
                picker=next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Choose bands and segments…')
                self.assertIn('disabled',picker.state())
                category.set('Band / frequency segment');self.assertNotIn('disabled',picker.state());picker.invoke()
                notes=[editor.getvar(w.cget('textvariable')) for w in widgets if isinstance(w,ttk.Label) and w.cget('textvariable')]
                self.assertTrue(any('other bands may still pass' in note for note in notes))
                dialog=next(w for w in editor.winfo_children() if isinstance(w,tk.Toplevel));parts=list(children(dialog))
                tree=next(w for w in parts if isinstance(w,ttk.Treeview));tree.selection_set(('40-CW','6'))
                next(w for w in parts if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
                self.assertEqual(item['connection'].commands,[])
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Pass matching').invoke()
                self.assertEqual(item['connection'].commands,['set/filter DXBM/pass 40-CW,6'])
                picker.invoke();dialog=next(w for w in editor.winfo_children() if isinstance(w,tk.Toplevel));parts=list(children(dialog))
                item['epoch']+=1
                with patch('app.messagebox.showerror') as error:
                    next(w for w in parts if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
                    error.assert_called_once()
                self.assertEqual(len(item['connection'].commands),1)
        finally:window.quit_app()

    def test_local_node_connect_filter_spot_and_readback(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(6)
        commands=[];errors=[]
        def server():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(6);stream=sock.makefile('rb')
                    sock.sendall(b'login: ');commands.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    commands.extend((stream.readline(),stream.readline(),stream.readline()))
                    sock.sendall(b'CC11^14074.0^K1ABC^30-Oct-2026^1234Z^CQ^W3ABC^291\r\n')
                    commands.append(stream.readline())
                    sock.sendall(b'LZ3ZZ: spots\r\n  0 accept on 20m\r\nLZ3ZZ de NODE dxspider >\r\n')
                    time.sleep(.5);stream.close()
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            ident=window.start_secondary_node('127.0.0.1',listener.getsockname()[1],'LZ3ZZ')
            deadline=time.monotonic()+6
            while window.secondary_nodes[ident]['status']!='Connected' and time.monotonic()<deadline:window.update();time.sleep(.01)
            self.assertEqual(window.secondary_nodes[ident]['status'],'Connected')
            window.open_secondary_filters(ident);dialog=next(child for child in window.winfo_children() if isinstance(child,tk.Toplevel))
            widgets=list(children(dialog));entry=next(w for w in widgets if w.winfo_class()=='TEntry');entry.insert(0,'20m')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Accept matching').invoke()
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Show node filters').invoke()
            deadline=time.monotonic()+6
            while (not window.rows or '0 accept on 20m' not in window.secondary_nodes[ident]['activity']) and time.monotonic()<deadline:window.update();time.sleep(.01)
            self.assertEqual(window.rows[-1][1].dx,'K1ABC')
            self.assertIn('0 accept on 20m',window.secondary_nodes[ident]['activity'])
            self.assertEqual(window.secondary_nodes[ident]['filter_capture'].state,'complete')
            self.assertIn('0 accept on 20m',window.secondary_nodes[ident]['filter_capture'].text())
            worker.join(3);self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(commands,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'accept/spots 0 on 20m\r\n',b'sh/filter\r\n'])
        finally:window.quit_app();worker.join(1)

    def test_dxspider_routes_filters_and_readback_to_selected_node(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                first=window.start_secondary_node('first.invalid',7300,'LZ3ZZ')
                second=window.start_secondary_node('second.invalid',7301,'LZ3ZZ-1')
                for ident in (first,second):
                    conn=window.secondary_nodes[ident]['connection'];conn.emit('dialect','DXSpider');conn.emit('status','Connected')
                window.poll_secondary_nodes();target=window.secondary_nodes[second];window.open_secondary_filters(second)
                dialog=next(child for child in window.winfo_children() if isinstance(child,tk.Toplevel))
                widgets=list(children(dialog));entry=next(w for w in widgets if w.winfo_class()=='TEntry')
                selector=next(w for w in widgets if isinstance(w,ttk.Combobox) and 'Advanced expression' in w.cget('values'))
                self.assertEqual(selector.get(),'Band or range');selector.set('Advanced expression')
                entry.insert(0,'on 20m and by G')
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Accept matching').invoke()
                self.assertEqual(target['connection'].commands,['accept/spots 0 on 20m and by G'])
                self.assertEqual(window.secondary_nodes[first]['connection'].commands,[])
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Show node filters').invoke()
                self.assertEqual(target['connection'].commands[-1],'sh/filter')
                self.assertIsNotNone(target['filter_view'])
                target['epoch']+=1
                with patch('app.messagebox.showerror') as error:
                    next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Reject matching').invoke()
                    error.assert_called_once()
                self.assertEqual(len(target['connection'].commands),2)
        finally:window.quit_app()

    def test_readbacks_are_isolated_and_interruption_is_reported(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ids=[window.start_secondary_node(host,7300,'LZ3ZZ') for host in ('first.invalid','second.invalid')]
                for ident in ids:
                    conn=window.secondary_nodes[ident]['connection'];conn.emit('dialect','DXSpider');conn.emit('status','Connected')
                window.poll_secondary_nodes()
                for ident in ids:
                    window.open_secondary_filters(ident)
                    editor=[child for child in window.winfo_children() if isinstance(child,tk.Toplevel) and child.title()=='Additional node filters'][-1]
                    next(w for w in children(editor) if isinstance(w,ttk.Button) and w.cget('text')=='Show node filters').invoke()
                first,second=[window.secondary_nodes[i] for i in ids]
                a,b=first['connection'],second['connection']
                a.emit('sent','accept/spots 0 on 20m');a.emit('sent','sh/filter')
                a.emit('data','LZ3ZZ: spots\r\nold result\r\nLZ3ZZ de NODE dxspider >\r\n')
                a.emit('sent',a.commands[-1]);b.emit('sent',b.commands[-1])
                b.emit('data','OTHER: spots\r\nunrelated\r\nOTHER de NODE dxspider >\r\n')
                a.emit('data','LZ3ZZ: spots\r\n0 accept on 20m\r\nLZ3ZZ de NODE dxspider >')
                b.emit('data','LZ3ZZ: spots\r\n0 reject on 40m\r\n')
                window.poll_secondary_nodes()
                self.assertEqual(first['filter_capture'].state,'partial')
                self.assertIn('earlier filter request',first['filter_view']['status'].get())
                self.assertIn('accept on 20m',first['filter_capture'].text())
                self.assertNotIn('old result',first['filter_capture'].text())
                self.assertEqual(second['filter_capture'].state,'receiving')
                self.assertNotIn('unrelated',second['filter_capture'].text())
                b.emit('sent','sh/dx');window.poll_secondary_nodes()
                self.assertEqual(second['filter_capture'].state,'partial')
                self.assertIn('Partial',second['filter_view']['status'].get())
                self.assertEqual(first['filter_capture'].state,'partial')
        finally:window.quit_app()

    def test_readback_timeout_and_disconnect_are_partial(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=window.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident];conn=item['connection']
                conn.emit('dialect','DXSpider');conn.emit('status','Connected');window.poll_secondary_nodes()
                window.open_secondary_filters(ident);editor=next(child for child in window.winfo_children() if isinstance(child,tk.Toplevel))
                show=next(w for w in children(editor) if isinstance(w,ttk.Button) and w.cget('text')=='Show node filters')
                show.invoke();item['filter_deadline']=time.monotonic()-1;window.poll_secondary_nodes()
                self.assertEqual(item['filter_capture'].state,'partial')
                show.invoke();item['filter_deadline']=time.monotonic()+1
                conn.emit('sent',conn.commands[-1]);window.poll_secondary_nodes()
                self.assertEqual(item['filter_capture'].state,'waiting')
                self.assertTrue(item['filter_capture'].ambiguous)
                self.assertGreater(item['filter_deadline'],time.monotonic()+10)
                conn.emit('status','Disconnected');window.poll_secondary_nodes()
                self.assertEqual(item['filter_capture'].state,'partial')
                self.assertTrue(item['filter_unresolved'])
                window._start_secondary_attempt(ident)
                self.assertFalse(item['filter_unresolved'])
        finally:window.quit_app()

    def test_cc_filter_off_and_clear_confirmation_do_not_retarget(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=window.start_secondary_node('cc.invalid',7300,'LZ3ZZ')
                item=window.secondary_nodes[ident];item['connection'].emit('dialect','CC Cluster');item['connection'].emit('status','Connected');window.poll_secondary_nodes()
                window.open_secondary_filters(ident)
                dialog=next(child for child in window.winfo_children() if isinstance(child,tk.Toplevel));widgets=list(children(dialog))
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Turn filter off').invoke()
                self.assertEqual(item['connection'].commands,['set/filter keyword/off'])
        finally:window.quit_app()

    def test_clear_all_rechecks_connection_after_confirmation(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            with patch('app.Connection',FakeConnection):
                ident=window.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=window.secondary_nodes[ident]
                item['connection'].emit('dialect','DXSpider');item['connection'].emit('status','Connected');window.poll_secondary_nodes()
                window.open_secondary_filters(ident);dialog=next(child for child in window.winfo_children() if isinstance(child,tk.Toplevel))
                def changed(*args,**kwargs):item['epoch']+=1;return True
                with patch('app.messagebox.askyesno',side_effect=changed),patch('app.messagebox.showerror') as error:
                    next(w for w in children(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Clear all…').invoke();error.assert_called_once()
                self.assertEqual(item['connection'].commands,[])
        finally:window.quit_app()


if __name__=='__main__':unittest.main()
