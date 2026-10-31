"""Bounded capture of a selected station's node response."""
import re
from urllib.parse import quote

from cluster_mail import NODE_PROMPT
from locator import grid_center


def qrz_url(call):
    call = call.strip()
    if not call.isascii(): raise ValueError('The selected value is not a supported callsign for QRZ.')
    call = call.upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9/-]{0,31}', call) or not any(char.isdigit() for char in call):
        raise ValueError('The selected value is not a supported callsign for QRZ.')
    return 'https://www.qrz.com/db/' + quote(call, safe='')


def station_command(call):
    call = call.strip()
    if not call.isascii(): raise ValueError('Enter a callsign using ASCII letters, numbers, / or -.')
    call = call.upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9/-]{0,31}', call):
        raise ValueError('Enter a callsign using letters, numbers, / or -.')
    return 'sh/sta ' + call



def profile_command(field, value, *, dialect=''):
    """Build a single own-profile command for CC Cluster or DXSpider."""
    commands={'Name':'name', 'QTH':'qth', 'Grid':'qra'}
    if field not in commands: raise ValueError('Choose a supported profile field.')
    value=value.strip()
    if not value or len(value)>80 or any(not 32<=ord(char)<=126 for char in value):
        raise ValueError('Enter up to 80 printable ASCII characters on one line.')
    if dialect != 'CC Cluster' and '\\n' in value:
        raise ValueError('The characters \\n cannot be used in profile values because some nodes treat them as a command separator.')
    if field == 'Grid':
        grid_center(value); value=value.upper()
    elif '{' in value or '}' in value:
        raise ValueError('Braces are not supported in name or QTH.')
    return f'set/{commands[field]} {value}'


def node_location(lines):
    """Return a single recognized node location, or None if absent/ambiguous."""
    locations=[]
    for line in lines:
        match=re.fullmatch(r'\s*Location\s*:\s*(\d{1,2})\s+(\d{1,2})\s*([NS])\s+(\d{1,3})\s+(\d{1,2})\s*([EW])(?:\s+\([A-Ra-r]{2}\d{2}(?:[A-Xa-x]{2})?\))?\s*',line,re.I)
        if not match:continue
        lat_deg,lat_min,ns,lon_deg,lon_min,ew=match.groups()
        lat_deg,lat_min,lon_deg,lon_min=map(int,(lat_deg,lat_min,lon_deg,lon_min))
        if lat_min>=60 or lon_min>=60 or lat_deg>90 or lon_deg>180 or (lat_deg==90 and lat_min) or (lon_deg==180 and lon_min):continue
        lat=(lat_deg+lat_min/60)*(-1 if ns.upper()=='S' else 1)
        lon=(lon_deg+lon_min/60)*(-1 if ew.upper()=='W' else 1)
        locations.append((lat,lon))
    return locations[0] if len(locations)==1 else None


class StationCapture:
    def __init__(self, call):
        self.command = station_command(call); self.call = self.command.split()[-1]
        self.state = 'queued'; self.lines = []; self.chars = 0; self.truncated = False; self.not_found = False

    def sent(self):
        if self.state == 'queued': self.state = 'waiting'

    def feed(self, line):
        if self.state not in ('waiting','receiving'): return False
        line = line.rstrip('\r')
        if self.state == 'waiting':
            marker = re.match(r'^\s*User\s*:\s*([A-Za-z0-9/-]+)(?:\s|$)', line, re.I)
            missing = re.fullmatch(r'\s*\*\*\* User record for ([A-Za-z0-9/-]+) not found \*\*\*\s*',line,re.I)
            match = marker or missing
            if not match or match.group(1).upper() != self.call: return False
            self.not_found = bool(missing)
            self.state = 'receiving'
        elif NODE_PROMPT.fullmatch(line):
            self.state = 'complete'; return True
        if len(self.lines) < 500 and self.chars + len(line) <= 100000:
            self.lines.append(line); self.chars += len(line)
        else: self.truncated = True
        return True

    def interrupt(self):
        if self.state in ('queued','waiting','receiving'): self.state = 'partial'
