from datetime import date, datetime
import unittest

from app import spot_order
from cluster_dates import parse_cluster_date
from protocol import Spot
from spot_archive import record_in_date_range, spot_from_archive


class ClusterDateTests(unittest.TestCase):
    def test_undated_spot_near_midnight_tolerates_small_clock_difference(self):
        now=datetime(2026,12,31,23,58)
        spot=lambda stamp: Spot('S','14074','K1ABC',stamp,'')
        self.assertEqual(spot_order(spot('0001Z'),now),datetime(2027,1,1,0,1))
        self.assertEqual(spot_order(spot('0010Z'),now),datetime(2026,12,31,0,10))
        self.assertEqual(spot_order(spot('2359Z'),datetime(2027,1,1,0,2)),datetime(2026,12,31,23,59))

    def test_english_months_and_calendar_validation(self):
        for number, name in enumerate(('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'),1):
            with self.subTest(month=name):
                self.assertEqual(parse_cluster_date(f' 1-{name.upper()}-2024 '),date(2024,number,1))
        self.assertEqual(parse_cluster_date('29-feb-2024'),date(2024,2,29))
        for bad in ('29-Feb-2023','31-Apr-2024','1-Xxx-2024','2024-01-01','1-Oct-0000'):
            with self.subTest(value=bad),self.assertRaises(ValueError):parse_cluster_date(bad)

    def test_lowercase_utc_suffix_sorts_and_saved_dates_filter(self):
        dated=Spot('S','14074','K1ABC','2359z','','29-Oct-2026')
        undated=Spot('S','14074','K1ABC','0001z','')
        self.assertEqual(spot_order(dated),datetime(2026,10,29,23,59))
        self.assertEqual(spot_order(undated,datetime(2026,10,30,0,2)),datetime(2026,10,30,0,1))
        row={'dx_call':'K1ABC','spotter':'W3ABC','frequency_khz':'14074','time_utc':'2359z','date':'29-oct-2026'}
        self.assertEqual(spot_from_archive(row).time,'2359Z')
        self.assertTrue(record_in_date_range(row,date(2026,10,29),date(2026,10,29)))
        self.assertFalse(record_in_date_range(row,date(2026,10,30),None))


if __name__ == '__main__': unittest.main()
