"""Build explicit, single-line commands for the tested filter dialects."""
import re

CC_FILTERS = {
    'Keywords': 'keyword',
    'Band / frequency segment': 'DXBM',
    'Spotter country': 'DOC',
    'Spotter state': 'DOS',
    'DX country': 'DXCTY',
    'DX state': 'DXSTATE',
    'Announce country': 'AOC',
    'Announce state': 'AOS',
    'Weather country': 'WOC',
    'Weather state': 'WOS',
}
CC_PER_BAND_CATEGORIES = frozenset(('Spotter country', 'Spotter state', 'DX country', 'DX state'))
CC_FILTER_BANDS = ('All bands', '160 m', '80 m', '60 m', '40 m', '30 m', '20 m', '17 m', '15 m', '12 m', '10 m', '6 m', '4 m', '2 m', '70 cm')
# The CC Cluster reference defines these mode names as frequency segments.
CC_DXBM_SEGMENTS = {
    '160': ('CW','SSB'), '80': ('CW','RTTY','SSB'), '60': ('SSB',),
    '40': ('CW','RTTY','SSB'), '30': ('CW','RTTY'), '20': ('CW','RTTY','SSB'),
    '17': ('CW','RTTY','SSB'), '15': ('CW','RTTY','SSB'),
    '12': ('CW','RTTY','SSB'), '10': ('CW','RTTY','SSB'),
    '6': ('CW','SSB','FM'), '4': (), '2': ('CW','SSB','FM'), '1': ('CW','SSB','FM'),
    '70': ('CW','SSB','FM'),
}
CC_DXBM_RANGES = {
    '160-CW':'1800–1850', '160-SSB':'1850–2000', '80-CW':'3500–3580',
    '80-RTTY':'3580–3700', '80-SSB':'3700–4000', '60-SSB':'5260–5405',
    '40-CW':'7000–7040', '40-RTTY':'7040–7100', '40-SSB':'7100–7300',
    '30-CW':'10100–10130', '30-RTTY':'10130–10150', '20-CW':'14000–14070',
    '20-RTTY':'14070–14150', '20-SSB':'14150–14350', '17-CW':'18068–18100',
    '17-RTTY':'18100–18110', '17-SSB':'18110–18168', '15-CW':'21000–21070',
    '15-RTTY':'21070–21200', '15-SSB':'21200–21450', '12-CW':'24890–24920',
    '12-RTTY':'24920–24930', '12-SSB':'24930–24990', '10-CW':'28000–28070',
    '10-RTTY':'28070–28300', '10-SSB':'28300–29700', '6-CW':'50000–50080',
    '6-SSB':'50080–50500', '6-FM':'50500–54000', '2-CW':'144000–144100',
    '2-SSB':'144100–144500', '2-FM':'144500–148000', '1-CW':'220000–221000',
    '1-SSB':'222000–224000', '1-FM':'221000–222000', 'MW-MW':'500000–20000000',
    '70-CW':'Node-defined segment', '70-SSB':'Node-defined segment', '70-FM':'Node-defined segment',
}
CC_DXBM_VALUES = (set(CC_DXBM_SEGMENTS) - {'70'}) | {f'{band}-{mode}' for band,modes in CC_DXBM_SEGMENTS.items() for mode in modes} | {'MW-MW'}

SPIDER_FILTERS = {
    'Advanced expression': '',
    'Band or range': 'on',
    'DX callsign text': 'call',
    'Spotter callsign text': 'by',
    'Spot comment text': 'info',
    'DX country': 'call_dxcc',
    'Spotter country': 'by_dxcc',
    'DX state': 'call_state',
    'Spotter state': 'by_state',
    'DX CQ zone': 'call_zone',
    'Spotter CQ zone': 'by_zone',
    'DX ITU zone': 'call_itu',
    'Spotter ITU zone': 'by_itu',
    'Origin callsign text': 'origin',
    'Origin country': 'origin_dxcc',
    'Origin CQ zone': 'origin_zone',
    'Origin ITU zone': 'origin_itu',
    'Announcement text': 'info',
    'Announcement group text': 'group',
    'Minimum skimmers': 'q',
    'Minimum RBN strength': 'db',
}


