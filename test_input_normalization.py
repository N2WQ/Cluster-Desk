import unittest
import tempfile
from pathlib import Path

from announcements import message_command, message_commands
from alarms import compile_rules, matches
from chat import group_command
from cluster_mail import directory_command
from country_lookup import CountryIndex
from eqsl_activity import read_activity
from locator import grid_center
from mail_compose import MailCompose
from protocol import Spot, parse_spot, spot_command
from test_country_lookup import DATA


class InputNormalizationTests(unittest.TestCase):
    def test_non_ascii_cannot_be_converted_into_a_different_destination(self):
        checks = (
            lambda: spot_command('14074', 'ß1', ''),
            lambda: message_command('CC Cluster', 'Direct message', 'hello', 'ß1'),
            lambda: message_commands('CC Cluster', 'Direct message', 'hello', 'K1ABC,ß1'),
            lambda: MailCompose('ß1', 'subject', 'body'),
            lambda: group_command('Join', 'ß1'),
            lambda: grid_center('ß31'),
            lambda: spot_command('١٤٠٧٤', 'K1ABC', ''),
            lambda: directory_command('DXSpider', 'From', 'ß1'),
        )
        for check in checks:
            with self.subTest(check=check), self.assertRaises(ValueError):
                check()

    def test_lowercase_ascii_still_works(self):
        self.assertEqual(spot_command('14074', 'k1abc', ''), 'DX 14074 K1ABC')
        self.assertEqual(message_commands('CC Cluster', 'Direct message', 'hi', 'k1abc'), ['TALK K1ABC hi'])
        self.assertEqual(group_command('Join', 'dx'), 'JOIN DX')
        self.assertEqual(grid_center('fn31'), grid_center('FN31'))

    def test_country_and_eqsl_data_do_not_convert_non_ascii_calls(self):
        index=CountryIndex.from_text(DATA)
        self.assertIsNone(index.lookup('ß1'))
        with self.assertRaises(ValueError):CountryIndex.from_text(DATA.replace('LZ,LZ0', 'ß1,LZ0'))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'eqsl.txt'
            path.write_bytes('List of AG members as of 30-Oct-2026 11:15 UTC - Dated\nß1,2026-01-01\nK1ABC,2026-01-01\n'.encode('cp1252'))
            activity=read_activity(path)
            self.assertEqual(activity.skipped,1)
            self.assertNotIn('SS1',activity.calls)
            self.assertIn('K1ABC',activity.calls)

    def test_incoming_non_ascii_fields_do_not_match_other_alarm_values(self):
        spot=Spot('W1AW','14074','ß1','1200Z','',dx_state='ß',dx_cq='٥')
        self.assertFalse(matches(compile_rules('SS1','',''),spot))
        self.assertFalse(matches(compile_rules('','SS',''),spot))
        self.assertFalse(matches(compile_rules('','','','5'),spot))

    def test_non_ascii_frequency_or_time_is_not_accepted_as_a_spot(self):
        self.assertIsNone(parse_spot('CC11^١٤٠٧٤^K1ABC^31-Oct-2026^1200Z^CQ^W1AW'))
        self.assertIsNone(parse_spot('CC11^14074^K1ABC^31-Oct-2026^١٢٠٠Z^CQ^W1AW'))
        self.assertIsNotNone(parse_spot('CC11^14074^K1ABC^31-Oct-2026^1200Z^CQ^W1AW'))
