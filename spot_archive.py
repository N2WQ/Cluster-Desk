"""Read Cluster Desk CSV recordings without replaying them as live spots."""
import csv
import re
from collections import deque
from pathlib import Path
from datetime import datetime
import json

from spot_log import HEADER, MAX_ROW_CHARS, MAX_FILE_BYTES, MAX_RECORDS
from protocol import Spot, valid_spot_time
from cluster_dates import parse_cluster_date

MAX_SHOWN = 1000


def spot_from_archive(row):
    """Validate one saved row before it enters the spot workspace."""
    dx=row.get('dx_call','').strip();spotter=row.get('spotter','').strip()
    frequency=row.get('frequency_khz','').strip();stamp=row.get('time_utc','').strip().upper()
    if not re.fullmatch(r'[A-Za-z0-9/#-]{1,32}',dx) or not re.fullmatch(r'[A-Za-z0-9/#-]{1,32}',spotter):
        raise ValueError('A saved record has an invalid callsign.')
    if not re.fullmatch(r'\d{1,12}(?:\.\d{1,9})?',frequency):raise ValueError('A saved record has an invalid frequency.')
    if not valid_spot_time(stamp):raise ValueError('A saved record has an invalid UTC time.')
    if not stamp.endswith('Z'):stamp+='Z'
    date=row.get('date','').strip()
    if date:
        try:parse_cluster_date(date)
        except ValueError:raise ValueError('A saved record has an invalid date.') from None
    def fields(name):
        value=row.get(name,'')
        if not value:return ()
        try:result=json.loads(value)
        except (ValueError,TypeError):raise ValueError('A saved record has invalid metadata.') from None
        if not isinstance(result,list) or len(result)>100 or any(not isinstance(item,str) for item in result):
            raise ValueError('A saved record has invalid metadata.')
        return tuple(result)
    return Spot(spotter,frequency,dx,stamp,row.get('comment',''),date,
                *(row.get(key,'') for key in ('dx_dxcc','spotter_dxcc','origin','dx_itu','dx_cq','spotter_itu','spotter_cq','dx_state','spotter_state','dx_country','spotter_country','dx_locator','spotter_locator')),
                extra_fields=fields('extra_fields_json'),metadata_fields=fields('metadata_fields_json'))



def date_range(start, end):
    """Parse inclusive ISO dates entered by the operator."""
    def parse(value):
        if not value: return None
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value): raise ValueError('Use dates in YYYY-MM-DD format.')
        try: return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError: raise ValueError('Use dates in YYYY-MM-DD format.') from None
    first, last = parse(start.strip()), parse(end.strip())
    if first and last and first > last: raise ValueError('The start date must not be after the end date.')
    return first, last


def record_in_date_range(row, first, last):
    if first is None and last is None: return True
    try: date = parse_cluster_date(row.get('date', ''))
    except ValueError: return False
    return (first is None or date >= first) and (last is None or date <= last)


def load_archive(path, cancelled=None):
    result = search_archive(path, cancelled=cancelled)
    return None if result is None else (result[0], result[2])


def search_archive(path, cancelled=None, predicate=None):
    rows = deque(maxlen=MAX_SHOWN); count = 0; matched = 0
    with Path(path).open('r', encoding='utf-8', newline='') as file:
        file.seek(0, 2)
        if file.tell() > MAX_FILE_BYTES: raise ValueError('The file exceeds the 20 MB viewer limit.')
        file.seek(0)
        reader = csv.reader(file, strict=True)
        header = next(reader, None)
        if header is None or len(header) < 10 or tuple(header) != HEADER[:len(header)]:
            raise ValueError('This is not a supported Cluster Desk spot recording.')
        for record in reader:
            if cancelled and cancelled.is_set(): return None
            if len(record) != len(header) or sum(map(len, record)) > MAX_ROW_CHARS:
                raise ValueError(f'Invalid or oversized record at line {reader.line_num}.')
            count += 1
            if count > MAX_RECORDS: raise ValueError('The file exceeds the 100,000-record viewer limit.')
            row = dict(zip(header, record))
            if predicate is None or predicate(row):
                matched += 1; rows.append(row)
    return count, matched, list(rows)


def search_recordings(paths, cancelled=None, predicate=None):
    """Search selected CSV files in supplied order, retaining the last matching rows."""
    paths=tuple(paths)
    if not 1 <= len(paths) <= 50:raise ValueError('Select between 1 and 50 recording files.')
    rows=deque(maxlen=MAX_SHOWN);total=matched=0
    for path in paths:
        if cancelled and cancelled.is_set():return None
        result=search_archive(path,cancelled,predicate)
        if result is None:return None
        count,found,records=result;total+=count;matched+=found
        for record in records:
            row=dict(record);row['recording_file']=str(path);rows.append(row)
    return total,matched,list(rows)


def search_ve7cc_archive(path, cancelled=None, predicate=None):
    """Read the observed 19-field VE7CC log variant without decoding ambiguous escapes."""
    names = ('time_utc', 'frequency_khz', 'dx_call', 'spotter', 'origin', 'comment',
             'field_7', 'dx_itu', 'spotter_itu', 'dx_cq', 'spotter_cq', 'dx_locator',
             'spotter_locator', 'dx_country', 'spotter_country', 'dx_state', 'spotter_state',
             'field_18', 'terminator')
    rows=deque(maxlen=MAX_SHOWN); count=matched=0
    with Path(path).open('r', encoding='cp1252', newline='') as file:
        file.seek(0, 2)
        if file.tell()>MAX_FILE_BYTES: raise ValueError('The file exceeds the 20 MB viewer limit.')
        file.seek(0)
        for line in file:
            if cancelled and cancelled.is_set(): return None
            if len(line)>MAX_ROW_CHARS: raise ValueError('An original log record exceeds the viewer limit.')
            fields=line.rstrip('\r\n').split(',')
            if (len(fields)!=19 or fields[-1]!='~' or not re.fullmatch(r'\d{4}',fields[0])
                    or not re.fullmatch(r'\d+(?:\.\d+)?',fields[1]) or not fields[2] or not fields[3]):
                raise ValueError(f'Unsupported original log record at line {count+1}.')
            count+=1
            if count>MAX_RECORDS: raise ValueError('The file exceeds the 100,000-record viewer limit.')
            row=dict(zip(names,fields)); row['date']=''
            if predicate is None or predicate(row): matched+=1;rows.append(row)
    return count,matched,list(rows)
