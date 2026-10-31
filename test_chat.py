import unittest
from tkinter import ttk
from unittest.mock import patch

from app import App
from chat import GroupReadback, group_command, parse_chat


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class FakeConnection:
    def __init__(self): self.commands=[]
    def send(self,value): self.commands.append(value)
    def close(self): pass


class ChatTests(unittest.TestCase):
    def test_membership_readback_requires_matching_call_and_complete_prompt(self):
        capture=GroupReadback('LZ3ZZ');capture.sent()
        self.assertFalse(capture.feed('LZ3ZZ de NODE dxspider >'))
        capture.feed('Callsign: LZ3ZZ');capture.feed('Group: local, FOC,');capture.feed('       : #9000');capture.feed('Name: Test')
        capture.feed('LZ3ZZ de NODE dxspider >')
        self.assertEqual(capture.state,'complete');self.assertEqual(capture.groups,{'LOCAL','FOC','#9000'})
        bad=GroupReadback('LZ3ZZ');bad.sent();bad.feed('Callsign: OTHER');bad.feed('Group: FOC');bad.feed('LZ3ZZ de NODE dxspider >')
        self.assertEqual(bad.state,'partial');self.assertIsNone(bad.groups)
        incomplete=GroupReadback('LZ3ZZ');incomplete.sent();incomplete.feed('Callsign: LZ3ZZ');incomplete.interrupt()
        self.assertEqual(incomplete.state,'partial');self.assertIsNone(incomplete.groups)

    def test_membership_readback_rejects_duplicate_fields(self):
        for lines in (('Callsign: OTHER','Callsign: LZ3ZZ','Group: FOC'),
                      ('Callsign: LZ3ZZ','Group: FOC','Group: OTHER')):
            with self.subTest(lines=lines):
                capture=GroupReadback('LZ3ZZ');capture.sent()
                for line in lines:capture.feed(line)
                capture.feed('LZ3ZZ de NODE dxspider >')
                self.assertEqual(capture.state,'partial');self.assertIsNone(capture.groups)

    def test_main_membership_change_during_refresh_keeps_live_update(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.connection_target=('example.invalid',7300,'LZ3ZZ');app.status.set('Connected');app.dialect='DXSpider'
            app.chat_groups={'FOC'};app.open_chat()
            refresh=next(w for w in descendants(app.chat_window) if isinstance(w,ttk.Button) and w.cget('text')=='Refresh groups')
            refresh.invoke();app.events.put((app.generation,'sent','stat/user'))
            app.events.put((app.generation,'data','Callsign: LZ3ZZ\r\nGroup: FOC\r\njoining group NEW\r\nLZ3ZZ de NODE dxspider >\r\nNEW de K1ABC: hello\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.chat_readback.state,'partial');self.assertEqual(app.chat_groups,{'FOC','NEW'})
            self.assertEqual(app.announcements[-1][0],'NEW de K1ABC: hello')
            self.assertIn('incomplete',app.chat_note.get().lower())
        finally:app.quit_app()

    def test_refresh_groups_restores_recognition_without_joining(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.connection_target=('example.invalid',7300,'LZ3ZZ');app.status.set('Connected');app.dialect='DXSpider';app.open_chat()
            widgets=list(descendants(app.chat_window));button=next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Refresh groups')
            button.invoke();self.assertEqual(conn.commands,['stat/user'])
            app.events.put((app.generation,'sent','stat/user'))
            app.events.put((app.generation,'data','Callsign: LZ3ZZ\r\nGroup: local, FOC\r\nLZ3ZZ de NODE dxspider >\r\nFOC de K1ABC: hello\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.chat_groups,{'LOCAL','FOC'});self.assertEqual(list(app.announcements),[('FOC de K1ABC: hello',None,'chat')])
            self.assertIn('FOC',app.chat_note.get())
        finally:app.quit_app()

    def test_commands_require_a_group_and_nonempty_one_line_message(self):
        self.assertEqual(group_command('Join','foc'),'JOIN FOC')
        self.assertEqual(group_command('Leave','#9000'),'LEAVE #9000')
        self.assertEqual(group_command('History','foc'),'SH/CHAT FOC')
        self.assertEqual(group_command('History','','', '50'),'SH/CHAT 50')
        self.assertEqual(group_command('History','foc','', '100'),'SH/CHAT FOC 100')
        self.assertEqual(group_command('History',''),'SH/CHAT')
        self.assertEqual(group_command('Find groups',''),'SH/GROUPS')
        self.assertEqual(group_command('Find groups','ignored','', '50'),'SH/GROUPS 50')
        for count in ('0','1001','-1','1 2','12345'):
            with self.subTest(count=count), self.assertRaises(ValueError): group_command('History','FOC','',count)
        self.assertEqual(group_command('Send','foc','hello'),'CHAT FOC hello')
        for action,group,text in [('Join','X\nBYE',''),('Send','FOC',''),('Send','FOC','hello\nbye'),('Join','x y','')]:
            with self.subTest(action=action,group=group),self.assertRaises(ValueError):group_command(action,group,text)

    def test_recognizes_group_chat_but_not_spots_or_direct_talk(self):
        groups={'FOC','#9000'}
        self.assertEqual(parse_chat('FOC de K1ABC: hello','LZ3ZZ',groups),'FOC de K1ABC: hello')
        self.assertEqual(parse_chat('FOC de K1ABC: hello\a\a','LZ3ZZ',groups),'FOC de K1ABC: hello')
        self.assertEqual(parse_chat('#9000 de W1AW: hello','LZ3ZZ',groups),'#9000 de W1AW: hello')
        self.assertIsNone(parse_chat('DX de K1ABC: 14074 W1AW CQ 1200Z','LZ3ZZ',groups))
        self.assertIsNone(parse_chat('LZ3ZZ de K1ABC: private','LZ3ZZ',groups))
        self.assertIsNone(parse_chat('W1AW de K1ABC: other','LZ3ZZ',groups))
        self.assertIsNone(parse_chat('LZ3ZZ de NODE dxspider >','LZ3ZZ',groups))

    def test_window_sends_only_after_action_and_rejects_stale_connection(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.status.set('Connected');app.dialect='DXSpider';app.open_chat()
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');widgets=list(descendants(dialog))
            entries=[w for w in widgets if type(w) is ttk.Entry];entries[0].insert(0,'FOC');entries[1].insert(0,'hello')
            buttons={w.cget('text'):w for w in widgets if isinstance(w,ttk.Button)}
            self.assertEqual(conn.commands,[]);buttons['Join'].invoke();buttons['Send'].invoke()
            self.assertEqual(conn.commands,['JOIN FOC','CHAT FOC hello'])
            app.generation+=1
            with patch('app.messagebox.showerror') as error:buttons['Leave'].invoke();error.assert_called_once()
            self.assertEqual(len(conn.commands),2);dialog.destroy()
        finally:app.quit_app()

    def test_find_active_groups_requests_source_command_without_joining(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.status.set('Connected');app.dialect='DXSpider';app.open_chat()
            widgets=list(descendants(app.chat_window));entries=[w for w in widgets if type(w) is ttk.Entry]
            entries[2].insert(0,'100')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Find active groups').invoke()
            self.assertEqual(conn.commands,['SH/GROUPS 100'])
            self.assertEqual(str(app.notebook.select()),str(app.console_page))
            self.assertEqual(app.chat_groups,set())
        finally:app.quit_app()

    def test_history_form_requests_all_groups_and_displays_source_format(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            conn=FakeConnection();app.connection=conn;app.connection_target=('example.invalid',7300,'LZ3ZZ')
            app.status.set('Connected');app.dialect='DXSpider';app.open_chat()
            widgets=list(descendants(app.chat_window))
            entries=[w for w in widgets if type(w) is ttk.Entry]
            entries[2].insert(0,'50')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='History').invoke()
            self.assertEqual(conn.commands,['SH/CHAT 50'])
            app.events.put((app.generation,'data','30Oct2026@12:34:56 K1ABC -> FOC hello there\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.announcements[-1][0], '30Oct2026@12:34:56 K1ABC -> FOC hello there')
        finally:app.quit_app()

    def test_incoming_group_chat_reaches_messages(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.connection_target=('example.invalid',7300,'LZ3ZZ');app.dialect='DXSpider'
            app.events.put((app.generation,'data','joining group FOC\r\nFOC de K1ABC: hello\r\nDX de K1ABC: 14074 W1AW CQ 1200Z\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(list(app.announcements),[('FOC de K1ABC: hello',None,'chat')])
            app.events.put((app.generation,'data','leaving group FOC\r\nFOC de K1ABC: after leaving\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(list(app.announcements),[('FOC de K1ABC: hello',None,'chat')])
        finally:app.quit_app()

    def test_other_sent_command_interrupts_main_group_refresh(self):
        from chat import GroupReadback
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.connection=FakeConnection();app.connection_target=('example.invalid',7300,'LZ3ZZ');app.dialect='DXSpider';app.status.set('Connected')
            app.chat_groups={'FOC'};app.chat_readback=GroupReadback('LZ3ZZ')
            app.events.put((app.generation,'sent','stat/user'));app.events.put((app.generation,'sent','sh/dx'))
            app.events.put((app.generation,'data','Callsign: LZ3ZZ\r\nGroup: WRONG\r\nLZ3ZZ de NODE dxspider >'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.chat_readback.state,'partial');self.assertEqual(app.chat_groups,{'FOC'})
        finally:app.quit_app()

    def test_chat_control_is_visible_at_minimum_window_size(self):
        app=App();app._save_settings=lambda:None
        try:
            app.geometry('780x560');app.notebook.select(5);app.update()
            button=next(w for w in descendants(app) if isinstance(w,ttk.Button) and w.cget('text')=='Chat groups…')
            self.assertTrue(button.winfo_ismapped())
            self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),app.winfo_rootx()+app.winfo_width())
        finally:app.quit_app()


if __name__=='__main__':unittest.main()
