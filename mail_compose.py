"""Bounded mail composition, advanced only by recognized node prompts."""
import re


# Pinned DXSpider Messages templates, with only its node alias interpolated.
_REGISTRATION_RESPONSES = (
    ('Sorry, you need to be registered (SP ', ' to register)'),
    ("Désolé, vous devez d'abord vous enregistrer (SP ", ' pour ce faire)'),
    ('Lo siento, necesitas registrarte (SP ', " para registrarte)', "),
    ('Sorry, Du musst Dich registrieren (SP ', ' to register)'),
    ('Spiacente, devi essere registrato  (SP ', ' per registrarti)'),
    ('Lituji, musis byt registrovan (SP ', ' k registraci)'),
    ('Desculpe, voc precisa ser registado (SP ', ' para registrar)'),
)
_NO_RECIPIENT_RESPONSES = (
    '*** No station specified ***', '*** Geen station gespecifiseerd ***',
    '*** Aucune station spécifiée ***', '*** No se ha especificado una estación ***',
    '*** Keine Station angegeben ***', '*** Nessuna stazione specificata ***',
    '*** Zadna stanice specifikovana ***', '*** Nenhuma estao especificada ***',
)
_REGISTRATION = re.compile(r'^(?:' + '|'.join(re.escape(start) + r'[A-Z0-9/-]+' + re.escape(end.rstrip()) for start, end in _REGISTRATION_RESPONSES) + r')$', re.I)
_NO_RECIPIENT = re.compile(r'^(?:' + '|'.join(re.escape(value) for value in _NO_RECIPIENT_RESPONSES) + r')$', re.I)


class MailCompose:
    def __init__(self, recipient, subject, body, dialect='DXSpider', *, bulletin=False, receipt=False):
        if dialect not in ('DXSpider','CC Cluster'): raise ValueError('Connect to a supported node first.')
        self.dialect=dialect
        if not isinstance(bulletin, bool) or not isinstance(receipt, bool): raise ValueError('Invalid mail options.')
        if dialect != 'DXSpider' and (bulletin or receipt): raise ValueError('These mail options require DXSpider.')
        subject=subject.strip()
        if not recipient.isascii(): raise ValueError('Enter ASCII recipient addresses separated by commas.')
        recipients=tuple(value.strip().upper() for value in recipient.split(','))
        if len(recipients)>10 or (dialect=='CC Cluster' and len(recipients)!=1):
            raise ValueError('Enter up to 10 comma-separated recipients for DXSpider, or one for CC Cluster.')
        if any(not re.fullmatch(r'[A-Z0-9]+(?:-[0-9]{1,2})?', value) or len(value)>20 for value in recipients):
            raise ValueError('Enter recipient addresses separated by commas, optionally with numeric SSIDs.')
        if len(set(recipients)) != len(recipients): raise ValueError('Remove duplicate recipients.')
        if not subject or len(subject)>30 or any(not 32<=ord(c)<=126 for c in subject):
            raise ValueError('Enter a subject of up to 30 printable ASCII characters.')
        lines=body.replace('\r\n','\n').split('\n')
        if not body.strip() or len(lines)>50 or len(body)>4000:
            raise ValueError('Enter a message of up to 50 lines and 4000 characters.')
        if any(len(line)>200 or line.startswith('/') or any(not 32<=ord(c)<=126 for c in line) for line in lines):
            raise ValueError('Use printable ASCII, at most 200 characters per line; lines cannot start with /.')
        if dialect == 'DXSpider' and any(value in ('B', 'P', 'RR', 'COPY', 'C', 'CC') or value.startswith(('NOP', 'PRI')) for value in recipients):
            raise ValueError('Enter a recipient or bulletin address, not a mail option.')
        self.recipients=recipients;self.subject=subject;self.lines=lines;self.bulletin=bulletin;self.receipt=receipt
        self.state='subject';self.observed='';self.saved=False;self.saved_recipients=set();self.rejection=''

    def start(self):
        options = (['NOPRIVATE'] if self.bulletin else []) + (['RR'] if self.receipt else [])
        return ' '.join(['SEND', *options, *self.recipients])

    def feed(self, text):
        self.observed=(self.observed+text)[-8192:]
        subject_prompt = (r'Enter Subject \(30 characters\):' if self.dialect=='DXSpider'
                          else r'Enter Subject \(30 characters max\):')
        body_prompt = (r'Enter lines of text, /EX to send or /ABORT to exit' if self.dialect=='DXSpider'
                       else r'Enter body of message,  Ctrl-Z or /EXit to end, /CAncel to cancel')
        if self.state == 'subject' and self.dialect == 'DXSpider':
            prompts = list(re.finditer(r'(?:^|[\r\n])[ \t]*[A-Z0-9/-]+\s+de\s+[A-Z0-9/-]+(?:\s+[^:\r\n]*)?\s+dxspider\s*>[ \t]*(?:[\r\n]|$)', self.observed, re.I))
            if prompts:
                prompt = prompts[-1]
                complete_lines = self.observed[:prompt.start()+1].splitlines()
                for line in complete_lines:
                    if _REGISTRATION.fullmatch(line.strip()): self.rejection = 'registration required'
                    elif _NO_RECIPIENT.fullmatch(line.strip()): self.rejection = 'no valid recipient'
                if self.rejection:
                    self.state = 'rejected'; return []
        if self.state=='subject' and re.search(r'(?:^|[\r\n])[ \t]*'+subject_prompt,self.observed,re.I):
            self.observed='';self.state='body';return [self.subject]
        if self.state=='body' and re.search(r'(?:^|[\r\n])[ \t]*'+body_prompt,self.observed,re.I):
            self.observed='';self.state='result';return self.lines+['/EX']
        if self.state=='result':
            if self.dialect=='DXSpider':
                for match in re.finditer(r'(?:^|[\r\n])[ \t]*Message no \d+ saved and directed to[ \t]+([A-Z0-9]+(?:-[0-9]{1,2})?)[ \t]*(?=\r\n|\n|\r)',self.observed,re.I):
                    if match.group(1).upper() in self.recipients: self.saved_recipients.add(match.group(1).upper())
                self.saved = len(self.saved_recipients) == len(self.recipients)
            if self.dialect=='CC Cluster' and re.search(r'(?:^|[\r\n])[ \t]*Message #\d+ accepted[ \t]*(?:\r\n|\n|\r)',self.observed,re.I): self.saved=True
            node = 'dxspider' if self.dialect=='DXSpider' else 'CCC'
            if re.search(r'(?:^|[\r\n])[ \t]*[A-Z0-9/-]+(?:\s+de\s+[A-Z0-9/-]+(?:\s+[^:\r\n]*)?)?\s+'+node+r'\s*>[ \t]*(?:[\r\n]|$)',self.observed,re.I):
                self.state='complete';return []
        return None
