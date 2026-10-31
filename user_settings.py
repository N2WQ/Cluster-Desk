"""Bounded, conservative DXSpider current-user settings readback."""
import re
from cluster_mail import SPIDER_PROMPT

FIELDS = {
    'req dx spots': 'Req DX Spots', 'req announce': 'Req Announce',
    'req talk': 'Req Talk', 'req wwv': 'Req WWV', 'req wcy': 'Req WCY',
    'req wx': 'Req WX', 'show cq zone': 'Spider CQ display',
    'show itu zone': 'Spider ITU display', 'show dx grid': 'Spider grid display',
    'show us state': 'Spider US state display',
    'req beep': 'Req Beep', 'login info req': 'Login Info Req',
    'talklike anns': 'Spider AnnTalk',
    'rbn see me': 'Spider RBN SeeMe',
    'allow ftx': 'Spider FT spots', 'allow autoftx': 'Spider auto FT spots',
    'want rbn spots': 'Spider RBN', 'want rbn cw': 'Spider RBN CW',
    'want rbn beacon': 'Spider RBN Beacon', 'want rbn psk': 'Spider RBN PSK',
    'want rbn rtty': 'Spider RBN RTTY', 'want rbn ft4/8': 'Spider RBN FT',
}

RBN_FIELDS = ('Spider RBN', 'Spider RBN CW', 'Spider RBN Beacon', 'Spider RBN PSK', 'Spider RBN RTTY', 'Spider RBN FT')

def skimmer_command(action, categories=()):
    if action == 'all': return 'set/skimmer'
    if action == 'off': return 'unset/skimmer'
    if action != 'selected': raise ValueError('Choose a skimmer action.')
    allowed = ('CW', 'BEACON', 'PSK', 'RTTY', 'FT')
    selected = set(categories)
    if not selected or not selected.issubset(allowed): raise ValueError('Select at least one supported category.')
    return 'set/skimmer ' + ' '.join(name for name in allowed if name in selected)

class SettingsReadback:
    def __init__(self, call):
        self.call = call.upper(); self.state = 'queued'; self.login = None
        self.values = {}; self.lines = 0; self.invalid = False

    def sent(self):
        if self.state == 'queued': self.state = 'waiting'

    def feed(self, line):
        if self.state not in ('waiting', 'receiving'): return False
        line = line.rstrip('\r')
        if SPIDER_PROMPT.fullmatch(line):
            if self.state == 'waiting': return False
            self.state = 'complete' if self.login == self.call and self.values and not self.invalid else 'partial'
            return True
        self.lines += 1
        if self.lines > 500:
            self.state = 'partial'; return True
        match = re.fullmatch(r'\s*([^:]+):\s*(.*?)\s*', line)
        if not match: return False
        name, value = match.group(1).strip().casefold(), match.group(2)
        if name == 'callsign':
            if self.login is not None: self.invalid = True
            self.login = value.upper(); self.state = 'receiving'; return True
        if name in FIELDS:
            self.state = 'receiving'
            key = FIELDS[name]; normalized = value.casefold()
            if key in self.values or normalized not in ('yes', 'no'): self.invalid = True
            else: self.values[key] = normalized.capitalize()
            return True
        return False

    def interrupt(self):
        if self.state in ('queued', 'waiting', 'receiving'): self.state = 'partial'
