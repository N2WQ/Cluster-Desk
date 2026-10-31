"""Opt-in loopback Telnet bridge for logging programs.

This is a spot-and-command bridge, not a complete CC User port emulation.
"""
import queue
import re
import selectors
import socket
import threading
from dataclasses import dataclass, field
from protocol import TelnetDecoder
from bands import SPOT_BANDS, band_for

MAX_CLIENTS = 4
MAX_PENDING = 128 * 1024
MAX_LINE = 2048
TEXT_CATEGORIES = frozenset(('announcements', 'weather', 'talk', 'chat', 'wwv', 'wcy', 'other'))


def load_text_categories(value, version):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value): return set(TEXT_CATEGORIES)
    if version == 2:
        return set(value) if set(value) <= TEXT_CATEGORIES else set(TEXT_CATEGORIES)
    legacy = {'announcements', 'reports', 'other'}
    if not set(value) <= legacy: return set(TEXT_CATEGORIES)
    result = set()
    if 'announcements' in value: result.update(('announcements', 'weather', 'talk', 'chat'))
    if 'reports' in value: result.update(('wwv', 'wcy'))
    if 'other' in value: result.add('other')
    return result


def format_spot(spot):
    def clean(value):
        return value.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
    frequency = spot.frequency.strip()
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', frequency): raise ValueError('Invalid spot frequency')
    if '.' not in frequency: frequency += '.0'
    stamp = clean(spot.time).upper()
    if len(stamp) == 4 and stamp.isdigit(): stamp += 'Z'
    return (f'DX de {clean(spot.spotter) + ":":<9} {frequency:>8}  '
            f'{clean(spot.dx):<12} {clean(spot.comment)[:30]:<30} {stamp}\r\n').encode('cp1252', 'replace')


@dataclass
class Client:
    sock: socket.socket
    ident: int
    decoder: TelnetDecoder = field(default_factory=TelnetDecoder)
    incoming: bytearray = field(default_factory=bytearray)
    outgoing: bytearray = field(default_factory=bytearray)
    logged_in: bool = False
    closing: bool = False
    enhanced: bool = False
    after_cr: bool = False


