import unittest
from unittest.mock import patch
from announcements import parse_announcement, parse_talk, talk_sender, message_command, message_commands, is_weather
from app import App


class FakeConnection:
    def __init__(self): self.commands=[]
    def send(self, text): self.commands.append(text)
    def send_batch(self, values): self.commands.extend(values)
    def close(self): pass


class AnnouncementTests(unittest.TestCase):
    def test_dxspider_message_commands_and_validation(self):
        self.assertEqual(message_command('DXSpider', 'Direct message', 'hello', 'lz3zz-1'), 'TALK LZ3ZZ-1 hello')
        self.assertEqual(message_command('DXSpider', 'Local announcement', 'FULL moon'), 'ANNOUNCE LOCAL FULL moon')
        self.assertEqual(message_command('DXSpider', 'Network announcement', 'hello'), 'ANNOUNCE FULL hello')
        for kind, body, target in [('Direct message', 'hello\nDX 1 X', 'K1ABC'), ('Direct message', ' ', 'K1ABC'), ('Direct message', 'hello', 'A B'), ('Direct message', 'x'*401, 'K1ABC')]:
            with self.subTest(kind=kind, body=body, target=target):
                with self.assertRaises(ValueError): message_command('DXSpider', kind, body, target)
        with self.assertRaises(ValueError): message_command('Unknown', 'Local announcement', 'hello')

    def test_cc_cluster_message_commands(self):
        self.assertEqual(message_command('CC Cluster', 'Direct message', 'hello', 'K1ABC'), 'TALK K1ABC hello')
        self.assertEqual(message_command('CC Cluster', 'Local announcement', 'hello'), 'ANNOUNCE hello')
        self.assertEqual(message_command('CC Cluster', 'Network announcement', 'hello'), 'ANNOUNCE/FULL hello')

    def test_multiple_direct_message_recipients_are_validated_before_queueing(self):
        self.assertEqual(message_commands('DXSpider','Direct message','hello','k1abc, W1AW-1'), ['TALK K1ABC hello','TALK W1AW-1 hello'])
        for calls in ('K1ABC,,W1AW','K1ABC,k1abc',','.join(['K'+str(i) for i in range(11)]),'K1ABC,W1AW\nBYE'):
            with self.subTest(calls=calls), self.assertRaises(ValueError): message_commands('CC Cluster','Direct message','hello',calls)

    def test_talk_recognizes_only_the_logged_in_recipient(self):
        self.assertEqual(parse_talk('LZ3ZZ de K1ABC: hello', 'lz3zz'), 'LZ3ZZ de K1ABC: hello')
        self.assertEqual(parse_talk('LZ3ZZ de K1ABC/P: hello', 'LZ3ZZ'), 'LZ3ZZ de K1ABC/P: hello')
        self.assertIsNone(parse_talk('W1AW de K1ABC: hello', 'LZ3ZZ'))
        self.assertIsNone(parse_talk('LZ3ZZ de NODE dxspider >', 'LZ3ZZ'))

    def test_cc_self_addressed_ssid_talk_requires_exact_login(self):
        line = 'LZ3ZZ-15 de LZ3ZZ-15: CLUSTERDESKSSIDVERIFY'
        self.assertEqual(parse_talk(line, 'lz3zz-15'), line)
        self.assertEqual(talk_sender(line, 'lz3zz-15'), 'LZ3ZZ-15')
        for other_login in ('LZ3ZZ', 'LZ3ZZ-1', 'LZ3ZZ-16'):
            with self.subTest(login=other_login):
                self.assertIsNone(parse_talk(line, other_login))
                self.assertIsNone(talk_sender(line, other_login))

    def test_cc_talk_with_observed_login_prefix(self):
        line = 'login: LZ3ZZ-15 de LZ3ZZ-15: CLUSTERDESKSSIDTEST'
        self.assertEqual(parse_talk(line, 'LZ3ZZ-15'), line)
        self.assertEqual(talk_sender(line, 'LZ3ZZ-15'), 'LZ3ZZ-15')
        self.assertIsNone(parse_talk(line, 'LZ3ZZ'))
        self.assertIsNone(talk_sender(line, 'LZ3ZZ'))

    def test_reply_requires_a_replyable_direct_message_to_current_login(self):
        self.assertEqual(talk_sender('LZ3ZZ de K1ABC-1: hello', 'lz3zz'), 'K1ABC-1')
        for line in ('OTHER de K1ABC: hello', 'To ALL de K1ABC: hello', 'LZ3ZZ de K1ABC/P: hello', 'LZ3ZZ de NODE dxspider >'):
            with self.subTest(line=line): self.assertIsNone(talk_sender(line, 'LZ3ZZ'))

    def test_reply_opens_addressed_form_without_sending(self):
        from tkinter import ttk
        app=App(); app.withdraw(); app._save_settings=lambda:None
        try:
            fake=FakeConnection(); app.connection=fake; app.connection_target=('example.invalid',7300,'LZ3ZZ')
            app.dialect='DXSpider'; app.status.set('Connected')
            app.announcements.append('LZ3ZZ de K1ABC: hello'); app.render_announcements()
            app.ann_tree.selection_set(app.ann_tree.get_children()[0]); app.show_announcement()
            self.assertEqual(str(app.reply_button['state']), 'normal')
            app.reply_to_talk(); self.assertEqual(fake.commands, [])
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            panel=dialog.winfo_children()[0]
            entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry]
            self.assertEqual(entries[0].get(), 'K1ABC'); self.assertEqual(entries[1].get(), '')
            app.status.set('Disconnected'); self.assertEqual(str(app.reply_button['state']), 'disabled')
        finally: app.quit_app()

    def test_queue_message_never_sends_on_changed_connection(self):
        app=App(); app.withdraw(); app._save_settings=lambda:None
        try:
            fake=FakeConnection(); app.connection=fake; app.status.set('Connected'); app.dialect='DXSpider'
            generation=app.generation
            app.queue_message(generation, 'Direct message', 'hello', 'K1ABC')
            self.assertEqual(fake.commands,['TALK K1ABC hello'])
            app.queue_message(generation, 'Direct message', 'group', 'K1ABC,W1AW')
            self.assertEqual(fake.commands[-2:],['TALK K1ABC group','TALK W1AW group'])
            app.generation += 1
            with self.assertRaises(OSError): app.queue_message(generation, 'Network announcement', 'hello')
            self.assertEqual(fake.commands,['TALK K1ABC hello','TALK K1ABC group','TALK W1AW group'])
        finally: app.quit_app()

    def test_incoming_talk_reaches_message_view(self):
        app=App(); app.withdraw(); app._save_settings=lambda:None
        try:
            app.connection_target=('example.invalid', 7300, 'LZ3ZZ'); app.dialect='DXSpider'
            app.events.put((app.generation, 'data', 'LZ3ZZ de K1ABC: hello\r\nW1AW de K1ABC: other\r\n'))
            app.after_cancel(app.poll_after); app.poll()
            self.assertEqual(list(app.announcements),[('LZ3ZZ de K1ABC: hello',None,'talk')])
        finally: app.quit_app()

    def test_cc_cluster_observed_self_talk_is_displayed_and_replyable(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            connection=FakeConnection();app.connection=connection;app.connection_target=('example.invalid',7300,'LZ3ZZ')
            app.spot_source=('example.invalid',7300,'LZ3ZZ');app.dialect='CC Cluster';app.status.set('Connected')
            app.events.put((app.generation,'data','LZ3ZZ de LZ3ZZ: CLUSTERDESKSELFTEST\r\nOTHER de LZ3ZZ: not ours\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(len(app.announcements),1)
            row=app.ann_tree.get_children()[0];app.ann_tree.selection_set(row);app.show_announcement()
            self.assertEqual(str(app.reply_button['state']),'normal')
            from tkinter import ttk
            app.reply_to_talk();self.assertEqual(connection.commands,[])
            dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            panel=dialog.winfo_children()[0];entries=[w for w in panel.winfo_children() if type(w) is ttk.Entry]
            self.assertEqual(entries[0].get(),'LZ3ZZ');entries[1].insert(0,'test reply')
            next(w for w in panel.winfo_children()[-1].winfo_children() if w.cget('text')=='Send message').invoke()
            self.assertEqual(connection.commands,['TALK LZ3ZZ test reply'])
        finally:app.quit_app()
    def test_known_history_and_live_formats(self):
        lines=[
            '30-Oct 0003Z ALL     SR4DXC:    SR4DXC DXCluster telnet example.invalid 7300',
            '29Oct2026@14:03:00 SR4DXC -> ALL DXCluster information',
            'To ALL de K1ABC: Good evening',
            'WX de K1ABC: Snow today',
            'To ALL de K1ABC <2349Z> : Good evening',
        ]
        for line in lines:
            with self.subTest(line=line): self.assertEqual(parse_announcement(line),line)
        for line in ['Date  Time  To      From       Announcement','DX de K1ABC: 14074.0 W1AW hello 0000Z','LZ3ZZ de NODE CCC >','']:
            with self.subTest(line=line): self.assertIsNone(parse_announcement(line))

    def test_dxspider_specialty_announcements_and_node_beep(self):
        self.assertEqual(parse_announcement('To IOTA de K1ABC: Island active\a\a'),'To IOTA de K1ABC: Island active')
        self.assertEqual(parse_announcement('To #9000 de W1AW: Notice\a\a'),'To #9000 de W1AW: Notice')
        self.assertIsNone(parse_announcement('To bad target de K1ABC: Notice'))
        self.assertIsNone(parse_announcement('K1ABC de W1AW: private'))
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.dialect='DXSpider';app.events.put((app.generation,'data','To IOTA de K1ABC: Island active\a\a\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.announcements[-1][0],'To IOTA de K1ABC: Island active')
        finally:app.quit_app()

    def test_dxspider_pc12_announcements_and_weather(self):
        announcement='PC12^K1ABC^*^hello all^ ^NODE^0^H50^~'
        weather='PC12^K1ABC^*^snow^ ^NODE^1^H50^~'
        self.assertEqual(parse_announcement(announcement),'Announcement de K1ABC: hello all')
        self.assertEqual(parse_announcement('PC12^K1ABC^*^notice^*^NODE^0^H50^~'),'To SYSOP de K1ABC: notice')
        self.assertEqual(parse_announcement('PC12^K1ABC^*^notice^FOC^NODE^0^H50^~'),'PC12 target FOC de K1ABC: notice')
        self.assertEqual(parse_announcement('PC12^K1ABC^*^storm^*^NODE^1^H50^~'),'WX de K1ABC: storm')
        self.assertEqual(parse_announcement(weather),'WX de K1ABC: snow')
        self.assertTrue(is_weather(parse_announcement(weather)))
        for value in ('PC12^K1ABC^*^hello^ ^NODE^2^H50^~','PC12^K1ABC^*^^ ^NODE^0^H50^~','PC12^K1ABC^*^hello^ ^NODE^0^H50'):
            with self.subTest(value=value):self.assertIsNone(parse_announcement(value))
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.events.put((app.generation,'data',weather+'\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(list(app.announcements),[('WX de K1ABC: snow',None,'weather')])
            self.assertIn(weather,app.transcript.get('1.0','end'))
        finally:app.quit_app()

    def test_weather_classification_and_request(self):
        weather = ['WX de K1ABC: snow', 'To WX de K1ABC <2349Z> : snow',
                   '30-Oct 0003Z WX K1ABC: snow', '29Oct2026@14:03:00 K1ABC -> WX snow']
        for line in weather:
            with self.subTest(line=line):
                self.assertEqual(parse_announcement(line),line); self.assertTrue(is_weather(line))
        self.assertFalse(is_weather('To ALL de K1ABC: snow'))
        app=App(); app.withdraw(); app._save_settings=lambda:None
        try:
            fake=FakeConnection(); app.connection=fake; app.status.set('Connected')
            app.announcements.extend(['To ALL de K1ABC: hello', *weather])
            app.request_weather()
            self.assertEqual(fake.commands,['sh/wx']); self.assertEqual(app.message_kind.get(),'Weather')
            self.assertEqual(len(app.ann_tree.get_children()),4)
            app.request_announcements()
            self.assertEqual(fake.commands,['sh/wx','sh/ann']); self.assertEqual(len(app.ann_tree.get_children()),5)
        finally: app.quit_app()

    def test_auto_open_only_for_direct_messages_and_can_show_filtered_view(self):
        from tkinter import ttk
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.connection_target=('main.example',7300,'LZ3ZZ');app.spot_source=('main.example',7300,'LZ3ZZ');app.dialect='CC Cluster'
            app.events.put((app.generation,'data','LZ3ZZ de K1ABC: first\r\n'))
            app.after_cancel(app.poll_after);app.poll();self.assertIsNone(app.talk_alert_window)
            app.auto_open_talk.set(True)
            app.events.put((app.generation,'data','To ALL de K1ABC: announce\r\nOTHER de K1ABC: ignored\r\n'))
            app.after_cancel(app.poll_after);app.poll();self.assertIsNone(app.talk_alert_window)
            app.events.put((app.generation,'data','LZ3ZZ de K1ABC: second\r\nLZ3ZZ de W1AW: third\r\n'))
            app.after_cancel(app.poll_after);app.poll();dialog=app.talk_alert_window
            self.assertIsNotNone(dialog);self.assertEqual(len(app.talk_alerts),2)
            text=app.talk_alert_text.get('1.0','end');self.assertIn('main.example:7300 · LZ3ZZ',text);self.assertIn('third',text)
            app.message_kind.set('Weather');app.message_search.set('no match')
            button=next(w for w in dialog.winfo_children()[0].winfo_children()[-1].winfo_children() if isinstance(w,ttk.Button) and w.cget('text')=='Show direct messages')
            button.invoke();self.assertIsNone(app.talk_alert_window);self.assertEqual(app.message_kind.get(),'Direct messages')
            self.assertEqual(app.message_search.get(),'');self.assertEqual(len(app.ann_tree.get_children()),3)
        finally:app.quit_app()

    def test_auto_open_direct_messages_from_additional_node_is_bounded(self):
        class Secondary:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.auto_open_talk.set(True)
            with patch('app.Connection',Secondary):
                ident=app.start_secondary_node('other.invalid',7301,'LZ3ZZ');item=app.secondary_nodes[ident]
                item['connection'].emit('dialect','CC Cluster');item['connection'].emit('status','Connected')
                item['connection'].emit('data',''.join(f'LZ3ZZ de K1ABC: message {i}\r\n' for i in range(51)))
                app.poll_secondary_nodes();self.assertEqual(len(app.talk_alerts),50)
                self.assertEqual(app.talk_alerts[-1][1],item['source'])
                self.assertNotIn('message 0\n',app.talk_alert_text.get('1.0','end'))
                self.assertIn('message 50',app.talk_alert_text.get('1.0','end'))
        finally:app.quit_app()

    def test_message_type_filter_keeps_arrival_classification(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.dialect='DXSpider';app.connection_target=('node',7300,'LZ3ZZ');app.chat_groups={'FOC'}
            lines=['To ALL de K1ABC: announce','WX de K1ABC: rain','LZ3ZZ de K1ABC: direct',
                   'FOC de K1ABC: group','30Oct2026@12:34:56 K1ABC -> FOC historic']
            app.events.put((app.generation,'data','\r\n'.join(lines)+'\r\n'))
            app.after_cancel(app.poll_after);app.poll();app.chat_groups.clear()
            for kind,expected in [('Announcements',lines[0]),('Weather',lines[1]),('Direct messages',lines[2]),
                                  ('Group chat',lines[3]),('History (unclassified)',lines[4])]:
                with self.subTest(kind=kind):
                    app.message_kind.set(kind);app.render_announcements()
                    rows=app.ann_tree.get_children();self.assertEqual(len(rows),1)
                    self.assertEqual(app.ann_tree.item(rows[0],'values')[0],expected)
            app.message_kind.set('All messages');app.render_announcements();self.assertEqual(len(app.ann_tree.get_children()),5)
        finally:app.quit_app()

    def test_message_source_and_text_filters_combine_without_changing_records(self):
        app=App();app.deiconify();app.geometry('780x560');app.notebook.select(5);app.update();app._save_settings=lambda:None
        try:
            first=('first.example',7300,'LZ3ZZ');second=('second.example',7301,'LZ3ZZ-1')
            app.announcements.extend([('LZ3ZZ de K1ABC: DX opening',first,'talk'),
                                      ('LZ3ZZ-1 de W1AW: DX opening',second,'talk'),
                                      ('WX de K1ABC: clear',first,'weather')])
            app.render_announcements();app.update()
            label=app.source_label(first);self.assertIn(label,tuple(app.message_source_box.cget('values')))
            app.message_source_filter.set(label);app.message_source_box.event_generate('<<ComboboxSelected>>')
            app.message_kind.set('Direct messages');app.message_search.set('dx OPEN');app.update()
            rows=app.ann_tree.get_children();self.assertEqual(len(rows),1)
            self.assertEqual(app.ann_tree.item(rows[0],'values')[0],'LZ3ZZ de K1ABC: DX opening')
            self.assertEqual(len(app.announcements),3)
            app.clear_announcements();self.assertEqual(app.message_source_filter.get(),label)
            app.message_source_filter.set('All sources');app.message_source_box.event_generate('<<ComboboxSelected>>');app.update()
            self.assertNotIn(label,tuple(app.message_source_box.cget('values')))
            for widget in (app.message_node_box,app.message_source_box,app.message_search_box):
                self.assertTrue(widget.winfo_ismapped());self.assertGreater(widget.winfo_width(),50)
                self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),app.winfo_rootx()+app.winfo_width())
        finally:app.quit_app()

    def test_message_filter_controls_visible_at_minimum_window_size(self):
        from tkinter import ttk
        app=App();app._save_settings=lambda:None
        def descendants(w):
            for child in w.winfo_children():yield child;yield from descendants(child)
        try:
            app.geometry('780x560');app.notebook.select(5);app.update()
            page=app.nametowidget(app.notebook.tabs()[5])
            widgets=[w for w in descendants(page) if isinstance(w,ttk.Combobox) and 'History (unclassified)' in w.cget('values')]
            self.assertEqual(len(widgets),1);self.assertTrue(widgets[0].winfo_ismapped())
            self.assertLessEqual(widgets[0].winfo_rootx()+widgets[0].winfo_width(),app.winfo_rootx()+app.winfo_width())
        finally:app.quit_app()

    def test_message_history_requests_target_selected_secondary(self):
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
                app.message_node.set('other.invalid:7301 · LZ3ZZ')
                app.request_announcements();app.request_weather()
                self.assertEqual(old.commands,['sh/ann','sh/wx']);self.assertEqual(main.commands,[])
                old.emit('data','WX de K1ABC: rain\r\n');app.poll_secondary_nodes()
                self.assertEqual(app.announcements[-1],('WX de K1ABC: rain',item['source'],'weather'))
                app._start_secondary_attempt(ident)
                with patch('app.messagebox.showerror') as error:app.request_weather();error.assert_called_once()
                self.assertEqual(item['connection'].commands,[]);self.assertEqual(main.commands,[])
        finally:app.quit_app()

    def test_message_history_available_with_secondary_only(self):
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
                self.assertEqual(str(app.ann_button['state']),'normal');app.request_announcements()
                self.assertEqual(conn.commands,['sh/ann'])
        finally:app.quit_app()

    def test_read_only_request_and_clear_view(self):
        app=App(); app.withdraw()
        try:
            fake=FakeConnection(); app.connection=fake; app.status.set('Connected')
            app.request_announcements(); self.assertEqual(fake.commands,['sh/ann'])
            app.announcements.extend(['first','second']); app.render_announcements()
            rows=app.ann_tree.get_children(); self.assertEqual([app.ann_tree.item(x,'values')[0] for x in rows],['second','first'])
            app.ann_tree.selection_set(rows[0]); app.show_announcement()
            app.announcements.append('third'); app.render_announcements()
            self.assertEqual(app.ann_detail.get('1.0','end-1c'),'second')
            app.clear_announcements(); self.assertEqual(len(app.ann_tree.get_children()),0)
            self.assertEqual(app.ann_detail.get('1.0','end-1c'),'')
            self.assertEqual(fake.commands,['sh/ann'])
        finally: app.quit_app()

    def test_command_entry_remains_visible_at_minimum_window_size(self):
        app=App()
        try:
            app.geometry('780x560'); app.update()
            self.assertTrue(app.command.winfo_ismapped())
            self.assertGreater(app.command.winfo_height(), 1)
            self.assertLessEqual(app.command.winfo_rooty()+app.command.winfo_height(), app.winfo_rooty()+app.winfo_height())
            app.notebook.select(5); app.update()
            self.assertTrue(app.ann_detail.winfo_ismapped())
            self.assertGreater(app.ann_detail.winfo_height(), 40)
            self.assertLessEqual(app.ann_detail.winfo_rooty()+app.ann_detail.winfo_height(), app.command.winfo_rooty())
        finally: app.quit_app()

if __name__ == '__main__': unittest.main()

