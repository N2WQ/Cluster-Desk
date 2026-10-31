import unittest
from tkinter import ttk
from unittest.mock import patch

from app import App
from protocol import Spot
from station import StationCapture, node_location, station_command, profile_command, qrz_url


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class FakeConnection:
    def __init__(self): self.commands=[]
    def send(self,value): self.commands.append(value)
    def close(self): pass


class StationTests(unittest.TestCase):
    def test_qrz_url_accepts_callsigns_and_rejects_unsafe_values(self):
        self.assertEqual(qrz_url('ea8/k1abc'),'https://www.qrz.com/db/EA8%2FK1ABC')
        self.assertEqual(qrz_url('LZ3ZZ-1'),'https://www.qrz.com/db/LZ3ZZ-1')
        for value in ('https://evil.example','K1ABC?x=1','K1ABC\nW1AW','ALL','K1ABC-#','A'*33,'ß1'):
            with self.subTest(value=value),self.assertRaises(ValueError):qrz_url(value)

    def test_station_lookup_does_not_convert_non_ascii_into_another_callsign(self):
        with self.assertRaises(ValueError): station_command('ß1')
        self.assertEqual(station_command('lz3zz'),'sh/sta LZ3ZZ')

    def test_qrz_selector_opens_only_the_explicitly_chosen_call(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.add_spot(Spot('LZ3ZZ','14074','K1ABC','1200Z','CQ'));app.render_spots()
            app.spots.selection_set(app.spots.get_children()[0]);app.qrz_button.invoke()
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            buttons={w.cget('text'):w for w in descendants(dialog) if isinstance(w,ttk.Button)}
            with patch('app.webbrowser.open_new_tab',return_value=True) as browser:
                self.assertEqual(browser.call_count,0);buttons['Spotter: LZ3ZZ'].invoke()
                browser.assert_called_once_with('https://www.qrz.com/db/LZ3ZZ')
        finally:app.quit_app()

    def test_shift_click_targets_only_callsign_columns(self):
        from types import SimpleNamespace
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.add_spot(Spot('LZ3ZZ','14074','K1ABC','1200Z','CQ'));app.render_spots();row=app.spots.get_children()[0]
            event=SimpleNamespace(x=5,y=5)
            with patch.object(app.spots,'identify_row',return_value=row),patch.object(app.spots,'identify_column',return_value='#4'),patch('app.webbrowser.open_new_tab',return_value=True) as browser:
                self.assertEqual(app.open_qrz_from_spot_event(event),'break');browser.assert_called_once_with('https://www.qrz.com/db/K1ABC')
            with patch.object(app.spots,'identify_row',return_value=row),patch.object(app.spots,'identify_column',return_value='#3'),patch('app.webbrowser.open_new_tab') as browser:
                self.assertIsNone(app.open_qrz_from_spot_event(event));browser.assert_not_called()
            with patch('app.webbrowser.open_new_tab',return_value=False),patch('app.messagebox.showerror') as error:
                self.assertFalse(app.open_qrz_call('K1ABC'));error.assert_called_once()
        finally:app.quit_app()

    def test_profile_commands_validate_fields_and_prevent_extra_commands(self):
        self.assertEqual(profile_command('Name','  Ada  '),'set/name Ada')
        self.assertEqual(profile_command('QTH','Sofia Bulgaria'),'set/qth Sofia Bulgaria')
        self.assertEqual(profile_command('Grid','kn12ab'),'set/qra KN12AB')
        for field,value in [('Name','a\nbye'),('QTH','bad {value}'),('Grid','ZZ99'),('Grid',''),('Name','x'*81),('unknown','x')]:
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):profile_command(field,value)

    def test_node_profile_sends_only_selected_field_and_rejects_stale_session(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.status.set('Connected');app.dialect='DXSpider';app.open_node_profile()
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');widgets=list(descendants(dialog))
            entries=[w for w in widgets if type(w) is ttk.Entry];entries[2].insert(0,'KN12')
            button=next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Set Grid')
            self.assertEqual(conn.commands,[]);button.invoke();self.assertEqual(conn.commands,['set/qra KN12'])
            app.generation+=1
            with patch('app.messagebox.showerror') as error:button.invoke();error.assert_called_once()
            self.assertEqual(conn.commands,['set/qra KN12'])
        finally:app.quit_app()

    def test_cc_cluster_profile_uses_same_documented_commands(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.status.set('Connected');app.dialect='CC Cluster';app.open_node_profile()
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');widgets=list(descendants(dialog))
            entries=[w for w in widgets if type(w) is ttk.Entry];entries[1].insert(0,'Sofia, Bulgaria')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Set QTH').invoke()
            self.assertEqual(conn.commands,['set/qth Sofia, Bulgaria'])
            app.dialect='DXSpider'
            with patch('app.messagebox.showerror') as error:
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Set QTH').invoke();error.assert_called_once()
            self.assertEqual(len(conn.commands),1)
        finally:app.quit_app()

    def test_cc_frequency_precision_is_explicit_and_session_bound(self):
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            conn=FakeConnection();app.connection=conn;app.status.set('Connected');app.dialect='CC Cluster';app.open_node_profile()
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');widgets=list(descendants(dialog))
            self.assertEqual(conn.commands,[])
            for label,command in (('Show current','sh/res'),('2 decimals','set/res 2'),('1 decimal','set/res 1')):
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')==label).invoke()
                self.assertEqual(conn.commands[-1],command)
            app.generation+=1
            with patch('app.messagebox.showerror') as error:
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='2 decimals').invoke();error.assert_called_once()
            self.assertEqual(len(conn.commands),3)
        finally:app.quit_app()

    def test_profile_view_requests_current_call_and_does_not_replace_active_lookup(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.connection_target=('example.invalid',7300,'LZ3ZZ')
            app.status.set('Connected');app.dialect='DXSpider';app.open_node_profile()
            profile=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            button=next(w for w in descendants(profile) if isinstance(w,ttk.Button) and w.cget('text')=='View current profile…')
            button.invoke();self.assertEqual(conn.commands,['sh/sta LZ3ZZ'])
            self.assertEqual(app.station_call.get(),'LZ3ZZ')
            with patch('app.messagebox.showerror') as error:button.invoke();error.assert_called_once()
            self.assertEqual(conn.commands,['sh/sta LZ3ZZ'])
            app.events.put((app.generation,'sent','sh/sta LZ3ZZ'))
            app.events.put((app.generation,'data','User: LZ3ZZ\r\nName: Test\r\nLZ3ZZ de NODE dxspider >'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertIn('Name: Test',app.station_text.get('1.0','end'))
        finally:app.quit_app()

    def test_node_location_uses_valid_unambiguous_coordinates(self):
        self.assertEqual(node_location(['Location     : 49 11 N 122 30 W']),(49+11/60,-122.5))
        self.assertEqual(node_location(['Location : 00 30 S 002 00 E (JJ00)']),(-.5,2.0))
        for lines in (['Location : 91 00 N 002 00 E'],['Location : 90 01 N 002 00 E'],['Location : 00 60 N 002 00 E'],['Location :'],['Location : 49 11 N 122 30 W','Location : 40 00 N 073 00 W']):
            with self.subTest(lines=lines):self.assertIsNone(node_location(lines))

    def test_capture_requires_selected_call_and_handles_both_prompts(self):
        for prompt in ('LZ3ZZ de NODE CCC >','LZ3ZZ de NODE dxspider >'):
            capture=StationCapture('w1aw');capture.sent()
            self.assertFalse(capture.feed('User : W1AW-1'))
            self.assertTrue(capture.feed('User         : W1AW (at NODE)'))
            capture.feed('Name         : Test');capture.feed(prompt)
            self.assertEqual(capture.state,'complete');self.assertEqual(capture.lines[-1],'Name         : Test')
        for value in ('','A\nBYE','A B','*','A'*33):
            with self.subTest(value=value),self.assertRaises(ValueError):station_command(value)

    def test_missing_dxspider_record_requires_exact_call(self):
        capture=StationCapture('W1AW');capture.sent()
        self.assertFalse(capture.feed('*** User record for W1AW-1 not found ***'))
        self.assertTrue(capture.feed('*** User record for W1AW not found ***'))
        capture.feed('LZ3ZZ de NODE dxspider >')
        self.assertEqual(capture.state,'complete');self.assertTrue(capture.not_found)

    def test_lookup_selected_spot_and_node_response(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.status.set('Connected')
            app.add_spot(Spot('S','14074','W1AW','1200Z',''));app.render_spots();app.spots.selection_set(app.spots.get_children()[0])
            app.station_info_button.invoke();dialog=app.station_window;widgets=list(descendants(dialog))
            entry=next(w for w in widgets if type(w) is ttk.Entry);self.assertEqual(entry.get(),'W1AW')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Look up').invoke()
            self.assertEqual(conn.commands,['sh/sta W1AW'])
            app.events.put((app.generation,'sent','sh/sta W1AW'))
            app.events.put((app.generation,'data','User         : W1AW\r\nName         : Test\r\nLZ3ZZ de NODE CCC >'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.station_capture.state,'complete');self.assertIn('Name         : Test',app.station_text.get('1.0','end'))
            entry.delete(0,'end');entry.insert(0,'ZZ9ZZZZZZ')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Look up').invoke()
            app.events.put((app.generation,'sent','sh/sta ZZ9ZZZZZZ'))
            app.events.put((app.generation,'data','*** User record for ZZ9ZZZZZZ not found ***\r\nLZ3ZZ de NODE dxspider >'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertIn('no user record',app.station_note.get());self.assertIn('not found',app.station_text.get('1.0','end'))
        finally:app.quit_app()

    def test_secondary_station_response_is_isolated_and_handles_prompt(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def send(self,value):self.commands.append(value)
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            main=FakeConnection();app.connection=main;app.connection_target=('main.invalid',7300,'LZ3ZZ')
            app.spot_source=('main.invalid',7300,'LZ3ZZ');app.status.set('Connected')
            with patch('app.Connection',Secondary):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ')
                item=app.secondary_nodes[ident];conn=item['connection'];conn.emit('status','Connected');app.poll_secondary_nodes()
                app.add_spot(Spot('S','14074','W1AW','1200Z',''),source=item['source']);app.render_spots();app.spots.selection_set(app.spots.get_children()[0])
                app.open_station();widgets=list(descendants(app.station_window))
                selector=next(w for w in widgets if isinstance(w,ttk.Combobox));self.assertIn('other.invalid:7301',selector.get())
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Look up').invoke()
                self.assertEqual(conn.commands,['sh/sta W1AW']);self.assertEqual(main.commands,[])
                app.events.put((app.generation,'sent','sh/sta W1AW'));app.events.put((app.generation,'data','User: W1AW\r\nName: Wrong node\r\nLZ3ZZ de NODE CCC >\r\n'))
                app.after_cancel(app.poll_after);app.poll();self.assertEqual(app.station_capture.state,'queued')
                conn.emit('sent','sh/sta W1AW');conn.emit('data','User: W1AW\r\nName: Correct node\r\nLZ3ZZ de NODE dxspider >');app.poll_secondary_nodes()
                self.assertEqual(app.station_capture.state,'complete');self.assertIn('Correct node',app.station_text.get('1.0','end'))
                self.assertNotIn('Wrong node',app.station_text.get('1.0','end'))
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Look up').invoke()
                conn.emit('sent','sh/sta W1AW');conn.emit('data','User: W1AW\r\nLocation: 00 30 N 002 00 E\r\n')
                conn.emit('sent','sh/dx');conn.emit('data','LZ3ZZ de NODE dxspider >');app.poll_secondary_nodes()
                self.assertEqual(app.station_capture.state,'partial');self.assertIn('Incomplete',app.station_note.get())
        finally:app.quit_app()

    def test_secondary_station_is_interrupted_by_its_reconnect_not_main_disconnect(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def send(self,value):self.commands.append(value)
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            main=FakeConnection();app.connection=main;app.status.set('Connected')
            with patch('app.Connection',Secondary):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');item=app.secondary_nodes[ident];conn=item['connection']
                conn.emit('status','Connected');app.poll_secondary_nodes();app.open_station()
                widgets=list(descendants(app.station_window));next(w for w in widgets if isinstance(w,ttk.Combobox)).set('other.invalid:7301 · LZ3ZZ')
                next(w for w in widgets if type(w) is ttk.Entry).insert(0,'W1AW')
                button=next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Look up');button.invoke()
                app.disconnect();self.assertEqual(app.station_capture.state,'queued')
                app._start_secondary_attempt(ident);self.assertEqual(app.station_capture.state,'partial')
                with patch('app.messagebox.showerror') as error:button.invoke();error.assert_called_once()
                self.assertEqual(item['connection'].commands,[])
        finally:app.quit_app()

    def test_station_estimate_requires_complete_location_response(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.status.set('Connected');app.local_grid.set('JJ00');app.open_station()
            widgets=list(descendants(app.station_window));entry=next(w for w in widgets if type(w) is ttk.Entry);entry.insert(0,'W1AW')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Look up').invoke()
            app.events.put((app.generation,'sent','sh/sta W1AW'))
            app.events.put((app.generation,'data','User: W1AW\r\nLocation: 00 30 N 002 00 E\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertNotIn('Estimate from',app.station_text.get('1.0','end'))
            app.events.put((app.generation,'data','LZ3ZZ de NODE CCC >'))
            app.after_cancel(app.poll_after);app.poll()
            text=app.station_text.get('1.0','end');self.assertIn('090° true',text);self.assertIn('111 km',text)
        finally:app.quit_app()

    def test_other_main_command_interrupts_station_estimate(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.connection=FakeConnection();app.status.set('Connected');app.local_grid.set('JJ00');app.open_station()
            widgets=list(descendants(app.station_window));next(w for w in widgets if type(w) is ttk.Entry).insert(0,'W1AW')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Look up').invoke()
            app.events.put((app.generation,'sent','sh/sta W1AW'))
            app.events.put((app.generation,'data','User: W1AW\r\nLocation: 00 30 N 002 00 E\r\n'))
            app.events.put((app.generation,'sent','sh/wx'));app.events.put((app.generation,'data','LZ3ZZ de NODE CCC >'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.station_capture.state,'partial');self.assertIn('Incomplete',app.station_note.get())
            self.assertNotIn('Estimate from',app.station_text.get('1.0','end'))
        finally:app.quit_app()

    def test_changed_connection_cannot_submit_and_disconnect_marks_partial(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.status.set('Connected');app.open_station()
            widgets=list(descendants(app.station_window));entry=next(w for w in widgets if type(w) is ttk.Entry);entry.insert(0,'W1AW')
            button=next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Look up')
            button.invoke();app.disconnect();self.assertEqual(app.station_capture.state,'partial')
            with patch('app.messagebox.showerror') as error:button.invoke();error.assert_called_once()
            self.assertEqual(conn.commands,['sh/sta W1AW'])
        finally:app.quit_app()


if __name__=='__main__':unittest.main()
