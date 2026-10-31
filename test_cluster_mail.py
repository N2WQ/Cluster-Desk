import unittest
from tkinter import ttk
from unittest.mock import patch

from app import App
from cluster_mail import MailDirectory, MailRead, NODE_PROMPT, SPIDER_PROMPT, directory_command, directory_fields, directory_reply_draft, read_command, split_prompt_prefix


class FakeConnection:
    def __init__(self): self.commands=[]
    def send(self, value): self.commands.append(value)
    def send_mail(self, recipient, subject, body, dialect='DXSpider'): self.commands.append((recipient,subject,body))
    def close(self): pass


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class MailTests(unittest.TestCase):
    def test_directory_draft_requires_recognized_sender_and_subject(self):
        self.assertEqual(directory_reply_draft('19 10860 ALL IK5PWJ 23-Oct 1717Z News','CC Cluster'),('IK5PWJ','Re: News'))
        self.assertEqual(directory_reply_draft('42 p 15 LZ3ZZ K1ABC-1 29-Oct 1200Z Re: Hello','DXSpider'),('K1ABC-1','Re: Hello'))
        for line in ('not a directory row','19 1 ALL K1ABC/P 23-Oct 1717Z News','19 1 ALL K1ABC 23-Oct 1717Z café','19 1 ALL ALL 23-Oct 1717Z News'):
            with self.subTest(line=line):self.assertIsNone(directory_reply_draft(line,'CC Cluster'))

    def test_directory_draft_is_editable_and_does_not_send(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            conn=FakeConnection();window.connection=conn;window.status.set('Connected');window.dialect='CC Cluster';window.open_mail()
            window.mail_directory.dialect='CC Cluster';window.mail_directory.rows=[('19','19 10860 ALL IK5PWJ 23-Oct 1717Z News')];window.render_mail()
            window.mail_tree.selection_set(window.mail_tree.get_children()[0])
            button=next(w for w in descendants(window.mail_window) if isinstance(w,ttk.Button) and w.cget('text')=='Draft to sender…')
            with patch('app.messagebox.showerror') as error:button.invoke();error.assert_called_once()
            window.mail_directory.state='complete'
            with patch.object(window,'open_mail_compose') as compose:
                button.invoke();compose.assert_called_once_with('IK5PWJ','Re: News')
            self.assertEqual(conn.commands,[])
            window.generation+=1
            with patch.object(window,'open_mail_compose') as compose, patch('app.messagebox.showerror') as error:
                button.invoke();error.assert_called_once();compose.assert_not_called()
        finally:window.quit_app()
    def test_directory_commands_and_subject_validation(self):
        self.assertEqual(directory_command('CC Cluster','Own'),'dir/own')
        self.assertEqual(directory_command('CC Cluster','New'),'dir/new')
        self.assertEqual(directory_command('CC Cluster','Bulletins'),'dir/bulletin')
        self.assertEqual(directory_command('CC Cluster','Subject','DXNEWS'),'dir/subject DXNEWS')
        self.assertEqual(directory_command('DXSpider','Own'),'directory own')
        self.assertEqual(directory_command('DXSpider','New'),'directory new')
        self.assertEqual(directory_command('DXSpider','Subject','IOTA'),'directory subject IOTA')
        self.assertEqual(directory_command('DXSpider','From','lz3zz-1','050'),'directory from LZ3ZZ-1 50')
        self.assertEqual(directory_command('DXSpider','To','K1ABC','500'),'directory to K1ABC 500')
        self.assertEqual(directory_command('DXSpider','Recent','','25'),'dir 25')
        for dialect,kind,value,count in [('DXSpider','From','bad call',''),('DXSpider','To','K1ABC\nBYE',''),('DXSpider','Recent','','0'),('DXSpider','Recent','','501'),('DXSpider','Recent','','1\nBYE'),('CC Cluster','Recent','','10'),('CC Cluster','From','K1ABC','')]:
            with self.subTest(kind=kind,value=value,count=count),self.assertRaises(ValueError): directory_command(dialect,kind,value,count)
        for dialect,kind,subject in [('DXSpider','Bulletins',''),('Other','Recent',''),('CC Cluster','Subject',''),('DXSpider','Subject','abc\nBYE'),('DXSpider','Subject','two words')]:
            with self.subTest(dialect=dialect,kind=kind),self.assertRaises(ValueError): directory_command(dialect,kind,subject)

    def test_dxspider_own_directory_uses_matching_sent_event(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='DXSpider';window.open_mail()
            dialog=window.mail_window;widgets=list(descendants(dialog))
            selector=next(w for w in widgets if isinstance(w,ttk.Combobox));selector.set('Own')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Get mail list').invoke()
            self.assertEqual(fake.commands,['directory own'])
            window.events.put((window.generation,'sent','dir'))
            window.after_cancel(window.poll_after);window.poll();self.assertEqual(window.mail_directory.state,'partial')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Get mail list').invoke()
            window.events.put((window.generation,'sent','directory own'))
            window.events.put((window.generation,'data','No messages found\r\nLZ3ZZ de NODE dxspider >'))
            window.after_cancel(window.poll_after);window.poll();self.assertEqual(window.mail_directory.state,'complete')
        finally:window.quit_app()

    def test_delete_selected_requires_confirmation_and_current_connection(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='DXSpider';window.open_mail()
            window.mail_directory.rows=[('42','42 p 15 LZ3ZZ K1ABC 29-Oct 1200Z hello')];window.render_mail()
            button=next(w for w in descendants(window.mail_window) if isinstance(w,ttk.Button) and w.cget('text')=='Delete selected…')
            with patch('app.messagebox.showerror') as error:
                button.invoke();error.assert_called_once()
            window.mail_tree.selection_set(window.mail_tree.get_children()[0])
            with patch('app.messagebox.askyesno',return_value=False): button.invoke()
            self.assertEqual(fake.commands,[])
            def reconnect_during_confirmation(*args,**kwargs):
                window.generation+=1;return True
            with patch('app.messagebox.askyesno',side_effect=reconnect_during_confirmation), patch('app.messagebox.showerror') as error:
                button.invoke();error.assert_called_once()
            self.assertEqual(fake.commands,[])
            window.mail_window.destroy();window.mail_window=None;window.open_mail()
            window.mail_directory.rows=[('42','42 p 15 LZ3ZZ K1ABC 29-Oct 1200Z hello')];window.render_mail()
            window.mail_tree.selection_set(window.mail_tree.get_children()[0]);window.mail_directory.state='waiting'
            button=next(w for w in descendants(window.mail_window) if isinstance(w,ttk.Button) and w.cget('text')=='Delete selected…')
            with patch('app.messagebox.showerror') as error, patch('app.messagebox.askyesno') as confirm:
                button.invoke();error.assert_called_once();confirm.assert_not_called()
            self.assertEqual(fake.commands,[])
            window.mail_window.destroy();window.mail_window=None;window.open_mail()
            window.mail_directory.rows=[('42','42 p 15 LZ3ZZ K1ABC 29-Oct 1200Z hello')];window.render_mail()
            window.mail_tree.selection_set(window.mail_tree.get_children()[0])
            button=next(w for w in descendants(window.mail_window) if isinstance(w,ttk.Button) and w.cget('text')=='Delete selected…')
            with patch('app.messagebox.askyesno',return_value=True) as confirm: button.invoke()
            self.assertEqual(fake.commands,['KILL 42']);self.assertEqual(confirm.call_args.kwargs['default'],'no')
            self.assertIn('Check Activity',window.mail_note.get())
            self.assertEqual(len(window.mail_tree.get_children()),1)
        finally:window.quit_app()

    def test_cc_delete_selected_uses_documented_command_and_checks_connection(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='CC Cluster';window.open_mail()
            window.mail_directory.rows=[('19','19 10860 ALL IK5PWJ 23-Oct 1717Z subject')];window.render_mail()
            window.mail_tree.selection_set(window.mail_tree.get_children()[0])
            button=next(w for w in descendants(window.mail_window) if isinstance(w,ttk.Button) and w.cget('text')=='Delete selected…')
            with patch('app.messagebox.askyesno',return_value=False):button.invoke()
            self.assertEqual(fake.commands,[])
            def change_dialect(*args,**kwargs):window.dialect='DXSpider';return True
            with patch('app.messagebox.askyesno',side_effect=change_dialect), patch('app.messagebox.showerror') as error:
                button.invoke();error.assert_called_once()
            self.assertEqual(fake.commands,[]);window.dialect='CC Cluster'
            with patch('app.messagebox.askyesno',return_value=True):button.invoke()
            self.assertEqual(fake.commands,['DEL 19']);self.assertEqual(len(window.mail_tree.get_children()),1)
            self.assertIn('Check Activity',window.mail_note.get())
        finally:window.quit_app()

    def test_mail_directory_fields_and_selection_survive_refresh(self):
        cc='19 10860 ALL IK5PWJ 23-Oct 1717Z 425 DX News #1851'
        spider='42 p   15    LZ3ZZ    K1ABC 29-Oct 1200Z Hello there'
        self.assertEqual(directory_fields(cc,'CC Cluster'),('19','','10860','ALL','IK5PWJ','23-Oct 1717Z','425 DX News #1851'))
        self.assertEqual(directory_fields(spider,'DXSpider'),('42','p','15','LZ3ZZ','K1ABC','29-Oct 1200Z','Hello there'))
        self.assertEqual(directory_fields('unknown format','DXSpider')[-1],'unknown format')
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            window.deiconify();window.update()
            window.connection=FakeConnection();window.status.set('Connected');window.dialect='DXSpider';window.open_mail()
            window.mail_directory.rows=[('42',spider)];window.render_mail()
            item=window.mail_tree.get_children()[0];window.mail_tree.selection_set(item);window.update()
            self.assertEqual(window.mail_tree.item(item,'values')[3:5],('LZ3ZZ','K1ABC'))
            self.assertEqual(window.mail_detail.get(),spider)
            window.mail_directory.rows.append(('43','43 p 20 W1AW K1ABC 29-Oct 1210Z next'))
            window.render_mail();window.update()
            selected=window.mail_tree.selection();self.assertEqual(window.mail_tree.item(selected[0],'tags')[0],'42')
            self.assertEqual(window.mail_detail.get(),spider)
            long_line='44 p 20 W1AW K1ABC 29-Oct 1210Z '+('long subject ' * 400)
            window.mail_directory.rows=[('44',long_line)];window.render_mail()
            window.mail_tree.selection_set(window.mail_tree.get_children()[0]);window.update()
            self.assertEqual(window.mail_detail_text.get('1.0','end-1c'),long_line)
            close=next(w for w in descendants(window.mail_window) if isinstance(w,ttk.Button) and w.cget('text')=='Close')
            self.assertTrue(close.winfo_ismapped())
            self.assertLessEqual(close.winfo_rooty()+close.winfo_height(),window.mail_window.winfo_rooty()+window.mail_window.winfo_height())
        finally:window.quit_app()

    def test_dxspider_mail_list_by_sender_with_count(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='DXSpider';window.open_mail()
            widgets=list(descendants(window.mail_window))
            next(w for w in widgets if isinstance(w,ttk.Combobox)).set('From')
            entries=[w for w in widgets if type(w) is ttk.Entry]
            next(w for w in entries if int(w.cget('width'))==20).insert(0,'k1abc')
            next(w for w in entries if int(w.cget('width'))==5).insert(0,'50')
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Get mail list').invoke()
            self.assertEqual(fake.commands,['directory from K1ABC 50'])
            window.events.put((window.generation,'sent','directory from K1ABC 50'))
            window.events.put((window.generation,'data','42 p 15 LZ3ZZ K1ABC 29-Oct 1200Z hello\r\nLZ3ZZ de NODE dxspider >'))
            window.after_cancel(window.poll_after);window.poll()
            self.assertEqual(window.mail_directory.state,'complete');self.assertEqual(window.mail_directory.rows[0][0],'42')
        finally:window.quit_app()

    def test_other_command_interrupts_mail_directory_and_message_read(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            window.connection=FakeConnection();window.status.set('Connected');window.dialect='DXSpider';window.open_mail()
            window.mail_directory.queued('DXSpider','dir')
            window.events.put((window.generation,'sent','dir'))
            window.events.put((window.generation,'data','42 p 15 LZ3ZZ K1ABC 29-Oct 1200Z hello\r\n'))
            window.events.put((window.generation,'sent','sh/dx'))
            window.events.put((window.generation,'data','LZ3ZZ de NODE dxspider >'))
            window.after_cancel(window.poll_after);window.poll()
            self.assertEqual(window.mail_directory.state,'partial');self.assertIn('Partial',window.mail_note.get())
            window.open_mail_reader('42')
            window.events.put((window.generation,'sent','READ 42'))
            window.events.put((window.generation,'data','Msg: 42 From: K1ABC Date: 29-Oct-2026 Subj: Hello\r\n'))
            window.events.put((window.generation,'sent','sh/wx'))
            window.events.put((window.generation,'data','LZ3ZZ de NODE dxspider >'))
            window.after_cancel(window.poll_after);window.poll()
            self.assertEqual(window.mail_read.state,'partial');self.assertIn('Incomplete',window.mail_read_note.get())
            self.assertEqual(str(window.mail_reply_button['state']),'disabled')
        finally:window.quit_app()

    def test_prompt_coalesced_with_response_is_split(self):
        self.assertEqual(split_prompt_prefix('LZ3ZZ de NODE dxspider >Msg: 42 From: K1ABC'), ['LZ3ZZ de NODE dxspider >','Msg: 42 From: K1ABC'])
        self.assertEqual(split_prompt_prefix('(LZ3ZZ) de NODE dxspider >Msg: 42 From: K1ABC'), ['(LZ3ZZ) de NODE dxspider >','Msg: 42 From: K1ABC'])
        self.assertEqual(split_prompt_prefix('NODE CCC >DX Spot Orig Cty Filter/All set OFF'), ['NODE CCC >','DX Spot Orig Cty Filter/All set OFF'])
        self.assertEqual(split_prompt_prefix('unrelated text'), ['unrelated text'])

    def test_dxspider_away_prompt_requires_complete_valid_form(self):
        prompt='(LZ3ZZ-1) de NODE 30-Oct-2026 1234Z dxspider >'
        self.assertIsNotNone(NODE_PROMPT.fullmatch(prompt))
        self.assertIsNotNone(SPIDER_PROMPT.fullmatch(prompt))
        for line in ('(LZ3ZZ de NODE dxspider >','(LZ3ZZ) de NODE CCC >',
                     'To ALL: (LZ3ZZ) de NODE dxspider >','(LZ3ZZ) de NODE dxspider > extra'):
            with self.subTest(line=line):self.assertIsNone(NODE_PROMPT.fullmatch(line))

    def test_read_capture_requires_selected_header_and_is_bounded(self):
        capture=MailRead('42');capture.sent()
        self.assertFalse(capture.feed('LZ3ZZ de NODE dxspider >'))
        self.assertFalse(capture.feed('Msg: 43 From: K1ABC Date: 29-Oct 1200Z Subj: other'))
        capture.feed('Msg: 42 From: K1ABC Date: 29-Oct 1200Z Subj: hello')
        for _ in range(1001): capture.feed('body')
        capture.feed('LZ3ZZ de NODE dxspider >')
        self.assertEqual(capture.state,'complete');self.assertEqual(len(capture.lines),1000);self.assertTrue(capture.truncated)
        missing=MailRead('42');missing.sent();missing.feed('Msg 42 not found');missing.feed('LZ3ZZ de NODE dxspider >')
        self.assertEqual(missing.state,'complete');self.assertEqual(missing.lines,['Msg 42 not found'])
        partial=MailRead('42');partial.sent();partial.feed('Msg: 42 From: K1ABC Date: 29-Oct 1200Z Subj: hello');partial.feed('first line');partial.interrupt()
        self.assertEqual(partial.state,'partial');self.assertEqual(partial.lines[-1],'first line')

    def test_dxspider_translated_read_errors_match_selected_number(self):
        errors=(
            'Msg 42 not found','Msg 42 not available',
            'Bericht 42 niet gevonden','Bericht 42 niet beschikbaar',
            'Message 42 introuvable','Message 42 non disponible',
            'Mensaje 42 no encontrado','Mensaje 42 no disponible',
            'Msg 42 nicht gefunden','Msg 42 nicht verfuegbar',
            'Messaggio 42 non trovato','Messaggio 42 non disponibile',
            'Zprava 42 nenalezena','Msg 42 neni k dispozici',
            'Msg 42 no foi encontrada','Msg 42 no est disponivel',
        )
        for line in errors:
            with self.subTest(line=line):
                capture=MailRead('42');capture.sent()
                self.assertFalse(capture.feed(line.replace('42','43')))
                self.assertTrue(capture.feed(line));capture.feed('LZ3ZZ de NODE dxspider >')
                self.assertEqual(capture.state,'complete');self.assertEqual(capture.lines,[line])
                self.assertIsNone(capture.reply_draft())
        capture=MailRead('42');capture.sent()
        self.assertFalse(capture.feed('Msg '+('9'*5000)+' not found'))
        self.assertFalse(capture.feed('Msg: '+('9'*5000)+' From: K1ABC Date: 29-Oct 1200Z Subj: hi'))
        self.assertEqual(capture.state,'waiting')

    def test_dxspider_read_window_handles_fragmented_body_and_prompt(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='DXSpider'
            window.open_mail();dialog=window.mail_window
            window.mail_directory.rows=[('42','42 p 15 LZ3ZZ K1ABC 29-Oct 1200Z hello')];window.render_mail()
            window.mail_tree.selection_set(window.mail_tree.get_children()[0])
            next(w for w in descendants(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Read selected').invoke()
            self.assertEqual(fake.commands,['READ 42']);self.assertIsNotNone(window.mail_reader)
            window.events.put((window.generation,'sent','READ 42'))
            window.events.put((window.generation,'data','LZ3ZZ de NODE dxspider >'))
            window.after_cancel(window.poll_after);window.poll()
            self.assertEqual(window.mail_read.state,'waiting');self.assertEqual(window.partial,'')
            window.events.put((window.generation,'data','Msg: 42 From: K1ABC Date: 29-Oct 1200Z Subj: hello\r\npart'))
            window.after_cancel(window.poll_after);window.poll()
            self.assertEqual(window.mail_read.state,'receiving');self.assertEqual(len(window.mail_read.lines),1)
            window.events.put((window.generation,'data','ial body\r\nLZ3ZZ de NODE dxspider >'))
            window.after_cancel(window.poll_after);window.poll()
            self.assertEqual(window.mail_read.state,'complete')
            self.assertIn('partial body',window.mail_read_text.get('1.0','end'))
            self.assertIn('response received',window.mail_read_note.get())
        finally: window.quit_app()

    def test_reply_draft_requires_complete_header_and_never_sends_on_open(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='DXSpider'
            window.open_mail_reader('42');reader=window.mail_reader;capture=window.mail_read
            button=next(w for w in descendants(reader) if isinstance(w,ttk.Button) and w.cget('text')=='Reply…')
            capture.sent();capture.feed('Msg: 42 From: K1ABC Date: 29-Oct 1200Z Subj: hello')
            window.render_mail_reader();self.assertEqual(str(button['state']),'disabled')
            capture.feed('body');capture.feed('LZ3ZZ de NODE dxspider >');window.render_mail_reader()
            self.assertEqual(str(button['state']),'normal');button.invoke();self.assertEqual(fake.commands,[])
            dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel' and w is not reader)
            entries=[w for w in descendants(dialog) if type(w) is ttk.Entry]
            self.assertEqual([w.get() for w in entries],['K1ABC','Re: hello'])
            self.assertEqual(next(w for w in descendants(dialog) if w.winfo_class()=='Text').get('1.0','end-1c'),'')
            dialog.destroy();window.generation+=1
            with patch('app.messagebox.showerror') as error:button.invoke();error.assert_called_once()
            self.assertEqual(fake.commands,[])
        finally:window.quit_app()

    def test_cc_reply_draft_from_observed_header(self):
        capture=MailRead('22','CC Cluster');capture.sent()
        capture.feed('To ALL de K1ABC: other traffic')
        capture.feed('Msg: 22 From: LZ3ZZ Date: 30-Oct-2026 0833Z Subj: ClusterDesk self test 1030')
        capture.feed('Cluster Desk self-addressed mail compatibility test.')
        self.assertIsNone(capture.reply_draft())
        capture.feed('LZ3ZZ de N2WQ-1 30-Oct-2026 0838Z   CCC >')
        self.assertEqual(capture.reply_draft(),('LZ3ZZ','Re: ClusterDesk self test 1030'))
        capture.truncated=True;self.assertIsNone(capture.reply_draft())

    def test_cc_observed_reply_opens_editable_draft_without_sending(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='CC Cluster'
            window.open_mail_reader('22');reader=window.mail_reader;capture=window.mail_read
            capture.sent();capture.feed('Msg: 22 From: LZ3ZZ Date: 30-Oct-2026 0833Z Subj: ClusterDesk self test 1030')
            capture.feed('body');capture.feed('LZ3ZZ de N2WQ-1 30-Oct-2026 0838Z CCC >')
            window.render_mail_reader();self.assertEqual(str(window.mail_reply_button['state']),'normal')
            window.mail_reply_button.invoke();self.assertEqual(fake.commands,[])
            dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel' and w is not reader)
            entries=[w for w in descendants(dialog) if type(w) is ttk.Entry]
            self.assertEqual([w.get() for w in entries],['LZ3ZZ','Re: ClusterDesk self test 1030'])
        finally:window.quit_app()

    def test_cc_reply_requires_one_matching_valid_header(self):
        for lines in (
            ['Msg: 23 From: LZ3ZZ Date: 30-Oct-2026 0833Z Subj: wrong'],
            ['Msg: 22 From: LZ3ZZ/P Date: 30-Oct-2026 0833Z Subj: unsupported'],
            ['Msg: 22 From: LZ3ZZ Date: x Subj: one', 'Msg: 22 From: K1ABC Date: x Subj: two'],
        ):
            with self.subTest(lines=lines):
                capture=MailRead('22','CC Cluster');capture.sent()
                for line in lines:capture.feed(line)
                capture.feed('LZ3ZZ de NODE CCC >')
                self.assertIsNone(capture.reply_draft())

    def test_reply_draft_ignores_errors_and_invalid_senders(self):
        for header in ('Msg 42 not found','Msg: 42 From: K1ABC/P Date: 29-Oct 1200Z Subj: hi'):
            capture=MailRead('42');capture.sent();capture.feed(header);capture.feed('LZ3ZZ de NODE dxspider >')
            self.assertIsNone(capture.reply_draft())
        capture=MailRead('42');capture.sent();capture.feed('Msg: 42 From: K1ABC Date: 29-Oct 1200Z Subj: Re: hi');capture.feed('LZ3ZZ de NODE dxspider >')
        self.assertEqual(capture.reply_draft(),('K1ABC','Re: hi'))

    def test_cc_mail_reader_shows_unverified_raw_response_and_ignores_prior_prompt(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='CC Cluster';window.open_mail()
            dialog=window.mail_window;entry=next(w for w in descendants(dialog) if type(w) is ttk.Entry and int(w.cget('width'))==12)
            entry.insert(0,'42');next(w for w in descendants(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Read number').invoke()
            self.assertEqual(fake.commands,['READ 42']);self.assertIsNotNone(window.mail_reader)
            window.events.put((window.generation,'sent','READ 42'))
            window.events.put((window.generation,'data','LZ3ZZ de NODE CCC >'))
            window.after_cancel(window.poll_after);window.poll();self.assertEqual(window.mail_read.state,'waiting')
            window.events.put((window.generation,'data','Msg: 42 From: K1ABC\r\nbody text\r\nLZ3ZZ de NODE CCC >'))
            window.after_cancel(window.poll_after);window.poll()
            self.assertEqual(window.mail_read.state,'complete');self.assertIn('body text',window.mail_read_text.get('1.0','end'))
            self.assertIn('may be absent',window.mail_read_note.get());self.assertEqual(str(window.mail_reply_button['state']),'disabled')
        finally:window.quit_app()

    def test_cc_directory_ignores_earlier_prompt(self):
        capture=MailDirectory();capture.queued('CC Cluster');capture.sent()
        self.assertFalse(capture.feed('NODE CCC >'))
        capture.feed('   Msg   Size       To    From   Date  Time  Subject')
        capture.feed('    19  10860      ALL   IK5PWJ 23-Oct 1717Z 425 DX News #1851')
        capture.feed('LZ3ZZ de NODE CCC >')
        self.assertEqual(capture.state,'complete');self.assertEqual(capture.rows[0][0],'19')

    def test_dxspider_directory_and_empty_response(self):
        capture=MailDirectory();capture.queued('DXSpider');capture.sent()
        capture.feed('    42 p   15    LZ3ZZ    K1ABC 29-Oct 1200Z Hello')
        capture.feed('LZ3ZZ de NODE dxspider >')
        self.assertEqual(capture.rows[0][0],'42');self.assertEqual(capture.state,'complete')
        capture.queued('DXSpider');capture.sent();capture.feed('No messages found');capture.feed('NODE dxspider >')
        self.assertEqual(capture.state,'complete');self.assertEqual(capture.rows,[])

    def test_dxspider_directory_accepts_blank_subject_and_prompt_text_in_subject(self):
        capture=MailDirectory();capture.queued('DXSpider');capture.sent()
        empty='    42 p   15    LZ3ZZ    K1ABC 29-Oct 1200Z'
        tricky='    43 p   15    LZ3ZZ    K1ABC 29-Oct 1200Z dxspider > status'
        capture.feed(empty);capture.feed(tricky)
        self.assertEqual(capture.state,'receiving')
        self.assertEqual([row[0] for row in capture.rows],['42','43'])
        self.assertEqual(directory_fields(empty,'DXSpider'),('42','p','15','LZ3ZZ','K1ABC','29-Oct 1200Z',''))
        capture.feed('LZ3ZZ de NODE dxspider >');self.assertEqual(capture.state,'complete')

    def test_dxspider_localized_empty_directories(self):
        for text in ('Aucun message trouvé !','No hay mensajes nuevos','Keine Nachricht gefunden',
                     'Nessun messaggio trovato','Zadne zpravy nebyly nalezeny','Nenhumas mensagens encontradas'):
            with self.subTest(text=text):
                capture=MailDirectory();capture.queued('DXSpider');capture.sent();capture.feed(text)
                self.assertEqual(capture.state,'receiving')
                capture.feed('LZ3ZZ de NODE dxspider >');self.assertEqual(capture.state,'complete')
                self.assertEqual(capture.rows,[])

    def test_read_command_validation(self):
        self.assertEqual(read_command(' 0019 '),'READ 19')
        for text in ('0','','12\nDX 1 X','-2','a','1234567890'):
            with self.subTest(text=text),self.assertRaises(ValueError):read_command(text)

    def test_directory_bound_and_interruption(self):
        capture=MailDirectory();capture.queued('CC Cluster');capture.sent()
        capture.feed('Msg Size To From Date Time Subject')
        for index in range(501): capture.feed(f'{index+1} 10 ALL K1ABC 23-Oct 1717Z subject')
        self.assertEqual(len(capture.rows),500);self.assertTrue(capture.truncated)
        capture.interrupt();self.assertEqual(capture.state,'partial')

    def test_window_requests_only_selected_message(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='CC Cluster'
            window.open_mail();dialog=window.mail_window
            buttons={w.cget('text'):w for w in descendants(dialog) if isinstance(w,ttk.Button)}
            buttons['Get mail list'].invoke();self.assertEqual(fake.commands,['dir'])
            window.events.put((window.generation,'sent','dir'))
            window.events.put((window.generation,'data','Msg Size To From Date Time Subject\r\n19 10860 ALL IK5PWJ 23-Oct 1717Z news\r\nNODE CCC >\r\n'))
            window.after_cancel(window.poll_after);window.poll()
            self.assertEqual(window.mail_directory.state,'complete')
            row=window.mail_tree.get_children()[0];window.mail_tree.selection_set(row)
            buttons['Read selected'].invoke()
            self.assertEqual(fake.commands,['dir','READ 19']);self.assertIsNone(window.mail_window)
        finally:window.quit_app()

    def test_manual_read_validates_number_and_connection(self):
        for dialect in ('CC Cluster','DXSpider'):
            with self.subTest(dialect=dialect):
                window=App();window.withdraw();window._save_settings=lambda:None
                try:
                    fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect=dialect
                    window.open_mail();dialog=window.mail_window;widgets=list(descendants(dialog))
                    entry=next(w for w in widgets if type(w) is ttk.Entry and int(w.cget('width'))==12)
                    button=next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Read number')
                    entry.insert(0,'0')
                    with patch('app.messagebox.showerror') as error:button.invoke();error.assert_called_once()
                    self.assertEqual(fake.commands,[])
                    entry.delete(0,'end');entry.insert(0,'0042')
                    window.generation+=1
                    with patch('app.messagebox.showerror') as error:button.invoke();error.assert_called_once()
                    self.assertEqual(fake.commands,[])
                    window.generation-=1;button.invoke()
                    self.assertEqual(fake.commands,['READ 42'])
                    self.assertIsNone(window.mail_window)
                    if dialect=='DXSpider':self.assertEqual(window.mail_read.number,42)
                    else:self.assertEqual(window.mail_read.dialect,'CC Cluster')
                finally:window.quit_app()

    def test_stale_window_cannot_issue_commands(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='CC Cluster';window.open_mail()
            button=next(w for w in descendants(window.mail_window) if isinstance(w,ttk.Button) and w.cget('text')=='Get mail list')
            window.generation+=1
            with patch('app.messagebox.showerror') as error:button.invoke();error.assert_called_once()
            self.assertEqual(fake.commands,[])
        finally:window.quit_app()

    def test_dxspider_compose_passes_explicit_public_and_receipt_choices(self):
        class CaptureConnection(FakeConnection):
            def send_mail(self,recipient,subject,body,dialect='DXSpider',**options):
                self.commands.append((recipient,subject,body,dialect,options))
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=CaptureConnection();window.connection=fake;window.status.set('Connected');window.dialect='DXSpider'
            window.open_mail_compose();dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
            entries=[w for w in descendants(dialog) if type(w) is ttk.Entry]
            entries[0].insert(0,'ALL,EU');entries[1].insert(0,'Subject')
            next(w for w in descendants(dialog) if w.winfo_class()=='Text').insert('1.0','hello')
            options=[w for w in descendants(dialog) if isinstance(w,ttk.Checkbutton)]
            self.assertEqual(len(options),2)
            for option in options: option.invoke()
            next(w for w in descendants(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Send mail').invoke()
            self.assertEqual(fake.commands,[('ALL,EU','Subject','hello','DXSpider',{'bulletin':True,'receipt':True})])
        finally:window.quit_app()

    def test_cc_compose_form_sends_only_after_confirmation_and_binds_dialect(self):
        class CaptureConnection(FakeConnection):
            def send_mail(self,recipient,subject,body,dialect='DXSpider'):
                self.commands.append((recipient,subject,body,dialect))
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=CaptureConnection();window.connection=fake;window.status.set('Connected');window.dialect='CC Cluster';window.open_mail()
            compose=next(w for w in descendants(window.mail_window) if isinstance(w,ttk.Button) and w.cget('text')=='Compose…')
            compose.invoke();dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel' and w is not window.mail_window)
            widgets=list(descendants(dialog));entries=[w for w in widgets if isinstance(w,ttk.Entry)]
            entries[0].insert(0,'K1ABC');entries[1].insert(0,'Subject')
            next(w for w in widgets if w.winfo_class()=='Text').insert('1.0','hello')
            self.assertEqual(fake.commands,[])
            button=next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Send mail')
            window.dialect='DXSpider'
            with patch('app.messagebox.showerror') as error:button.invoke();error.assert_called_once()
            self.assertEqual(fake.commands,[]);window.dialect='CC Cluster';button.invoke()
            self.assertEqual(fake.commands,[('K1ABC','Subject','hello','CC Cluster')])
        finally:window.quit_app()

    def test_compose_waits_for_user_and_rejects_changed_connection(self):
        window=App();window.withdraw();window._save_settings=lambda:None
        try:
            fake=FakeConnection();window.connection=fake;window.status.set('Connected');window.dialect='DXSpider'
            window.open_mail_compose()
            dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
            widgets=list(descendants(dialog)); entries=[w for w in widgets if isinstance(w,ttk.Entry)]
            entries[0].insert(0,'K1ABC');entries[1].insert(0,'Subject')
            body=next(w for w in widgets if w.winfo_class()=='Text');body.insert('1.0','hello')
            self.assertEqual(fake.commands,[])
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Send mail').invoke()
            self.assertEqual(fake.commands,[('K1ABC','Subject','hello')])
            window.open_mail_compose();dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
            button=next(w for w in descendants(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Send mail')
            window.generation+=1
            with patch('app.messagebox.showerror') as error:button.invoke();error.assert_called_once()
            self.assertEqual(len(fake.commands),1);dialog.destroy()
        finally:window.quit_app()


if __name__=='__main__':unittest.main()


