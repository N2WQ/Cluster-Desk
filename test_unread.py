import unittest
from unittest.mock import patch
from app import App
from protocol import Spot


class FakeConnection:
    def __init__(self,emit):self.emit=emit
    def start(self,*args):pass
    def close(self):pass


class UnreadTests(unittest.TestCase):
    def test_unread_counters_track_incoming_and_clear_on_visit(self):
        app=App();app._save_settings=lambda:None
        try:
            app.geometry('1120x760');app.update();app.alarm_calls.set('K1*');app.alarm_enabled.set(True);app.apply_alarm()
            app.events.put((app.generation,'data','To ALL de K1ABC: hello\r\nWWV de K1ABC <1200Z>: SFI=100, A=5, K=2\r\nDX de S: 14074 K1ABC CQ 1200Z\r\n'))
            app.after_cancel(app.poll_after);app.poll();app.update()
            self.assertEqual(app.unread_counts,{'messages':1,'reports':1,'alarms':1})
            self.assertIn('(1)',app.notebook.tab(app.ann_page,'text'))
            app.notebook.select(app.ann_page);app.update();self.assertEqual(app.unread_counts['messages'],0)
            app.events.put((app.generation,'data','To ALL de K1ABC: another\r\n'));app.after_cancel(app.poll_after);app.poll();app.update()
            self.assertEqual(app.unread_counts['messages'],0);self.assertEqual(app.unread_counts['reports'],1)
            app.notebook.select(app.report_page);app.update();self.assertEqual(app.unread_counts['reports'],0)
            app.clear_alarms();app.update();self.assertEqual(app.unread_counts['alarms'],0)
        finally:app.quit_app()

    def test_secondary_messages_and_reports_count_and_compact_tabs_remain_reachable(self):
        app=App();app._save_settings=lambda:None
        try:
            app.geometry('780x560');app.update()
            with patch('app.Connection',FakeConnection):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');connection=app.secondary_nodes[ident]['connection']
                connection.emit('dialect','DXSpider');connection.emit('status','Connected')
                connection.emit('data','To ALL de K1ABC: hello\r\nWCY de K1ABC <1200Z>: K=1\r\n');app.poll_secondary_nodes();app.update()
                self.assertEqual(app.unread_counts['messages'],1);self.assertEqual(app.unread_counts['reports'],1)
                self.assertIn('•',app.notebook.tab(app.ann_page,'text'));self.assertIn('•',app.notebook.tab(app.report_page,'text'))
                indexes=[]
                for x in range(app.notebook.winfo_width()):
                    try:indexes.append(app.notebook.index(f'@{x},15'))
                    except Exception:pass
                self.assertIn(8,indexes)
                app.clear_announcements();app.clear_reports();app.update()
                self.assertEqual(app.unread_counts['messages'],0);self.assertEqual(app.unread_counts['reports'],0)
        finally:app.quit_app()

    def test_pending_indicator_refresh_is_cancelled_on_shutdown(self):
        app=App();app._save_settings=lambda:None
        app.mark_unread('messages');self.assertIsNotNone(app._unread_pending)
        app.quit_app();self.assertIsNone(app._unread_pending)


if __name__=='__main__':unittest.main()
