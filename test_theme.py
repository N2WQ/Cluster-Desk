import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

import app
from protocol import Spot


class ThemeTests(unittest.TestCase):
    def test_node_state_prompt_keeps_invalid_input_for_correction(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window._save_settings=lambda:True;observed=[]
            try:
                def exercise():
                    dialog=next(w for w in window.winfo_children() if isinstance(w,app.NodeStateDialog))
                    dialog.entry.insert(0,'W1 AW');dialog.ok()
                    observed.extend((dialog.winfo_exists(),dialog.entry.get(),dialog.error.get()))
                    dialog.entry.delete(0,'end');dialog.entry.insert(0,'W1AW');dialog.ok()
                window.after(50,exercise)
                self.assertEqual(app.ask_node_state_call(window,window.theme_bg),'W1AW')
                self.assertEqual(observed[0:2],[1,'W1 AW']);self.assertIn('callsign',observed[2])
            finally:window.quit_app()

    def test_node_state_prompt_uses_active_theme(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window._save_settings=lambda:True
            try:
                for _ in range(2):
                    seen=[]
                    def submit():
                        dialog=next(w for w in window.winfo_children() if isinstance(w,tk.Toplevel) and w.title()=='Node state lookup')
                        seen.append(dialog.cget('background'))
                        def walk(widget):
                            for child in widget.winfo_children():yield child;yield from walk(child)
                        entry=next(w for w in walk(dialog) if isinstance(w,ttk.Entry));entry.insert(0,'W1AW')
                        next(w for w in walk(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Look up').invoke()
                    window.after(50,submit)
                    self.assertEqual(app.ask_node_state_call(window,window.theme_bg),'W1AW')
                    self.assertEqual(seen,[window.theme_bg]);window.switch_theme()
            finally:window.quit_app()

    def test_connected_status_is_green_in_both_themes(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                window.status.set('Connected');window.refresh_status_indicator()
                self.assertEqual(window.status_dot.cget('fg'),'#187c42')
                window.switch_theme()
                self.assertEqual(window.status_dot.cget('fg'),'#65dc8a')
                window.status.set('Connection error — see Activity');window.refresh_status_indicator()
                self.assertEqual(window.status_dot.cget('fg'),'#bd783e')
                window.status.set('Disconnected');window.refresh_status_indicator()
                self.assertEqual(window.status_dot.cget('fg'),window.theme_muted)
            finally:window.quit_app()

    def test_small_labels_have_readable_contrast_in_both_themes(self):
        def luminance(color):
            components=[int(color[i:i+2],16)/255 for i in (1,3,5)]
            linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in components]
            return sum(weight*value for weight,value in zip((.2126,.7152,.0722),linear))
        def contrast(foreground,background):
            light,dark=sorted((luminance(foreground),luminance(background)),reverse=True)
            return (light+.05)/(dark+.05)
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                style=ttk.Style(window)
                for _ in range(2):
                    for label in ('Muted.TLabel','BandCount.TLabel'):
                        self.assertGreaterEqual(contrast(style.lookup(label,'foreground'),style.lookup(label,'background')),4.5)
                    self.assertGreaterEqual(contrast(style.lookup('TCheckbutton','indicatorforeground',('selected',)),
                                                     style.lookup('TCheckbutton','indicatorbackground',('selected',))),4.5)
                    self.assertGreaterEqual(contrast(style.lookup('TCheckbutton','upperbordercolor'),
                                                     style.lookup('TCheckbutton','indicatorbackground')),3.0)
                    suffix='dark' if window.dark_theme.get() else 'light'
                    for name,point in [('book',(1,3)),('globe',(8,1)),('trash',(3,4)),('lock',(4,7)),('monitor',(1,2)),('house',(1,7)),('post',(1,3))]:
                        scaled_point=tuple((coordinate*5+3)//4 for coordinate in point)
                        rgb=window.ui_icons[name+'_'+suffix].get(*scaled_point)
                        color='#%02x%02x%02x'%rgb
                        self.assertGreaterEqual(contrast(color,window.theme_surface),3.0,msg=name+' '+suffix)
                    window.switch_theme()
            finally:window.quit_app()

    def test_connection_and_theme_icons_follow_button_actions(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                self.assertFalse(hasattr(window,'brand_eyebrow'))
                self.assertEqual(str(window.connect_btn.cget('image')[0]),str(window.ui_icons['play']))
                self.assertEqual(window.ui_icons['play'].get(4,7),(22,148,71))
                window.set_connection_button(True)
                self.assertEqual(str(window.connect_btn.cget('image')[0]),str(window.ui_icons['stop']))
                self.assertEqual(window.ui_icons['stop'].get(7,7),(213,59,59))
                self.assertEqual(str(window.theme_button.cget('image')[0]),str(window.ui_icons['moon']))
                window.switch_theme()
                self.assertEqual(str(window.theme_button.cget('image')[0]),str(window.ui_icons['sun']))
                self.assertEqual(str(window.connect_btn.cget('image')[0]),str(window.ui_icons['stop_dark']))
                window.set_connection_button(False)
                self.assertEqual(str(window.connect_btn.cget('image')[0]),str(window.ui_icons['play_dark']))
                self.assertEqual(window.ui_icons['play_dark'].get(4,7),(101,220,138))
                window.switch_theme()
                self.assertEqual(str(window.connect_btn.cget('image')[0]),str(window.ui_icons['play']))
            finally:window.quit_app()

    def test_button_depth_and_selected_state_in_both_themes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                style=ttk.Style(window)
                for _ in range(2):
                    self.assertEqual(str(style.lookup('TButton','relief',('!pressed',))),'raised')
                    self.assertEqual(str(style.lookup('TButton','relief',('pressed',))),'sunken')
                    self.assertEqual(str(style.lookup('BandSelected.TButton','relief',('!pressed',))),'sunken')
                    self.assertNotEqual(style.lookup('TButton','lightcolor'),style.lookup('TButton','darkcolor'))
                    for button_style in ('TButton','Band.TButton','BandSelected.TButton','Accent.TButton'):
                        self.assertNotEqual(style.lookup(button_style,'bordercolor',('focus',)),style.lookup(button_style,'bordercolor'))
                    window.theme_button.invoke();window.update()
            finally:window.quit_app()

    def test_switch_updates_open_views_and_persists(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw()
            try:
                dialog=tk.Toplevel(window);text=tk.Text(dialog);text.pack()
                window.add_spot(Spot('W3ABC','14074','K1ABC','1200Z','CQ'))
                window.color_bands=True;window.render_spots()
                window.theme_button.invoke();window.update()
                self.assertTrue(window.dark_theme.get())
                self.assertEqual(window.theme_button.cget('text'),'Light')
                self.assertEqual(text.cget('background'),window.theme_surface)
                self.assertEqual(dialog.cget('background'),window.theme_bg)
                self.assertEqual(str(window.spots.tag_configure('band-20 m','background')),app.DARK_BAND_COLORS['20 m'])
                self.assertEqual(ttk.Style(window).lookup('Treeview','foreground'),window.theme_ink)
                self.assertTrue(json.loads(app.SETTINGS.read_text())['dark_theme'])
            finally:window.quit_app()
            restored=app.App();restored.withdraw()
            try:
                self.assertTrue(restored.dark_theme.get())
                self.assertEqual(restored.transcript.cget('background'),restored.theme_surface)
                restored.theme_button.invoke()
                self.assertFalse(restored.dark_theme.get())
                self.assertEqual(restored.theme_button.cget('text'),'Dark')
                self.assertFalse(json.loads(app.SETTINGS.read_text())['dark_theme'])
            finally:restored.quit_app()

    def test_theme_switch_fits_compact_window(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window._save_settings=lambda:True
            try:
                window.geometry('780x560');window.update()
                for _ in range(2):
                    button=window.theme_button
                    self.assertTrue(button.winfo_ismapped())
                    self.assertGreaterEqual(button.winfo_width(),40)
                    self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),window.winfo_rootx()+window.winfo_width())
                    button.invoke();window.update()
            finally:window.quit_app()

    def test_icon_button_hints_describe_actions_in_both_themes(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                for hint,expected in ((window.refresh_spots_hint,'Refresh spots'),(window.send_hint,'Send command'),(window.theme_hint,'Switch to dark theme'),
                                      (window.history_hint,'History (older spots)'),(window.qrz_hint,'QRZ lookup'),
                                      (window.clear_spots_hint,'Clear spots · right-click for selected bands'),(window.spot_lock_hint,'Lock scroll'),
                                      (window.display_options_hint,'Display options'),(window.station_info_hint,'Station info'),
                                      (window.spot_button_hint,'Post a spot')):
                    hint.show();self.assertEqual(hint.popup.winfo_children()[0].cget('text'),expected)
                    hint.hide();self.assertIsNone(hint.popup)
                window.switch_theme();window.theme_hint.show()
                self.assertEqual(window.theme_hint.popup.winfo_children()[0].cget('text'),'Switch to light theme')
                window.theme_hint.hide()
            finally:window.quit_app()

    def test_spot_icons_follow_theme_and_preserve_actions(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                for suffix in ('light','dark'):
                    for widget,name in ((window.history_button,'book'),(window.qrz_button,'globe'),
                                        (window.clear_spots_button,'trash'),(window.spot_lock_button,'lock'),
                                        (window.display_options_button,'monitor'),(window.empty_display_options_button,'monitor'),(window.station_info_button,'house')):
                        self.assertEqual(str(widget.cget('image')[0]),str(window.ui_icons[name+'_'+suffix]))
                    self.assertEqual(str(window.spot_button.cget('image')[0]),str(window.ui_icons['post_'+suffix]))
                    self.assertIn(str(window.ui_icons['post_disabled_'+suffix]),str(window.spot_button.cget('image')))
                    window.switch_theme()
                window.spot_lock_button.invoke();self.assertTrue(window.spot_view_locked.get())
                self.assertIn('selected',window.spot_lock_button.state())
                window.spot_lock_hint.show();self.assertEqual(window.spot_lock_hint.popup.winfo_children()[0].cget('text'),'Unlock scroll');window.spot_lock_hint.hide()
                window.spot_lock_button.invoke();self.assertFalse(window.spot_view_locked.get())
                self.assertNotIn('selected',window.spot_lock_button.state())
                self.assertNotIn('indicator',str(ttk.Style(window).layout('Lock.Toolbutton')).lower())
                window.add_spot(Spot('W1AW','14074','K1ABC','1200Z','CQ'))
                window.clear_spots_button.invoke();self.assertEqual(len(window.rows),0)
                window.display_options_button.invoke();self.assertTrue(any(w.winfo_class()=='Toplevel' and w.title()=='Spot display options' for w in window.winfo_children()))
            finally:window.quit_app()

    def test_lock_button_selected_appearance_survives_theme_change(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'SETTINGS',Path(directory)/'settings.json'):
            window=app.App();window.withdraw();window._save_settings=lambda:True
            try:
                for _ in range(2):
                    button=window.spot_lock_button;style=ttk.Style(window)
                    button.invoke();self.assertTrue(window.spot_view_locked.get())
                    self.assertEqual(str(style.lookup('Lock.Toolbutton','relief',('selected',))),'sunken')
                    self.assertNotEqual(style.lookup('Lock.Toolbutton','background',('selected',)),style.lookup('Lock.Toolbutton','background'))
                    window.switch_theme();self.assertIn('selected',button.state())
                    button.invoke();self.assertFalse(window.spot_view_locked.get())
            finally:window.quit_app()


if __name__=='__main__':unittest.main()
