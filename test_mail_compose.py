import socket
import threading
import time
import unittest

from mail_compose import MailCompose
from protocol import Connection


class MailComposeTests(unittest.TestCase):
    def test_saved_response_requires_complete_exact_recipient(self):
        mail=MailCompose('K1ABC','subject','body')
        mail.feed('Enter Subject (30 characters):\r\n')
        mail.feed('Enter lines of text, /EX to send or /ABORT to exit\r\n')
        self.assertIsNone(mail.feed('Message no 12 saved and directed to K1ABC'))
        self.assertFalse(mail.saved)
        self.assertEqual(mail.feed('-1\r\nLZ3ZZ de NODE dxspider >\r\n'),[])
        self.assertFalse(mail.saved)

    def test_prompt_order_and_validation(self):
        mail=MailCompose('k1abc','Subject','first\nsecond')
        self.assertEqual(mail.start(),'SEND K1ABC')
        self.assertIsNone(mail.feed('To ALL de X: Enter Subject (30 characters):\r\n'))
        self.assertIsNone(mail.feed('Enter Sub'))
        self.assertEqual(mail.feed('ject (30 characters):\r\n'),['Subject'])
        self.assertIsNone(mail.feed('some unrelated data\r\n'))
        self.assertEqual(mail.feed('Enter lines of text, /EX to send or /ABORT to exit\r\n'),['first','second','/EX'])
        self.assertEqual(mail.feed('Message no 12 saved and directed to K1ABC\r\nLZ3ZZ de NODE dxspider >\r\n'),[])
        self.assertTrue(mail.saved);self.assertEqual(mail.state,'complete')
        for args in [('K1ABC','x','/EX'),('K1ABC','x','/abort'),('K1ABC','x','hi\n/quit'),('K1ABC','x'*31,'hello'),('K1ABC\nBYE','x','hello')]:
            with self.subTest(args=args),self.assertRaises(ValueError):MailCompose(*args)

    def test_dxspider_registration_rejection_requires_complete_response_and_prompt(self):
        mail=MailCompose('LZ3ZZ','subject','body')
        self.assertIsNone(mail.feed('Sorry, you need to be regis'))
        self.assertIsNone(mail.feed('tered (SP N2WQ to register)\r\n'))
        self.assertEqual(mail.feed('LZ3ZZ de N2WQ-73 30-Oct-2026 0842Z dxspider >'),[])
        self.assertEqual(mail.state,'rejected');self.assertFalse(mail.saved)
        mail=MailCompose('LZ3ZZ','subject','body')
        self.assertIsNone(mail.feed('To ALL de K1ABC: Sorry, you need to be registered (SP N2WQ to register)\r\nLZ3ZZ de NODE dxspider >'))
        self.assertEqual(mail.state,'subject')

    def test_dxspider_translated_rejections_and_invalid_recipient(self):
        messages = (
            ("Désolé, vous devez d'abord vous enregistrer (SP N2WQ pour ce faire)", 'registration required'),
            ("Lo siento, necesitas registrarte (SP N2WQ para registrarte)', ", 'registration required'),
            ('Sorry, Du musst Dich registrieren (SP N2WQ to register)', 'registration required'),
            ('Spiacente, devi essere registrato  (SP N2WQ per registrarti)', 'registration required'),
            ('Lituji, musis byt registrovan (SP N2WQ k registraci)', 'registration required'),
            ('Desculpe, voc precisa ser registado (SP N2WQ para registrar)', 'registration required'),
            ('*** No station specified ***', 'no valid recipient'),
            ('*** Aucune station spécifiée ***', 'no valid recipient'),
            ('*** No se ha especificado una estación ***', 'no valid recipient'),
            ('*** Nessuna stazione specificata ***', 'no valid recipient'),
            ('*** Geen station gespecifiseerd ***', 'no valid recipient'),
            ('*** Keine Station angegeben ***', 'no valid recipient'),
            ('*** Zadna stanice specifikovana ***', 'no valid recipient'),
            ('*** Nenhuma estao especificada ***', 'no valid recipient'),
        )
        for response, reason in messages:
            with self.subTest(response=response):
                mail=MailCompose('LZ3ZZ','subject','body')
                self.assertEqual(mail.feed(response+'\r\nLZ3ZZ de NODE dxspider >'),[])
                self.assertEqual(mail.state,'rejected');self.assertEqual(mail.rejection,reason)
        mail=MailCompose('LZ3ZZ','subject','body')
        self.assertIsNone(mail.feed('To ALL de K1ABC: *** No station specified ***\r\nLZ3ZZ de NODE dxspider >'))
        self.assertEqual(mail.state,'subject')
        mail=MailCompose('LZ3ZZ','subject','body')
        self.assertIsNone(mail.feed('LZ3ZZ de NODE dxspider >\r\n'))
        self.assertEqual(mail.feed('*** No station specified ***\r\nLZ3ZZ de NODE dxspider >'),[])
        self.assertEqual(mail.rejection,'no valid recipient')

    def test_dxspider_public_and_read_receipt_options(self):
        self.assertEqual(MailCompose('K1ABC','subject','body').start(),'SEND K1ABC')
        self.assertEqual(MailCompose('K1ABC','subject','body',receipt=True).start(),'SEND RR K1ABC')
        self.assertEqual(MailCompose('ALL','subject','body',bulletin=True).start(),'SEND NOPRIVATE ALL')
        self.assertEqual(MailCompose('ALL','subject','body',bulletin=True,receipt=True).start(),'SEND NOPRIVATE RR ALL')
        for bad in ('RR','PRIVATE','NOPRIVATE','COPY','PRINTER','NOPX'):
            with self.subTest(recipient=bad),self.assertRaises(ValueError):MailCompose(bad,'subject','body')
        for options in ({'bulletin':True},{'receipt':True}):
            with self.subTest(options=options),self.assertRaises(ValueError):MailCompose('K1ABC','subject','body',dialect='CC Cluster',**options)
        with self.assertRaises(ValueError):MailCompose('K1ABC','subject','body',receipt='yes')

    def test_connection_queues_the_requested_dxspider_options(self):
        connection=Connection(lambda *_:None)
        connection.sock=object();connection.ready=True
        connection.send_mail('ALL','subject','body',bulletin=True,receipt=True)
        mail=connection.commands.get_nowait()
        self.assertEqual(mail.start(),'SEND NOPRIVATE RR ALL')
        self.assertTrue(connection.mail_busy)

    def test_dxspider_multiple_recipients_require_each_exact_acknowledgement(self):
        mail=MailCompose('K1ABC, LZ3ZZ-1','Subject','body',receipt=True)
        self.assertEqual(mail.start(),'SEND RR K1ABC LZ3ZZ-1')
        mail.feed('Enter Subject (30 characters):\r\n')
        mail.feed('Enter lines of text, /EX to send or /ABORT to exit\r\n')
        self.assertIsNone(mail.feed('Message no 1 saved and directed to K1ABC\r\n'))
        self.assertEqual(mail.saved_recipients,{'K1ABC'});self.assertFalse(mail.saved)
        self.assertIsNone(mail.feed('Message no 2 saved and directed to LZ3ZZ-10\r\n'))
        self.assertFalse(mail.saved)
        self.assertEqual(mail.feed('Message no 3 saved and directed to LZ3ZZ-1\r\nLZ3ZZ de NODE dxspider >\r\n'),[])
        self.assertTrue(mail.saved);self.assertEqual(mail.saved_recipients,{'K1ABC','LZ3ZZ-1'})

    def test_dxspider_partial_acknowledgement_remains_unknown(self):
        mail=MailCompose('K1ABC,LZ3ZZ','Subject','body')
        mail.feed('Enter Subject (30 characters):\r\n')
        mail.feed('Enter lines of text, /EX to send or /ABORT to exit\r\n')
        self.assertEqual(mail.feed('Message no 1 saved and directed to K1ABC\r\nLZ3ZZ de NODE dxspider >'),[])
        self.assertEqual(mail.saved_recipients,{'K1ABC'});self.assertFalse(mail.saved)
        for recipients in ('K1ABC,k1abc','K1ABC,','K1ABC,RR',','.join('K'+str(i) for i in range(11))):
            with self.subTest(recipients=recipients),self.assertRaises(ValueError):MailCompose(recipients,'subject','body')
        with self.assertRaises(ValueError):MailCompose('K1ABC,LZ3ZZ','subject','body',dialect='CC Cluster')

    def test_registration_rejection_keeps_connection_usable(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();seen=[];errors=[]
        def server():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(5);f=sock.makefile('rb');sock.sendall(b'login: ');seen.append(f.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n');seen.extend([f.readline(),f.readline(),f.readline()])
                    sock.sendall(b'Sorry, you need to be registered (SP N2WQ to register)\r\nLZ3ZZ de NODE dxspider >\r\n')
                    seen.append(f.readline())
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start();events=[]
        connection=Connection(lambda kind,value:events.append((kind,value)))
        try:
            connection.start('127.0.0.1',listener.getsockname()[1],'LZ3ZZ');deadline=time.monotonic()+8
            while ('status','Connected') not in events and time.monotonic()<deadline:time.sleep(.01)
            self.assertIn(('status','Connected'),events)
            connection.send_mail('LZ3ZZ','Subject','body')
            while ('mail_status','Node rejected mail: registration required.') not in events and time.monotonic()<deadline:time.sleep(.01)
            self.assertIn(('mail_status','Node rejected mail: registration required.'),events)
            connection.send('sh/dx');worker.join(4)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'SEND LZ3ZZ\r\n',b'sh/dx\r\n'])
        finally:
            connection.close()
            if connection.thread:connection.thread.join(2)
            listener.close()

    def test_cc_cluster_requires_its_specific_prompts_and_exact_acceptance(self):
        mail=MailCompose('K1ABC','Subject','first\nsecond',dialect='CC Cluster')
        self.assertEqual(mail.start(),'SEND K1ABC')
        self.assertIsNone(mail.feed('To ALL de X: Enter Subject (30 characters max):\r\n'))
        self.assertIsNone(mail.feed('Enter Subject (30 characters):\r\n'))
        self.assertIsNone(mail.feed('Enter Subject (30 characters '))
        self.assertEqual(mail.feed('max):\r\n'),['Subject'])
        self.assertIsNone(mail.feed('Enter lines of text, /EX to send or /ABORT to exit\r\n'))
        self.assertEqual(mail.feed('Enter body of message,  Ctrl-Z or /EXit to end, /CAncel to cancel\r\n'),['first','second','/EX'])
        self.assertIsNone(mail.feed('Message #22 accept'))
        self.assertFalse(mail.saved)
        self.assertEqual(mail.feed('ed\r\nLZ3ZZ de NODE CCC >\r\n'),[])
        self.assertEqual(mail.state,'complete');self.assertTrue(mail.saved)
        with self.assertRaises(ValueError):MailCompose('K1ABC','Subject','text',dialect='Other')

    def test_cc_cluster_prompt_alone_and_other_lines_do_not_prove_acceptance(self):
        for response in ('Message #22 accepted elsewhere', 'To ALL de K1ABC: Message #22 accepted', 'Message #22 rejected'):
            with self.subTest(response=response):
                mail = MailCompose('LZ3ZZ', 'Subject', 'Body', dialect='CC Cluster')
                mail.feed('Enter Subject (30 characters max):\r\n')
                mail.feed('Enter body of message,  Ctrl-Z or /EXit to end, /CAncel to cancel\r\n')
                self.assertEqual(mail.feed(response + '\r\nLZ3ZZ de NODE CCC >\r\n'), [])
                self.assertFalse(mail.saved)

    def test_cc_cluster_exchange_reports_observed_acceptance(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();seen=[];errors=[]
        def server():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(5);f=sock.makefile('rb');sock.sendall(b'login: ');seen.append(f.readline())
                    sock.sendall(b'LZ3ZZ de NODE CCC >\r\n');seen.extend([f.readline(),f.readline(),f.readline(),f.readline()])
                    sock.sendall(b'Enter Subject (30 characters max):\r\n');seen.append(f.readline())
                    sock.sendall(b'Enter body of message,  Ctrl-Z or /EXit to end, /CAncel to cancel\r\n');seen.extend([f.readline(),f.readline()])
                    sock.sendall(b'Message #22 accepted\r\nLZ3ZZ de NODE CCC >\r\n');seen.append(f.readline())
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start();events=[]
        connection=Connection(lambda kind,value:events.append((kind,value)))
        try:
            connection.start('127.0.0.1',listener.getsockname()[1],'LZ3ZZ');deadline=time.monotonic()+8
            while ('status','Connected') not in events and time.monotonic()<deadline:time.sleep(.01)
            self.assertIn(('status','Connected'),events)
            connection.send_mail('K1ABC','Subject','body',dialect='CC Cluster')
            while ('mail_status','Node reports the message accepted.') not in events and time.monotonic()<deadline:time.sleep(.01)
            self.assertIn(('mail_status','Node reports the message accepted.'),events)
            connection.send('sh/dx');worker.join(4)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'sh/settings\r\n',b'SEND K1ABC\r\n',b'Subject\r\n',b'body\r\n',b'/EX\r\n',b'sh/dx\r\n'])
        finally:
            connection.close()
            if connection.thread:connection.thread.join(2)
            listener.close()

    def test_local_node_exchange_blocks_other_commands_until_completion(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen()
        seen=[];errors=[];subject_wait=threading.Event();proceed=threading.Event()
        def server():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(5);f=sock.makefile('rb');sock.sendall(b'login: ');seen.append(f.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n');seen.extend([f.readline(),f.readline()])
                    seen.append(f.readline());subject_wait.set();proceed.wait(3)
                    sock.sendall(b'Enter Sub');time.sleep(.03);sock.sendall(b'ject (30 characters):\r\n');seen.append(f.readline())
                    sock.sendall(b'Enter lines of text, /EX to send or /ABORT to exit\r\n')
                    seen.extend([f.readline(),f.readline(),f.readline()])
                    sock.sendall(b'Message no 12 saved and directed to K1ABC\r\nLZ3ZZ de NODE dxspider >\r\n')
                    seen.append(f.readline())
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start();events=[]
        connection=Connection(lambda kind,value:events.append((kind,value)))
        connection.start('127.0.0.1',listener.getsockname()[1],'LZ3ZZ');deadline=time.monotonic()+5
        while ('status','Connected') not in events and time.monotonic()<deadline:time.sleep(.01)
        self.assertIn(('status','Connected'),events)
        connection.send_mail('K1ABC','Subject','first\nsecond')
        self.assertTrue(subject_wait.wait(3))
        with self.assertRaises(OSError):connection.send('sh/dx')
        proceed.set()
        while ('mail_status','Node reports the message saved.') not in events and time.monotonic()<deadline:time.sleep(.01)
        self.assertIn(('mail_status','Node reports the message saved.'),events)
        connection.send('sh/dx');worker.join(4);connection.close();connection.thread.join(2)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
        self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'SEND K1ABC\r\n',b'Subject\r\n',b'first\r\n',b'second\r\n',b'/EX\r\n',b'sh/dx\r\n'])

    def test_disconnect_before_subject_never_sends_the_body(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();seen=[]
        def server():
            sock,_=listener.accept()
            with sock:
                sock.settimeout(4);f=sock.makefile('rb');sock.sendall(b'login: ');seen.append(f.readline())
                sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n');f.readline();f.readline();seen.append(f.readline())
            listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start();events=[]
        connection=Connection(lambda kind,value:events.append((kind,value)))
        connection.start('127.0.0.1',listener.getsockname()[1],'LZ3ZZ');deadline=time.monotonic()+4
        while ('status','Connected') not in events and time.monotonic()<deadline:time.sleep(.01)
        connection.send_mail('K1ABC','Subject','body');worker.join(4);connection.thread.join(3)
        self.assertEqual(seen,[b'LZ3ZZ\r\n',b'SEND K1ABC\r\n'])
        self.assertIn(('mail_status','Mail exchange ended; delivery may be unknown.'),events)
        self.assertFalse(connection.mail_busy)


if __name__=='__main__':unittest.main()
