import unittest
from app import App
from directory import DirectoryCapture


class FakeConnection:
    def __init__(self): self.commands=[]
    def send(self, text): self.commands.append(text)
    def close(self): pass


class DirectoryTests(unittest.TestCase):
    def test_both_header_formats_and_prompt_completion(self):
        for header in ('  Callsign Type      Started           Name             Link            NodePort',
                       '  Callsign Type      State    Started           Name     Ave RTT Link',
                       'Cluster Configuration:', 'Node         Connected Nodes', 'Node         Callsigns'):
            with self.subTest(header=header):
                c=DirectoryCapture(); self.assertTrue(c.feed(header)); self.assertTrue(c.feed('NODE         USER1 USER2'))
                self.assertTrue(c.feed('LZ3ZZ de NODE 30-Oct-2026 0016Z dxspider >'))
                self.assertTrue(c.complete); self.assertFalse(c.active); self.assertIn('USER1',c.text())
                self.assertFalse(c.feed('not part of the result'))

    def test_limits_are_explicit_and_preserve_partial_result(self):
        c=DirectoryCapture(max_lines=2,max_chars=100)
        c.feed('Node         Callsigns');c.feed('line 1');c.feed('line 2')
        self.assertTrue(c.truncated);self.assertEqual(c.lines,['Node         Callsigns','line 1'])
        c.feed('NODE CCC >');self.assertTrue(c.complete);self.assertTrue(c.truncated)

    def test_view_sends_only_requested_commands(self):
        app=App();app.withdraw()
        try:
            fake=FakeConnection();app.connection=fake;app.status.set('Connected')
            for command in ('who','sh/config/node','sh/config'):app.request_directory(command)
            self.assertEqual(fake.commands,['who','sh/config/node','sh/config'])
            app.directory.feed('Node         Callsigns');app.directory.feed('NODE USER1');app.render_directory()
            self.assertIn('NODE USER1',app.directory_text.get('1.0','end'))
            app.directory_disconnected();self.assertIn('Partial',app.directory_note.get())
            app.clear_directory();self.assertEqual(app.directory_text.get('1.0','end-1c'),'')
        finally:app.quit_app()

if __name__ == '__main__':unittest.main()
