import unittest

from announcements import message_command
from chat import group_command
from filter_commands import filter_command, spider_expression
from protocol import spot_command
from station import profile_command


class StructuredCommandSeparatorTests(unittest.TestCase):
    def test_dxspider_separator_cannot_escape_structured_text(self):
        text = r'hello\nbye'
        builders = (
            lambda: spot_command('14074', 'K1ABC', text),
            lambda: message_command('DXSpider', 'Direct message', text, 'K1ABC'),
            lambda: message_command('DXSpider', 'Local announcement', text),
            lambda: group_command('Send', 'FOC', text),
            lambda: spider_expression('Advanced expression', text),
            lambda: filter_command('DXSpider', 'accept', text, line=0),
            lambda: profile_command('QTH', text),
        )
        for build in builders:
            with self.subTest(build=build), self.assertRaisesRegex(ValueError, 'separate|separator'):
                build()

    def test_cc_message_text_remains_available(self):
        self.assertEqual(message_command('CC Cluster', 'Local announcement', r'hello\nbye'),
                         r'ANNOUNCE hello\nbye')
        self.assertEqual(spot_command('14074', 'K1ABC', r'hello\nbye', dialect='CC Cluster'),
                         r'DX 14074 K1ABC hello\nbye')
        self.assertEqual(profile_command('QTH', r'hello\nbye', dialect='CC Cluster'),
                         r'set/qth hello\nbye')
