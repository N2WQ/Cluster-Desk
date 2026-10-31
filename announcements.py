"""Recognize observed cluster announcement lines without changing their text."""
import re

CC_HISTORY = re.compile(r'^\s*\d{1,2}-[A-Za-z]{3}\s+\d{4}Z\s+\S+\s+\S+:\s+\S', re.I)
SPIDER_HISTORY = re.compile(r'^\s*\d{1,2}[A-Za-z]{3}\d{4}@\d{2}:\d{2}:\d{2}\s+\S+\s+->\s+\S+\s+\S', re.I)
LIVE_ANNOUNCE = re.compile(r'^\s*(?:to\s+[A-Za-z0-9#][A-Za-z0-9#_/-]{0,63}|wx|announce)\s+de\s+\S+(?:\s+<[^>]+>)?\s*:\s*\S', re.I)
LIVE_TALK = re.compile(r'^\s*(?:login:\s*)?([A-Za-z0-9/-]+)\s+de\s+([A-Za-z0-9/-]+):\s*(\S.*)$', re.I)
WEATHER = re.compile(r'^\s*(?:wx\s+de\b|to\s+wx\s+de\b|\d{1,2}-[A-Za-z]{3}\s+\d{4}Z\s+WX\s+|\d{1,2}[A-Za-z]{3}\d{4}@\d{2}:\d{2}:\d{2}\s+\S+\s+->\s+WX\s+)', re.I)



def parse_pc12(line):
    """Recognize the DXSpider PC12 wire variant; the raw packet stays in Activity."""
    line=line.rstrip('\r\n')
    if not line.startswith('PC12^') or not line.endswith(('^','^~')): return None
    fields=line.split('^')
    if len(fields)<9 or not re.fullmatch(r'[A-Za-z0-9/#-]+',fields[1]) or not fields[3].strip() or fields[6] not in ('0','1'): return None
    flag=fields[4].strip()
    if flag and flag!='*' and not re.fullmatch(r'[A-Za-z0-9#._/-]{1,64}',flag):return None
    if fields[6]=='1':label='WX'
    elif flag=='*':label='To SYSOP'
    elif flag:label=f'PC12 target {flag}'
    else:label='Announcement'
    return f'{label} de {fields[1]}: {fields[3]}'


def parse_announcement(line):
    line = line.rstrip('\r').strip().rstrip('\a').rstrip()
    packet = parse_pc12(line)
    if packet: return packet
    if CC_HISTORY.match(line) or SPIDER_HISTORY.match(line) or LIVE_ANNOUNCE.match(line):
        return line
    return None


def is_weather(line):
    return bool(WEATHER.match(line))


def parse_talk(line, login):
    line = line.rstrip('\r').strip()
    match = LIVE_TALK.match(line)
    if match and match.group(1).upper() == login.upper(): return line
    return None



def talk_sender(line, login):
    """Return a replyable sender only for a direct message to this login."""
    match = LIVE_TALK.fullmatch(line.rstrip('\r').strip())
    if not match or match.group(1).upper() != login.upper(): return None
    sender = match.group(2).upper()
    if len(sender) > 20 or not re.fullmatch(r'[A-Z0-9]+(?:-[0-9]+)?', sender): return None
    return sender


def message_command(dialect, kind, text, recipient=''):
    if dialect not in ('DXSpider', 'CC Cluster'): raise ValueError('This node type is not supported by the message form.')
    text = text.strip()
    if not text or len(text) > 400 or any(not 32 <= ord(char) <= 126 for char in text):
        raise ValueError('Enter up to 400 printable ASCII characters on one line.')
    if dialect == 'DXSpider' and '\\n' in text:
        raise ValueError('The characters \\n cannot be used in messages to DXSpider because they separate commands.')
    if kind == 'Local announcement': return ('ANNOUNCE LOCAL ' if dialect == 'DXSpider' else 'ANNOUNCE ') + text
    if kind == 'Network announcement': return ('ANNOUNCE FULL ' if dialect == 'DXSpider' else 'ANNOUNCE/FULL ') + text
    if kind == 'Direct message':
        recipient = recipient.strip()
        if not recipient.isascii(): raise ValueError('Enter an ASCII callsign, optionally followed by a numeric SSID.')
        recipient = recipient.upper()
        if not re.fullmatch(r'[A-Z0-9]+(?:-[0-9]+)?', recipient) or len(recipient) > 20:
            raise ValueError('Enter a callsign, optionally followed by a numeric SSID.')
        return f'TALK {recipient} {text}'
    raise ValueError('Select a message type.')


def message_commands(dialect, kind, text, recipients=''):
    if kind != 'Direct message': return [message_command(dialect, kind, text)]
    if not recipients.isascii(): raise ValueError('Enter ASCII callsigns for all recipients.')
    calls = [value.strip().upper() for value in recipients.split(',')]
    if len(calls) > 10: raise ValueError('Enter no more than 10 recipients.')
    if len(set(calls)) != len(calls): raise ValueError('Remove duplicate recipients.')
    return [message_command(dialect, kind, text, call) for call in calls]