def spider_expression(category, value):
    """Translate a supported UI criterion into a safe DXSpider filter expression."""
    if category not in SPIDER_FILTERS: raise ValueError('Choose a supported spot filter.')
    value = value.strip()
    if not value or len(value) > 200 or any(not 32 <= ord(char) <= 126 for char in value):
        raise ValueError('Enter up to 200 printable ASCII characters on one line.')
    if '\\n' in value:
        raise ValueError('The characters \\n cannot be used in a DXSpider filter because they separate commands.')
    field = SPIDER_FILTERS[category]
    if not field: return value
    if category == 'Minimum skimmers':
        if not re.fullmatch(r'[1-9]',value): raise ValueError('Enter a minimum skimmer count from 1 to 9.')
        return f'q {value}+'
    if category == 'Minimum RBN strength':
        if not re.fullmatch(r'(?:0|[1-9][0-9]?)',value): raise ValueError('Enter a minimum signal strength from 0 to 99 dB.')
        return f'db {value}+'
    if category == 'Band or range':
        parts = [part.strip() for part in value.split(',')]
        if len(parts) > 50 or any(not re.fullmatch(r'(?:[0-9]+[-/][0-9]+|[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)?)', part) for part in parts):
            raise ValueError('Enter bands or whole-kHz ranges, such as 20m or 14000/14350, separated by commas.')
        for part in parts:
            numeric = re.fullmatch(r'([0-9]+)[-/]([0-9]+)', part)
            if numeric and int(numeric.group(1)) > int(numeric.group(2)):
                raise ValueError('Enter the lower frequency first, for example 14000/14350.')
        value = ','.join(parts)
    else:
        parts=[part.strip().upper() for part in value.split(',')]
        if len(parts)>50: raise ValueError('Enter at most 50 values.')
        pattern = r'[A-Z]{2}' if category.endswith('state') else r'[A-Z0-9/\-]{1,16}'
        if category in ('DX callsign text','Spotter callsign text','Origin callsign text'):
            pattern = r'[A-Z0-9/#\-]{1,16}\*?' if category == 'Spotter callsign text' else r'[A-Z0-9/\-]{1,16}\*?'
        elif category in ('Spot comment text', 'Announcement text', 'Announcement group text'):
            pattern = r'[A-Z0-9_ /.+#\-]{1,50}\*?'
        if category.endswith(('CQ zone', 'ITU zone')):
            maximum = 40 if category.endswith('CQ zone') else 90
            if any((not 1 <= int(part) <= maximum) if part.isdigit() else not re.fullmatch(r'[A-Z0-9/\-]{1,16}', part) for part in parts):
                raise ValueError(f'Enter zone numbers from 1 to {maximum} or callsign prefixes, separated by commas.')
        elif any(not re.fullmatch(pattern, part) for part in parts):
            raise ValueError('Enter comma-separated prefixes, identifiers or states for this filter.')
        # The pinned DXSpider include_regex implementation leaves a trailing
        # '*' in the generated Perl regex (K1* also matches K2). Braced regexes
        # are decoded by the node, so an explicit anchor preserves prefix intent.
        if category in ('DX callsign text','Spotter callsign text','Origin callsign text'):
            parts=[f'{{^{re.escape(part[:-1]) if "#" in part else part[:-1]}}}' if part.endswith('*') else
                   f'{{{re.escape(part)}}}' if '#' in part else part for part in parts]
        elif category in ('Spot comment text','Announcement text','Announcement group text'):
            # The node's braced expressions preserve spaces as one value.
            # Escape allowed punctuation so this simple control stays literal.
            parts=[f'{{^{re.escape(part[:-1])}}}' if part.endswith('*') else
                   part if re.fullmatch(r'[A-Z0-9_\-]+',part) else f'{{{re.escape(part)}}}'
                   for part in parts]
        value=','.join(parts)
    return f'{field} {value}'


