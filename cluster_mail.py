"""Recognize documented CC Cluster and DXSpider mail directory output."""
import re

CC_HEADER = re.compile(r'^\s*Msg\s+Size\s+To\s+From\s+Date\s+Time\s+Subject\s*$', re.I)
CC_ROW = re.compile(r'^\s*(\d+)\s+\d+\s+\S+\s+\S+\s+\d{1,2}-[A-Za-z]{3}\s+\d{4}Z\s+\S', re.I)
SPIDER_ROW = re.compile(r'^\s*(\d+)[ !DE-][ p]\s*\d+\s+\S+\s+\S+\s+\d{1,2}-[A-Za-z]{3}\s+\d{4}Z(?:\s+.*)?\s*$', re.I)
NO_MAIL = re.compile(r'^\s*(?:No messages found|\*\*\* no records found\.)\s*$', re.I)
SPIDER_NO_MAIL = re.compile(r'^\s*(?:Aucun message trouvé !|No hay mensajes nuevos|Keine Nachricht gefunden|Nessun messaggio trovato|Zadne zpravy nebyly nalezeny|Nenhumas mensagens encontradas)\s*$', re.I)
SPIDER_PROMPT = re.compile(r'^\s*(?:[A-Z0-9/-]+|\([A-Z0-9/-]+\))\s+de\s+[A-Z0-9/-]+(?:\s+[^:\r\n]*)?\s+dxspider\s*>\s*$', re.I)
_NODE_PROMPT_BODY = (r'(?:[A-Z0-9/-]+(?:\s+de\s+[A-Z0-9/-]+(?:\s+[^:\r\n]*?)?)?\s+(?:CCC|dxspider)'
                     r'|\([A-Z0-9/-]+\)\s+de\s+[A-Z0-9/-]+(?:\s+[^:\r\n]*?)?\s+dxspider)\s*>')
NODE_PROMPT = re.compile(r'^\s*' + _NODE_PROMPT_BODY + r'\s*$', re.I)
NODE_PROMPT_PREFIX = re.compile(r'^\s*' + _NODE_PROMPT_BODY, re.I)

# Exact read2/read3 templates from the pinned DXSpider Messages file.
SPIDER_READ_ERRORS = (
    ('Msg', 'not found'), ('Msg', 'not available'),
    ('Bericht', 'niet gevonden'), ('Bericht', 'niet beschikbaar'),
    ('Message', 'introuvable'), ('Message', 'non disponible'),
    ('Mensaje', 'no encontrado'), ('Mensaje', 'no disponible'),
    ('Msg', 'nicht gefunden'), ('Msg', 'nicht verfuegbar'),
    ('Messaggio', 'non trovato'), ('Messaggio', 'non disponibile'),
    ('Zprava', 'nenalezena'), ('Msg', 'neni k dispozici'),
    ('Msg', 'no foi encontrada'), ('Msg', 'no est disponivel'),
)
SPIDER_READ_ERROR = re.compile(r'^\s*(?:' + '|'.join(
    re.escape(prefix) + r'\s+(?P<n' + str(index) + r'>\d{1,9})\s+' + re.escape(suffix)
    for index, (prefix, suffix) in enumerate(SPIDER_READ_ERRORS)
) + r')\s*$', re.I)


def spider_read_error_number(line):
    match = SPIDER_READ_ERROR.fullmatch(line)
    return next((int(value) for value in match.groups() if value is not None), None) if match else None


def split_prompt_prefix(line):
    match = NODE_PROMPT_PREFIX.match(line)
    if match and line[match.end():].strip(): return [line[:match.end()], line[match.end():]]
    return [line]


class MailDirectory:
    def __init__(self):
        self.state='idle'; self.rows=[]; self.truncated=False; self.dialect=''; self.command=''

    def queued(self, dialect, command='dir'):
        self.state='queued'; self.rows=[]; self.truncated=False; self.dialect=dialect; self.command=command

    def sent(self):
        if self.state=='queued': self.state='waiting'

    def feed(self, line):
        if self.state not in ('waiting','receiving'): return False
        line=line.rstrip('\r')
        pattern=CC_ROW if self.dialect=='CC Cluster' else SPIDER_ROW if self.dialect=='DXSpider' else None
        row=pattern.match(line) if pattern else None
        if self.state=='waiting':
            if (self.dialect=='CC Cluster' and CC_HEADER.match(line)) or row or NO_MAIL.match(line) or (self.dialect=='DXSpider' and SPIDER_NO_MAIL.match(line)): self.state='receiving'
            else: return False
        if NODE_PROMPT.fullmatch(line): self.state='complete'; return True
        if row:
            if len(self.rows)<500: self.rows.append((row.group(1), line.strip()))
            else: self.truncated=True
        return True

    def interrupt(self):
        if self.state in ('queued','waiting','receiving'): self.state='partial'


