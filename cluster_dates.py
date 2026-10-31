"""Parse the English month names used by cluster and eQSL data."""
from datetime import date
import re

MONTHS = {name: number for number, name in enumerate(
    ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}


def parse_cluster_date(value):
    match = re.fullmatch(r'(\d{1,2})-([A-Za-z]{3})-(\d{4})', value.strip())
    if not match or match.group(2).lower() not in MONTHS:
        raise ValueError('Invalid cluster date.')
    return date(int(match.group(3)), MONTHS[match.group(2).lower()], int(match.group(1)))
