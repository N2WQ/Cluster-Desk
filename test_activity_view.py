import unittest
from tkinter import ttk
from unittest.mock import patch

from app import App


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class ActivityViewTests(unittest.TestCase):
    def test_reading_older_output_is_not_interrupted_by_new_traffic(self):
        app=App();app._save_settings=lambda:None
        try:
            app.geometry('780x560');app.notebook.select(app.console_page);app.update()
            app.append(''.join(f'line {i:04d}\n' for i in range(300)));app.update()
            app.transcript.yview_moveto(.25);app.update()
            top=app.transcript.index('@0,0')
            before=app.transcript.get(top,top+' lineend')
            app.append('new live traffic\n');app.update()
            after_index=app.transcript.index('@0,0')
            self.assertEqual(app.transcript.get(after_index,after_index+' lineend'),before)
            button=next(w for w in descendants(app.console_page) if isinstance(w,ttk.Button) and w.cget('text')=='Jump to latest')
            button.invoke();app.update()
            self.assertGreaterEqual(app.transcript.yview()[1],.999)
            app.append('another line\n');app.update()
            self.assertGreaterEqual(app.transcript.yview()[1],.999)
        finally:app.quit_app()

    def test_additional_node_view_preserves_position_during_updates(self):
        class Connection:
            def __init__(self,emit):self.emit=emit
            def start(self,*args):pass
            def close(self):pass
        app=App();app._save_settings=lambda:None
        try:
            with patch('app.Connection',Connection):
                ident=app.start_secondary_node('example.invalid',7300,'LZ3ZZ');item=app.secondary_nodes[ident]
                app.append_secondary_activity(item,''.join(f'line {i:04d}\n' for i in range(300)))
                app.open_secondary_activity(ident);app.update();view=item['view'];output=view['text']
                output.yview_moveto(.25);app.update()
                top=output.index('@0,0');before=output.get(top,top+' lineend')
                app.append_secondary_activity(item,'new live traffic\n');app.render_secondary_activity(item);app.update()
                top=output.index('@0,0');self.assertEqual(output.get(top,top+' lineend'),before)
                button=next(w for w in descendants(view['window']) if isinstance(w,ttk.Button) and w.cget('text')=='Jump to latest')
                button.invoke();app.update();self.assertGreaterEqual(output.yview()[1],.999)
                app.append_secondary_activity(item,'another line\n');app.render_secondary_activity(item);app.update()
                self.assertGreaterEqual(output.yview()[1],.999)
        finally:app.quit_app()


if __name__=='__main__':unittest.main()
