"""DXSpider chat commands and recognizable live group messages."""
import re
from cluster_mail import SPIDER_PROMPT


def group_command(action, group, text='', history_lines=''):
    if not group.isascii(): raise ValueError('Enter a group using ASCII letters, numbers, #, _ or -.')
    group=group.strip().upper()
    if action in ('History', 'Find groups'):
        if action == 'History' and group and not re.fullmatch(r'[A-Z0-9#][A-Z0-9#_-]{0,31}', group):
            raise ValueError('Enter a group using letters, numbers, #, _ or -.')
        count = history_lines.strip()
        if count and (not re.fullmatch(r'[0-9]{1,4}', count) or not 1 <= int(count) <= 1000):
            raise ValueError('Enter a history count from 1 to 1000, or leave it empty for the node default.')
        if action == 'Find groups': return 'SH/GROUPS' + (' ' + count if count else '')
        return 'SH/CHAT' + (' ' + group if group else '') + (' ' + count if count else '')
    if not re.fullmatch(r'[A-Z0-9#][A-Z0-9#_-]{0,31}',group):
        raise ValueError('Enter a group using letters, numbers, #, _ or -.')
    if action == 'Join': return 'JOIN '+group
    if action == 'Leave': return 'LEAVE '+group
    if action == 'Send':
        text=text.strip()
        if not text or len(text)>400 or any(not 32<=ord(c)<=126 for c in text):
            raise ValueError('Enter a one-line message of up to 400 printable ASCII characters.')
        if '\\n' in text:
            raise ValueError('The characters \\n cannot be used in chat messages because they separate commands.')
        return 'CHAT '+group+' '+text
    raise ValueError('Choose a supported chat action.')


def parse_chat(line, login, groups):
    line=line.rstrip('\r').strip().rstrip('\a').rstrip()
    match=re.fullmatch(r'([A-Za-z0-9#][A-Za-z0-9#_-]{0,31})\s+de\s+([A-Za-z0-9/-]+):\s*(\S.*)',line,re.I)
    if match and match.group(1).upper() in groups and match.group(1).upper() not in {login.upper(),'DX','WWV','WCY','WX','ANNOUNCE'}: return line
    return None


def parse_group_change(line):
    match=re.fullmatch(r'\s*(joining|leaving) group ([A-Za-z0-9#][A-Za-z0-9#_-]{0,31})\s*',line,re.I)
    return (match.group(1).lower(),match.group(2).upper()) if match else None


class GroupReadback:
    """Collect current-user groups only after a recognized, complete response."""
    def __init__(self, login):
        self.login=login.upper();self.state='queued';self.call=None;self.parts=[];self.group_seen=False;self.in_group=False;self.lines=0;self.groups=None

    def sent(self):
        if self.state=='queued':self.state='waiting'

    def feed(self, line):
        if self.state not in ('waiting','receiving'):return False
        line=line.rstrip('\r')
        if SPIDER_PROMPT.fullmatch(line):
            if self.state=='waiting':return False
            values=[value.strip().upper() for value in ','.join(self.parts).split(',') if value.strip()]
            if self.call==self.login and self.group_seen and len(values)<=100 and all(re.fullmatch(r'[A-Z0-9#][A-Z0-9#_-]{0,31}',value) for value in values):
                self.groups=set(values);self.state='complete'
            else:self.state='partial'
            return True
        self.lines+=1
        if self.lines>500:self.state='partial';return True
        field=re.match(r'^\s*(Callsign|Group)\s*:\s*(.*?)\s*$',line,re.I)
        if field:
            self.state='receiving';self.in_group=field.group(1).lower()=='group'
            if (self.in_group and self.group_seen) or (not self.in_group and self.call is not None):
                self.state='partial';return True
            if self.in_group:self.group_seen=True;self.parts.append(field.group(2))
            else:self.call=field.group(2).upper()
            if sum(map(len,self.parts))>4000:self.state='partial'
            return True
        continuation=re.match(r'^\s*:\s*(.*?)\s*$',line)
        if self.in_group and continuation:
            self.parts.append(continuation.group(1))
            if sum(map(len,self.parts))>4000:self.state='partial'
            return True
        if re.match(r'^\s*[^:]+\s*:',line):self.in_group=False
        return False

    def interrupt(self):
        if self.state in ('queued','waiting','receiving'):self.state='partial'
