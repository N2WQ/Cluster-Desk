"""Read eQSL.cc's public dated Authenticity Guaranteed member list."""
from dataclasses import dataclass
from datetime import date, datetime
import os
from pathlib import Path
import re
import tempfile
from urllib.request import urlopen
from cluster_dates import parse_cluster_date

URL = 'https://eqsl.cc/qslcard/DownloadedFiles/AGMemberListDated.txt'
MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 1_000_000
HEADER = re.compile(r'List of AG members as of (\d{1,2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}) UTC - Dated\Z',re.I)
CALL = re.compile(r'[A-Z0-9/#-]{1,64}\Z')
DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}\Z')


@dataclass(frozen=True)
class Activity:
    calls: dict
    list_time: str
    skipped: int


def read_activity(path):
    with open(path,'rb') as stream:data=stream.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:raise ValueError('eQSL AG list is too large.')
    lines=data.decode('cp1252',errors='replace').splitlines()
    header=HEADER.fullmatch(lines[0].strip()) if lines else None
    if not header:raise ValueError('This is not a dated eQSL AG member list.')
    try:
        day,clock=header.group(1).split()
        hour,minute=map(int,clock.split(':'))
        stamp=datetime.combine(parse_cluster_date(day),datetime.min.time()).replace(hour=hour,minute=minute)
    except ValueError:raise ValueError('Invalid eQSL list timestamp.') from None
    calls={};skipped=0
    for number,line in enumerate(lines[1:],2):
        if number>MAX_ROWS+1:raise ValueError('eQSL AG list has too many rows.')
        call,separator,day=line.rpartition(',');call=call.strip();day=day.strip()
        if not separator or not DATE.fullmatch(day):raise ValueError(f'Invalid eQSL AG row {number}.')
        if day!='0000-00-00':
            try:date.fromisoformat(day)
            except ValueError:raise ValueError(f'Invalid eQSL AG date on row {number}.') from None
        if not call.isascii():skipped+=1;continue
        call=call.upper()
        if not CALL.fullmatch(call):skipped+=1;continue
        if day>calls.get(call,''):calls[call]=day
    if not calls:raise ValueError('No usable calls were found in the eQSL AG list.')
    return Activity(calls,stamp.strftime('%Y-%m-%d %H:%M UTC'),skipped)


def download_activity(destination):
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.eqsl-',suffix='.tmp',dir=destination.parent)
    try:
        with os.fdopen(fd,'wb') as output,urlopen(URL,timeout=20) as response:
            total=0
            while True:
                chunk=response.read(65536)
                if not chunk:break
                total+=len(chunk)
                if total>MAX_BYTES:raise ValueError('eQSL AG download is too large.')
                output.write(chunk)
        index=read_activity(name);os.replace(name,destination);return index
    finally:
        if os.path.exists(name):os.unlink(name)
