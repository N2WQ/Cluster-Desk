import tempfile
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

import app as app_module
from app import App
from locator import grid_center, grid_path
from protocol import Spot


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class LocatorTests(unittest.TestCase):
    def test_grid_centers_and_reference_path(self):
        lat,lon=grid_center('JN58td');self.assertAlmostEqual(lat,48.1458333333);self.assertAlmostEqual(lon,11.625)
        distance,bearing=grid_path('IO91WM','FN31PR')
        self.assertAlmostEqual(distance,5414.7,delta=1);self.assertAlmostEqual(bearing,288.6,delta=.2)
        self.assertIsNone(grid_path('FN31','FN31')[1]);self.assertIsNone(grid_path('AA00','JR09')[1])
        for invalid in ('','JS12YY','SS00','FN3','FN31PR00','FN31\nBYE'):
            with self.subTest(value=invalid),self.assertRaises(ValueError):grid_center(invalid)

    def test_local_grid_is_saved_and_selected_spot_shows_estimate(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app_module,'SETTINGS',Path(directory)/'settings.json'):
            app=App();app.withdraw()
            try:
                app.add_spot(Spot('S','14074','W1AW','1200Z','CQ',dx_locator='FN31PR'));app.render_spots();app.spots.selection_set(app.spots.get_children()[0])
                app.open_local_grid();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel');widgets=list(descendants(dialog))
                entry=next(w for w in widgets if type(w) is ttk.Entry);entry.insert(0,'IO91WM')
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke()
                self.assertIn('289° true',app.spot_detail.get('1.0','end'));self.assertIn('5415 km',app.spot_detail.get('1.0','end'))
            finally:app.quit_app()
            restored=App();restored.withdraw()
            try:self.assertEqual(restored.local_grid.get(),'IO91WM')
            finally:restored.quit_app()


if __name__=='__main__':unittest.main()
