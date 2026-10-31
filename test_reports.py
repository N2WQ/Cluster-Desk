import unittest
from unittest.mock import patch
from app import App
from reports import ReportCollector


class FakeConnection:
    def __init__(self): self.commands=[]
    def send(self, text): self.commands.append(text)
    def close(self): pass


class ReportTests(unittest.TestCase):
    def test_spot_summary_shows_last_received_wwv_and_clears(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            window.reports.append(('WWV','WWV de W0MU <1200>: SFI=89, A=4, K=1',('node.example',7300,'LZ3ZZ')))
            window.reports.append(('WCY','WCY de DK0WCY <1300>: K=2',('other.example',7300,'LZ3ZZ')))
            window.report_kind.set('WCY');window.render_reports()
            text=window.spot_wwv_summary.get()
            self.assertIn('Last received WWV',text);self.assertIn('SFI=89',text);self.assertIn('node.example',text)
            self.assertNotIn('DK0WCY',text)
            window.reports.append(('WWV','older history report',('other.example',7300,'LZ3ZZ')))
            window.render_reports()
            self.assertIn('older history report',window.spot_wwv_summary.get())
            window.clear_reports();self.assertEqual(window.spot_wwv_summary.get(),'WWV · No report received yet')
        finally:window.quit_app()

    def test_observed_history_rows_and_prompt_boundaries(self):
        collector=ReportCollector()
        self.assertIsNone(collector.feed('Date        Hour   SFI   A   K Forecast                               Logger'))
        line='30-Oct-2026    0    89   4   1 No Storms -> No Storms                <VE7CC>'
        self.assertEqual(collector.feed(line),('WWV',line))
        self.assertIsNone(collector.feed('LZ3ZZ de N2WQ-1 30-Oct-2026 0008Z   CCC >'))
        self.assertIsNone(collector.feed(line))
        self.assertIsNone(collector.feed('Date        Hour   SFI   A   K Exp.K   R SA    GMF   Aurora   Logger'))
        line='30-Oct-2026   00    89   6   1     0  20 qui   qui       no   <DK0WCY>'
        self.assertEqual(collector.feed(line),('WCY',line))
        self.assertIsNone(collector.feed('LZ3ZZ de N2WQ-73 30-Oct-2026 0008Z dxspider >'))
        self.assertIsNone(collector.feed('unrelated <text>'))

    def test_live_reports_are_identified_without_a_history_header(self):
        collector=ReportCollector()
        self.assertEqual(collector.feed('WWV de W0MU <0000>: SFI=89, A=4, K=1'),('WWV','WWV de W0MU <0000>: SFI=89, A=4, K=1'))
        self.assertEqual(collector.feed('WCY de DK0WCY <0000> : K=1 expK=0 A=6'),('WCY','WCY de DK0WCY <0000> : K=1 expK=0 A=6'))
        self.assertIsNone(collector.feed('Not WWV de W0MU: something'))

    def test_pc23_and_pc27_wwv_packets_preserve_values(self):
        collector=ReportCollector()
        line='PC23^14-Dec-2001^15^220^  4^ 1^R=212 SA:mo=>mo-hi GF:qu=>qu-un^KH2D^KH2D^H48^~'
        self.assertEqual(collector.feed(line),('WWV','WWV 14-Dec-2001 15Z · SFI=220, A=4, K=1 · R=212 SA:mo=>mo-hi GF:qu=>qu-un · KH2D · node KH2D'))
        line='PC27^30-Oct-2026^ 0^89^4^1^No storms^W0MU^NODE^ ^~'
        self.assertEqual(collector.feed(line),('WWV','WWV 30-Oct-2026 00Z · SFI=89, A=4, K=1 · No storms · W0MU · node NODE'))
        for invalid in ('PC23^bad^15^220^4^1^forecast^KH2D^NODE^H48^~',
                        'PC23^14-Dec-2001^25^220^4^1^forecast^KH2D^NODE^H48^~',
                        'PC23^14-Dec-2001^15^nan^4^1^forecast^KH2D^NODE^H48^~',
                        'To ALL de K1ABC: PC23^14-Dec-2001^15^220^4^1^forecast^KH2D^NODE^H48^~'):
            with self.subTest(invalid=invalid):self.assertIsNone(collector.feed(invalid))

    def test_pc73_wcy_packet_preserves_values_and_rejects_bad_frames(self):
        collector=ReportCollector()
        line='PC73^14-Dec-2001^15^220^  3^1^0^212^act^qui^no^DK0WCY-3^DB0SUE-7^H96^'
        self.assertEqual(collector.feed(line),('WCY','WCY 14-Dec-2001 15Z · SFI=220, A=3, K=1, expK=0, R=212 · SA=act, GMF=qui, Au=no · DK0WCY-3 · node DB0SUE-7'))
        for invalid in (line.replace('^15^','^24^'),line.replace('^220^','^bad^'),
                        line.replace('^act^','^^'),line[:-1], 'To ALL de K1ABC: '+line):
            with self.subTest(invalid=invalid):self.assertIsNone(collector.feed(invalid))

    def test_pc73_packet_from_secondary_preserves_source(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',Secondary):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');item=app.secondary_nodes[ident]
                item['connection'].emit('status','Connected')
                item['connection'].emit('data','PC73^14-Dec-2001^15^220^3^1^0^212^act^qui^no^DK0WCY-3^DB0SUE-7^H96^\r\n')
                app.poll_secondary_nodes()
                self.assertEqual(app.reports[-1][0],'WCY');self.assertIn('expK=0',app.reports[-1][1]);self.assertEqual(app.reports[-1][2],item['source'])
        finally:app.quit_app()

    def test_pc23_packet_reaches_propagation_view_and_raw_activity(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            line='PC23^14-Dec-2001^15^220^4^1^forecast^KH2D^NODE^H48^~'
            app.events.put((app.generation,'data',line+'\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.reports[-1][0],'WWV');self.assertIn('SFI=220',app.reports[-1][1])
            self.assertIn(line,app.transcript.get('1.0','end'))
        finally:app.quit_app()

    def test_translated_headers_and_bell_enabled_live_reports(self):
        collector=ReportCollector()
        row='30-Oct-2026    0    89   4   1 No Storms <VE7CC>'
        for header,kind in [
            ('   Date     Heure  SFI   A   K           Prévision                    Source','WWV'),
            ('   Date     Heure  SFI   A   K Kprévu  R  SA   GMF   Aurora  Source','WCY'),
            ('Fecha       Hora   SFI   A   K Previsión                              Logger','WWV'),
            ('Fecha       Hora   SFI   A   K Exp.K   R SA    GMF   Aurora   Logger','WCY'),
        ]:
            with self.subTest(header=header):
                self.assertIsNone(collector.feed(header));self.assertEqual(collector.feed(row),(kind,row))
        self.assertEqual(collector.feed('WWV de W0MU <0000>: SFI=89, A=4, K=1\a\a'),('WWV','WWV de W0MU <0000>: SFI=89, A=4, K=1'))
        self.assertEqual(collector.feed('WCY de DK0WCY <0000>: K=1\a\a'),('WCY','WCY de DK0WCY <0000>: K=1'))

    def test_report_text_containing_prompt_words_does_not_end_history(self):
        collector=ReportCollector();collector.feed('Date Hour SFI A K Forecast Logger')
        row='30-Oct-2026 0 89 4 1 example dxspider > text <VE7CC>'
        self.assertEqual(collector.feed(row),('WWV',row))
        self.assertEqual(collector.history_kind,'WWV')
        collector.feed('LZ3ZZ de NODE dxspider >');self.assertIsNone(collector.history_kind)

    def test_view_requests_and_filters_without_modifying_values(self):
        app=App(); app.withdraw()
        try:
            fake=FakeConnection(); app.connection=fake; app.status.set('Connected')
            app.request_report('WWV'); app.request_report('WCY')
            self.assertEqual(fake.commands,['sh/wwv','sh/wcy'])
            app.report_count.set('20');app.request_report('WWV');app.request_report('WCY')
            self.assertEqual(fake.commands[-2:],['sh/wwv/20','sh/wcy 20'])
            app.reports.extend([('WWV','line one'),('WCY','line two')]); app.report_kind.set('WCY'); app.render_reports()
            rows=app.report_tree.get_children(); self.assertEqual(len(rows),1)
            self.assertEqual(app.report_tree.item(rows[0],'values'),('WCY','line two','Main connection'))
        finally: app.quit_app()

    def test_request_targets_selected_secondary_and_checks_session(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def send(self,text):self.commands.append(text)
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            main=FakeConnection();app.connection=main;app.connection_target=('main.invalid',7300,'LZ3ZZ');app.status.set('Connected')
            with patch('app.Connection',Secondary):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');item=app.secondary_nodes[ident];old=item['connection']
                old.emit('status','Connected');app.poll_secondary_nodes()
                app.report_node.set('other.invalid:7301 · LZ3ZZ');app.report_count.set('20');app.request_report('WWV')
                self.assertEqual(old.commands,['sh/wwv/20']);self.assertEqual(main.commands,[])
                old.emit('data','WWV de W0MU <0000>: SFI=89\r\n');app.poll_secondary_nodes()
                self.assertEqual(app.reports[-1],('WWV','WWV de W0MU <0000>: SFI=89',item['source']))
                app._start_secondary_attempt(ident)
                with patch('app.messagebox.showerror') as error:app.request_report('WCY');error.assert_called_once()
                self.assertEqual(item['connection'].commands,[]);self.assertEqual(main.commands,[])
        finally:app.quit_app()

    def test_secondary_report_remains_available_without_main(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def send(self,text):self.commands.append(text)
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            with patch('app.Connection',Secondary):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');conn=app.secondary_nodes[ident]['connection']
                conn.emit('status','Connected');app.poll_secondary_nodes()
                self.assertEqual(str(app.wwv_button['state']),'normal')
                app.request_report('WWV');self.assertEqual(conn.commands,['sh/wwv'])
        finally:app.quit_app()

    def test_station_lookup_targets_selected_node_and_validates_input(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit;self.commands=[]
            def start(self,*args):pass
            def send(self,text):self.commands.append(text)
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            main=FakeConnection();app.connection=main;app.dialect='CC Cluster';app.status.set('Connected')
            with patch('app.Connection',Secondary):
                ident=app.start_secondary_node('spider.invalid',7300,'LZ3ZZ');item=app.secondary_nodes[ident]
                item['dialect']='DXSpider';item['connection'].emit('status','Connected');app.poll_secondary_nodes()
                label='spider.invalid:7300 · LZ3ZZ'
                for kind in ('Prefix','Heading','Sun','Moon'):app.request_propagation_lookup(label,kind,'ea8/k1abc')
                self.assertEqual(item['connection'].commands,['sh/prefix EA8/K1ABC','sh/heading EA8/K1ABC','sh/sun EA8/K1ABC','sh/moon EA8/K1ABC'])
                app.request_propagation_lookup(label,'Time','');app.request_propagation_lookup(label,'Sun','')
                self.assertEqual(item['connection'].commands[-2:],['sh/time','sh/sun'])
                app.request_propagation_lookup('Main connection','Sun','zl');app.request_propagation_lookup('Main connection','Time','');app.request_propagation_lookup('Main connection','Heading','lz3zz')
                self.assertEqual(main.commands,['sh/sun ZL','sh/time','sh/head LZ3ZZ'])
                for value in ('','K1ABC\nsh/dx','K1ABC W1AW','A'*21,'ß1'):
                    with self.subTest(value=value),self.assertRaises(ValueError):app.request_propagation_lookup(label,'Prefix',value)
                with self.assertRaises(ValueError):app.request_propagation_lookup('Main connection','Prefix','K1ABC')
                item['epoch']+=1
                with self.assertRaises(OSError):app.request_propagation_lookup(label,'Prefix','K1ABC')
                self.assertEqual(main.commands,['sh/sun ZL','sh/time','sh/head LZ3ZZ']);self.assertEqual(len(item['connection'].commands),6)
        finally:app.quit_app()

    def test_lookup_dialog_rejects_reconnected_node_with_same_label(self):
        from tkinter import ttk
        def descendants(widget):
            for child in widget.winfo_children():yield child;yield from descendants(child)
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            old=FakeConnection();app.connection=old;app.connection_target=('main.invalid',7300,'LZ3ZZ');app.dialect='DXSpider';app.status.set('Connected');app.refresh_report_targets()
            app.open_propagation_lookup();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            widgets=list(descendants(dialog));next(w for w in widgets if type(w) is ttk.Entry).insert(0,'LZ3ZZ')
            new=FakeConnection();app.generation+=1;app.connection=new;app.refresh_report_targets()
            button=next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Request')
            with patch('app.messagebox.showerror') as error:button.invoke();error.assert_called_once()
            self.assertEqual(old.commands,[]);self.assertEqual(new.commands,[])
        finally:app.quit_app()

if __name__ == '__main__': unittest.main()
