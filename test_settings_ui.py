import unittest
from tkinter import ttk
from unittest.mock import patch
from app import App
from user_settings import skimmer_command


class FakeConnection:
    def __init__(self): self.commands=[]
    def send(self, command): self.commands.append(command)
    def close(self): pass


class SettingsViewTests(unittest.TestCase):
    def test_skimmer_category_commands(self):
        self.assertEqual(skimmer_command('all'),'set/skimmer')
        self.assertEqual(skimmer_command('off'),'unset/skimmer')
        self.assertEqual(skimmer_command('selected',['FT','CW']),'set/skimmer CW FT')
        for values in ([],['INVALID']):
            with self.subTest(values=values),self.assertRaises(ValueError):skimmer_command('selected',values)

    def test_skimmer_dialog_replaces_selection_and_rejects_changed_connection(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        def descendants(widget):
            for child in widget.winfo_children():yield child;yield from descendants(child)
        try:
            connection=FakeConnection();app.connection=connection;app.dialect='DXSpider';app.status.set('Connected');app.update_setting_actions()
            self.assertEqual(str(app.skimmer_button['state']),'normal')
            app.open_skimmer_categories();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            widgets=list(descendants(dialog));buttons={w.cget('text'):w for w in widgets if isinstance(w,ttk.Button)}
            with patch('app.messagebox.showerror') as error:buttons['Apply selected'].invoke();error.assert_called_once()
            self.assertEqual(connection.commands,[])
            checks={w.cget('text'):w for w in widgets if isinstance(w,ttk.Checkbutton)}
            checks['CW'].invoke();checks['FT4 / FT8'].invoke();buttons['Apply selected'].invoke()
            self.assertEqual(connection.commands,['set/skimmer CW FT'])
            app.open_skimmer_categories();dialog=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
            buttons={w.cget('text'):w for w in descendants(dialog) if isinstance(w,ttk.Button)}
            app.generation+=1
            with patch('app.messagebox.showerror') as error:buttons['Enable all'].invoke();error.assert_called_once()
            self.assertEqual(connection.commands,['set/skimmer CW FT'])
        finally:app.quit_app()

    def test_skimmer_flags_are_read_from_current_user(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.connection=FakeConnection();app.connection_target=('example.invalid',7300,'LZ3ZZ')
            app.events.put((app.generation,'dialect','DXSpider'));app.events.put((app.generation,'status','Connected'))
            app.after_cancel(app.poll_after);app.poll();app.refresh_settings()
            app.events.put((app.generation,'sent','stat/user'))
            app.events.put((app.generation,'data','Callsign: LZ3ZZ\r\nWant RBN spots: No\r\nWant RBN CW: Yes\r\nWant RBN Beacon: No\r\nWant RBN PSK: Yes\r\nWant RBN RTTY: No\r\nWant RBN FT4/8: Yes\r\nLZ3ZZ de NODE dxspider >'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual([app.node_settings[x] for x in ('Spider RBN','Spider RBN CW','Spider RBN Beacon','Spider RBN PSK','Spider RBN RTTY','Spider RBN FT')],['No','Yes','No','Yes','No','Yes'])
        finally:app.quit_app()
    def test_dxspider_seeme_control_and_readback(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            connection=FakeConnection();app.connection=connection;app.connection_target=('example.invalid',7300,'LZ3ZZ');app.status.set('Connected');app.dialect='DXSpider'
            app.events.put((app.generation,'dialect','DXSpider'));app.events.put((app.generation,'status','Connected'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.node_settings['Spider RBN SeeMe'],'Unknown')
            app.settings_tree.selection_set('Spider RBN SeeMe');app.update_setting_actions();app.change_setting(True)
            self.assertEqual(connection.commands[-1],'set/seeme')
            app.refresh_settings();app.events.put((app.generation,'sent','stat/user'))
            app.events.put((app.generation,'data','Callsign: LZ3ZZ\r\nRBN See Me: Yes\r\nLZ3ZZ de NODE dxspider >'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.node_settings['Spider RBN SeeMe'],'Yes')
            app.settings_tree.selection_set('Spider RBN SeeMe');app.change_setting(False)
            self.assertEqual(connection.commands[-1],'unset/seeme')
        finally:app.quit_app()

    def test_dxspider_ft_comment_controls_and_readback(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            connection=FakeConnection();app.connection=connection;app.connection_target=('example.invalid',7300,'LZ3ZZ')
            app.events.put((app.generation,'dialect','DXSpider'));app.events.put((app.generation,'status','Connected'))
            app.after_cancel(app.poll_after);app.poll()
            for name in ('Spider FT spots','Spider auto FT spots'):
                self.assertEqual(app.node_settings[name],'Unknown')
                app.settings_tree.selection_set(name);app.update_setting_actions();app.change_setting(False)
            self.assertEqual(connection.commands,['disable/ftx','disable/autoftx'])
            app.refresh_settings();app.events.put((app.generation,'sent','stat/user'))
            app.events.put((app.generation,'data','Callsign: LZ3ZZ\r\nAllow FTx: Yes\r\nAllow AutoFTx: No\r\nLZ3ZZ de NODE dxspider >'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual((app.node_settings['Spider FT spots'],app.node_settings['Spider auto FT spots']),('Yes','No'))
            for name in ('Spider FT spots','Spider auto FT spots'):
                app.settings_tree.selection_set(name);app.change_setting(True)
            self.assertEqual(connection.commands[-2:],['enable/ftx','enable/autoftx'])
        finally:app.quit_app()

    def test_settings_controls_and_table_visible_at_minimum_size(self):
        app=App();app._save_settings=lambda:None
        def descendants(widget):
            for child in widget.winfo_children():yield child;yield from descendants(child)
        try:
            app.geometry('780x560');app.notebook.select(2);app.update()
            page=app.nametowidget(app.notebook.tabs()[2])
            profile=next(w for w in descendants(page) if isinstance(w,ttk.Button) and w.cget('text')=='My node profile…')
            for button in (app.setting_enable,app.setting_disable,profile):
                self.assertTrue(button.winfo_ismapped())
                self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),app.command.winfo_rooty())
            self.assertTrue(app.settings_tree.winfo_ismapped());self.assertGreaterEqual(app.settings_tree.winfo_height(),55)
        finally:app.quit_app()

    def test_dxspider_commands_start_unknown_and_use_node_responses(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            connection=FakeConnection();app.connection=connection;app.connection_target=('example.invalid',7300,'LZ3ZZ')
            app.events.put((app.generation,'dialect','DXSpider'));app.events.put((app.generation,'status','Connected'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.node_settings['Req DX Spots'],'Unknown')
            app.settings_tree.selection_set('Req DX Spots');app.update_setting_actions();app.change_setting(False)
            self.assertEqual(connection.commands,['unset/dx']);self.assertEqual(app.node_settings['Req DX Spots'],'Unknown')
            app.events.put((app.generation,'data','DX Spots disabled for OTHER\r\nDX Spots disabled for LZ3ZZ\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.node_settings['Req DX Spots'],'No')
            self.assertEqual(app.node_settings['Req Talk'],'Unknown')
            self.assertEqual(str(app.settings_refresh['state']),'normal')
        finally:app.quit_app()

    def test_dxspider_refresh_applies_only_complete_current_user_response(self):
        app=App(); app.withdraw(); app._save_settings=lambda:None
        try:
            connection=FakeConnection(); app.connection=connection; app.connection_target=('example.invalid',7300,'LZ3ZZ')
            app.events.put((app.generation,'dialect','DXSpider')); app.events.put((app.generation,'status','Connected'))
            app.after_cancel(app.poll_after); app.poll(); app.refresh_settings()
            self.assertEqual(connection.commands,['stat/user'])
            app.events.put((app.generation,'sent','stat/user'))
            app.events.put((app.generation,'data','Callsign: LZ3ZZ\r\nReq DX Spots: Yes\r\nShow CQ Zone: No\r\nLZ3ZZ de NODE dxspider >'))
            app.after_cancel(app.poll_after); app.poll()
            self.assertEqual(app.node_settings['Req DX Spots'],'Yes')
            self.assertEqual(app.node_settings['Spider CQ display'],'No')
            self.assertEqual(app.node_settings['Req Talk'],'Unknown')
            app.refresh_settings(); app.events.put((app.generation,'sent','stat/user'))
            app.events.put((app.generation,'data','Callsign: OTHER\r\nReq DX Spots: No\r\nLZ3ZZ de NODE dxspider >'))
            app.after_cancel(app.poll_after); app.poll()
            self.assertEqual(app.node_settings['Req DX Spots'],'Yes')
            self.assertIn('incomplete',app.settings_note.get())
        finally: app.quit_app()

    def test_overlapping_settings_response_does_not_replace_newer_value(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            connection=FakeConnection();app.connection=connection;app.connection_target=('example.invalid',7300,'LZ3ZZ')
            app.events.put((app.generation,'dialect','DXSpider'));app.events.put((app.generation,'status','Connected'))
            app.after_cancel(app.poll_after);app.poll();app.refresh_settings()
            app.events.put((app.generation,'sent','stat/user'))
            app.events.put((app.generation,'data','Callsign: LZ3ZZ\r\nReq DX Spots: No\r\nDX Spots enabled for LZ3ZZ\r\nLZ3ZZ de NODE dxspider >'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.settings_readback.state,'partial')
            self.assertEqual(app.node_settings['Req DX Spots'],'Yes')
            self.assertEqual(app.node_settings['Req Talk'],'Unknown')
            self.assertIn('interrupted',app.settings_note.get())
        finally:app.quit_app()

    def test_other_sent_command_interrupts_settings_refresh(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            connection=FakeConnection();app.connection=connection;app.connection_target=('example.invalid',7300,'LZ3ZZ')
            app.events.put((app.generation,'dialect','DXSpider'));app.events.put((app.generation,'status','Connected'))
            app.after_cancel(app.poll_after);app.poll();app.refresh_settings()
            app.events.put((app.generation,'sent','stat/user'));app.events.put((app.generation,'sent','set/dx'))
            app.events.put((app.generation,'data','Callsign: LZ3ZZ\r\nReq DX Spots: No\r\nLZ3ZZ de NODE dxspider >'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.settings_readback.state,'partial')
            self.assertEqual(app.node_settings['Req DX Spots'],'Unknown')
            self.assertIn('incomplete',app.settings_note.get())
        finally:app.quit_app()

    def test_complete_refresh_marks_omitted_previously_known_fields_unknown(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.connection=FakeConnection();app.connection_target=('example.invalid',7300,'LZ3ZZ')
            app.events.put((app.generation,'dialect','DXSpider'));app.events.put((app.generation,'status','Connected'))
            app.after_cancel(app.poll_after);app.poll()
            app.node_settings['Spider RBN CW']='Yes';app.node_settings['Req Talk']='No'
            app.refresh_settings();app.events.put((app.generation,'sent','stat/user'))
            app.events.put((app.generation,'data','Callsign: LZ3ZZ\r\nShow CQ Zone: No\r\nLZ3ZZ de NODE dxspider >'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.node_settings['Spider RBN CW'],'Unknown');self.assertEqual(app.node_settings['Req Talk'],'Unknown')
            self.assertEqual(app.node_settings['Spider CQ display'],'No');self.assertIn('omitted',app.settings_note.get())
        finally:app.quit_app()

    def test_dxspider_interacting_display_settings_follow_each_response(self):
        app=App(); app.withdraw(); app._save_settings=lambda:None
        try:
            connection=FakeConnection(); app.connection=connection; app.connection_target=('example.invalid',7300,'LZ3ZZ')
            app.events.put((app.generation,'dialect','DXSpider')); app.events.put((app.generation,'status','Connected'))
            app.after_cancel(app.poll_after); app.poll()
            app.settings_tree.selection_set('Spider ITU display'); app.update_setting_actions(); app.change_setting(True)
            self.assertEqual(connection.commands,['set/dxitu'])
            self.assertEqual(app.node_settings['Spider ITU display'],'Unknown')
            app.events.put((app.generation,'data','US State display disabled for LZ3ZZ\r\nDX ITU Zones enabled for LZ3ZZ\r\n'))
            app.after_cancel(app.poll_after); app.poll()
            self.assertEqual(app.node_settings['Spider US state display'],'No')
            self.assertEqual(app.node_settings['Spider ITU display'],'Yes')
        finally: app.quit_app()

    def test_dxspider_beep_login_and_anntalk_controls_and_readback(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            connection=FakeConnection();app.connection=connection;app.connection_target=('example.invalid',7300,'LZ3ZZ')
            app.events.put((app.generation,'dialect','DXSpider'));app.events.put((app.generation,'status','Connected'))
            app.after_cancel(app.poll_after);app.poll()
            for name in ('Req Beep','Login Info Req','Spider AnnTalk'):
                self.assertEqual(app.node_settings[name],'Unknown')
                app.settings_tree.selection_set(name);app.update_setting_actions();app.change_setting(True)
            self.assertEqual(connection.commands,['set/beep','set/logininfo','set/anntalk'])
            self.assertEqual(app.node_settings['Req Beep'],'Unknown')
            app.events.put((app.generation,'data','AnnTalk enabled for LZ3ZZ\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.node_settings['Spider AnnTalk'],'Yes')
            app.refresh_settings();app.events.put((app.generation,'sent','stat/user'))
            app.events.put((app.generation,'data','Callsign: LZ3ZZ\r\nReq Beep: Yes\r\nLogin Info Req: No\r\nTalklike Anns: Yes\r\nLZ3ZZ de NODE dxspider >'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual([app.node_settings[name] for name in ('Req Beep','Login Info Req','Spider AnnTalk')],['Yes','No','Yes'])
            for name in ('Req Beep','Login Info Req','Spider AnnTalk'):
                app.settings_tree.selection_set(name);app.update_setting_actions();app.change_setting(False)
            self.assertEqual(connection.commands[-3:],['unset/beep','unset/logininfo','unset/anntalk'])
        finally:app.quit_app()

    def test_controls_keep_selection_and_send_verified_command(self):
        app=App(); app.withdraw()
        try:
            connection=FakeConnection(); app.connection=connection; app.dialect='CC Cluster'; app.status.set('Connected')
            app.node_settings={'Req FT8   Spots':'No','Req Echo':'No'}; app.render_settings()
            app.settings_tree.selection_set('Req FT8   Spots'); app.update_setting_actions()
            self.assertEqual(app.settings_tree.item('Req FT8   Spots','values')[0], 'FT8 spots')
            self.assertEqual(str(app.setting_enable['state']), 'normal')
            app.change_setting(True)
            self.assertEqual(connection.commands, ['set/ft8'])
            self.assertIn('may be stale',app.settings_note.get())
            app.node_settings['Req FT8   Spots']='Yes'; app.render_settings()
            self.assertEqual(app.settings_tree.selection(), ('Req FT8   Spots',))
            self.assertEqual(app.settings_tree.item('Req FT8   Spots','values')[1], 'Yes')
            app.dialect='DXSpider'; app.update_setting_actions()
            self.assertEqual(str(app.setting_enable['state']), 'disabled')
        finally: app.quit_app()

    def test_cc_self_spot_control_uses_node_readback(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            connection=FakeConnection();app.connection=connection;app.dialect='CC Cluster';app.status.set('Connected')
            app.node_settings={'Req Self Spots':'No'};app.render_settings()
            app.settings_tree.selection_set('Req Self Spots');app.update_setting_actions();app.change_setting(True)
            self.assertEqual(connection.commands,['set/self'])
            self.assertEqual(app.node_settings['Req Self Spots'],'No')
            app.events.put((app.generation,'data','Req Self Spots: Yes\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.node_settings['Req Self Spots'],'Yes')
        finally:app.quit_app()

    def test_cc_beacon_control_uses_documented_commands(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            connection=FakeConnection();app.connection=connection;app.dialect='CC Cluster';app.status.set('Connected')
            app.node_settings={'Req Beacon Spot':'No'};app.render_settings()
            app.settings_tree.selection_set('Req Beacon Spot');app.update_setting_actions()
            app.change_setting(True);app.change_setting(False)
            self.assertEqual(connection.commands,['set/beacon','set/nobeacon'])
            self.assertEqual(app.node_settings['Req Beacon Spot'],'No')
        finally:app.quit_app()

    def test_cc_display_settings_request_node_changes_and_wait_for_readback(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            connection=FakeConnection();app.connection=connection;app.dialect='CC Cluster';app.status.set('Connected')
            names=('Show CQ Zone','Show ITU Zone','Show DX Grid','Show US State')
            app.node_settings={name:'No' for name in names};app.render_settings()
            for name in names:
                app.settings_tree.selection_set(name);app.update_setting_actions();app.change_setting(True)
            self.assertEqual(connection.commands,['set/dxcq','set/dxitu','set/grid','set/usstate'])
            self.assertTrue(all(app.node_settings[name]=='No' for name in names))
            app.events.put((app.generation,'data','Show DX Grid: Yes\r\nShow CQ Zone: No\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(app.node_settings['Show DX Grid'],'Yes');self.assertEqual(app.node_settings['Show CQ Zone'],'No')
            for name in names:
                app.settings_tree.selection_set(name);app.update_setting_actions();app.change_setting(False)
            self.assertEqual(connection.commands[-4:],['set/nodxcq','set/nodxitu','set/nogrid','set/nousstate'])
        finally:app.quit_app()

if __name__ == '__main__': unittest.main()
