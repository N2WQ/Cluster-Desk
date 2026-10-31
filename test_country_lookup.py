import tempfile
import time
import unittest
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

import app
from country_lookup import Country, CountryIndex
from protocol import Spot


DATA='''Exampleland: 20: 28: EU: 42.0: -25.0: -2.0: LZ:
    LZ,LZ0(19)[27],=SPECIAL(18)[26];
Otherland: 10: 12: SA: -10.0: 76.0: 5.0: OA:
    OA,=EXACT;
'''


class CountryTests(unittest.TestCase):
    def test_bulk_selection_over_limit_does_not_change_filter(self):
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        window=app.App();window.withdraw();window._save_settings=lambda:True
        window.country_index=CountryIndex({}, {}, [Country(f'Country {i}',f'X{i}',20,28,'EU',0,0) for i in range(51)])
        try:
            window.cc_filter_kind.set('DX country');window.keyword_text.set('')
            window.open_filter_value_picker(window.keyword_text,window.cc_filter_kind,'CC Cluster',window)
            dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
            widgets=list(walk(dialog))
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Select shown').invoke()
            with patch('app.messagebox.showerror') as error:
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
            self.assertIn('50 values',error.call_args.args[1]);self.assertEqual(window.keyword_text.get(),'')
            self.assertTrue(dialog.winfo_exists())
        finally:window.quit_app()

    def test_country_download_link_opens_in_browser_only_on_request(self):
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        window=app.App();window.withdraw();window._save_settings=lambda:True
        try:
            with patch('app.webbrowser.open_new_tab',return_value=True) as browser:
                window.open_country_file();browser.assert_not_called()
                dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Get CTY.DAT…').invoke()
                browser.assert_called_once_with('https://www.country-files.com/cty/cty.dat')
                self.assertIsNone(window.country_index)
        finally:window.quit_app()

    def test_filter_countries_excludes_ambiguous_and_unusable_primary_prefixes(self):
        extra='''Differentland: 20: 28: EU: 42.0: -25.0: -2.0: LZ:\n    XX;\nNumericland: 20: 28: EU: 42.0: -25.0: -2.0: 123:\n    ZZ;\n'''
        self.assertEqual(CountryIndex.from_text(DATA+extra).filter_countries(),[('OA','Otherland','SA')])

    def test_filter_picker_preserves_selections_and_existing_numeric_values(self):
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            window.country_index=CountryIndex.from_text(DATA)
            try:
                self.assertEqual(window.country_index.filter_countries(),[('LZ','Exampleland','EU'),('OA','Otherland','SA')])
                window.spider_filter_kind.set('DX country');window.spider_expression.set('291')
                window.open_filter_value_picker(window.spider_expression,window.spider_filter_kind,'DXSpider',window,stream=window.spider_stream)
                dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
                widgets=list(walk(dialog));listing=next(w for w in widgets if isinstance(w,tk.Listbox))
                window.switch_theme();window.update()
                self.assertEqual(listing.cget('background'),window.theme_surface)
                listing.selection_set(0);listing.event_generate('<<ListboxSelect>>');window.update()
                entry=next(w for w in widgets if type(w) is ttk.Entry);entry.insert(0,'Otherland');window.update()
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Select shown').invoke()
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
                self.assertEqual(window.spider_expression.get(),'291,LZ,OA')
            finally:window.quit_app()

    def test_filter_picker_requires_country_file(self):
        window=app.App();window.withdraw();window._save_settings=lambda:True
        try:
            window.country_index=None;window.cc_filter_kind.set('DX country')
            with patch('app.messagebox.showinfo') as info:
                window.open_filter_value_picker(window.keyword_text,window.cc_filter_kind,'CC Cluster',window)
            self.assertIn('CTY.DAT',info.call_args.args[1])
        finally:window.quit_app()

    def test_bulk_clear_only_changes_visible_countries(self):
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        window=app.App();window.withdraw();window._save_settings=lambda:True
        window.country_index=CountryIndex.from_text(DATA)
        try:
            window.cc_filter_kind.set('DX country');window.keyword_text.set('LZ,OA,K')
            window.open_filter_value_picker(window.keyword_text,window.cc_filter_kind,'CC Cluster',window)
            dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
            widgets=list(walk(dialog));entry=next(w for w in widgets if type(w) is ttk.Entry)
            entry.insert(0,'Exampleland');window.update()
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Clear shown').invoke()
            next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
            self.assertEqual(window.keyword_text.get(),'OA,K')
        finally:window.quit_app()

    def test_filter_picker_rejects_changed_form_without_sending(self):
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        window=app.App();window.withdraw();window._save_settings=lambda:True
        window.country_index=CountryIndex.from_text(DATA)
        try:
            window.cc_filter_kind.set('DX country');window.keyword_text.set('K')
            window.open_filter_value_picker(window.keyword_text,window.cc_filter_kind,'CC Cluster',window)
            dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel))
            widgets=list(walk(dialog));listing=next(w for w in widgets if isinstance(w,tk.Listbox))
            listing.selection_set(0);window.cc_filter_kind.set('Spotter country')
            with patch('app.messagebox.showerror') as error:
                next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Use selection').invoke()
            self.assertIn('changed',error.call_args.args[1]);self.assertEqual(window.keyword_text.get(),'K')
            dialog.destroy()
        finally:window.quit_app()

    def test_prefix_exact_and_zone_overrides(self):
        index=CountryIndex.from_text(DATA)
        country=index.lookup('lz3zz-16')
        self.assertEqual((country.name,country.cq,country.itu,country.longitude,country.matched),('Exampleland',20,28,25.0,'LZ'))
        country=index.lookup('LZ0ABC')
        self.assertEqual((country.cq,country.itu,country.matched),(19,27,'LZ0'))
        country=index.lookup('SPECIAL')
        self.assertEqual((country.name,country.cq,country.itu,country.exact),('Exampleland',18,26,True))
        self.assertEqual(index.lookup('OA/LZ3ZZ').name,'Otherland')
        self.assertEqual(index.lookup('LZ3ZZ/OA').name,'Otherland')
        self.assertEqual(index.lookup('OA/LZ3ZZ/P').name,'Otherland')
        for call in ('LZ3ZZ/P','LZ3ZZ/MM','LZ3ZZ/AM','LZ3ZZ/4','UNKNOWN','XX/LZ3ZZ','OA/LZ3ZZ/4'):
            with self.subTest(call=call):self.assertIsNone(index.lookup(call))

    def test_ambiguous_and_malformed_data_are_not_guessed(self):
        duplicate=DATA+'Third: 14: 27: EU: 50: 1: 0: Z:\n    LZ;\n'
        index=CountryIndex.from_text(duplicate)
        self.assertIsNone(index.lookup('LZ3ZZ'))
        for text in ('','bad','Example: 20: 28: EU: 42: -25: 0: LZ:\n LZ',DATA+'bad'):
            with self.subTest(text=text[-20:]),self.assertRaises(ValueError):CountryIndex.from_text(text)

    def test_alias_location_and_continent_overrides(self):
        data='Entity: 20: 28: EU: 42: -25: 0: LZ:\n LZ,=LZ1XYZ(5)[8]<40.5/73.25>{NA}~5~;\n'
        value=CountryIndex.from_text(data).lookup('LZ1XYZ')
        self.assertEqual((value.cq,value.itu,value.latitude,value.longitude,value.continent),(5,8,40.5,-73.25,'NA'))
        with self.assertRaises(ValueError):CountryIndex.from_text(data.replace('40.5/73.25','91/73.25'))

    def test_pinned_dxspider_file_can_be_loaded(self):
        index=CountryIndex.from_file(Path(__file__).resolve().parent/'reference'/'dxspider'/'data'/'cty.dat')
        self.assertEqual(index.lookup('LZ3ZZ').name,'Bulgaria')
        self.assertEqual(index.lookup('3D2CR').name,'Conway Reef')
        self.assertEqual(index.lookup('DL/LZ3ZZ').name,'Fed. Rep. of Germany')
        self.assertEqual(index.lookup('LZ3ZZ/DL').name,'Fed. Rep. of Germany')

    def test_user_file_loads_and_estimate_never_overwrites_node_data(self):
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'cty.dat';file.write_text(DATA,encoding='utf8')
            settings=Path(directory)/'settings.json'
            with patch.object(app,'SETTINGS',settings):
                window=app.App();window.withdraw()
                try:
                    window.open_country_file();dialog=next(w for w in window.winfo_children() if w.winfo_class()=='Toplevel')
                    with patch('app.filedialog.askopenfilename',return_value=str(file)):
                        next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Choose file…').invoke()
                    deadline=time.monotonic()+3
                    while window.country_loading and time.monotonic()<deadline:window.update();time.sleep(.01)
                    self.assertIsNotNone(window.country_index)
                    window.add_spot(Spot('OA1ABC','14074','LZ3ZZ','1200Z','CQ'));window.render_spots()
                    window.spots.selection_set(window.spots.get_children()[0]);window.show_spot_detail()
                    self.assertIn('Local CTY estimate (DX): Exampleland',window.spot_detail.get('1.0','end'))
                    window.clear_spots();window.add_spot(Spot('OA1ABC','14074','OA/LZ3ZZ','1200Z','CQ'));window.render_spots()
                    window.spots.selection_set(window.spots.get_children()[0]);window.show_spot_detail()
                    self.assertIn('Local CTY estimate (DX): Otherland',window.spot_detail.get('1.0','end'))
                    window.clear_spots();window.add_spot(Spot('OA1ABC','14074','LZ3ZZ','1200Z','CQ',dx_country='Node Country'));window.render_spots()
                    window.spots.selection_set(window.spots.get_children()[0]);window.show_spot_detail()
                    detail=window.spot_detail.get('1.0','end')
                    self.assertIn('Node Country',detail);self.assertNotIn('Local CTY estimate (DX)',detail)
                    invalid=Path(directory)/'bad.dat';invalid.write_text('broken',encoding='utf8')
                    with patch('app.filedialog.askopenfilename',return_value=str(invalid)):
                        next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Choose file…').invoke()
                    deadline=time.monotonic()+3
                    while window.country_loading and time.monotonic()<deadline:window.update();time.sleep(.01)
                    self.assertEqual(window.country_file,str(file))
                    self.assertEqual(window.country_index.lookup('LZ3ZZ').name,'Exampleland')
                    self.assertIn('previous data remains active',window.country_status.get())
                finally:window.quit_app()
                restored=app.App();restored.withdraw();restored._save_settings=lambda:None
                try:
                    deadline=time.monotonic()+3
                    while restored.country_loading and time.monotonic()<deadline:restored.update();time.sleep(.01)
                    self.assertEqual(restored.country_index.lookup('LZ3ZZ').name,'Exampleland')
                finally:restored.quit_app()


if __name__=='__main__':unittest.main()