SPIDER_STREAMS = {'Spots': 'spots', 'RBN spots': 'rbn', 'Announcements': 'announce', 'WWV': 'wwv', 'WCY': 'wcy'}
SPIDER_PICKER_BANDS = ('160m','80m','60m','40m','30m','20m','17m','15m','12m','10m','6m','4m','2m','220','70cm')
SPIDER_SPOT_CATEGORIES = tuple(list(SPIDER_FILTERS)[:13]) + ('Origin callsign text',)
SPIDER_COMMON_CATEGORIES = ('Advanced expression', 'Spotter callsign text', 'Spotter country', 'Spotter CQ zone', 'Spotter ITU zone', 'Origin callsign text', 'Origin country', 'Origin CQ zone', 'Origin ITU zone')


def spider_categories(stream):
    """Offer filter criteria appropriate to the selected DXSpider traffic stream."""
    if stream not in SPIDER_STREAMS: raise ValueError('Choose a supported traffic type.')
    if stream == 'Spots': return SPIDER_SPOT_CATEGORIES
    if stream == 'RBN spots': return SPIDER_SPOT_CATEGORIES + ('Minimum skimmers', 'Minimum RBN strength')
    return SPIDER_COMMON_CATEGORIES + (('Announcement text', 'Announcement group text') if stream == 'Announcements' else ())


def filter_command(dialect, action, text='', line=None, category='Keywords', stream='Spots', band='All bands'):
    """Build a supported node filter command without sending or applying it.

    Validation prevents unsupported combinations and unintended command
    separators. A returned command still needs node acceptance and readback.
    """
    if dialect == 'CC Cluster':
        if action == 'reset_all': return 'set/nofilter'
        if category not in CC_FILTERS: raise ValueError('Choose a supported filter.')
        if band not in CC_FILTER_BANDS or (band != 'All bands' and category not in CC_PER_BAND_CATEGORIES):
            raise ValueError('Choose All bands or a supported band for this country or state filter.')
        name = CC_FILTERS[category]
        command = 'set/filter' + ('/' + band.split()[0] if band != 'All bands' else '')
        if action == 'off': return f'{command} {name}/off'
        if action not in ('pass', 'reject'): raise ValueError('Choose pass, reject, or off.')
        prefix = f'{command} {name}/' + ('pass' if action == 'pass' else 'rej' if category == 'Keywords' else 'reject')
    elif dialect == 'DXSpider':
        if stream not in SPIDER_STREAMS: raise ValueError('Choose a supported traffic type.')
        target = SPIDER_STREAMS[stream]
        if action == 'clear_all': return f'clear/{target} all'
        if not isinstance(line, int) or isinstance(line, bool) or not 0 <= line <= 9:
            raise ValueError('Choose a filter line from 0 to 9.')
        if action == 'clear': return f'clear/{target} {line}'
        if action not in ('accept', 'reject'): raise ValueError('Choose accept, reject, or clear.')
        prefix = f'{action}/{target} {line}'
    else:
        raise ValueError('Connect to a supported cluster first.')
    text = text.strip()
    if not text or len(text) > 200 or any(not 32 <= ord(c) <= 126 for c in text):
        raise ValueError('Enter up to 200 printable ASCII characters on one line.')
    if dialect == 'DXSpider' and '\\n' in text:
        raise ValueError('The characters \\n cannot be used in a DXSpider filter because they separate commands.')
    if dialect == 'CC Cluster' and category != 'Keywords':
        parts = [part.strip().upper() for part in text.split(',')]
        if category == 'Band / frequency segment':
            if len(parts)>50 or any(part not in CC_DXBM_VALUES for part in parts):
                raise ValueError('Enter supported bands or segments, for example 6,40-CW,20-RTTY.')
        else:
            pattern = r'[A-Z]{2,3}' if category.endswith('state') else r'[A-Z0-9/\-]{1,16}'
            if len(parts) > 50 or any(not re.fullmatch(pattern, part) for part in parts):
                raise ValueError('Enter comma-separated country prefixes or state abbreviations for the selected filter.')
        text = ','.join(parts)
    return f'{prefix} {text}'