class LoggerBridge:
    def __init__(self, port, emit, ignore_bye=False, spots_only=False, text_categories=None, spot_bands=None):
        self.port = port
        self.ignore_bye = ignore_bye
        self.spots_only = spots_only
        self.text_categories = TEXT_CATEGORIES if text_categories is None else frozenset(text_categories)
        if not self.text_categories <= TEXT_CATEGORIES: raise ValueError('Unknown node text category.')
        self.spot_bands=SPOT_BANDS if spot_bands is None else frozenset(spot_bands)
        if not self.spot_bands <= SPOT_BANDS: raise ValueError('Unknown spot band.')
        self.emit = emit
        self.stop_event = threading.Event()
        self.actions = queue.Queue(maxsize=512)
        self.thread = None
        self.bound_port = None

    def start(self):
        if self.thread is not None: raise RuntimeError('Bridge already started')
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _post(self, action):
        if self.stop_event.is_set(): return False
        try: self.actions.put_nowait(action); return True
        except queue.Full:
            self.emit('error', 'Logger bridge stopped because its queue filled.')
            self.stop_event.set(); return False

    def publish(self, spot):
        if (band_for(spot.frequency) or 'Other') not in self.spot_bands: return not self.stop_event.is_set()
        standard = format_spot(spot).replace(b'\xff', b'\xff\xff')
        enhanced = (spot.raw_cc11.replace('\r', ' ').replace('\n', ' ') + '\r\n').encode('cp1252', 'replace').replace(b'\xff', b'\xff\xff') if spot.raw_cc11 else standard
        return self._post(('spot', standard, enhanced))

    def publish_text(self, text, category='other'):
        if category not in TEXT_CATEGORIES: raise ValueError('Unknown node text category.')
        if self.spots_only or category not in self.text_categories: return not self.stop_event.is_set()
        return self._post(('broadcast', (text.rstrip('\r\n') + '\r\n').encode('cp1252', 'replace').replace(b'\xff', b'\xff\xff')))

    def publish_fragment(self, text, category='other'):
        if category not in TEXT_CATEGORIES: raise ValueError('Unknown node text category.')
        if self.spots_only or category not in self.text_categories: return not self.stop_event.is_set()
        return self._post(('broadcast', text.encode('cp1252', 'replace').replace(b'\xff', b'\xff\xff')))

    def reply(self, ident, text):
        return self._post(('reply', ident, (text.rstrip('\r\n') + '\r\n').encode('cp1252', 'replace').replace(b'\xff', b'\xff\xff')))

    def stop(self):
        self.stop_event.set()

    def _run(self):
        selector = selectors.DefaultSelector(); clients = {}; listener = None; next_id = 1
        def close_client(client):
            try: selector.unregister(client.sock)
            except (KeyError, ValueError): pass
            clients.pop(client.ident, None)
            try: client.sock.close()
            except OSError: pass
            self.emit('clients', len(clients))
        def enqueue(client, data):
            if len(client.outgoing) + len(data) > MAX_PENDING:
                close_client(client)
                self.emit('warning', 'A slow logging program was disconnected.')
                return
            client.outgoing.extend(data)
            selector.modify(client.sock, selectors.EVENT_WRITE if client.closing else selectors.EVENT_READ | selectors.EVENT_WRITE, client)
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(('127.0.0.1', self.port)); listener.listen(MAX_CLIENTS); listener.setblocking(False)
            self.bound_port = listener.getsockname()[1]
            selector.register(listener, selectors.EVENT_READ, None)
            self.emit('listening', self.bound_port)
            while not self.stop_event.is_set():
                for _ in range(100):
                    try: action = self.actions.get_nowait()
                    except queue.Empty: break
                    if action[0] == 'broadcast':
                        for client in list(clients.values()):
                            if client.logged_in and not client.closing: enqueue(client, action[1])
                    elif action[0] == 'spot':
                        for client in list(clients.values()):
                            if client.logged_in and not client.closing:
                                enqueue(client, action[2] if client.enhanced else action[1])
                    elif action[0] == 'reply':
                        client = clients.get(action[1])
                        if client: enqueue(client, action[2])
                for key, mask in selector.select(.1):
                    if key.data is None:
                        sock, _ = listener.accept(); sock.setblocking(False)
                        if len(clients) >= MAX_CLIENTS: sock.close(); continue
                        client = Client(sock, next_id); next_id += 1; clients[client.ident] = client
                        selector.register(sock, selectors.EVENT_READ, client)
                        enqueue(client, b'Welcome to Cluster Desk by N2WQ\r\nPlease enter your call:\r\nlogin: ')
                        self.emit('clients', len(clients)); continue
                    client = key.data
                    if client.ident not in clients: continue
                    try:
                        if mask & selectors.EVENT_READ:
                            data = client.sock.recv(4096)
                            if not data: close_client(client); continue
                            data, reply = client.decoder.feed(data)
                            if reply: enqueue(client, reply)
                            if client.ident not in clients: continue
                            for byte in data:
                                if client.after_cr:
                                    client.after_cr = False
                                    if byte in (0, 10): continue
                                if byte not in (10, 13):
                                    client.incoming.append(byte)
                                    if len(client.incoming) > MAX_LINE:
                                        close_client(client); break
                                    continue
                                client.after_cr = byte == 13
                                line = client.incoming.decode('ascii', 'replace').strip(' ')
                                client.incoming.clear()
                                if not line: continue
                                if not client.logged_in:
                                    if len(line) > 32 or not all(c.isascii() and (c.isalnum() or c in '/-') for c in line):
                                        close_client(client); break
                                    client.logged_in = True
                                    enqueue(client, b'Connected to Cluster Desk by N2WQ.\r\n')
                                elif line.upper() in ('SET/VE7CC', 'UNSET/VE7CC'):
                                    client.enhanced = line.upper() == 'SET/VE7CC'
                                    enqueue(client, b'OK\r\n')
                                elif line.upper() in ('BYE', 'B', 'QUIT'):
                                    if line.upper() == 'BYE' and self.ignore_bye:
                                        enqueue(client, b'BYE ignored; connection remains open.\r\n')
                                    else:
                                        client.closing = True
                                        enqueue(client, f'{line.upper()} was not sent to the cluster.\r\n'.encode('ascii'))
                                        break
                                elif any(not 32 <= ord(char) <= 126 for char in line):
                                    enqueue(client, b'Command must contain ASCII characters (printable only).\r\n')
                                else:
                                    self.emit('command', (client.ident, line))
                        if client.ident in clients and mask & selectors.EVENT_WRITE and client.outgoing:
                            sent = client.sock.send(client.outgoing)
                            del client.outgoing[:sent]
                            if not client.outgoing:
                                if client.closing: close_client(client)
                                else: selector.modify(client.sock, selectors.EVENT_READ, client)
                    except (OSError, ValueError): close_client(client)
        except OSError as exc:
            self.emit('error', str(exc))
        finally:
            for client in list(clients.values()): close_client(client)
            if listener:
                try: selector.unregister(listener)
                except (KeyError, ValueError): pass
                listener.close()
            selector.close(); self.emit('stopped', None)
