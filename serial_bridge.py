"""Opt-in serial logger transport; no device opens until start()."""
import queue
import re
import threading

from logger_bridge import format_spot, MAX_LINE, TEXT_CATEGORIES
from bands import SPOT_BANDS, band_for


def serial_port_names(devices):
    """Return supported COM names in numeric order, without opening devices."""
    names = set()
    for device in devices:
        name = str(device).upper()
        if re.fullmatch(r'COM(?:[1-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-6])', name):
            names.add(name)
    return sorted(names, key=lambda name: int(name[3:]))


def list_serial_ports():
    from serial.tools import list_ports
    return serial_port_names(port.device for port in list_ports.comports())


def open_serial(port, baudrate):
    import serial
    return serial.Serial(port=port, baudrate=baudrate, timeout=.1, write_timeout=1)


class SerialBridge:
    def __init__(self, port, baudrate, emit, ignore_bye=False, spots_only=False, factory=open_serial, text_categories=None, spot_bands=None, local_echo=False):
        if not re.fullmatch(r'COM(?:[1-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-6])',port.upper()):
            raise ValueError('Enter a COM port from COM1 to COM256.')
        if isinstance(baudrate,bool) or baudrate not in (1200,2400,4800,9600,19200,38400,57600,115200):
            raise ValueError('Choose a supported baud rate.')
        self.port=port.upper();self.baudrate=baudrate;self.emit=emit;self.factory=factory
        self.ignore_bye=ignore_bye;self.spots_only=spots_only;self.local_echo=local_echo
        self.text_categories=TEXT_CATEGORIES if text_categories is None else frozenset(text_categories)
        if not self.text_categories <= TEXT_CATEGORIES:raise ValueError('Unknown node text category.')
        self.spot_bands=SPOT_BANDS if spot_bands is None else frozenset(spot_bands)
        if not self.spot_bands <= SPOT_BANDS:raise ValueError('Unknown spot band.')
        self.stop_event=threading.Event();self.actions=queue.Queue(maxsize=512);self.thread=None

    def start(self):
        if self.thread is not None:raise RuntimeError('Serial bridge already started')
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()

    def stop(self):self.stop_event.set()

    def _post(self,action):
        if self.stop_event.is_set():return False
        try:self.actions.put_nowait(action);return True
        except queue.Full:
            self.emit('error','Serial logger stopped because its queue filled.');self.stop_event.set();return False

    def publish(self,spot):
        if (band_for(spot.frequency) or 'Other') not in self.spot_bands:return not self.stop_event.is_set()
        standard=format_spot(spot)
        enhanced=(spot.raw_cc11.replace('\r',' ').replace('\n',' ')+'\r\n').encode('cp1252','replace') if spot.raw_cc11 else standard
        return self._post(('spot',standard,enhanced))

    def publish_text(self,text,category='other'):
        if category not in TEXT_CATEGORIES:raise ValueError('Unknown node text category.')
        if self.spots_only or category not in self.text_categories:return not self.stop_event.is_set()
        return self._post(('text',(text.rstrip('\r\n')+'\r\n').encode('cp1252','replace')))

    def publish_fragment(self,text,category='other'):
        if category not in TEXT_CATEGORIES:raise ValueError('Unknown node text category.')
        if self.spots_only or category not in self.text_categories:return not self.stop_event.is_set()
        return self._post(('text',text.encode('cp1252','replace')))

    def reply(self,text):return self._post(('text',(text.rstrip('\r\n')+'\r\n').encode('cp1252','replace')))

    def _run(self):
        device=None;incoming=bytearray();enhanced=False
        def write(data):
            if device.write(data)!=len(data):raise OSError('Incomplete serial write.')
        try:
            if self.stop_event.is_set():return
            device=self.factory(self.port,self.baudrate);self.emit('started',(self.port,self.baudrate))
            while not self.stop_event.is_set():
                for _ in range(100):
                    if self.stop_event.is_set():break
                    try:action=self.actions.get_nowait()
                    except queue.Empty:break
                    write(action[2] if action[0]=='spot' and enhanced else action[1])
                if self.stop_event.is_set():break
                data=device.read(4096)
                for byte in data:
                    if byte in (10,13):
                        if not incoming:continue
                        line=bytes(incoming).decode('ascii','replace').strip();incoming.clear()
                        if not line:continue
                        if any(not 32<=ord(c)<=126 for c in line):
                            write(b'Command must contain printable ASCII.\r\n');continue
                        if self.local_echo:write(line.encode('ascii')+b'\r\n')
                        upper=line.upper()
                        if upper in ('SET/VE7CC','UNSET/VE7CC'):
                            enhanced=upper=='SET/VE7CC';write(b'OK\r\n')
                        elif upper in ('BYE','B','QUIT'):
                            if upper=='BYE' and self.ignore_bye:write(b'BYE ignored; connection remains open.\r\n')
                            else:write(b'Logger disconnected.\r\n');self.stop_event.set();break
                        else:self.emit('command',line)
                    else:
                        incoming.append(byte)
                        if len(incoming)>MAX_LINE:raise ValueError('Serial logger command exceeded 2048 bytes.')
        except (OSError,ValueError,ImportError) as exc:self.emit('error',str(exc))
        finally:
            self.stop_event.set()
            if device:
                try:device.close()
                except OSError:pass
            self.emit('stopped',None)
