import unittest
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch
from filter_commands import filter_command, spider_categories, spider_expression, SPIDER_PICKER_BANDS
from app import App
from tkinter import ttk


class FakeConnection:
    def __init__(self): self.commands=[]
    def send(self, command): self.commands.append(command)
    def send_tracked(self, command):
        from protocol import TrackedCommand
        command=TrackedCommand(command);self.send(command);return command
    def close(self): pass


class FilterTests(unittest.TestCase):
    def test_state_lookup_shows_latest_activity(self):
        app=App();app._save_settings=lambda:True
        try:
            app.connection=FakeConnection();app.status.set('Connected');app.dialect='DXSpider'
            app.notebook.select(app.console_page);app.update()
            app.append('\n'.join('Earlier activity '+str(i) for i in range(100)))
            app.transcript.yview_moveto(0);app.update();self.assertLess(app.transcript.yview()[1],.99)
            app.notebook.select(3);app.update()
            with patch('app.ask_node_state_call',return_value='W1AW'):app.lookup_main_state()
            app.update();self.assertGreaterEqual(app.transcript.yview()[1],.999)
            app.append('\nNode response\n');app.update()
            self.assertGreaterEqual(app.transcript.yview()[1],.999)
        finally:app.quit_app()

    def test_main_state_lookup_checks_input_and_connection(self):
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            for dialect in ('CC Cluster','DXSpider'):
                app.connection=FakeConnection();app.status.set('Connected');app.dialect=dialect
                with patch('app.ask_node_state_call',return_value=' w1aw '):app.lookup_main_state()
                self.assertEqual(app.connection.commands,['sh/usdb W1AW'])
                self.assertEqual(app.notebook.select(),str(app.console_page))
            with patch('app.ask_node_state_call',return_value='W1AW\nsh/dx'),patch('app.messagebox.showerror') as error:
                app.lookup_main_state();error.assert_called_once()
            self.assertEqual(app.connection.commands,['sh/usdb W1AW'])
            with patch('app.ask_node_state_call',return_value='ß'),patch('app.messagebox.showerror') as error:
                app.lookup_main_state();error.assert_called_once()
            self.assertEqual(app.connection.commands,['sh/usdb W1AW'])
            def changed(*args,**kwargs):app.generation+=1;return 'W1AW'
            with patch('app.ask_node_state_call',side_effect=changed),patch('app.messagebox.showerror') as error:
                app.lookup_main_state();error.assert_called_once()
            self.assertEqual(app.connection.commands,['sh/usdb W1AW'])
        finally:app.quit_app()

    def test_state_picker_includes_canadian_provinces_and_keeps_other_values(self):
        from us_states import US_STATES, CANADIAN_PROVINCES
        self.assertEqual(len(CANADIAN_PROVINCES),13)
        self.assertEqual(len({code for code,_ in US_STATES+CANADIAN_PROVINCES}),64)
        app=App();app.withdraw();app._save_settings=lambda:True
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            app.cc_filter_kind.set('DX state');app.keyword_text.set('ZZ,TX')
            app.cc_band_picker.invoke();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            next(w for w in walk(dialog) if isinstance(w,ttk.Checkbutton) and w.cget('text')=='Ontario (ON)').invoke()
            next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
            self.assertEqual(app.keyword_text.get(),'ZZ,TX,ON');self.assertIsNone(app.connection)
        finally:app.quit_app()

    def test_band_choosers_do_not_overwrite_newer_form_values(self):
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        for dialect in ('CC Cluster','DXSpider'):
            with self.subTest(dialect=dialect):
                app=App();app.withdraw();app._save_settings=lambda:True
                try:
                    app.connection=FakeConnection();app.status.set('Connected');app.dialect=dialect
                    if dialect=='CC Cluster':
                        app.cc_filter_kind.set('Band / frequency segment');value=app.keyword_text
                        value.set('40-CW');app.cc_band_picker.invoke();new_value='20-SSB'
                    else:
                        app.spider_filter_kind.set('Band or range');value=app.spider_expression
                        value.set('20m,14000/14350')
                        next(w for w in walk(app.spider_filter) if isinstance(w,ttk.Button) and w.cget('text')=='Choose…').invoke();new_value='40m'
                    dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
                    value.set(new_value)
                    with patch('app.messagebox.showerror') as error:
                        next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
                    self.assertEqual(value.get(),new_value);self.assertEqual(app.connection.commands,[])
                    self.assertTrue(dialog.winfo_exists());error.assert_called_once()
                finally:app.quit_app()

    def test_main_country_and_spider_band_choosers_reject_changed_connection(self):
        import tkinter as tk
        from country_lookup import CountryIndex
        from test_country_lookup import DATA
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        for dialect,kind in (('CC Cluster','DX country'),('DXSpider','Band or range')):
            with self.subTest(dialect=dialect):
                app=App();app.withdraw();app._save_settings=lambda:True
                try:
                    app.connection=FakeConnection();app.status.set('Connected');app.dialect=dialect
                    if dialect=='CC Cluster':
                        app.country_index=CountryIndex.from_text(DATA);app.cc_filter_kind.set(kind);app.cc_band_picker.invoke();value=app.keyword_text
                    else:
                        app.spider_filter_kind.set(kind)
                        button=next(w for w in walk(app.spider_filter) if isinstance(w,ttk.Button) and w.cget('text')=='Choose…')
                        button.invoke();value=app.spider_expression
                    dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');widgets=list(walk(dialog))
                    if dialect=='CC Cluster':next(w for w in widgets if isinstance(w,tk.Listbox)).selection_set(0)
                    else:next(w for w in widgets if isinstance(w,ttk.Checkbutton) and w.cget('text')=='20m').invoke()
                    app.generation+=1
                    with patch('app.messagebox.showerror') as error:
                        next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
                    self.assertEqual(value.get(),'');self.assertEqual(app.connection.commands,[]);error.assert_called_once()
                finally:app.quit_app()

    def test_main_state_picker_rejects_changed_connection(self):
        app=App();app.withdraw();app._save_settings=lambda:True
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            app.connection=FakeConnection();app.status.set('Connected');app.dialect='CC Cluster'
            app.cc_filter_kind.set('DX state');app.cc_band_picker.invoke()
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            next(w for w in walk(dialog) if isinstance(w,ttk.Checkbutton) and w.cget('text')=='Texas (TX)').invoke()
            app.generation+=1
            with patch('app.messagebox.showerror') as error:
                next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
            self.assertEqual(app.keyword_text.get(),'');self.assertEqual(app.connection.commands,[])
            error.assert_called_once()
        finally:app.quit_app()

    def test_main_band_button_rejects_changed_connection(self):
        app=App();app.withdraw();app._save_settings=lambda:True
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            app.connection=FakeConnection();app.status.set('Connected');app.dialect='CC Cluster'
            app.cc_filter_kind.set('Band / frequency segment');app.cc_band_picker.invoke()
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');widgets=list(walk(dialog))
            next(w for w in widgets if isinstance(w,ttk.Treeview)).selection_set('20-CW')
            app.generation+=1
            with patch('app.messagebox.showerror') as error:
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
            self.assertEqual(app.keyword_text.get(),'');self.assertEqual(app.connection.commands,[])
            error.assert_called_once()
        finally:app.quit_app()

    def test_state_picker_follows_theme_changes(self):
        import tkinter as tk
        app=App();app.withdraw();app._save_settings=lambda:True
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            app.cc_filter_kind.set('DX state');app.cc_band_picker.invoke()
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            canvas=next(w for w in walk(dialog) if isinstance(w,tk.Canvas))
            app.switch_theme();self.assertEqual(canvas.cget('background'),app.theme_bg)
            app.switch_theme();self.assertEqual(canvas.cget('background'),app.theme_bg)
        finally:app.quit_app()

    def test_state_picker_scrolls_on_short_screen(self):
        import tkinter as tk
        app=App();app.withdraw();app._save_settings=lambda:True
        original_scaling=app.tk.call('tk','scaling')
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            app.tk.call('tk','scaling',2.5);app.cc_filter_kind.set('DX state')
            with patch.object(app,'winfo_screenheight',return_value=600):app.cc_band_picker.invoke()
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            dialog.update_idletasks();widgets=list(walk(dialog))
            canvas=next(w for w in widgets if isinstance(w,tk.Canvas))
            self.assertLess(dialog.winfo_reqheight(),600)
            self.assertTrue(any(isinstance(w,ttk.Scrollbar) and str(w.cget('orient'))=='vertical' for w in widgets))
            checkbox=next(w for w in widgets if isinstance(w,ttk.Checkbutton) and w.cget('text')=='Idaho (ID)')
            checkbox.event_generate('<FocusIn>');dialog.update_idletasks()
            self.assertGreater(canvas.yview()[0],0)
            checkbox.invoke()
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
            self.assertEqual(app.keyword_text.get(),'ID')
        finally:
            app.tk.call('tk','scaling',original_scaling)
            app.quit_app()

    def test_state_picker_does_not_overwrite_changed_form(self):
        app=App();app.withdraw();app._save_settings=lambda:True
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            app.cc_filter_kind.set('DX state');app.keyword_text.set('TX')
            app.cc_band_picker.invoke()
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            app.keyword_text.set('ON')
            with patch('app.messagebox.showerror') as error:
                next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
            self.assertEqual(app.keyword_text.get(),'ON')
            self.assertTrue(dialog.winfo_exists())
            error.assert_called_once()
        finally:app.quit_app()

    def test_state_picker_fills_form_and_preserves_other_values(self):
        from us_states import US_STATES
        self.assertEqual(len(US_STATES),51);self.assertEqual(len({code for code,_ in US_STATES}),51)
        app=App();app.withdraw();app._save_settings=lambda:True
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            app.cc_filter_kind.set('DX state');app.keyword_text.set('ON,TX')
            self.assertEqual(str(app.cc_band_picker['state']),'normal');app.cc_band_picker.invoke()
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            widgets=list(walk(dialog));checks={w.cget('text'):w for w in widgets if isinstance(w,ttk.Checkbutton)}
            checks['Texas (TX)'].invoke();checks['California (CA)'].invoke()
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
            self.assertEqual(app.keyword_text.get(),'ON,CA')
            self.assertIsNone(app.connection)
        finally:app.quit_app()

    def test_country_picker_is_available_for_both_filter_dialects(self):
        app=App();app.withdraw();app._save_settings=lambda:True
        try:
            app.cc_filter_kind.set('DX country')
            with patch.object(app,'open_filter_country_picker') as picker:
                self.assertEqual(str(app.cc_band_picker['state']),'normal')
                app.cc_band_picker.invoke();self.assertEqual(picker.call_args.args[0],app.keyword_text)
            app.spider_filter_kind.set('DX country')
            def walk(widget):
                for child in widget.winfo_children():yield child;yield from walk(child)
            button=next(w for w in walk(app.spider_filter) if isinstance(w,ttk.Button) and w.cget('text')=='Choose…')
            with patch.object(app,'open_filter_country_picker') as picker:
                self.assertEqual(str(button['state']),'normal')
                button.invoke();self.assertEqual(picker.call_args.args[0],app.spider_expression)
        finally:app.quit_app()

    def test_cc_band_scoped_filters_target_only_the_requested_band(self):
        for category,name,value in (('Spotter country','DOC','K'),('Spotter state','DOS','NY'),('DX country','DXCTY','K'),('DX state','DXSTATE','NY')):
            with self.subTest(category=category):
                self.assertEqual(filter_command('CC Cluster','pass',value,category=category,band='20 m'),f'set/filter/20 {name}/pass {value}')
                self.assertEqual(filter_command('CC Cluster','off',category=category,band='20 m'),f'set/filter/20 {name}/off')
        self.assertEqual(filter_command('CC Cluster','pass','K',category='DX country',band='2 m'),'set/filter/2 DXCTY/pass K')
        self.assertEqual(filter_command('CC Cluster','pass','K',category='DX country',band='4 m'),'set/filter/4 DXCTY/pass K')
        self.assertEqual(filter_command('CC Cluster','pass','K',category='DX country',band='70 cm'),'set/filter/70 DXCTY/pass K')
        for category,band in (('Keywords','20 m'),('DX country','20/ALL'),('Announce country','20 m')):
            with self.subTest(category=category,band=band),self.assertRaises(ValueError):
                filter_command('CC Cluster','pass','K',category=category,band=band)

    def test_main_filter_scope_resets_when_category_does_not_support_bands(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.status.set('Connected');app.dialect='CC Cluster'
            app.cc_filter_kind.set('DX country');app.cc_filter_band.set('20 m');app.keyword_text.set('K');app.change_filter('pass')
            self.assertEqual(conn.commands,['set/filter/20 DXCTY/pass K'])
            app.cc_filter_kind.set('Keywords')
            self.assertEqual(app.cc_filter_band.get(),'All bands')
            self.assertIn('disabled',app.cc_scope_box.state())
        finally:app.quit_app()

    def test_manual_filter_command_marks_completed_readbacks_stale(self):
        from filter_readback import FilterReadback, is_filter_change
        self.assertTrue(is_filter_change('SET/FILTER DXBM/PASS 20','CC Cluster'))
        self.assertTrue(is_filter_change('reject/spots 1 on 20m','DXSpider'))
        self.assertFalse(is_filter_change('sh/filter','DXSpider'))
        self.assertFalse(is_filter_change('set/name Alice','CC Cluster'))
        app=App();app.withdraw();app.dialect='CC Cluster'
        try:
            app.filter_readback.queued('CC Cluster','LZ3ZZ');app.filter_readback.state='complete'
            app.events.put((app.generation,'sent','set/filter DXBM/pass 20'))
            app.after_cancel(app.poll_after);app.poll();self.assertTrue(app.filter_readback.stale)
            app.dialect='DXSpider';app.filter_readback.queued('DXSpider','LZ3ZZ');app.filter_readback.state='complete'
            app.events.put((app.generation,'sent',r'sh/time\nc/sp all'))
            app.after_cancel(app.poll_after);app.poll();self.assertTrue(app.filter_readback.stale)
            class Node:
                def __init__(self,emit):self.emit=emit
                def start(self,*args):pass
                def close(self):pass
            with patch('app.Connection',Node):
                ident=app.start_secondary_node('other.invalid',7300,'LZ3ZZ-1');item=app.secondary_nodes[ident]
                item['dialect']='DXSpider';item['status']='Connected'
                capture=FilterReadback();capture.queued('DXSpider','LZ3ZZ-1');capture.state='complete';item['filter_capture']=capture
                item['connection'].emit('sent',r'sh/time\nc/sp all');app.poll_secondary_nodes()
                self.assertTrue(capture.stale)
        finally:app.quit_app()

    def test_disconnected_readback_is_identified_as_previous_session(self):
        from filter_readback import FilterReadback
        app=App();app.withdraw();node=FakeConnection();app.connection=node;app.status.set('Connected');app.dialect='DXSpider'
        try:
            app.filter_readback.queued('DXSpider','LZ3ZZ');app.filter_readback.state='complete';app.open_filter_readback()
            app.disconnect()
            self.assertIn('previous connection',app.filter_readback_note.get())
            app.filter_readback.queued('DXSpider','LZ3ZZ');self.assertFalse(app.filter_readback.session_ended)
            capture=FilterReadback();capture.queued('DXSpider','LZ3ZZ-1');capture.state='complete'
            item={'source':('other.invalid',7300,'LZ3ZZ-1'),'filter_capture':capture,'filter_view':None}
            app.open_secondary_filter_readback(item);app.end_secondary_filter_session(item)
            self.assertIn('previous connection',item['filter_view']['status'].get())
            capture.queued('DXSpider','LZ3ZZ-1');self.assertFalse(capture.session_ended)
        finally:app.quit_app()

    def test_filter_change_marks_completed_readback_stale_only_after_queueing(self):
        app=App();app.withdraw();node=FakeConnection();app.connection=node;app.status.set('Connected');app.dialect='DXSpider'
        try:
            app.filter_readback.queued('DXSpider','LZ3ZZ');app.filter_readback.state='complete'
            app.open_filter_readback();app.spider_expression.set('20m');app.change_filter('accept')
            self.assertTrue(app.filter_readback.stale)
            self.assertIn('refresh to check the node',app.filter_readback_note.get())
            app.filter_readback.queued('DXSpider','LZ3ZZ');self.assertFalse(app.filter_readback.stale)
            app.filter_readback.state='complete';app.spider_expression.set('')
            with patch('app.messagebox.showerror'):app.change_filter('accept')
            self.assertFalse(app.filter_readback.stale)
        finally:app.connection=None;app.quit_app()

    def test_additional_node_filter_change_marks_or_interrupts_readback(self):
        import tkinter as tk
        from filter_readback import FilterReadback
        app=App();app.withdraw()
        class Node:
            def __init__(self,emit):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def close(self):pass
            def send(self,command):self.commands.append(command)
        try:
            with patch('app.Connection',Node):
                ident=app.start_secondary_node('node.invalid',7300,'LZ3ZZ');item=app.secondary_nodes[ident]
                item['dialect']='DXSpider';item['status']='Connected'
                capture=FilterReadback();capture.queued('DXSpider','LZ3ZZ');capture.state='complete';item['filter_capture']=capture
                app.open_secondary_filter_readback(item);app.open_secondary_filters(ident)
                dialog=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel) and w.title()=='Additional node filters')
                def walk(w):
                    for child in w.winfo_children():yield child;yield from walk(child)
                widgets=list(walk(dialog));next(w for w in widgets if type(w) is ttk.Entry).insert(0,'20m')
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Accept matching').invoke()
                self.assertTrue(capture.stale);self.assertIn('refresh to check the node',item['filter_view']['status'].get())
                capture.queued('DXSpider','LZ3ZZ');capture.sent()
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Accept matching').invoke()
                self.assertEqual(capture.state,'partial');self.assertTrue(item['filter_unresolved'])
        finally:app.quit_app()

    def test_rejected_filter_change_replaces_previous_requested_status(self):
        class Node(FakeConnection):
            reject=False
            def send(self,command):
                if self.reject:raise OSError('DXSpider command limit reached. Wait before trying again.')
                super().send(command)
        app=App();app.withdraw();node=Node();app.connection=node;app.status.set('Connected');app.dialect='DXSpider'
        try:
            app.spider_expression.set('20m');app.change_filter('accept')
            self.assertIn('Change requested',app.filter_note.get())
            node.reject=True
            with patch('app.messagebox.showerror') as error:app.change_filter('accept')
            error.assert_called_once()
            self.assertIn('Change not sent',app.filter_note.get())
            self.assertEqual(len(node.commands),1)
            self.assertEqual(app.spider_expression.get(),'20m')
        finally:app.connection=None;app.quit_app()

    def test_rejected_filter_clear_and_reset_are_reported_as_not_sent(self):
        class Node(FakeConnection):
            def send(self,command):raise OSError('Command limit reached')
        app=App();app.withdraw();app.connection=Node();app.status.set('Connected')
        try:
            with patch('app.messagebox.askyesno',return_value=True),patch('app.messagebox.showerror'):
                app.dialect='DXSpider';app.filter_note.set('Change requested');app.clear_spider_stream()
                self.assertIn('Clear not sent',app.filter_note.get())
                app.dialect='CC Cluster';app.filter_note.set('Change requested');app.reset_cc_filters()
                self.assertIn('Reset not sent',app.filter_note.get())
        finally:app.connection=None;app.quit_app()

    def test_cc_time_filter_readback_sends_only_a_show_command(self):
        app=App();app.withdraw();fake=FakeConnection();app.connection=fake;app.status.set('Connected');app.dialect='CC Cluster'
        try:
            app.show_cc_bandtime()
            self.assertEqual(fake.commands,['sh/filter bandtime'])
            app.events.put((app.generation,'sent',fake.commands[-1]))
            app.events.put((app.generation,'data','BandTime Filter set OFF\r\nLZ3ZZ de NODE CCC >'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.filter_readback.state,'complete')
            self.assertIn('BandTime Filter set OFF',app.filter_readback_text.get('1.0','end'))
            self.assertIn('BandTime Filter set OFF',app.transcript.get('1.0','end'))
        finally:app.connection=None;app.quit_app()

    def test_bandtime_readback_marks_earlier_filter_response_ambiguous(self):
        app=App();app.withdraw();fake=FakeConnection();app.connection=fake;app.status.set('Connected');app.dialect='CC Cluster'
        try:
            app.show_cc_bandtime()
            app.events.put((app.generation,'sent','sh/filter'))
            app.events.put((app.generation,'sent',fake.commands[-1]))
            app.events.put((app.generation,'data','BandTime Filter set OFF\r\nLZ3ZZ de NODE CCC >'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.filter_readback.state,'partial')
            self.assertTrue(app.filter_readback.ambiguous)
        finally:app.connection=None;app.quit_app()

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider filter parser')
    def test_each_picker_band_matches_the_pinned_node(self):
        frequencies=(1900,3700,5350,7100,10125,14100,18100,21100,24920,28500,51000,70000,145000,222000,435000)
        self.assertEqual(len(SPIDER_PICKER_BANDS),len(frequencies))
        rows=[]
        for frequency in frequencies:
            row=['']*17;row[0]=frequency;rows.append(row)
        for index,band in enumerate(SPIDER_PICKER_BANDS):
            with self.subTest(band=band):
                result=subprocess.run(['perl','tools/check-dxspider-filter.pl'],
                    input=json.dumps({'expression':spider_expression('Band or range',band),'rows':rows}),
                    capture_output=True,text=True,cwd=Path(__file__).resolve().parent,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout)['matches'],[int(i==index) for i in range(len(rows))])

    def test_dxspider_band_picker_preserves_ranges_and_does_not_send(self):
        import tkinter as tk
        app=App();app.withdraw();fake=FakeConnection();app.connection=fake;app.status.set('Connected');app.dialect='DXSpider'
        try:
            app.spider_expression.set('20m,14000/14350')
            app.open_spider_band_picker()
            dialog=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel))
            def walk(widget):
                for child in widget.winfo_children():yield child;yield from walk(child)
            widgets=list(walk(dialog))
            next(w for w in widgets if isinstance(w,ttk.Checkbutton) and w.cget('text')=='40m').invoke()
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
            self.assertEqual(app.spider_expression.get(),'40m,20m,14000/14350')
            self.assertEqual(fake.commands,[])
            app.change_filter('accept')
            self.assertEqual(fake.commands,['accept/spots 1 on 40m,20m,14000/14350'])
        finally:app.connection=None;app.quit_app()

    def test_dxspider_spotter_marker_filter_is_queued(self):
        app=App();app.withdraw();fake=FakeConnection();app.connection=fake;app.status.set('Connected');app.dialect='DXSpider'
        try:
            app.spider_filter_kind.set('Spotter callsign text');app.spider_expression.set('-#')
            app.change_filter('reject')
            self.assertEqual(fake.commands,[r'reject/spots 1 by {\-\#}'])
        finally:app.connection=None;app.quit_app()

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider filter command')
    def test_marker_filter_through_pinned_command_handler(self):
        rows=[]
        for call in ('W1AW-#','W1AW','W2AW-#'):
            row=['']*17;row[4]=call;rows.append(row)
        payload={'expression':spider_expression('Spotter callsign text','-#'),'rows':rows,'command':True}
        result=subprocess.run(['perl','tools/check-dxspider-filter.pl'],input=json.dumps(payload),
                              capture_output=True,text=True,cwd=Path(__file__).resolve().parent,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['matches'],[1,0,1])

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider filter parser')
    def test_simple_filters_match_with_pinned_dxspider_parser(self):
        def row(index,value):
            record=['']*17;record[index]=value;return record
        cases=[('DX callsign text','K1*',[row(1,'K1ABC'),row(1,'K2ABC'),row(1,'VK1ABC')],[1,0,0]),
               ('Spotter callsign text','K1',[row(4,'K1ABC'),row(4,'VK1ABC'),row(4,'W2ABC')],[1,1,0]),
               ('Spotter callsign text','-#',[row(4,'W1AW-#'),row(4,'W1AW'),row(4,'W1AW-1')],[1,0,0]),
               ('Spotter callsign text','W1AW-#*',[row(4,'W1AW-#'),row(4,'W1AW'),row(4,'XW1AW-#')],[1,0,0]),
               ('Spot comment text','IOTA*',[row(3,'IOTA EU-001'),row(3,'CQ IOTA'),row(3,'CQ')],[1,0,0]),
               ('Spot comment text','CQ DX',[row(3,'CQ DX'),row(3,'CQ  DX'),row(3,'X CQ DX TEST')],[1,0,1]),
               ('Spot comment text','14.2',[row(3,'14.2 kHz'),row(3,'14X2 kHz'),row(3,'x14.2')],[1,0,1]),
               ('Spot comment text','A+B',[row(3,'A+B'),row(3,'AAAB'),row(3,'AB')],[1,0,0]),
               ('Spot comment text','CQ OR BY K',[row(3,'CQ OR BY K'),row(3,'CQ'),row(3,'BY K')],[1,0,0]),
               ('Spot comment text','POTA/US*',[row(3,'POTA/US-123'),row(3,'CQ POTA/US'),row(3,'POTA/CA')],[1,0,0]),
               ('Spot comment text','CQ DX,POTA/US*',[row(3,'CQ DX'),row(3,'POTA/US-123'),row(3,'CQ  DX')],[1,1,0]),
               ('Minimum skimmers','3',[row(16,2),row(16,3),row(16,9)],[0,1,1]),
               ('Minimum RBN strength','15',[row(15,14),row(15,15),row(15,25)],[0,1,1]),
               ('DX country','291,227',[row(5,291),row(5,227),row(5,212)],[1,1,0]),
               ('DX country','K,VE',[row(5,226),row(5,197),row(5,291)],[1,1,0]),
               ('DX country','291',[row(5,226),row(5,291)],[0,1]),
               ('Spotter country','K',[row(6,226),row(6,291)],[1,0]),
               ('Spotter country','212',[row(6,291),row(6,212)],[0,1]),
               ('DX CQ zone','5,14',[row(9,5),row(9,14),row(9,20)],[1,1,0]),
               ('Spotter ITU zone','28',[row(10,8),row(10,28)],[0,1]),
               ('DX state','TX,CA',[row(12,'TX'),row(12,'CA'),row(12,'FL')],[1,1,0]),
               ('Spotter state','MA',[row(13,'MA'),row(13,'TX')],[1,0]),
               ('Band or range','14000/14350',[row(0,13999),row(0,14000),row(0,14350),row(0,14351)],[0,1,1,0]),
               ('Band or range','20m, 7000-7300',[row(0,7074),row(0,14074),row(0,21074)],[1,1,0]),
               ('Band or range','20m',[row(0,13999),row(0,14000),row(0,14350),row(0,14351)],[0,1,1,0]),
               ('Band or range','20m/cw',[row(0,13999),row(0,14000),row(0,14100),row(0,14101)],[0,1,1,0]),
               ('Band or range','70cm',[row(0,419999),row(0,420000),row(0,450000),row(0,450001)],[0,1,1,0])]
        for category,value,rows,expected in cases:
            with self.subTest(category=category):
                data={'expression':spider_expression(category,value),'rows':rows}
                result=subprocess.run(['perl','tools/check-dxspider-filter.pl'],input=json.dumps(data),capture_output=True,text=True,
                                      cwd=Path(__file__).resolve().parent,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout)['matches'],expected)

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider filter parser')
    def test_pinned_dxspider_rejects_decimal_band_range(self):
        result=subprocess.run(['perl','tools/check-dxspider-filter.pl'],
                              input=json.dumps({'expression':'on 14074.5/14075','rows':[]}),
                              capture_output=True,text=True,cwd=Path(__file__).resolve().parent,timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Filter parser rejected expression',result.stderr)

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider filter parser')
    def test_reversed_frequency_range_would_match_no_spots(self):
        rows=[]
        for frequency in (13999,14000,14074,14350,14351):
            row=['']*17;row[0]=frequency;rows.append(row)
        result=subprocess.run(['perl','tools/check-dxspider-filter.pl'],
                              input=json.dumps({'expression':'on 14350/14000','rows':rows}),
                              capture_output=True,text=True,cwd=Path(__file__).resolve().parent,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['matches'],[0,0,0,0,0])
        for value in ('14350/14000','20m,14350-14000'):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'lower frequency first'):
                spider_expression('Band or range',value)
        self.assertEqual(spider_expression('Band or range','14074/14074'),'on 14074/14074')

    @unittest.skipUnless(shutil.which('perl'), 'Perl is required to exercise the pinned DXSpider filter parser')
    def test_pinned_dxspider_state_filter_passes_when_usdb_unavailable(self):
        row=['']*17;row[12]='FL'
        data={'expression':spider_expression('DX state','TX'),'rows':[row],'usdb':0}
        result=subprocess.run(['perl','tools/check-dxspider-filter.pl'],input=json.dumps(data),capture_output=True,text=True,
                              cwd=Path(__file__).resolve().parent,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['matches'],[1])

    def test_commands_match_observed_cluster_dialects(self):
        self.assertEqual(filter_command('CC Cluster','pass','ALPHA BRAVO'), 'set/filter keyword/pass ALPHA BRAVO')
        self.assertEqual(filter_command('CC Cluster','reject','TESTWORD'), 'set/filter keyword/rej TESTWORD')
        self.assertEqual(filter_command('CC Cluster','off'), 'set/filter keyword/off')
        self.assertEqual(filter_command('CC Cluster','reset_all'), 'set/nofilter')
        self.assertEqual(filter_command('CC Cluster','pass','k, ve', category='Spotter country'), 'set/filter DOC/pass K,VE')
        self.assertEqual(filter_command('CC Cluster','reject','TX,MA', category='DX state'), 'set/filter DXSTATE/reject TX,MA')
        self.assertEqual(filter_command('CC Cluster','off', category='DX country'), 'set/filter DXCTY/off')
        for category,token,example in [('Announce country','AOC','K,VE'),('Announce state','AOS','TX,CA'),('Weather country','WOC','K,VE'),('Weather state','WOS','TX,CA')]:
            with self.subTest(category=category):
                self.assertEqual(filter_command('CC Cluster','pass',example,category=category),f'set/filter {token}/pass {example}')
                self.assertEqual(filter_command('CC Cluster','reject',example,category=category),f'set/filter {token}/reject {example}')
                self.assertEqual(filter_command('CC Cluster','off',category=category),f'set/filter {token}/off')
        self.assertEqual(filter_command('DXSpider','reject','on 20m',9), 'reject/spots 9 on 20m')
        self.assertEqual(filter_command('DXSpider','clear','',9), 'clear/spots 9')

    def test_cc_band_frequency_segment_commands(self):
        kind='Band / frequency segment'
        self.assertEqual(filter_command('CC Cluster','pass','6',category=kind),'set/filter DXBM/pass 6')
        self.assertEqual(filter_command('CC Cluster','pass','4',category=kind),'set/filter DXBM/pass 4')
        self.assertEqual(filter_command('CC Cluster','reject','80-cw, 40-cw',category=kind),'set/filter DXBM/reject 80-CW,40-CW')
        self.assertEqual(filter_command('CC Cluster','pass','70-CW,70-SSB,70-FM',category=kind),'set/filter DXBM/pass 70-CW,70-SSB,70-FM')
        self.assertEqual(filter_command('CC Cluster','off',category=kind),'set/filter DXBM/off')
        for value in ('40-FT8','60-CW','6,,20','40-CW\nbye','0','MW','70'):
            with self.subTest(value=value),self.assertRaises(ValueError):filter_command('CC Cluster','pass',value,category=kind)

    def test_cc_picker_prepares_multiple_values_without_sending(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.status.set('Connected');app.dialect='CC Cluster';app.update_filter_view()
            app.cc_filter_kind.set('Band / frequency segment');app.keyword_text.set('40-CW');app.open_cc_band_picker()
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            def descendants(w):
                for child in w.winfo_children():
                    yield child;yield from descendants(child)
            widgets=list(descendants(dialog));tree=next(w for w in widgets if isinstance(w,ttk.Treeview))
            self.assertEqual(tree.selection(),('40-CW',));self.assertEqual(tree.item('40-CW','values')[1],'7000–7040')
            self.assertEqual(tree.item('70-CW','values'),('70-CW  ·  70 cm','Node-defined segment'))
            self.assertFalse(tree.exists('70'))
            tree.selection_add('6');next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
            self.assertEqual(app.keyword_text.get(),'40-CW,6');self.assertEqual(conn.commands,[])
            app.change_filter('reject');self.assertEqual(conn.commands,['set/filter DXBM/reject 40-CW,6'])
        finally:app.quit_app()

    def test_filter_drafts_are_cleared_when_new_session_starts(self):
        class Connection:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.call.set('LZ3ZZ');app.cc_filter_kind.set('Band / frequency segment');app.keyword_text.set('20-CW')
            app.spider_stream.set('RBN spots');app.spider_filter_kind.set('Minimum skimmers');app.spider_expression.set('3');app.spider_line.set('7')
            with patch('app.Connection',Connection):app.connect()
            self.assertEqual((app.cc_filter_kind.get(),app.keyword_text.get()),('Keywords',''))
            self.assertEqual((app.spider_stream.get(),app.spider_filter_kind.get(),app.spider_expression.get(),app.spider_line.get()),('Spots','Band or range','','1'))
            self.assertIn('Filter form cleared',app.transcript.get('1.0','end'))
        finally:app.quit_app()

    def test_main_cc_picker_refuses_selection_after_connection_changes(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            app.connection=FakeConnection();app.status.set('Connected');app.dialect='CC Cluster'
            app.cc_filter_kind.set('Band / frequency segment');app.open_cc_band_picker()
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');widgets=list(walk(dialog))
            tree=next(w for w in widgets if isinstance(w,ttk.Treeview));tree.selection_set('20-CW')
            app.generation+=1
            with patch('app.messagebox.showerror') as error:
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke();error.assert_called_once()
            self.assertEqual(app.keyword_text.get(),'')
        finally:app.quit_app()

    def test_cc_reset_requires_confirmation_and_same_connection(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.status.set('Connected');app.dialect='CC Cluster'
            with patch('app.messagebox.askyesno',return_value=False):app.reset_cc_filters()
            self.assertEqual(conn.commands,[])
            def changed(*args,**kwargs):app.generation+=1;return True
            with patch('app.messagebox.askyesno',side_effect=changed),patch('app.messagebox.showerror') as error:
                app.reset_cc_filters();error.assert_called_once()
            self.assertEqual(conn.commands,[])
            with patch('app.messagebox.askyesno',return_value=True):app.reset_cc_filters()
            self.assertEqual(conn.commands,['set/nofilter'])
        finally:app.quit_app()

    def test_dxspider_traffic_types_use_separate_rule_commands(self):
        for name, token in [('Spots','spots'),('RBN spots','rbn'),('Announcements','announce'),('WWV','wwv'),('WCY','wcy')]:
            with self.subTest(name=name):
                self.assertEqual(filter_command('DXSpider','accept','by G',2,stream=name), f'accept/{token} 2 by G')
                self.assertEqual(filter_command('DXSpider','reject','by G',2,stream=name), f'reject/{token} 2 by G')
                self.assertEqual(filter_command('DXSpider','clear','',2,stream=name), f'clear/{token} 2')
        with self.assertRaises(ValueError): filter_command('DXSpider','clear','',2,stream='bad/name')

    def test_rejects_invalid_rules_and_command_injection(self):
        for args in [('CC Cluster','pass','x\nset/dx'), ('DXSpider','reject','on 20m\rbye',1), ('DXSpider','accept','on 20m',10), ('DXSpider','accept','',1), ('Other','off','')]:
            with self.subTest(args=args), self.assertRaises(ValueError): filter_command(*args)
        for category, text in [('Spotter state', 'TX,INVALID'), ('DX country', 'K,,VE'), ('DX state','TX\nBYE'),('Weather state','TEXAS'),('Announce country','K,,VE')]:
            with self.subTest(category=category), self.assertRaises(ValueError): filter_command('CC Cluster','pass',text,category=category)
        with self.assertRaises(ValueError): filter_command('CC Cluster','off',category='Unknown')

    def test_reversed_range_is_not_sent_from_filter_form(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='DXSpider'
            window.spider_filter_kind.set('Band or range');window.spider_expression.set('14350/14000')
            with patch('app.messagebox.showerror') as error:
                window.change_filter('accept')
            self.assertEqual(fake.commands,[])
            self.assertIn('lower frequency first',error.call_args.args[1])
        finally:window.quit_app()

    def test_stream_specific_origin_and_announcement_fields(self):
        self.assertEqual(spider_expression('Origin callsign text','GB7'), 'origin GB7')
        self.assertEqual(spider_expression('Origin country','K,VE'), 'origin_dxcc K,VE')
        self.assertEqual(spider_expression('Origin CQ zone','5,14'), 'origin_zone 5,14')
        self.assertEqual(spider_expression('Origin ITU zone','8,28'), 'origin_itu 8,28')
        self.assertEqual(spider_expression('Announcement text','IOTA'), 'info IOTA')
        self.assertEqual(spider_expression('Announcement group text','DX'), 'group DX')
        self.assertNotIn('Spotter state', spider_categories('Announcements'))
        self.assertNotIn('Origin country', spider_categories('Spots'))
        self.assertIn('Origin callsign text', spider_categories('Spots'))
        self.assertIn('Origin callsign text', spider_categories('RBN spots'))
        self.assertNotIn('Announcement text', spider_categories('WWV'))
        self.assertIn('Origin country', spider_categories('WWV'))
        self.assertIn('Announcement group text', spider_categories('Announcements'))

    def test_dxspider_simple_filter_builder(self):
        self.assertEqual(spider_expression('Band or range','20m'), 'on 20m')
        self.assertEqual(spider_expression('Band or range','20m, 14000-14350'), 'on 20m,14000-14350')
        for value in ('14074.5/14075', '20m,,40m', '/14000', '20m/ft8/extra'):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'whole-kHz'):
                spider_expression('Band or range',value)
        self.assertEqual(spider_expression('DX country','K, VE'), 'call_dxcc K,VE')
        self.assertEqual(spider_expression('Spotter state','TX, CA'), 'by_state TX,CA')
        self.assertEqual(spider_expression('DX CQ zone','5,14'), 'call_zone 5,14')
        self.assertEqual(spider_expression('Spotter CQ zone','K, VE'), 'by_zone K,VE')
        self.assertEqual(spider_expression('DX ITU zone','8, 90, G'), 'call_itu 8,90,G')
        self.assertEqual(spider_expression('Spotter ITU zone','28'), 'by_itu 28')
        self.assertEqual(spider_expression('DX callsign text','K,VE'), 'call K,VE')
        self.assertEqual(spider_expression('Spotter callsign text','G'), 'by G')
        self.assertEqual(spider_expression('Spot comment text','IOTA,QSL'), 'info IOTA,QSL')
        self.assertEqual(spider_expression('Spot comment text','CQ DX'), r'info {CQ\ DX}')
        self.assertEqual(spider_expression('Spot comment text','14.2'), r'info {14\.2}')
        self.assertEqual(spider_expression('Spot comment text','POTA/US*'), 'info {^POTA/US}')
        self.assertEqual(spider_expression('Announcement text','CQ DX'), r'info {CQ\ DX}')
        self.assertEqual(spider_expression('Announcement group text','WW 1'), r'group {WW\ 1}')
        self.assertEqual(spider_expression('Advanced expression','on 20m and by G'), 'on 20m and by G')
        for kind,text in [('DX CQ zone','41'),('DX ITU zone','91'),('DX ITU zone','K,,VE'),('DX state','INVALID'),('DX state','ONT'),('Spotter state','USA'),('Spot comment text','{unsafe}'),('Spot comment text','A$B'),('Band or range','20m\nbye'),('DX country','K,,VE')]:
            with self.subTest(kind=kind),self.assertRaises(ValueError): spider_expression(kind,text)

    def test_main_state_filter_discloses_usdb_dependency(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.spider_filter_kind.set('DX state')
            self.assertIn('USDB',app.spider_value_hint.get())
        finally:app.quit_app()

    def test_country_filter_hint_distinguishes_node_numbers_from_arrl(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.spider_filter_kind.set('DX country')
            self.assertIn('NODE COUNTRY NUMBERS',app.spider_value_hint.get())
            self.assertIn('NOT ARRL DXCC',app.spider_value_hint.get())
        finally:app.quit_app()

    def test_rbn_minimum_skimmers_builds_numeric_threshold(self):
        self.assertIn('Minimum skimmers',spider_categories('RBN spots'))
        for stream in ('Spots','Announcements','WWV','WCY'):
            self.assertNotIn('Minimum skimmers',spider_categories(stream))
        for value in ('1','3','9'):
            expression=spider_expression('Minimum skimmers',value)
            self.assertEqual(expression,'q '+value+'+')
            self.assertEqual(filter_command('DXSpider','accept',expression,line=2,stream='RBN spots'),'accept/rbn 2 q '+value+'+')
        for value in ('0','10','1+','-1','2,3','2\nBYE'):
            with self.subTest(value=value),self.assertRaises(ValueError):spider_expression('Minimum skimmers',value)

    def test_rbn_strength_builds_node_candidate_threshold(self):
        self.assertIn('Minimum RBN strength',spider_categories('RBN spots'))
        for stream in ('Spots','Announcements','WWV','WCY'):
            self.assertNotIn('Minimum RBN strength',spider_categories(stream))
        for value in ('0','15','99'):
            self.assertEqual(spider_expression('Minimum RBN strength',value),f'db {value}+')
            self.assertEqual(filter_command('DXSpider','accept',spider_expression('Minimum RBN strength',value),line=2,stream='RBN spots'),f'accept/rbn 2 db {value}+')
        for value in ('-1','100','01','1.5','1+','2\nBYE'):
            with self.subTest(value=value),self.assertRaises(ValueError):spider_expression('Minimum RBN strength',value)

    def test_rbn_minimum_skimmers_through_filter_controls(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.dialect='DXSpider';app.status.set('Connected');app.update_filter_view()
            app.spider_stream.set('RBN spots');app.spider_filter_kind.set('Minimum skimmers');app.spider_expression.set('3');app.spider_line.set('2')
            self.assertIn('MINIMUM 1–9',app.spider_value_hint.get());self.assertEqual(conn.commands,[])
            app.change_filter('accept');self.assertEqual(conn.commands,['accept/rbn 2 q 3+'])
            app.spider_filter_kind.set('Minimum RBN strength');app.spider_expression.set('15')
            self.assertIn('0–99 dB',app.spider_value_hint.get())
            app.change_filter('reject');self.assertEqual(conn.commands[-1],'reject/rbn 2 db 15+')
            app.spider_stream.set('WWV');app.spider_filter_kind.set('Minimum skimmers');app.spider_expression.set('3')
            with patch('app.messagebox.showerror') as error:app.change_filter('accept');error.assert_called_once()
            self.assertEqual(conn.commands,['accept/rbn 2 q 3+','reject/rbn 2 db 15+'])
        finally:app.quit_app()

    def test_dxspider_text_prefix_filters_use_trailing_star(self):
        for category,field,value,expected in [
            ('DX callsign text','call','K1*,VE','{^K1},VE'),
            ('Spotter callsign text','by','W1*','{^W1}'),
            ('Origin callsign text','origin','GB7*','{^GB7}'),
            ('Spot comment text','info','CQ*,IOTA','{^CQ},IOTA'),
            ('Announcement group text','group','DX*','{^DX}')]:
            with self.subTest(category=category):self.assertEqual(spider_expression(category,value),f'{field} {expected}')
        for value in ('*','*K1','K*1','K1**','K1*,','K1*\nBYE'):
            with self.subTest(value=value),self.assertRaises(ValueError):spider_expression('DX callsign text',value)
        with self.assertRaises(ValueError):spider_expression('DX country','K*')
        expression=spider_expression('DX callsign text','K1*,VE*')
        self.assertEqual(filter_command('DXSpider','accept',expression,2),'accept/spots 2 call {^K1},{^VE}')

    def test_desktop_uses_dialect_and_exposes_readback(self):
        app=App(); app.withdraw()
        try:
            fake=FakeConnection(); app.connection=fake; app.status.set('Connected'); app.dialect='CC Cluster'; app.update_filter_view()
            app.keyword_text.set('TESTWORD'); app.change_filter('reject'); app.show_filters()
            self.assertEqual(fake.commands,['set/filter keyword/rej TESTWORD','sh/filter'])
            self.assertIn('requested',app.filter_note.get().lower())
            app.cc_filter_kind.set('DX country'); app.keyword_text.set('F,OH'); app.change_filter('pass')
            self.assertEqual(fake.commands[-1], 'set/filter DXCTY/pass F,OH')
            app.cc_filter_kind.set('Weather state'); app.keyword_text.set('MA,CT'); app.change_filter('reject')
            self.assertEqual(fake.commands[-1], 'set/filter WOS/reject MA,CT')
            app.cc_filter_kind.set('Band / frequency segment'); app.keyword_text.set('6,40-CW'); app.change_filter('pass')
            self.assertEqual(fake.commands[-1], 'set/filter DXBM/pass 6,40-CW')
            self.assertIn('frequency ranges',app.cc_filter_hint.get())
            self.assertIn('other bands may still pass',app.cc_filter_hint.get())
            fake.commands.clear(); app.dialect='DXSpider'; app.update_filter_view()
            app.spider_line.set('9'); app.spider_expression.set('20m'); app.change_filter('reject'); app.change_filter('clear')
            self.assertEqual(fake.commands,['reject/spots 9 on 20m','clear/spots 9'])
            app.spider_filter_kind.set('DX country'); app.spider_expression.set('K,VE'); app.change_filter('accept')
            self.assertEqual(fake.commands[-1], 'accept/spots 9 call_dxcc K,VE')
            app.spider_filter_kind.set('Spotter ITU zone'); app.spider_expression.set('8,VE'); app.change_filter('reject')
            self.assertEqual(fake.commands[-1], 'reject/spots 9 by_itu 8,VE')
            app.spider_filter_kind.set('Spot comment text'); app.spider_expression.set('IOTA'); app.change_filter('accept')
            self.assertEqual(fake.commands[-1], 'accept/spots 9 info IOTA')
            self.assertIn('CONTAINS', app.spider_value_hint.get())
            app.spider_filter_kind.set('Origin callsign text'); app.spider_expression.set('GB7*'); app.change_filter('reject')
            self.assertEqual(fake.commands[-1], 'reject/spots 9 origin {^GB7}')
            app.spider_stream.set('RBN spots'); app.spider_filter_kind.set('Origin callsign text'); app.spider_expression.set('GB7'); app.change_filter('accept')
            self.assertEqual(fake.commands[-1], 'accept/rbn 9 origin GB7')
            app.spider_stream.set('WWV'); self.assertEqual(app.spider_filter_kind.get(),'Spotter callsign text')
            app.spider_expression.set('G'); app.change_filter('accept'); app.change_filter('clear')
            self.assertEqual(fake.commands[-2:],['accept/wwv 9 by G','clear/wwv 9'])
            app.spider_filter_kind.set('Origin country'); app.spider_expression.set('K'); app.change_filter('reject')
            self.assertEqual(fake.commands[-1], 'reject/wwv 9 origin_dxcc K')
            app.spider_stream.set('Announcements'); app.spider_filter_kind.set('Announcement text'); app.spider_expression.set('IOTA'); app.change_filter('accept')
            self.assertEqual(fake.commands[-1], 'accept/announce 9 info IOTA')
        finally: app.quit_app()

    def test_dxspider_clear_all_is_scoped_confirmed_and_session_bound(self):
        for stream,token in [('Spots','spots'),('RBN spots','rbn'),('Announcements','announce'),('WWV','wwv'),('WCY','wcy')]:
            with self.subTest(stream=stream):
                self.assertEqual(filter_command('DXSpider','clear_all',stream=stream),f'clear/{token} all')
        with self.assertRaises(ValueError):filter_command('DXSpider','clear_all',stream='bad/name')
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            fake=FakeConnection();app.connection=fake;app.status.set('Connected');app.dialect='DXSpider';app.update_filter_view()
            app.spider_stream.set('WWV')
            with patch('app.messagebox.askyesno',return_value=False):app.clear_spider_stream()
            self.assertEqual(fake.commands,[])
            with patch('app.messagebox.askyesno',return_value=True):app.clear_spider_stream()
            self.assertEqual(fake.commands,['clear/wwv all'])
            def change_connection(*args,**kwargs):app.generation+=1;return True
            with patch('app.messagebox.askyesno',side_effect=change_connection),patch('app.messagebox.showerror') as error:
                app.clear_spider_stream();error.assert_called_once()
            self.assertEqual(fake.commands,['clear/wwv all'])
        finally:app.quit_app()

    def test_dxspider_filter_buttons_visible_at_minimum_window_size(self):
        app=App();app._save_settings=lambda:None
        try:
            app.connection=FakeConnection();app.status.set('Connected');app.dialect='DXSpider';app.update_filter_view()
            app.geometry('780x560');app.notebook.select(3);app.update()
            buttons=[w for w in app.spider_filter.winfo_children() if isinstance(w,ttk.Frame)][-1].winfo_children()
            buttons += [w for w in app.spider_filter.winfo_children() if isinstance(w,ttk.Button)]
            self.assertTrue(any(button.cget('text')=='Find node country number…' for button in buttons))
            for button in buttons:
                with self.subTest(button=button.cget('text')):
                    self.assertTrue(button.winfo_ismapped())
                    self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),app.winfo_rootx()+app.winfo_width())
                    self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),app.command.winfo_rooty())
        finally:app.quit_app()

    def test_cc_filter_buttons_visible_at_minimum_window_size(self):
        app=App(); app._save_settings=lambda:None
        try:
            app.connection=FakeConnection(); app.status.set('Connected'); app.dialect='CC Cluster'
            app.update_filter_view(); app.geometry('780x560'); app.notebook.select(3); app.update()
            actions=app.cc_filter.winfo_children()[-1]
            for button in actions.winfo_children():
                self.assertTrue(button.winfo_ismapped())
                self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),app.command.winfo_rooty())
        finally: app.quit_app()

if __name__ == '__main__': unittest.main()
