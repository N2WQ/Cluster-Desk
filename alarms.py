"""Local spot alarm matching. This does not change node-side filters."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fnmatch import fnmatchcase
import re


@dataclass(frozen=True)
class AlarmRules:
    calls: tuple
    states: tuple
    ranges: tuple
    cq_zones: tuple
    countries: tuple
    country_prefixes: tuple = ()


def compile_rules(calls, states, ranges, cq_zones='', countries='', country_prefix_mode=False):
    def tokens(value):
        if len(value) > 2000: raise ValueError('Each list must be at most 2000 characters.')
        if not value.isascii(): raise ValueError('Use ASCII letters and digits for calls, states, frequencies, and CQ zones.')
        items = tuple(part.upper() for part in re.split(r'[\s,]+', value.strip()) if part)
        if len(items) > 100: raise ValueError('Use no more than 100 entries per list.')
        return items
    calls = tokens(calls); states = tokens(states); ranges = tokens(ranges); cq_zones = tokens(cq_zones)
    if len(countries) > 2000: raise ValueError('Each list must be at most 2000 characters.')
    countries = tuple(value.strip().casefold() for value in countries.split(',') if value.strip())
    if len(countries) > 100 or any(len(value) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in value) for value in countries):
        raise ValueError('Enter up to 100 comma-separated country values, each at most 100 characters.')
    if any(not re.fullmatch(r'[A-Z0-9/*?\-]{1,32}', value) for value in calls):
        raise ValueError('Call patterns may use letters, numbers, /, -, * and ?.')
    country_prefixes = tuple(value[1:] for value in calls if country_prefix_mode and re.fullmatch(r'\*[A-Z0-9/]{1,16}', value))
    calls = tuple(value for value in calls if not (country_prefix_mode and re.fullmatch(r'\*[A-Z0-9/]{1,16}', value)))
    if any(not re.fullmatch(r'[A-Z]{2,3}', value) for value in states):
        raise ValueError('State entries must be two or three letters.')
    if any(not re.fullmatch(r'\d{1,2}', value) or not 1 <= int(value) <= 40 for value in cq_zones):
        raise ValueError('CQ zones must be numbers from 1 to 40.')
    parsed = []
    for item in ranges:
        match = re.fullmatch(r'(\d{1,9}(?:\.\d{1,3})?)-(\d{1,9}(?:\.\d{1,3})?)', item)
        if not match: raise ValueError('Use frequency ranges in kHz, such as 14000-14350.')
        low, high = map(Decimal, match.groups())
        if low <= 0 or low > high: raise ValueError('Frequency ranges must have positive, ordered limits.')
        parsed.append((low, high))
    if not (calls or country_prefixes or states or parsed or cq_zones or countries): raise ValueError('Enter at least one alarm criterion.')
    return AlarmRules(calls, states, tuple(parsed), tuple(int(zone) for zone in cq_zones), countries, country_prefixes)


def resolve_country_prefix(country_index, prefix):
    return country_index.prefixes.get(prefix) if country_index else None


def matches(rules, spot, country_index=None):
    if rules.calls or rules.country_prefixes:
        call_match=spot.dx.isascii() and any(fnmatchcase(spot.dx.upper(), pattern) for pattern in rules.calls)
        if not call_match and rules.country_prefixes and country_index:
            target=country_index.lookup(spot.dx)
            if target:
                call_match=any((country:=resolve_country_prefix(country_index,prefix)) and country.prefix==target.prefix for prefix in rules.country_prefixes)
        if not call_match:return False
    if rules.states and (not spot.dx_state.isascii() or spot.dx_state.upper() not in rules.states): return False
    if rules.countries and not any(value and value.casefold() in rules.countries for value in (spot.dx_country, spot.dx_dxcc)): return False
    if rules.cq_zones:
        if not re.fullmatch(r'[0-9]{1,2}', spot.dx_cq) or int(spot.dx_cq) not in rules.cq_zones: return False
    if rules.ranges:
        try: frequency = Decimal(spot.frequency)
        except InvalidOperation: return False
        if not frequency.is_finite() or not any(low <= frequency <= high for low, high in rules.ranges): return False
    return True
