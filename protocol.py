"""Telnet transport and the spot formats observed from CC Cluster and DXSpider."""
from dataclasses import dataclass, field
from collections import deque
from decimal import Decimal, InvalidOperation
import queue
import re
import socket
import threading
import time
from mail_compose import MailCompose


class TelnetDecoder:
    """Separate cluster text from Telnet negotiation, including split packets."""
    def __init__(self):
        """Keep negotiation state between successive socket reads."""
        self.state = 'data'
        self.command = 0
        self.after_cr = False

    def feed(self, data):
        """Return application bytes and negotiation replies without losing partial commands."""
        output, reply = bytearray(), bytearray()
        def append_data(byte):
            if self.after_cr and byte == 0:
                self.after_cr = False
                return
            output.append(byte)
            self.after_cr = byte == 13
        for byte in data:
            if self.state == 'data':
                if byte == 255: self.state = 'iac'
                else: append_data(byte)
            elif self.state == 'iac':
                if byte == 255:
                    append_data(byte); self.state = 'data'
                elif byte in (251, 252, 253, 254):
                    self.command = byte; self.state = 'option'
                elif byte == 250: self.state = 'sub'
                else: self.state = 'data'
            elif self.state == 'option':
                if self.command == 251: reply.extend((255, 254, byte))
                elif self.command == 253: reply.extend((255, 252, byte))
                self.state = 'data'
            elif self.state == 'sub':
                if byte == 255: self.state = 'sub_iac'
            elif self.state == 'sub_iac':
                self.state = 'data' if byte == 240 else 'sub'
        return bytes(output), bytes(reply)


@dataclass(frozen=True)
class Spot:
    """Preserve a received spot and optional node metadata for display and recording."""
    spotter: str
    frequency: str
    dx: str
    time: str
    comment: str
    date: str = ''
    dx_dxcc: str = ''
    spotter_dxcc: str = ''
    origin: str = ''
    dx_itu: str = ''
    dx_cq: str = ''
    spotter_itu: str = ''
    spotter_cq: str = ''
    dx_state: str = ''
    spotter_state: str = ''
    dx_country: str = ''
    spotter_country: str = ''
    dx_locator: str = ''
    spotter_locator: str = ''
    extra_fields: tuple = ()
    metadata_fields: tuple = ()
    raw_cc11: str = field(default='', compare=False, repr=False)

LIVE_SPOT = re.compile(r'^\s*(?:login:\s*)?DX de\s+(\S+):\s*([0-9]+(?:\.[0-9]+)?)\s+(\S+)\s+(?:(.*)\s+)?(\d{4}Z)(?:\s|\a|$)', re.I)
HISTORY_SPOT = re.compile(r'^\s*([0-9]+(?:\.[0-9]+)?)\s+(\S+)\s+(\d{1,2}-[A-Za-z]{3}-\d{4})\s+(\d{4}Z)\s*(.*?)\s*<([^>]+)>\s*$', re.I)
# Match a complete prompt line, including the unterminated prompts nodes use.
# Keep short node prompts and the observed optional login prefix supported.
PROMPT = re.compile(
    r'(?im)^[ \t]*(?:login:[ \t]*)?'
    r'(?:[A-Z0-9/-]+|(?=\([A-Z0-9/-]+\)[ \t]+de[ \t]+[^\r\n]*\bdxspider[ \t]*>)\([A-Z0-9/-]+\))'
    r'(?:[ \t]+de[ \t]+[A-Z0-9/-]+(?:[ \t]+[^:\r\n]*?)?)?'
    r'[ \t]+(ccc|dxspider)[ \t]*>[ \t]*(?=\r?$|\n)', re.I)
# Match a prompt at a line boundary, but not a previous-login banner.
# The final line may be unterminated, as it usually is for Telnet prompts.
LOGIN = re.compile(r'(?im)^(?![^\r\n]*\blast[ \t]+login[ \t]*:)[^\r\n]*?\b(?:login|callsign|enter[ \t]+(?:your[ \t]+)?call(?:sign)?)[ \t]*:[ \t]*(?=\r?$|\n)')
PASSWORD = re.compile(r'(?im)^[ \t]*(?:please[ \t]+)?(?:enter[ \t]+)?(?:your[ \t]+)?password[ \t]*:[ \t]*(?=\r?$|\n)')
SETTING = re.compile(r'^\s*([^:]{1,40}):\s*(.*?)\s*$')

