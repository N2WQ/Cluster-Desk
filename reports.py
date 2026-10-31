"""Recognize observed WWV/WCY text without interpreting propagation values."""
import re
from cluster_mail import NODE_PROMPT

HISTORY_HEADER = re.compile(r'^\s*(?:Date|Fecha)\s+(?:Hour|Heure|Hora)\s+SFI\s+A\s+K\b', re.I)
WCY_HEADER = re.compile(r'\b(?:Exp\.?K|Kpr[ée]vu)\b', re.I)
HISTORY_ROW = re.compile(r'^\s*\d{1,2}-[A-Za-z]{3}-\d{4}\s+\d{1,2}\s+\S+\s+\S+\s+\S+\s+.*<[^<>]+>\s*$', re.I)
LIVE_REPORT = re.compile(r'^\s*(WWV|WCY)\s+de\s+\S+', re.I)


def parse_pc23_or_pc27(line):
    """Display the WWV packet layout documented by the pinned DXSpider source."""
    line=line.strip()
    if not line.startswith(('PC23^','PC27^')) or not line.endswith(('^','^~')):return None
    fields=line.split('^')
    if len(fields)!=11 or not re.fullmatch(r'\d{1,2}-[A-Za-z]{3}-\d{4}',fields[1].strip()):return None
    hour=fields[2].strip();values=[field.strip() for field in fields[3:6]]
    if not re.fullmatch(r'\d{1,2}',hour) or int(hour)>23 or any(not re.fullmatch(r'\d{1,5}',value) for value in values):return None
    logger,node=(field.strip() for field in fields[7:9])
    if not all(re.fullmatch(r'[A-Za-z0-9/#-]{1,32}',value) for value in (logger,node)):return None
    sfi,a,k=values;forecast=fields[6].strip()
    return f"WWV {fields[1].strip()} {int(hour):02d}Z · SFI={sfi}, A={a}, K={k} · {forecast} · {logger} · node {node}"


def parse_pc73(line):
    """Display the WCY packet layout documented by the pinned DXSpider source."""
    line=line.strip()
    if not line.startswith('PC73^') or not line.endswith(('^','^~')):return None
    fields=line.split('^')
    if len(fields)!=15 or not re.fullmatch(r'\d{1,2}-[A-Za-z]{3}-\d{4}',fields[1].strip()):return None
    hour=fields[2].strip();values=[field.strip() for field in fields[3:8]]
    if not re.fullmatch(r'\d{1,2}',hour) or int(hour)>23 or any(not re.fullmatch(r'\d{1,5}',value) for value in values):return None
    sa,gmf,aurora=(field.strip() for field in fields[8:11])
    if not all(re.fullmatch(r'[A-Za-z0-9_-]{1,20}',value) for value in (sa,gmf,aurora)):return None
    logger,node=(field.strip() for field in fields[11:13])
    if not all(re.fullmatch(r'[A-Za-z0-9/#-]{1,32}',value) for value in (logger,node)):return None
    sfi,a,k,expk,r=values
    return f"WCY {fields[1].strip()} {int(hour):02d}Z · SFI={sfi}, A={a}, K={k}, expK={expk}, R={r} · SA={sa}, GMF={gmf}, Au={aurora} · {logger} · node {node}"


class ReportCollector:
    def __init__(self):
        self.history_kind = None

    def feed(self, line):
        line = line.rstrip('\r')
        packet=parse_pc23_or_pc27(line)
        if packet:return 'WWV',packet
        packet=parse_pc73(line)
        if packet:return 'WCY',packet
        if HISTORY_HEADER.match(line):
            self.history_kind = 'WCY' if WCY_HEADER.search(line) else 'WWV'
            return None
        if NODE_PROMPT.fullmatch(line):
            self.history_kind = None
            return None
        live = LIVE_REPORT.match(line)
        if live:
            return live.group(1).upper(), line.strip().rstrip('\a').rstrip()
        if self.history_kind and HISTORY_ROW.match(line):
            return self.history_kind, line.strip()
        return None
