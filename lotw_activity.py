"""Read ARRL's public callsign activity list; no account or login is used."""
import csv
from datetime import datetime
import io
import os
from pathlib import Path
import re
import tempfile
from urllib.request import urlopen

URL = 'https://lotw.arrl.org/lotw-user-activity.csv'
MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 1_000_000
CALL = re.compile(r'[A-Z0-9]+(?:/[A-Z0-9]+)*\Z')
STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\Z')


def read_activity(path):
    with open(path, 'rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('LoTW activity file is too large.')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError as exc:
        raise ValueError('LoTW activity file is not valid UTF-8.') from exc
    result = {}
    try:
        for number, row in enumerate(csv.reader(io.StringIO(text), strict=True), 1):
            if number > MAX_ROWS:
                raise ValueError('LoTW activity file has too many rows.')
            if len(row) != 3:
                raise ValueError(f'Invalid LoTW activity row {number}.')
            call, date, clock = (part.strip() for part in row)
            stamp = date + 'T' + clock
            if len(call) > 64 or not CALL.fullmatch(call) or not STAMP.fullmatch(stamp):
                raise ValueError(f'Invalid LoTW activity row {number}.')
            try:
                datetime.fromisoformat(stamp)
            except ValueError as exc:
                raise ValueError(f'Invalid LoTW activity date on row {number}.') from exc
            if stamp > result.get(call, ''):
                result[call] = stamp
    except csv.Error as exc:
        raise ValueError('Invalid LoTW activity CSV.') from exc
    if not result:
        raise ValueError('LoTW activity file is empty.')
    return result


def download_activity(destination):
    """Validate a bounded download before atomically replacing the local cache."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.lotw-', suffix='.tmp', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as output, urlopen(URL, timeout=20) as response:
            total = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError('LoTW activity download is too large.')
                output.write(chunk)
        index = read_activity(name)
        os.replace(name, destination)
        return index
    finally:
        if os.path.exists(name):
            os.unlink(name)