def valid_spot_time(value):
    """Accept a four-digit UTC time, with or without the optional Z suffix."""
    match = re.fullmatch(r'([0-9]{2})([0-9]{2})Z?', value.strip(), re.I)
    return bool(match and int(match.group(1)) < 24 and int(match.group(2)) < 60)

SETTING_NAMES = {
    'Callsign', 'Name', 'Home Node', 'Home QTH', 'Location', 'Locator',
    'Login Info Req', 'Req Announce', 'Req Beep', 'Req Echo', 'Req Talk',
    'Req WCY', 'Req WWV', 'Req WX', 'Req DX Spots', 'Dx 2 Digit Res',
    'Req BoB Spots', 'Req Self Spots', 'Req Beacon Spot', 'Req FT4   Spots',
    'Req FT8   Spots', 'Req CW    Spots', 'Req RTTY  Spots', 'Req PSK   Spots',
    'Req MSK144 Spot', 'Req SkimmerSpot', 'Req Skimmer Own', 'Req SkimmerDupe',
    'Req PSKReporter', 'Enhanced DxSpot', 'Show US State', 'Show Cty/State',
    'Show CQ Zone', 'Show DX Grid', 'Show ITU Zone', 'DX Spot Width', 'Type of User',
}

# The original set was checked against CC Cluster's settings readback; the
# additional entries follow its published command reference and are unverified
# on the supplied live node. Do not reuse these for other dialects.
SETTING_COMMANDS = {
    'Req DX Spots': ('set/dx', 'set/nodx'),
    'Req BoB Spots': ('set/bob', 'set/nobob'),
    'Req Self Spots': ('set/self', 'set/noself'),
    'Req Skimmer Own': ('set/own', 'set/noown'),
    'Req Beep': ('set/beep', 'set/nobeep'),
    'Login Info Req': ('set/login', 'set/nologin'),
    'Req SkimmerSpot': ('set/skimmer', 'set/noskimmer'),
    'Req Beacon Spot': ('set/beacon', 'set/nobeacon'),
    'Req FT4   Spots': ('set/ft4', 'set/noft4'),
    'Req FT8   Spots': ('set/ft8', 'set/noft8'),
    'Req CW    Spots': ('set/cw', 'set/nocw'),
    'Req RTTY  Spots': ('set/rtty', 'set/nortty'),
    'Req PSK   Spots': ('set/psk', 'set/nopsk'),
    'Req MSK144 Spot': ('set/msk144', 'set/nomsk144'),
    'Req Announce': ('set/announce', 'set/noannounce'),
    'Req WWV': ('set/wwv', 'set/nowwv'),
    'Req WCY': ('set/wcy', 'set/nowcy'),
    'Req Talk': ('set/talk', 'set/notalk'),
    'Req WX': ('set/wx', 'set/nowx'),
    'Show CQ Zone': ('set/dxcq', 'set/nodxcq'),
    'Show ITU Zone': ('set/dxitu', 'set/nodxitu'),
    'Show DX Grid': ('set/grid', 'set/nogrid'),
    'Show US State': ('set/usstate', 'set/nousstate'),
}

