import unittest
from tkinter import ttk
from app import App


class CompactLayoutTests(unittest.TestCase):
    def test_default_spot_table_uses_compact_readable_columns(self):
        import tkinter.font as tkfont
        app=App();app._save_settings=lambda:None
        try:
            app.update();self.assertEqual(app.winfo_width(),960)
            font=tkfont.Font(root=app,font=ttk.Style(app).lookup('Spot.Treeview','font'))
            for column,value in (('time','Oct 31 17:41Z'),('frequency','1296000.00'),('band','Microwave')):
                self.assertGreaterEqual(app.spots.column(column,'width'),font.measure(value)+8)
            self.assertEqual(str(app.spots.column('frequency','anchor')),'e')
            self.assertGreaterEqual(app.spots.column('comment','width'),250)
        finally:app.quit_app()

    def test_state_lookup_is_visible_at_minimum_size(self):
        class Connection:
            def close(self):pass
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        app=App();app._save_settings=lambda:None
        try:
            app.geometry('780x560');app.notebook.select(3);app.connection=Connection();app.status.set('Connected')
            for dialect in ('CC Cluster','DXSpider'):
                with self.subTest(dialect=dialect):
                    app.dialect=dialect;app.update_filter_view();app.update()
                    page=app.cc_filter if dialect=='CC Cluster' else app.spider_filter
                    button=next(w for w in walk(page) if isinstance(w,ttk.Button) and w.cget('text')=='Look up node state…')
                    self.assertTrue(button.winfo_ismapped())
                    self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),app.winfo_rootx()+app.winfo_width())
                    self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),app.winfo_rooty()+app.winfo_height())
        finally:app.quit_app()

    def test_enter_button_is_next_to_command_field(self):
        app=App();app._save_settings=lambda:None
        try:
            for size in ('780x560','1120x760'):
                app.geometry(size);app.update()
                self.assertEqual(app.send_btn.cget('text'),'↵')
                self.assertTrue(app.send_btn.winfo_ismapped())
                self.assertGreaterEqual(app.send_btn.winfo_rootx(),app.command.winfo_rootx()+app.command.winfo_width())
                self.assertLessEqual(app.send_btn.winfo_rootx()-(app.command.winfo_rootx()+app.command.winfo_width()),8)
                self.assertGreaterEqual(app.spot_button.winfo_rootx(),app.send_btn.winfo_rootx()+app.send_btn.winfo_width())
        finally:app.quit_app()

    def test_enter_button_sends_the_command(self):
        class FakeConnection:
            def __init__(self):self.commands=[]
            def send(self,command):self.commands.append(command)
            def close(self):pass
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            connection=FakeConnection();app.connection=connection;app.status.set('Connected')
            app.command.insert(0,'sh/dx/5');app.send_btn.configure(state='normal');app.send_btn.invoke()
            self.assertEqual(connection.commands,['sh/dx/5']);self.assertEqual(app.command.get(),'')
        finally:app.quit_app()

    def test_brand_and_all_band_counts_fit(self):
        app=App();app._save_settings=lambda:None
        try:
            app.geometry('780x760');app.update()
            self.assertEqual(app.title(),'Cluster Desk by N2WQ')
            self.assertEqual(app.brand_title.cget('text'),'Cluster Desk by N2WQ')
            self.assertGreaterEqual(app.brand_title.winfo_width(),app.brand_title.winfo_reqwidth())
            for count in app.band_shortcut_counts.values():
                self.assertTrue(count.winfo_ismapped())
                self.assertGreaterEqual(count.winfo_width(),count.winfo_reqwidth())
                self.assertLessEqual(count.winfo_rootx()+count.winfo_width(),app.winfo_rootx()+app.winfo_width())
        finally:app.quit_app()

    def test_connection_status_and_header_actions_fit_minimum_window(self):
        app=App();app._save_settings=lambda:None
        try:
            app.geometry('780x560');app.update()
            top=app.brand_title.master.master
            buttons=[w for w in top.winfo_children() if isinstance(w,ttk.Button)]
            self.assertEqual(len(buttons),3)
            for status in ('Disconnected','Connected','Reconnecting in 60 seconds…','Connection error — see Activity'):
                with self.subTest(status=status):
                    app.status.set(status);app.update();box=app.status_dot.master
                    self.assertGreaterEqual(box.winfo_width(),box.winfo_reqwidth())
                    self.assertGreaterEqual(app.brand_title.winfo_width(),app.brand_title.winfo_reqwidth())
                    for button in buttons:
                        self.assertTrue(button.winfo_ismapped())
                        self.assertGreaterEqual(button.winfo_width(),button.winfo_reqwidth())
        finally:app.quit_app()

    def test_spot_search_controls_fit_at_minimum_size(self):
        app=App();app._save_settings=lambda:None
        try:
            app.geometry('780x560');app.update()
            for widget in (app.search_box,app.search_field_box,app.band_box,app.spot_source_box):
                self.assertTrue(widget.winfo_ismapped())
                self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),app.winfo_rootx()+app.winfo_width())
                self.assertGreater(widget.winfo_width(),40)
        finally:app.quit_app()

    def test_country_file_button_fits_at_minimum_size(self):
        app=App();app._save_settings=lambda:None
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            app.geometry('780x560');app.notebook.select(2);app.update()
            page=app.nametowidget(app.notebook.tabs()[2])
            button=next(w for w in walk(page) if isinstance(w,ttk.Button) and w.cget('text')=='Country file…')
            qsl=next(w for w in walk(page) if isinstance(w,ttk.Button) and w.cget('text')=='QSL data…')
            for widget in (button,qsl,app.settings_refresh):
                self.assertTrue(widget.winfo_ismapped())
                self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),app.winfo_rootx()+app.winfo_width())
                self.assertGreater(widget.winfo_width(),50)
        finally:app.quit_app()

    def test_recording_controls_fit_at_minimum_size(self):
        app=App();app._save_settings=lambda:None
        def walk(widget):
            for child in widget.winfo_children():yield child;yield from walk(child)
        try:
            app.geometry('780x560');app.notebook.select(7);app.update()
            for widget in (app.log_start,app.log_resume,app.log_stop):
                self.assertTrue(widget.winfo_ismapped())
                self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),app.winfo_rooty()+app.winfo_height())
        finally:app.quit_app()

    def test_main_views_keep_scrollbars_at_minimum_window_size(self):
        app = App(); app._save_settings = lambda: None
        try:
            app.geometry('780x560'); app.update()
            for widget in (app.spots, app.transcript, app.settings_tree, app.report_tree, app.ann_tree):
                page = widget
                while str(page.master) != str(app.notebook):
                    page = page.master
                app.notebook.select(page); app.update()
                scrollbars = [child for child in widget.master.winfo_children()
                              if isinstance(child, ttk.Scrollbar) and str(child.cget('orient')) == 'vertical']
                with self.subTest(view=str(widget)):
                    self.assertEqual(len(scrollbars), 1)
                    self.assertTrue(scrollbars[0].winfo_ismapped())
                    self.assertGreater(scrollbars[0].winfo_height(), 1)
        finally:
            app.quit_app()

    def test_propagation_notes_return_after_resizing(self):
        app = App(); app._save_settings = lambda: None
        try:
            app.notebook.select(app.report_table.master)
            for size, visible in [('780x560', False), ('1120x760', True), ('780x560', False), ('1120x760', True)]:
                app.geometry(size); app.update()
                with self.subTest(size=size):
                    self.assertEqual([bool(h.winfo_ismapped()) for h in app.report_footer_hints], [visible, visible])
                    self.assertGreater(app.report_tree.winfo_height(), 1)
        finally:
            app.quit_app()


if __name__ == '__main__': unittest.main()