class MailRead:
    """Capture a recognized DXSpider response or unverified CC Cluster output."""
    def __init__(self, number, dialect="DXSpider"):
        self.dialect = dialect
        self.number = int(number); self.state = 'queued'; self.lines = []; self.chars = 0; self.truncated = False

    def sent(self):
        if self.state == 'queued': self.state = 'waiting'

    def feed(self, line):
        if self.state not in ('waiting', 'receiving'): return False
        line = line.rstrip('\r')
        if self.state == 'waiting':
            if self.dialect == 'CC Cluster':
                if not line.strip() or NODE_PROMPT.fullmatch(line): return False
            else:
                header = re.match(r'^\s*Msg:\s*(\d{1,9})\s+From:\s*\S+\s+Date:.*\bSubj:', line, re.I)
                error_number = spider_read_error_number(line)
                if (not header or int(header.group(1)) != self.number) and error_number != self.number: return False
            self.state = 'receiving'
        elif (NODE_PROMPT if self.dialect == 'CC Cluster' else SPIDER_PROMPT).fullmatch(line):
            self.state = 'complete'; return True
        if len(self.lines) < 1000 and self.chars + len(line) <= 100000:
            self.lines.append(line); self.chars += len(line)
        else: self.truncated = True
        return True

    def reply_draft(self):
        if self.state != 'complete' or not self.lines or self.truncated: return None
        pattern = r'\s*Msg:\s*(\d{1,9})\s+From:\s*([A-Za-z0-9]+(?:-[0-9]{1,2})?)\s+Date:.*?\bSubj:\s*(.*?)\s*'
        candidates = self.lines if self.dialect == 'CC Cluster' else self.lines[:1] if self.dialect == 'DXSpider' else []
        headers = [match for line in candidates if (match := re.fullmatch(pattern, line, re.I)) and int(match.group(1)) == self.number]
        if len(headers) != 1: return None
        header = headers[0]
        if len(header.group(2)) > 20: return None
        sender, subject = header.group(2).upper(), header.group(3)
        return sender, subject if re.match(r'^Re:\s',subject,re.I) else 'Re: ' + subject

    def interrupt(self):
        if self.state in ('queued', 'waiting', 'receiving'): self.state = 'partial'


def directory_fields(line, dialect):
    """Extract display fields from recognized directory lines, preserving unknown rows."""
    if dialect == 'CC Cluster':
        pattern = r'^\s*(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\d{1,2}-[A-Za-z]{3})\s+(\d{4}Z)\s+(.+?)\s*$'
        match = re.match(pattern, line)
        if match:
            number, size, to, sender, day, time, subject = match.groups()
            return number, '', size, to, sender, f'{day} {time}', subject
    elif dialect == 'DXSpider':
        pattern = r'^\s*(\d+)([ !DE-][ p])\s*(\d+)\s+(\S+)\s+(\S+)\s+(\d{1,2}-[A-Za-z]{3})\s+(\d{4}Z)(?:\s+(.*?))?\s*$'
        match = re.match(pattern, line, re.I)
        if match:
            number, flags, size, to, sender, day, time, subject = match.groups()
            return number, flags.strip(), size, to, sender, f'{day} {time}', subject or ''
    return '', '', '', '', '', '', line.strip()


def directory_reply_draft(line, dialect):
    """Prepare an editable draft from a recognized directory row."""
    number, _, _, _, sender, _, subject = directory_fields(line, dialect)
    if (not number or len(sender) > 20 or not re.fullmatch(r'[A-Za-z0-9]+(?:-[0-9]{1,2})?', sender)
            or not any(char.isdigit() for char in sender.split('-')[0])): return None
    if any(not 32 <= ord(char) <= 126 for char in subject): return None
    subject = subject.strip()
    return sender.upper(), subject if re.match(r'^Re:\s', subject, re.I) else 'Re: ' + subject


def read_command(number):
    number=number.strip()
    if not re.fullmatch(r'\d{1,9}', number) or int(number)==0:
        raise ValueError('Enter a positive message number.')
    return f'READ {int(number)}'


def directory_command(dialect, selection, subject='', count=''):
    if dialect not in ('CC Cluster','DXSpider'): raise ValueError('Connect to a supported node first.')
    commands = {'Recent':'dir', 'Own':'dir/own' if dialect == 'CC Cluster' else 'directory own',
                'New':'dir/new' if dialect == 'CC Cluster' else 'directory new',
                'Subject':'dir/subject' if dialect == 'CC Cluster' else 'directory subject'}
    if dialect == 'CC Cluster': commands['Bulletins'] = 'dir/bulletin'
    else: commands.update({'From':'directory from', 'To':'directory to'})
    if selection not in commands: raise ValueError('Choose a supported mail list.')
    command=commands[selection]
    if selection == 'Subject':
        subject=subject.strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,30}',subject):
            raise ValueError('Enter one subject word using letters, numbers, underscore or hyphen (up to 30 characters).')
        command+=' '+subject
    elif selection in ('From','To'):
        call=subject.strip()
        if not call.isascii():raise ValueError('Enter an ASCII callsign, optionally followed by a numeric SSID.')
        call=call.upper()
        if len(call)>20 or not re.fullmatch(r'[A-Z0-9]+(?:-[0-9]+)?',call):
            raise ValueError('Enter a callsign, optionally followed by a numeric SSID.')
        command+=' '+call
    count=count.strip()
    if count:
        if dialect != 'DXSpider' or not re.fullmatch(r'[0-9]{1,3}',count) or not 1<=int(count)<=500:
            raise ValueError('Enter a message count from 1 to 500 for DXSpider.')
        command+=' '+str(int(count))
    return command