SETTING_LABELS = {
    'Req DX Spots': 'DX spots', 'Req SkimmerSpot': 'Skimmer spots',
    'Req BoB Spots': 'Bottom-of-band spots', 'Req Self Spots': 'Self spots',
    'Req Skimmer Own': 'Skimmer spots for own call', 'Req Beep': 'Node beep',
    'Login Info Req': 'User connect and disconnect notices',
    'Req Beacon Spot': 'Beacon spots', 'Req FT4   Spots': 'FT4 spots',
    'Req FT8   Spots': 'FT8 spots', 'Req CW    Spots': 'CW spots',
    'Req RTTY  Spots': 'RTTY spots', 'Req PSK   Spots': 'PSK spots',
    'Req MSK144 Spot': 'MSK144 spots', 'Req Announce': 'Announcements',
    'Req WWV': 'WWV reports', 'Req WCY': 'WCY reports',
    'Req Talk': 'Talk messages', 'Req WX': 'Weather announcements',
    'Spider CQ display': 'Spot display: CQ zones', 'Spider ITU display': 'Spot display: ITU zones',
    'Spider grid display': 'Spot display: grids', 'Spider US state display': 'Spot display: US states',
    'Spider AnnTalk': 'Talk-style announcements',
    'Spider RBN SeeMe': 'Skimmer spots for own base callsign (SeeMe)',
    'Spider FT spots': 'Allow spots with FT4/FT8 in comment',
    'Spider auto FT spots': 'Allow automated-looking FT4/FT8 comments',
    'Spider RBN': 'Skimmer delivery', 'Spider RBN CW': 'Skimmer: CW',
    'Spider RBN Beacon': 'Skimmer: beacons', 'Spider RBN PSK': 'Skimmer: PSK / FSK / MSK',
    'Spider RBN RTTY': 'Skimmer: RTTY', 'Spider RBN FT': 'Skimmer: FT4 / FT8',
    'Show CQ Zone': 'Spot display: CQ zones', 'Show ITU Zone': 'Spot display: ITU zones',
    'Show DX Grid': 'Spot display: grids', 'Show US State': 'Spot display: spotter US state',
}

SPIDER_SETTING_COMMANDS = {
    'Req DX Spots': ('set/dx', 'unset/dx'),
    'Req Announce': ('set/announce', 'unset/announce'),
    'Req Talk': ('set/talk', 'unset/talk'),
    'Req WWV': ('set/wwv', 'unset/wwv'),
    'Req WCY': ('set/wcy', 'unset/wcy'),
    'Req WX': ('set/wx', 'unset/wx'),
    'Req Beep': ('set/beep', 'unset/beep'),
    'Login Info Req': ('set/logininfo', 'unset/logininfo'),
    'Spider AnnTalk': ('set/anntalk', 'unset/anntalk'),
    'Spider RBN SeeMe': ('set/seeme', 'unset/seeme'),
    'Spider FT spots': ('enable/ftx', 'disable/ftx'),
    'Spider auto FT spots': ('enable/autoftx', 'disable/autoftx'),
    'Spider CQ display': ('set/dxcq', 'unset/dxcq'),
    'Spider ITU display': ('set/dxitu', 'unset/dxitu'),
    'Spider grid display': ('set/dxgrid', 'unset/dxgrid'),
    'Spider US state display': ('set/usstate', 'unset/usstate'),
}
SPIDER_SETTING_RESPONSES = {
    'DX Spots': 'Req DX Spots', 'Announces': 'Req Announce', 'Talk': 'Req Talk',
    'WWV': 'Req WWV', 'WCY': 'Req WCY', 'WX': 'Req WX',
    'DX CQ Zones': 'Spider CQ display', 'DX ITU Zones': 'Spider ITU display',
    'DX Grid': 'Spider grid display', 'US State display': 'Spider US state display',
    'AnnTalk': 'Spider AnnTalk',
}


def setting_command(dialect, name, enabled):
    """Map a supported UI setting to its node-specific enable or disable command."""
    commands = SETTING_COMMANDS if dialect == 'CC Cluster' else SPIDER_SETTING_COMMANDS if dialect == 'DXSpider' else {}
    if name not in commands or not isinstance(enabled, bool):
        raise ValueError('This setting cannot be changed through this control.')
    return commands[name][0 if enabled else 1]


def parse_spider_setting(line, call):
    """Accept a known DXSpider settings response only for the current callsign."""
    match = re.fullmatch(r'\s*(DX Spots|Announces|Talk|WWV|WCY|WX|DX CQ Zones|DX ITU Zones|DX Grid|US State display|AnnTalk) (enabled|disabled)(?: for)? ([A-Za-z0-9/-]+)\s*', line, re.I)
    if not match or match.group(3).upper() != call.upper(): return None
    name = next(key for key in SPIDER_SETTING_RESPONSES if key.casefold() == match.group(1).casefold())
    return SPIDER_SETTING_RESPONSES[name], 'Yes' if match.group(2).lower() == 'enabled' else 'No'


