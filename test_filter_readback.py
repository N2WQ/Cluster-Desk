import unittest
import time
from unittest.mock import patch

from app import App
from filter_readback import FilterReadback, is_filter_change
from protocol import TrackedCommand


class FakeConnection:
    def __init__(self): self.commands=[]
    def send(self, command): self.commands.append(command)
    def send_tracked(self, command):
        command=TrackedCommand(command);self.send(command);return command
    def close(self): pass


class FilterReadbackTests(unittest.TestCase):
    def test_dxspider_detects_filter_change_after_command_separator(self):
        self.assertTrue(is_filter_change(r'sh/time\nclear/spots all','DXSpider'))
        self.assertTrue(is_filter_change(r'sh/time\naccept/spots 1 on 20m','DXSpider'))
        self.assertTrue(is_filter_change(r'sh/time\nc/sp all','DXSpider'))
        self.assertTrue(is_filter_change('rej//ann 1 by K','DXSpider'))
        self.assertTrue(is_filter_change('clear/route all','DXSpider'))
        self.assertFalse(is_filter_change(r'sh/time\nsh/dx','DXSpider'))
        self.assertFalse(is_filter_change(r'sh/time\nclear/spots all','CC Cluster'))

    def test_late_timed_out_filter_response_cannot_make_next_readback_complete(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            window.connection=FakeConnection();window.status.set('Connected');window.dialect='CC Cluster'
            window.show_filters();window.events.put((window.generation,'sent',window.connection.commands[-1]))
            window.after_cancel(window.poll_after);window.poll()
            window.filter_deadline=time.monotonic()-1
            window.after_cancel(window.poll_after);window.poll()
            self.assertEqual(window.filter_readback.state,'partial')
            window.show_cc_bandtime();window.events.put((window.generation,'sent',window.connection.commands[-1]))
            window.events.put((window.generation,'data','BandTime Filter set OFF\r\nLZ3ZZ de NODE CCC >'))
            window.after_cancel(window.poll_after);window.poll()
            self.assertEqual(window.filter_readback.state,'partial')
            self.assertTrue(window.filter_readback.ambiguous)
        finally:window.connection=None;window.quit_app()

    def test_cc_bandtime_readback_requires_recognized_marker(self):
        for header in ('BandTime Filter set OFF','DX Spot BandTime Filter/20 set to Pass 0100,0200'):
            with self.subTest(header=header):
                capture=FilterReadback();capture.queued('CC Cluster',bandtime=True);capture.sent()
                self.assertFalse(capture.feed('unrelated traffic'))
                self.assertTrue(capture.feed(header))
                self.assertTrue(capture.feed('LZ3ZZ de NODE CCC >'))
                self.assertEqual(capture.state,'complete');self.assertEqual(capture.text(),header)

    def test_starts_after_send_and_finishes_on_prompt(self):
        capture=FilterReadback(); capture.queued('CC Cluster')
        self.assertFalse(capture.feed('unrelated before sending'))
        capture.sent(); self.assertFalse(capture.feed('LZ3ZZ de NODE CCC >'))
        self.assertTrue(capture.feed('DX Spot Orig Cty Filter/All set OFF\r'))
        self.assertTrue(capture.feed('Keyword Filter set OFF\r'))
        self.assertTrue(capture.feed('LZ3ZZ de NODE CCC >\r'))
        self.assertEqual(capture.state,'complete'); self.assertIn('Keyword Filter set OFF',capture.text())
        self.assertFalse(capture.feed('later traffic'))

    def test_limits_and_interruption(self):
        capture=FilterReadback(); capture.queued('DXSpider'); capture.sent(); capture.feed('LZ3ZZ : spots')
        for _ in range(501): capture.feed('line')
        self.assertEqual(len(capture.lines),500); self.assertTrue(capture.truncated)
        capture.interrupt(); self.assertEqual(capture.state,'partial')

    def test_truncated_response_is_partial_even_when_node_prompt_arrives(self):
        for dialect,header,prompt in (
            ('DXSpider','LZ3ZZ: spots','LZ3ZZ de NODE dxspider >'),
            ('CC Cluster','DX Spot Orig Cty Filter/All set OFF','LZ3ZZ de NODE CCC >'),
        ):
            with self.subTest(dialect=dialect):
                capture=FilterReadback();capture.queued(dialect,'LZ3ZZ');capture.sent();capture.feed(header)
                for _ in range(500):capture.feed('line')
                self.assertTrue(capture.truncated)
                capture.feed(prompt)
                self.assertEqual(capture.state,'partial')

    def test_dxspider_old_style_filter_header(self):
        for header in ('LZ3ZZ: Old Style Filter  spots', 'LZ3ZZ: Old Style Filter input wwv'):
            with self.subTest(header=header):
                capture=FilterReadback(); capture.queued('DXSpider'); capture.sent()
                self.assertTrue(capture.feed(header))
                self.assertTrue(capture.feed('LZ3ZZ de NODE dxspider >'))
                self.assertEqual(capture.state, 'complete')
                self.assertEqual(capture.text(), header)

    def test_dxspider_reads_each_pinned_filter_category(self):
        # show/filter.pl asks for these categories, and Filter::print emits
        # the call, category and optional input flag in this order.
        for category in ('route', 'ann', 'spots', 'wcy', 'wwv', 'rbn'):
            for flag in ('', ' input'):
                with self.subTest(category=category,flag=flag):
                    capture=FilterReadback();capture.queued('DXSpider','LZ3ZZ');capture.sent()
                    header=f'LZ3ZZ : {category}{flag}'
                    self.assertTrue(capture.feed(header))
                    capture.feed(' 1 reject on 20m')
                    self.assertTrue(capture.feed('LZ3ZZ de NODE dxspider >'))
                    self.assertEqual(capture.state,'complete')
                    self.assertIn(header,capture.text())

    def test_dxspider_empty_filter_response(self):
        capture=FilterReadback(); capture.queued('DXSpider'); capture.sent()
        self.assertFalse(capture.feed('LZ3ZZ de NODE dxspider >'))
        self.assertTrue(capture.feed('No filters defined for LZ3ZZ'))
        self.assertTrue(capture.feed('LZ3ZZ de NODE dxspider >'))
        self.assertEqual(capture.state,'complete'); self.assertEqual(capture.text(),'No filters defined for LZ3ZZ')

    def test_dxspider_away_prompt_completes_filter_readback(self):
        capture=FilterReadback();capture.queued('DXSpider','LZ3ZZ');capture.sent()
        capture.feed('LZ3ZZ : spots');capture.feed(' 1 accept on 20m')
        self.assertTrue(capture.feed('To ALL: (LZ3ZZ) de NODE dxspider > is a prompt'))
        self.assertEqual(capture.state,'receiving')
        self.assertTrue(capture.feed('(LZ3ZZ) de NODE 30-Oct-2026 1234Z dxspider >'))
        self.assertEqual(capture.state,'complete');self.assertIn('accept on 20m',capture.text())

    def test_dxspider_localized_empty_filter_responses_require_exact_call(self):
        prefixes=('No filters defined for','Pas de filtres d\u00e9finis pour',
                  'No hay filtros definidos para','Keine Filter definiert fuer',
                  'Nessun filtro definito per','Nejsou zadne definovane filtry pro',
                  'Nenhuns filtros definidos para')
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                capture=FilterReadback();capture.queued('DXSpider','LZ3ZZ-1');capture.sent()
                self.assertFalse(capture.feed(prefix+' LZ3ZZ'))
                self.assertFalse(capture.feed('To ALL: '+prefix+' LZ3ZZ-1'))
                self.assertFalse(capture.feed(prefix+' LZ3ZZ-1 extra'))
                line=prefix+' LZ3ZZ-1'
                self.assertTrue(capture.feed(line))
                self.assertTrue(capture.feed('LZ3ZZ-1 de NODE dxspider >'))
                self.assertEqual(capture.state,'complete');self.assertEqual(capture.text(),line)

    def test_dxspider_readback_requires_exact_connection_callsign(self):
        for other,correct in (('OTHER: spots','lz3zz-1 : spots'),
                              ('No filters defined for LZ3ZZ','No filters defined for LZ3ZZ-1')):
            with self.subTest(other=other):
                capture=FilterReadback();capture.queued('DXSpider','LZ3ZZ-1');capture.sent()
                self.assertFalse(capture.feed(other))
                self.assertEqual(capture.state,'waiting')
                self.assertTrue(capture.feed(correct))
                self.assertTrue(capture.feed('LZ3ZZ-1 de NODE dxspider >'))
                self.assertEqual(capture.state,'complete')
                self.assertEqual(capture.text(),correct)

    def test_main_dxspider_readback_uses_session_callsign(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            window.connection=FakeConnection();window.status.set('Connected');window.dialect='DXSpider'
            window.connection_target=('node.invalid',7300,'LZ3ZZ-1');window.call.set('OTHER')
            window.show_filters()
            window.events.put((window.generation,'sent',window.connection.commands[-1]))
            window.events.put((window.generation,'data','OTHER: spots\r\nwrong\r\nOTHER de NODE dxspider >\r\nLZ3ZZ-1: spots\r\nright\r\nLZ3ZZ-1 de NODE dxspider >'))
            window.after_cancel(window.poll_after);window.poll()
            self.assertEqual(window.filter_readback.state,'complete')
            self.assertIn('right',window.filter_readback.text())
            self.assertNotIn('wrong',window.filter_readback.text())
        finally:window.quit_app()

    def test_prompt_words_inside_traffic_do_not_end_readback(self):
        capture=FilterReadback();capture.queued('DXSpider');capture.sent()
        capture.feed('LZ3ZZ : spots')
        capture.feed('To ALL de K1ABC: the dxspider > text is in a message')
        self.assertEqual(capture.state,'receiving')
        self.assertIn('text is in a message',capture.text())
        capture.feed('LZ3ZZ de NODE dxspider >')
        self.assertEqual(capture.state,'complete')

    def test_other_command_interrupts_filter_readback(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            window.connection=FakeConnection();window.status.set('Connected');window.dialect='CC Cluster'
            window.show_filters();window.events.put((window.generation,'sent',window.connection.commands[-1]))
            window.events.put((window.generation,'data','DX Spot Orig Cty Filter/All set OFF\r\n'))
            window.events.put((window.generation,'sent','set/dx'))
            window.events.put((window.generation,'data','NODE CCC >'))
            window.after_cancel(window.poll_after);window.poll()
            self.assertEqual(window.filter_readback.state,'partial')
            self.assertIn('Partial',window.filter_readback_note.get())
        finally:window.quit_app()

    def test_earlier_identical_request_marks_readback_ambiguous(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            window.connection=FakeConnection();window.status.set('Connected');window.dialect='CC Cluster'
            window.show_filters()
            window.events.put((window.generation,'sent','set/filter keyword/pass CQ'))
            window.events.put((window.generation,'sent','sh/filter'))
            window.events.put((window.generation,'data','DX Spot Orig Cty Filter/All set OFF\r\nOld result\r\nNODE CCC >'))
            window.events.put((window.generation,'sent',window.connection.commands[-1]))
            window.events.put((window.generation,'data','DX Spot Orig Cty Filter/All set OFF\r\nKeyword Filter PASS CQ\r\nNODE CCC >'))
            window.after_cancel(window.poll_after);window.poll()
            self.assertEqual(window.filter_readback.state,'partial')
            self.assertIn('earlier filter request',window.filter_readback_note.get())
            self.assertIsNone(window.filter_deadline)
            self.assertIn('Keyword Filter PASS CQ',window.filter_readback.text())
            self.assertNotIn('Old result',window.filter_readback.text())
        finally:window.quit_app()

    def test_delayed_previous_response_is_not_reported_as_complete(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            window.connection=FakeConnection();window.status.set('Connected');window.dialect='CC Cluster';window.show_filters()
            window.events.put((window.generation,'sent','sh/filter'))
            window.events.put((window.generation,'sent',window.connection.commands[-1]))
            window.events.put((window.generation,'data','DX Spot Orig Cty Filter/All set OFF\r\nold result\r\nNODE CCC >'))
            window.after_cancel(window.poll_after);window.poll()
            self.assertEqual(window.filter_readback.state,'partial')
            self.assertIn('old result',window.filter_readback.text())
            self.assertIn('earlier filter request',window.filter_readback_note.get())
        finally:window.quit_app()

    def test_window_receives_output_and_reports_disconnect(self):
        window=App(); window.withdraw(); window._save_settings=lambda:None
        try:
            fake=FakeConnection(); window.connection=fake; window.status.set('Connected'); window.dialect='CC Cluster'
            window.show_filters(); self.assertEqual(fake.commands,['sh/filter'])
            window.filter_deadline=time.monotonic()+1
            window.events.put((window.generation,'sent',fake.commands[-1]))
            window.events.put((window.generation,'data','NODE CCC >'))
            window.after_cancel(window.poll_after); window.poll()
            self.assertEqual(window.filter_readback.state,'waiting')
            self.assertGreater(window.filter_deadline,time.monotonic()+10)
            window.events.put((window.generation,'data','DX Spot Orig Cty Filter/All set OFF\r\nKeyword Filter set OFF\r\nLZ3ZZ de NODE CCC >'))
            window.after_cancel(window.poll_after); window.poll()
            self.assertEqual(window.filter_readback.state,'complete')
            self.assertIn('Keyword Filter set OFF',window.filter_readback_text.get('1.0','end'))
            window.show_filters()
            with patch('app.messagebox.showerror') as error:
                window.show_filters(); error.assert_called_once()
            window.disconnect(); self.assertEqual(window.filter_readback.state,'partial')
            self.assertIn('Partial',window.filter_readback_note.get())
        finally: window.quit_app()

    def test_readback_opens_near_main_window(self):
        window=App(); window._save_settings=lambda:None
        try:
            window.geometry('780x560+100+80'); window.update()
            window.connection=FakeConnection(); window.status.set('Connected'); window.dialect='CC Cluster'
            window.show_filters(); window.update()
            dialog=window.filter_window
            self.assertGreaterEqual(dialog.winfo_x(), window.winfo_x())
            self.assertGreaterEqual(dialog.winfo_y(), window.winfo_y())
            self.assertLess(dialog.winfo_x(), window.winfo_x()+window.winfo_width())
            self.assertLess(dialog.winfo_y(), window.winfo_y()+window.winfo_height())
        finally: window.quit_app()

    def test_main_readback_preserves_scroll_when_more_output_arrives(self):
        window=App(); window._save_settings=lambda:None
        try:
            window.connection=FakeConnection(); window.status.set('Connected'); window.dialect='CC Cluster'
            window.show_filters(); capture=window.filter_readback; capture.sent()
            capture.feed('DX Spot Orig Cty Filter/All set OFF')
            for n in range(100): capture.feed(f'Filter line {n}')
            window.render_filter_readback();window.update()
            window.filter_readback_text.yview_moveto(.5);window.update()
            before=window.filter_readback_text.yview()[0]
            self.assertGreater(before,.3)
            capture.feed('Another filter line');window.render_filter_readback();window.update()
            self.assertAlmostEqual(window.filter_readback_text.yview()[0],before,delta=.03)
            self.assertIn('Another filter line',window.filter_readback_text.get('1.0','end'))
        finally: window.quit_app()

    def test_coalesced_prompt_and_response_is_captured(self):
        window=App(); window.withdraw(); window._save_settings=lambda:None
        try:
            window.connection=FakeConnection(); window.status.set('Connected'); window.dialect='CC Cluster'; window.show_filters()
            window.events.put((window.generation,'sent',window.connection.commands[-1]))
            window.events.put((window.generation,'data','NODE CCC >DX Spot Orig Cty Filter/All set OFF\r\nKeyword Filter set OFF\r\nNODE CCC >'))
            window.after_cancel(window.poll_after); window.poll()
            self.assertEqual(window.filter_readback.state,'complete')
            self.assertIn('DX Spot Orig Cty',window.filter_readback.text())
        finally: window.quit_app()


if __name__ == '__main__': unittest.main()
