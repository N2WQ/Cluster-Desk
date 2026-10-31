import queue
import threading
import time
import unittest
from unittest.mock import patch, Mock
from app import App

from protocol import Spot, parse_spot
from serial_bridge import SerialBridge, serial_port_names


class FakeSerial:
    def __init__(self):self.reads=queue.Queue();self.writes=[];self.closed=False
    def read(self,size):
        try:return self.reads.get(timeout=.03)
        except queue.Empty:return b''
    def write(self,data):self.writes.append(data);return len(data)
    def close(self):self.closed=True


def wait_for(predicate, timeout=2):
    deadline=time.monotonic()+timeout
    while not predicate() and time.monotonic()<deadline:time.sleep(.01)
    return bool(predicate())


class SerialBridgeTests(unittest.TestCase):
    def setUp(self):
        self.device=FakeSerial();self.events=[]
        self.bridge=SerialBridge('COM8',9600,lambda kind,value:self.events.append((kind,value)),factory=lambda *_:self.device)
        self.bridge.start();self.assertTrue(wait_for(lambda:('started',('COM8',9600)) in self.events))
    def tearDown(self):
        self.bridge.stop();self.bridge.thread.join(2);self.assertFalse(self.bridge.thread.is_alive());self.assertTrue(self.device.closed)
    def test_commands_spots_and_enhanced_mode(self):
        self.device.reads.put(b'sh/d');self.device.reads.put(b'x\r\nset/ve7cc\r')
        self.assertTrue(wait_for(lambda:('command','sh/dx') in self.events and b'OK\r\n' in self.device.writes))
        line='CC11^14074.0^K1ABC^29-Oct-2026^2300Z^CQ^LZ3ZZ^25'
        self.bridge.publish(parse_spot(line,'DXSpider'))
        self.assertTrue(wait_for(lambda:(line+'\r\n').encode() in self.device.writes))
        self.device.reads.put(b'unset/ve7cc\n');self.assertTrue(wait_for(lambda:self.device.writes.count(b'OK\r\n')==2))
        self.bridge.publish(Spot('S','14074','W1AW','1200Z','CQ'))
        self.assertTrue(wait_for(lambda:any(b'W1AW' in item and b'DX de' in item for item in self.device.writes)))
    def test_selected_node_text_and_spots_are_forwarded_independently(self):
        self.bridge.text_categories=frozenset(('wwv',))
        self.bridge.publish_text('hidden announcement',category='announcements')
        self.bridge.publish_text('hidden response')
        self.bridge.publish_text('visible report',category='wwv')
        self.bridge.publish(Spot('S','14074','K1ABC','1200Z','CQ'))
        self.assertTrue(wait_for(lambda:any(b'K1ABC' in item for item in self.device.writes)))
        output=b''.join(self.device.writes)
        self.assertIn(b'visible report',output);self.assertNotIn(b'hidden announcement',output);self.assertNotIn(b'hidden response',output)
        self.bridge.reply('local reply');self.assertTrue(wait_for(lambda:b'local reply\r\n' in self.device.writes))

    def test_band_selection_filters_spots_but_not_other_text(self):
        self.bridge.spot_bands=frozenset(('40 m',))
        self.bridge.publish(Spot('S','14074','K1HIDDEN','1200Z','CQ'))
        self.bridge.publish(Spot('S','7074','K1VISIBLE','1201Z','CQ'))
        self.bridge.publish_text('node response')
        self.assertTrue(wait_for(lambda:b'node response\r\n' in self.device.writes))
        output=b''.join(self.device.writes)
        self.assertNotIn(b'K1HIDDEN',output);self.assertIn(b'K1VISIBLE',output)
        with self.assertRaises(ValueError):SerialBridge('COM1',9600,lambda *_:None,spot_bands={'invalid'})

    def test_optional_local_echo_returns_commands_once_and_rejects_control_bytes(self):
        self.bridge.local_echo=True;self.bridge.spots_only=True
        self.device.reads.put(b'sh/dx\r\nset/ve7cc\r\nbad\x01command\r\n')
        self.assertTrue(wait_for(lambda:b'Command must contain printable ASCII.\r\n' in self.device.writes))
        self.assertEqual(self.device.writes.count(b'sh/dx\r\n'),1)
        self.assertEqual(self.device.writes.count(b'set/ve7cc\r\n'),1)
        self.assertIn(b'OK\r\n',self.device.writes)
        self.assertFalse(any(b'bad\x01command' in item for item in self.device.writes))
        self.assertIn(('command','sh/dx'),self.events)
        self.assertNotIn(('command','set/ve7cc'),self.events)

    def test_invalid_input_and_overlong_command(self):
        self.device.reads.put(b'bad\x01command\r')
        self.assertTrue(wait_for(lambda:b'Command must contain printable ASCII.\r\n' in self.device.writes))
        self.device.reads.put(b'A'*2049)
        self.assertTrue(wait_for(lambda:any(k=='error' for k,v in self.events)))
        self.assertFalse(any(k=='command' for k,v in self.events))
    def test_bye_stops_only_serial_bridge(self):
        self.device.reads.put(b'BYE\r')
        self.assertTrue(wait_for(lambda:('stopped',None) in self.events))
        self.assertIn(b'Logger disconnected.\r\n',self.device.writes)
        self.assertFalse(any(kind=='command' for kind,value in self.events))

    def test_write_failure_reports_error_and_closes(self):
        def fail(_data):raise OSError('simulated serial failure')
        self.device.write=fail;self.bridge.publish(Spot('S','14074','K1ABC','1200Z','CQ'))
        self.assertTrue(wait_for(lambda:any(kind=='error' and 'simulated serial failure' in value for kind,value in self.events)))
        self.assertTrue(wait_for(lambda:self.device.closed))

    def test_stop_before_worker_starts_does_not_open_port(self):
        opened=[];events=[]
        bridge=SerialBridge('COM7',9600,lambda kind,value:events.append((kind,value)),factory=lambda *args:opened.append(args))
        bridge.stop();bridge._run()
        self.assertEqual(opened,[]);self.assertEqual(events,[('stopped',None)])

    def test_validation(self):
        for port,baud in [('COM0',9600),('COM257',9600),('loop://',9600),('COM2',True),('COM2',0)]:
            with self.subTest(port=port,baud=baud),self.assertRaises(ValueError):SerialBridge(port,baud,lambda *_:None)

    def test_app_sends_spots_and_text_to_both_logger_transports(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        telnet=Mock();serial_logger=Mock();app.logger=telnet;app.serial_logger=serial_logger
        try:
            app.events.put((app.generation,'data','DX de LZ3ZZ: 14074.0 K1ABC CQ 1200Z\r\nnode text\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            self.assertEqual(telnet.publish.call_count,1);self.assertEqual(serial_logger.publish.call_count,1)
            self.assertEqual(telnet.publish.call_args.args[0].dx,'K1ABC')
            self.assertEqual(serial_logger.publish.call_args.args[0].dx,'K1ABC')
            telnet.publish_text.assert_called_with('node text');serial_logger.publish_text.assert_called_with('node text')
        finally:
            app.logger=None;app.serial_logger=None;app.quit_app()

    def test_app_classifies_recognized_node_text_for_both_transports(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        telnet=Mock();serial_logger=Mock();app.logger=telnet;app.serial_logger=serial_logger
        app.dialect='DXSpider';app.connection_target=('example.invalid',7300,'LZ3ZZ');app.chat_groups={'FOC'}
        try:
            lines=('To ALL de K1ABC: hello','WX de K1ABC: snow','LZ3ZZ de K1ABC: private',
                   'FOC de K1ABC: group','WWV de K1ABC <1200Z>: SFI=100, A=5, K=2, fine',
                   'WCY de K1ABC <1200Z>: K=1','other reply')
            app.events.put((app.generation,'data','\r\n'.join(lines)+'\r\n'))
            app.after_cancel(app.poll_after);app.poll()
            categories=('announcements','weather','talk','chat','wwv','wcy',None)
            for bridge in (telnet,serial_logger):
                self.assertEqual(bridge.publish_text.call_count,len(lines))
                for call,line,category in zip(bridge.publish_text.call_args_list,lines,categories):
                    self.assertEqual(call.args,(line,))
                    self.assertEqual(call.kwargs,{'category':category} if category else {})
        finally:app.logger=None;app.serial_logger=None;app.quit_app()

    def test_serial_node_text_options_are_independent(self):
        from tkinter import ttk
        app=App();app.withdraw();app._save_settings=lambda:None
        def descendants(w):
            for child in w.winfo_children():yield child;yield from descendants(child)
        try:
            app.logger_text_categories={'wwv'};app.open_serial_logger();app.open_logger_text_options('serial')
            dialog=next(w for w in app.serial_window.winfo_children() if w.winfo_class()=='Toplevel')
            checks={w.cget('text'):w for w in descendants(dialog) if isinstance(w,ttk.Checkbutton)}
            
            for label,check in checks.items():
                if label != 'Announcements':check.invoke()
            next(w for w in descendants(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Apply').invoke()
            self.assertEqual(app.serial_text_categories,{'announcements'})
            self.assertEqual(app.logger_text_categories,{'wwv'})
            app.serial_logger=object()
            with patch('app.messagebox.showerror') as error:app.open_logger_text_options('serial');error.assert_called_once()
            app.serial_logger=None
        finally:app.quit_app()

    def test_app_routes_serial_commands_and_rejects_old_connection(self):
        class Main:
            def __init__(self):self.commands=[]
            def send(self,value):self.commands.append(value)
            def close(self):pass
        device=FakeSerial();app=App();app.withdraw();app._save_settings=lambda:None
        try:
            old=Main();app.connection=old;app.status.set('Connected');app.update_spot_source()
            with patch('app.SerialBridge',side_effect=lambda port,baud,emit,**options:SerialBridge(port,baud,emit,factory=lambda *_:device,**options)):
                app.open_serial_logger();app.serial_port.set('COM8');app.start_serial_logger()
                self.assertTrue(wait_for(lambda:not app.serial_events.empty()));app.poll_serial_logger()
                self.assertIn('COM8',app.serial_status.get())
                device.reads.put(b'sh/dx\r');self.assertTrue(wait_for(lambda:not app.serial_events.empty()));app.poll_serial_logger()
                self.assertEqual(old.commands,['sh/dx'])
                device.reads.put(b'sh/wx\r');self.assertTrue(wait_for(lambda:not app.serial_events.empty()))
                new=Main();app.generation+=1;app.connection=new;app.update_spot_source();app.poll_serial_logger()
                self.assertEqual(new.commands,[])
                self.assertTrue(wait_for(lambda:any(b'command not sent' in item.lower() for item in device.writes)))
                app.serial_logger.publish(Spot('S','14074','K1ABC','1200Z','CQ'))
                self.assertTrue(wait_for(lambda:any(b'K1ABC' in item for item in device.writes)))
                app.stop_serial_logger();self.assertTrue(wait_for(lambda:not app.serial_logger.thread.is_alive()))
                app.poll_serial_logger();self.assertIsNone(app.serial_logger);self.assertTrue(device.closed)
        finally:app.quit_app()

    def test_serial_options_are_independent_and_applied_on_start(self):
        device=FakeSerial();app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.serial_ignore_bye.set(True);app.serial_spots_only.set(True);app.serial_local_echo.set(True)
            app.logger_ignore_bye.set(False);app.logger_spots_only.set(False)
            with patch('app.SerialBridge',side_effect=lambda port,baud,emit,**options:SerialBridge(port,baud,emit,factory=lambda *_:device,**options)):
                app.open_serial_logger();app.start_serial_logger()
                self.assertTrue(wait_for(lambda:not app.serial_events.empty()));app.poll_serial_logger()
                self.assertEqual(str(app.serial_widgets[5]['state']),'disabled');self.assertEqual(str(app.serial_widgets[7]['state']),'disabled')
                app.serial_logger.publish_text('node text');app.serial_logger.publish(Spot('S','14074','K1ABC','1200Z','CQ'))
                device.reads.put(b'BYE\rsh/dx\r')
                self.assertTrue(wait_for(lambda:any(b'K1ABC' in item for item in device.writes) and any(b'BYE ignored' in item for item in device.writes)))
                self.assertTrue(wait_for(lambda:any(event[1]=='command' for event in list(app.serial_events.queue))))
                self.assertFalse(any(b'node text' in item for item in device.writes))
                self.assertTrue(app.serial_logger.thread.is_alive())
                app.stop_serial_logger();self.assertTrue(wait_for(lambda:not app.serial_logger.thread.is_alive()));app.poll_serial_logger()
                self.assertEqual(str(app.serial_widgets[5]['state']),'normal');self.assertEqual(str(app.serial_widgets[7]['state']),'normal')
        finally:app.quit_app()


class SerialDiscoveryTests(unittest.TestCase):
    def test_names_are_filtered_deduplicated_and_sorted(self):
        self.assertEqual(serial_port_names(['COM10','com3','COM3','COM1','COM0','COM257','COM01','other']),['COM1','COM3','COM10'])

    def test_discovery_keeps_manual_value_and_does_not_open_a_port(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.open_serial_logger();app.serial_port.set('COM42')
            with patch('app.list_serial_ports',return_value=['COM3','COM10']),patch('app.SerialBridge') as bridge:
                app.find_serial_ports();self.assertTrue(wait_for(lambda:not app.serial_scan_events.empty()));app.poll_serial_logger()
                self.assertEqual(tuple(app.serial_widgets[0]['values']),('COM3','COM10'))
                self.assertEqual(app.serial_port.get(),'COM42');bridge.assert_not_called()
                self.assertIn('Found 2',app.serial_status.get())
        finally:app.quit_app()

    def test_error_and_closed_dialog_do_not_overwrite_new_dialog(self):
        app=App();app.withdraw();app._save_settings=lambda:None
        release=threading.Event()
        try:
            app.open_serial_logger()
            with patch('app.list_serial_ports',side_effect=OSError('discovery failed')):
                app.find_serial_ports();self.assertTrue(wait_for(lambda:not app.serial_scan_events.empty()));app.poll_serial_logger()
                self.assertIn('discovery failed',app.serial_status.get())
            def delayed():
                release.wait(2);return ['COM9']
            with patch('app.list_serial_ports',side_effect=delayed):
                app.find_serial_ports();old=app.serial_window
                old.destroy();app.serial_window=None;app.open_serial_logger()
                release.set();self.assertTrue(wait_for(lambda:not app.serial_scan_events.empty()));app.poll_serial_logger()
                self.assertEqual(tuple(app.serial_widgets[0]['values']),())
                self.assertFalse(app.serial_scan_pending)
        finally:
            release.set();app.quit_app()


if __name__=='__main__':unittest.main()