def spot_command(frequency, call, comment, test=False, *, dialect=''):
    """Validate a spot and construct one node command, preventing injected lines."""
    frequency = frequency.strip(); call = call.strip(); comment = comment.strip()
    if not call.isascii(): raise ValueError('Enter the DX callsign using ASCII letters, numbers, / or -.')
    call = call.upper()
    if len(frequency) > 16 or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', frequency):
        raise ValueError('Enter a frequency in kHz, such as 14074.0.')
    try:
        if Decimal(frequency) <= 0: raise ValueError('Frequency must be greater than zero.')
    except InvalidOperation as exc: raise ValueError('Enter a valid frequency.') from exc
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9/-]{0,31}', call):
        raise ValueError('Enter the DX callsign using letters, numbers, / or -.')
    if len(comment) > 400 or any(not 32 <= ord(c) <= 126 for c in comment):
        raise ValueError('Use up to 400 printable ASCII characters for the comment.')
    if dialect != 'CC Cluster' and '\\n' in comment:
        raise ValueError('The characters \\n cannot be used in a spot comment because some nodes treat them as a command separator.')
    if not isinstance(test, bool): raise ValueError('Invalid spot type.')
    base = f'DXTEST {call} {frequency}' if test else f'DX {frequency} {call}'
    return base + (f' {comment}' if comment else '')


def older_filtered_spots_command(page, days_ago=''):
    """Request a filtered-history page, or start at an earlier node day."""
    if isinstance(page, bool) or not isinstance(page, int) or not 2 <= page <= 10:
        raise ValueError('Choose a page from 2 to 10.')
    if days_ago != '':
        if not isinstance(days_ago,str) or not re.fullmatch(r'[0-9]{1,2}',days_ago) or not 1 <= int(days_ago) <= 99:
            raise ValueError('Enter days back from 1 to 99, or leave it blank.')
        return f'sh/mydx/100 day {int(days_ago)}'
    return f'sh/mydx {(page - 1) * 100 + 1}-{page * 100}'


def cc_history_comment_command(text):
    """Build the documented CC Cluster single-word comment history request."""
    if not re.fullmatch(r'[A-Za-z0-9]{1,32}',text):
        raise ValueError('This text cannot be searched directly on the node.')
    return f"sh/dx '{text.upper()}'"


