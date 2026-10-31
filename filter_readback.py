"""Bounded best-effort capture of a command response from a live stream."""
import re
from cluster_mail import NODE_PROMPT

CC_START = re.compile(r'^\s*DX Spot Orig Cty Filter\b', re.I)
CC_BANDTIME_START = re.compile(r'^\s*(?:DX Spot BandTime Filter/|BandTime Filter set OFF\b)', re.I)
SPIDER_START = re.compile(
    r'^\s*(?:(?P<header_call>[A-Za-z0-9/-]+)\s*:\s*(?:(?:Old Style Filter\s+(?:input\s+)?)?(?:route|ann|spots|wcy|wwv|rbn))\b'
    r'|(?:No filters defined for|Pas de filtres d\u00e9finis pour|No hay filtros definidos para|'
    r'Keine Filter definiert fuer|Nessun filtro definito per|Nejsou zadne definovane filtry pro|'
    r'Nenhuns filtros definidos para)\s+(?P<empty_call>[A-Za-z0-9/-]+)\s*$)', re.I)


def is_filter_change(command, dialect):
    """Recognize full filter-change commands emitted or accepted by the app."""
    if dialect == 'CC Cluster':
        return bool(re.match(r'^\s*set/(?:filter(?:\s|/)|nofilter(?:\s|$))',command,re.I))
    if dialect == 'DXSpider':
        # DXSpider splits literal backslash-n into separate commands before
        # processing them and resolves abbreviated command path components.
        for part in command.split('\\n'):
            match = re.match(r'^\s*([A-Za-z]+)/+([A-Za-z]+)(?:\s|$)', part)
            if match:
                action, stream = (value.lower() for value in match.groups())
                if (any(name.startswith(action) for name in ('accept', 'reject', 'clear'))
                        and any(name.startswith(stream) for name in ('spots', 'rbn', 'announce', 'wwv', 'wcy', 'route'))):
                    return True
        return False
    return False


class FilterReadback:
    def __init__(self):
        self.state = 'idle'; self.lines = []; self.chars = 0; self.truncated = False; self.dialect = ''; self.command = None; self.call = ''; self.ambiguous = False; self.bandtime = False; self.stale = False; self.session_ended = False

    def queued(self, dialect, call='', *, bandtime=False):
        self.state = 'queued'; self.lines = []; self.chars = 0; self.truncated = False; self.ambiguous = False; self.stale = False; self.session_ended = False
        self.dialect = dialect; self.call = call.strip().upper(); self.bandtime = bandtime

    def sent(self):
        if self.state == 'queued': self.state = 'waiting'

    def feed(self, line):
        if self.state not in ('waiting', 'receiving'): return False
        line = line.rstrip('\r')
        if self.state == 'waiting':
            marker = (CC_BANDTIME_START if self.bandtime else CC_START) if self.dialect == 'CC Cluster' else SPIDER_START if self.dialect == 'DXSpider' else None
            match = marker.search(line) if marker else None
            if not match: return False
            if self.dialect == 'DXSpider' and self.call:
                response_call = match.group('header_call') or match.group('empty_call')
                if response_call.upper() != self.call: return False
            self.state = 'receiving'
        if NODE_PROMPT.fullmatch(line): self.state = 'partial' if self.ambiguous or self.truncated else 'complete'; return True
        if len(self.lines) >= 500 or self.chars + len(line) + 1 > 100000:
            self.truncated = True
        else:
            self.lines.append(line); self.chars += len(line) + 1
        return True

    def interrupt(self):
        if self.state in ('queued', 'waiting', 'receiving'): self.state = 'partial'

    def text(self): return '\n'.join(self.lines)