def cc_history_spotter_command(text):
    """Build the documented CC Cluster spotter callsign history request."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/\-]{0,31}',text) or not re.search(r'[A-Za-z]',text) or not re.search(r'[0-9]',text):
        raise ValueError('This text cannot be searched directly as a spotter callsign.')
    return f'sh/dxby {text.upper()}'


def spider_history_text_command(field, text):
    """Search a literal ASCII substring without exposing node regex syntax."""
    name={'DX call':'call','Spotter':'by','Comment':'info','Origin':'origin','All fields':'all'}.get(field)
    if not name or not 1 <= len(text) <= 50 or any(not 32 <= ord(c) <= 126 for c in text):
        raise ValueError('This text cannot be searched directly on the node.')
    literal=''.join(c.upper() if c.isascii() and c.isalnum() else f'\\x{ord(c):02x}' for c in text)
    command=(f'sh/dx/100 ( call {{{literal}}} or by {{{literal}}} or info {{{literal}}} or origin {{{literal}}} )'
             if name=='all' else f'sh/dx/100 {name} {{{literal}}}')
    # Pinned DXSpider defaults to a 512-character limit for the entire line.
    if len(command)>512:raise ValueError('This text is too long for a direct node search.')
    return command


def spider_history_country_command(field, text):
    """Search one exact DXSpider node country number in spot history."""
    name={'DX country':'call_dxcc','Spotter country':'by_dxcc'}.get(field)
    if not name or not re.fullmatch(r'[0-9]{1,5}',text) or int(text)==0:
        raise ValueError('Enter a numeric node country identifier.')
    return f'sh/dx/100 {name} {int(text)}'


def history_command(dialect, call, mode='Callsign', count=30, days_ago=None):
    """Build a bounded history request using the selected cluster dialect's syntax.

    The node chooses the returned records; this request does not guarantee a
    complete history. Invalid or unsupported inputs raise ValueError.
    """
    if dialect not in ('CC Cluster', 'DXSpider'): raise ValueError('This node type is not supported for history lookup.')
    if isinstance(count, bool) or not re.fullmatch(r'[0-9]{1,3}',str(count)) or not 1 <= int(count) <= 100:
        raise ValueError('Choose a spot count from 1 to 100.')
    if days_ago is None or days_ago == '': day_suffix=''
    else:
        if dialect != 'DXSpider': raise ValueError('Days-back search is available only for DXSpider.')
        if isinstance(days_ago,bool) or not re.fullmatch(r'[0-9]{1,2}',str(days_ago)) or not 1 <= int(days_ago) <= 99:
            raise ValueError('Enter days back from 1 to 99, or leave it blank.')
        day_suffix=f' day {int(days_ago)}'
    prefix = f'sh/dx/{int(count)} '
    call = call.strip()
    if not call.isascii(): raise ValueError('Enter an ASCII callsign, band or frequency range.')
    call = call.upper()
    if mode == 'Band':
        high_band = None
        if re.fullmatch(r'(?:1\.25\s*M?|220)',call): high_band=('220000','224000')
        elif re.fullmatch(r'70\s*CM',call): high_band=('420000','450000')
        elif call == 'MICROWAVE': high_band=('500000','21000000')
        if high_band:
            low,high=high_band
            return prefix + (low+'-'+high if dialect == 'CC Cluster' else 'on '+low+'/'+high) + day_suffix
        match = re.fullmatch(r'(160|80|60|40|30|20|17|15|12|10|6|4|2)\s*M?', call)
        if not match: raise ValueError('Enter a supported band, for example 20m, 1.25m or 70cm.')
        return prefix + (match.group(1) if dialect == 'CC Cluster' else 'on '+match.group(1)+'m') + day_suffix
    if mode == 'Frequency range':
        match = re.fullmatch(r'([0-9]{1,8})\s*[-/]\s*([0-9]{1,8})', call)
        if not match or not 0 < int(match.group(1)) < int(match.group(2)):
            raise ValueError('Enter an increasing whole-kHz range, for example 14000-14350.')
        low, high = (str(int(value)) for value in match.groups())
        return prefix + (low+'-'+high if dialect == 'CC Cluster' else 'on '+low+'/'+high) + day_suffix
    if mode != 'Callsign': raise ValueError('Choose a supported history search.')
    pattern = r'[A-Z0-9][A-Z0-9/\-]{0,31}\*?' if dialect == 'CC Cluster' else r'[A-Z0-9/*?\-]{1,32}'
    if len(call) > 32 or not re.fullmatch(pattern, call) or not re.search(r'[A-Z0-9]', call):
        raise ValueError('Enter a callsign or prefix. CC Cluster accepts a trailing *; DXSpider also accepts * and ? in the pattern.')
    return prefix + call + day_suffix


def frequency_history_command(dialect, text):
    """Request nearby whole-kHz history for a numeric display search."""
    if not re.fullmatch(r'[0-9]{1,8}(?:\.[0-9]{1,2})?', text):
        raise ValueError('This text cannot be searched directly as a frequency.')
    whole = int(text.split('.')[0])
    if not 1 <= whole < 99999999:
        raise ValueError('This frequency is outside the supported history range.')
    low=max(1,whole-1);high=whole+1
    return history_command(dialect,f'{low}-{high}',mode='Frequency range',count=100)


def parse_setting(line):
    """Recognize supported CC Cluster setting readbacks without guessing unknown fields."""
    match = SETTING.match(line)
    if not match: return None
    name, value = match.groups()
    if name not in SETTING_NAMES: return None
    return name, value


def parse_spot(line, dialect=''):
    """Normalize supported wire and text spots while retaining available metadata.

    Return None for unrecognized or invalid records. Country identifiers in
    CC11 records are interpreted as DXSpider values only for that dialect.
    """
    packet = line.rstrip('\r\n').lstrip()
    if packet.startswith('CC11^'):
        fields = packet.split('^')
        if len(fields) >= 7 and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', fields[1].strip()) and fields[2].strip() and valid_spot_time(fields[4]) and fields[6].strip():
            # DXSpider VE7CC.pm emits fields in Spot.pm order, followed by
            # country labels and four-character locators. Missing fields stay
            # empty; retain any additional fields without guessing semantics.
            metadata = [fields[i].strip() if i < len(fields) else '' for i in range(7, 20)]
            # CC Cluster uses different values in the first two positions;
            # keep them raw rather than presenting them as country numbers.
            if dialect != 'DXSpider': metadata[0:2] = ['', '']
            return Spot(fields[6].strip(), fields[1].strip(), fields[2].strip(), fields[4].strip(), fields[5], fields[3].strip(), *metadata, tuple(fields[20:]), tuple(fields[7:]), packet)
    # The PC11, PC26 and PC61 wire formats use comment before spotter.
    packet = packet.strip()
    # Require documented fields and caret termination (or DXSpider ^~); retain extensions raw.
    if packet.startswith(('PC11^', 'PC26^', 'PC61^')):
        fields = packet.split('^')
        minimum = 9 if fields[0] == 'PC11' else 10
        if (len(fields) >= minimum and packet.endswith(('^', '^~'))
                and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', fields[1].strip())
                and re.fullmatch(r'[A-Za-z0-9/#-]+', fields[2].strip())
                and re.fullmatch(r'\d{1,2}-[A-Za-z]{3}-\d{4}', fields[3].strip())
                and valid_spot_time(fields[4])
                and re.fullmatch(r'[A-Za-z0-9/#-]+', fields[6].strip())
                and fields[7].strip()):
            stamp=fields[4].strip().upper()
            if not stamp.endswith('Z'): stamp += 'Z'
            return Spot(fields[6].strip(), fields[1].strip(), fields[2].strip(), stamp,
                        fields[5], fields[3].strip(), origin='' if fields[0]=='PC26' else fields[7].strip(),
                        extra_fields=tuple(fields[7:] if fields[0]=='PC26' else fields[8:]), metadata_fields=tuple(fields[7:]))
        return None
    match = LIVE_SPOT.search(line)
    if match:
        spotter, freq, dx, comment, stamp = match.groups()
        if not valid_spot_time(stamp): return None
        return Spot(spotter, freq, dx, stamp, (comment or '').rstrip())
    match = HISTORY_SPOT.match(line)
    if match:
        freq, dx, date, stamp, comment, spotter = match.groups()
        if not valid_spot_time(stamp): return None
        return Spot(spotter, freq, dx, stamp, comment, date)
    return None


class TrackedCommand(str):
    """A command whose identity is retained through the local send queue."""


class CommandRateLimitError(OSError):
    """A command was not queued because the local node budget is full."""


class Connection:
    """Own one cluster session and deliver its events to the supplied callback.

    Socket work happens in a worker thread. Commands are bounded and queued;
    a successful enqueue does not mean the remote node accepted a command.
    """
    """A single-use session. The worker owns all socket reads and writes."""
    def __init__(self, emit):
        """Create an inactive session; start() begins its worker."""
        self.emit = emit
        self.stop = threading.Event()
        self.commands = queue.Queue(maxsize=100)
        self.passwords = queue.Queue(maxsize=1)
        self.password_requested = threading.Event()
        self.mail_busy = False
        self.sock = None
        self.lock = threading.Lock()
        self.thread = None
        self.dialect = None
        self.ready = False
        self.command_times = deque()
        self.pending_commands = 0

    def _check_command_budget(self, count):
        """Reject commands that exceed the local DXSpider pacing budget."""
        # Caller holds lock. Include queued and in-flight commands so their
        # eventual send cannot bunch up after the admission window has moved.
        now = time.monotonic()
        while self.command_times and self.command_times[0] <= now - 21:
            self.command_times.popleft()
        if self.dialect == 'DXSpider' and len(self.command_times) + self.pending_commands + count > 20:
            raise CommandRateLimitError('DXSpider command limit reached. Wait before trying again.')

    def _command_sent(self, *, queued, count=1):
        """Keep pending and pacing counts aligned with commands sent by the worker."""
        with self.lock:
            if queued: self.pending_commands -= count
            if self.dialect == 'DXSpider': self.command_times.extend([time.monotonic()] * count)

    def start(self, host, port, call):
        """Begin a single connection attempt without blocking the UI thread."""
        if self.thread is not None: raise RuntimeError('Session already started')
        self.thread = threading.Thread(target=self._run, args=(host, port, call), daemon=True)
        self.thread.start()

    def _run(self, host, port, call):
        """Drive login, Telnet input, outbound commands and shutdown for this session."""
        sock = None
        password_sent = False; state = 'login'; mail = None; mail_deadline = None
        try:
            sock = socket.create_connection((host, port), timeout=10)
            with self.lock:
                if self.stop.is_set(): return
                self.sock = sock
            sock.settimeout(.2)
            self.emit('status', 'Waiting for login')
            decoder = TelnetDecoder()
            observed = ''
            deadline = time.monotonic() + 20
            while not self.stop.is_set():
                if mail is not None and time.monotonic() > mail_deadline:
                    raise TimeoutError('Mail exchange timed out; delivery may be unknown. The connection was closed.')
                if state != 'ready' and time.monotonic() > deadline:
                    raise TimeoutError('The cluster did not complete login before the timeout.')
                if state == 'password_wait':
                    try: password = self.passwords.get_nowait()
                    except queue.Empty: password = None
                    if password is not None:
                        self.password_requested.clear()
                        sock.sendall(password.replace(b'\xff', b'\xff\xff') + b'\r\n')
                        password = None; password_sent = True
                        state = 'prompt'; observed = ''; deadline = time.monotonic() + 20
                        self.emit('status', 'Signing in')
                if state == 'ready' and mail is None:
                    for _ in range(20):
                        try: batch = self.commands.get_nowait()
                        except queue.Empty: break
                        if isinstance(batch, MailCompose):
                            mail=batch; mail_deadline=time.monotonic()+20
                            command=mail.start();sock.sendall(command.encode('ascii')+b'\r\n');self._command_sent(queued=True)
                            self.emit('sent',command);self.emit('mail_status','Waiting for the subject prompt.')
                            break
                        for command in batch:
                            if self.stop.is_set(): break
                            sock.sendall(command.encode('ascii') + b'\r\n')
                            self._command_sent(queued=True, count=command.count('\\n')+1)
                            self.emit('sent', command)
                try: data = sock.recv(4096)
                except socket.timeout: continue
                if not data:
                    if state != 'ready' and not password_sent and not self.stop.is_set():
                        self.emit('error', 'The node closed the connection before sign-in completed.')
                    break
                data, reply = decoder.feed(data)
                if reply: sock.sendall(reply)
                if not data: continue
                text = data.decode('cp1252', errors='replace')
                # A login prompt and the first spot can arrive in one read.
                # Identify the dialect before delivering that read so CC11
                # metadata is interpreted using the correct node format.
                candidate = (observed + text)[-8192:] if state == 'prompt' else ''
                early_prompt = (PROMPT.search(candidate) if candidate and not password_sent
                                and not PASSWORD.search(candidate) else None)
                if early_prompt:
                    self.emit('dialect', 'CC Cluster' if early_prompt.group(1).lower() == 'ccc' else 'DXSpider')
                # Hide the entire authentication response: some nodes echo input,
                # possibly split across reads, so replacing a substring is unsafe.
                if not password_sent: self.emit('data', text)
                if state == 'ready' and mail is not None:
                    outgoing = mail.feed(text)
                    if outgoing is not None:
                        if mail.state in ('complete', 'rejected'):
                            status = (f'Node rejected mail: {mail.rejection}.' if mail.state == 'rejected' else
                                      ('Node reports the message accepted.' if mail.dialect == 'CC Cluster' else 'Node reports the message saved.') if mail.saved else
                                      'Node response ended; message acceptance is unknown.')
                            self.emit('mail_status', status)
                            mail=None; mail_deadline=None
                            with self.lock: self.mail_busy=False
                        else:
                            for line in outgoing: sock.sendall(line.encode('ascii')+b'\r\n')
                            self.emit('mail_status','Waiting for the body prompt.' if mail.state=='body' else 'Message submitted; awaiting the node response.')
                            mail_deadline=time.monotonic()+20
                if state != 'ready':
                    observed = (observed + text)[-8192:]
                    if state == 'login' and LOGIN.search(observed):
                        sock.sendall(call.encode('ascii') + b'\r\n')
                        self.emit('status', 'Signing in')
                        state = 'prompt'; observed = ''; deadline = time.monotonic() + 20
                    elif state == 'prompt' and not password_sent and PASSWORD.search(observed):
                        state = 'password_wait'; observed = ''; deadline = time.monotonic() + 120
                        self.emit('data', '\n')  # Separate the prompt from subsequent ordinary output.
                        self.password_requested.set(); self.emit('status', 'Password required'); self.emit('password_required', None)
                    elif state == 'prompt' and (prompt := PROMPT.search(observed)):
                        dialect = 'CC Cluster' if prompt.group(1).lower() == 'ccc' else 'DXSpider'
                        with self.lock: self.dialect = dialect
                        if not early_prompt: self.emit('dialect', dialect)
                        startup = ['set/ve7cc', 'sh/mydx/100']
                        if dialect == 'CC Cluster': startup.append('sh/settings')
                        for command in startup:
                            sock.sendall(command.encode('ascii') + b'\r\n')
                            self._command_sent(queued=False)
                            self.emit('sent', command)
                        with self.lock: self.ready = True
                        if password_sent:
                            self.emit('notice','Password-login output was hidden; spots in that response may be missing. Recent history was requested.')
                        state = 'ready'; password_sent = False; self.emit('status', 'Connected')
        except (OSError, UnicodeError) as exc:
            if not self.stop.is_set(): self.emit('error', str(exc))
        finally:
            with self.lock:
                was_busy=self.mail_busy; self.mail_busy=False
            if was_busy: self.emit('mail_status','Mail exchange ended; delivery may be unknown.')
            self.password_requested.clear()
            try: self.passwords.get_nowait()
            except queue.Empty: pass
            if password_sent and state != 'ready' and not self.stop.is_set():
                self.emit('error', 'Authentication did not complete; the node may have rejected the password.')
            with self.lock: self.sock = None; self.ready = False
            if sock:
                try: sock.close()
                except OSError: pass
            self.emit('status', 'Disconnected')

    def provide_password(self, password):
        """Supply a password only to a currently waiting login, without persisting it."""
        if not self.password_requested.is_set() or self.stop.is_set(): raise OSError('The password request is no longer active.')
        if not password or len(password) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in password):
            raise ValueError('Enter a password without control characters, up to 200 characters.')
        encoded = password.encode('cp1252')
        try: self.passwords.put_nowait(encoded)
        except queue.Full: raise OSError('A password has already been submitted.')

    def send(self, command):
        """Queue one validated command; acceptance by the node remains unconfirmed."""
        self.send_batch((command,))

    def send_tracked(self, command):
        """Queue a command with an identity callers can use to track send events."""
        tracked = TrackedCommand(command)
        self.send_batch((tracked,))
        return tracked

    def send_batch(self, commands):
        """Queue a bounded group atomically, respecting session state and pacing limits."""
        commands = tuple(commands)
        if not 1 <= len(commands) <= 10: raise ValueError('Queue between 1 and 10 commands.')
        for command in commands:
            command.encode('ascii')
            if not command or '\r' in command or '\n' in command: raise ValueError('Enter one command per line.')
        with self.lock:
            if self.stop.is_set() or self.sock is None or not self.ready: raise OSError('Not connected')
            if self.mail_busy: raise OSError('A mail exchange is in progress.')
            # DXSpider treats literal backslash-n as a command separator,
            # even though the socket receives a single CRLF-terminated line.
            count=sum(command.count('\\n')+1 for command in commands)
            self._check_command_budget(count)
            try: self.commands.put_nowait(commands)
            except queue.Full: raise OSError('Command queue is full')
            self.pending_commands += count

    def send_mail(self, recipient, subject, body, dialect='DXSpider', *, bulletin=False, receipt=False):
        """Reserve the connection for an interactive mail exchange until it finishes."""
        mail=MailCompose(recipient,subject,body,dialect,bulletin=bulletin,receipt=receipt)
        with self.lock:
            if self.stop.is_set() or self.sock is None or not self.ready: raise OSError('Not connected')
            if self.mail_busy: raise OSError('A mail exchange is in progress.')
            self._check_command_budget(1)
            try: self.commands.put_nowait(mail)
            except queue.Full: raise OSError('Command queue is full')
            self.pending_commands += 1
            self.mail_busy=True

    def close(self):
        """Request worker shutdown and interrupt any active socket read."""
        self.stop.set()
        with self.lock:
            if self.sock:
                try: self.sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
