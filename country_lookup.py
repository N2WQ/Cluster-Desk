"""Bounded, conservative CTY.DAT prefix estimates; no network access."""
from dataclasses import dataclass, replace
from pathlib import Path
import re

MAX_FILE_BYTES=5*1024*1024
MAX_ALIASES=200000
HEADER=re.compile(r'^\s*([^:]{1,120}):\s*(\d{1,2}):\s*(\d{1,2}):\s*([A-Z]{2}):\s*([-+\d.]+):\s*([-+\d.]+):\s*[-+\d.]+:\s*\*?([^: ]{1,20}):\s*$')
NUMBER=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'
ALIAS=re.compile(r'^(=)?([A-Z0-9/]{1,32})(?:\((\d{1,2})\))?(?:\[(\d{1,2})\])?(?:<('+NUMBER+r')/('+NUMBER+r')>)?(?:\{(AF|AN|AS|EU|NA|OC|SA)\})?(?:~('+NUMBER+r')~)?$')

@dataclass(frozen=True)
class Country:
    name:str
    prefix:str
    cq:int
    itu:int
    continent:str
    latitude:float
    longitude:float
    matched:str=''
    exact:bool=False


class CountryIndex:
    def __init__(self,prefixes,exact,countries=()):
        self.prefixes=prefixes;self.exact=exact;self.countries=tuple(countries)

    @staticmethod
    def _put(mapping,key,value):
        if key in mapping and mapping[key]!=value:mapping[key]=None
        else:mapping[key]=value

    @classmethod
    def from_text(cls,text):
        prefixes={};exact={};current=None;aliases=[];count=0;countries=[]
        def finish():
            nonlocal count
            if current is None or not aliases:raise ValueError('Country file has an incomplete entry.')
            for token in aliases:
                if not token.isascii():raise ValueError('Country file contains an unsupported prefix entry.')
                match=ALIAS.fullmatch(token.strip().upper())
                if not match:raise ValueError('Country file contains an unsupported prefix entry.')
                is_exact,key,cq,itu,lat,west,continent,_offset=match.groups()
                value=replace(current,cq=int(cq) if cq else current.cq,itu=int(itu) if itu else current.itu,
                              latitude=float(lat) if lat else current.latitude,longitude=-float(west) if west else current.longitude,
                              continent=continent or current.continent)
                if not (1<=value.cq<=40 and 1<=value.itu<=90 and -90<=value.latitude<=90 and -180<=value.longitude<=180):raise ValueError('Country file contains an invalid zone or coordinates.')
                cls._put(exact if is_exact else prefixes,key,value);count+=1
                if count>MAX_ALIASES:raise ValueError('Country file has too many prefixes.')
        for line in text.splitlines():
            line=line.strip()
            if not line or line.startswith('#'):continue
            if current is None:
                match=HEADER.fullmatch(line)
                if not match:raise ValueError('Country file has an invalid header.')
                name,cq,itu,continent,lat,west,primary=match.groups()
                try:latitude=float(lat);longitude=-float(west)
                except ValueError as exc:raise ValueError('Country file has invalid coordinates.') from exc
                if not (-90<=latitude<=90 and -180<=longitude<=180 and 1<=int(cq)<=40 and 1<=int(itu)<=90):raise ValueError('Country file has invalid coordinates or zones.')
                current=Country(name.strip(),primary.upper(),int(cq),int(itu),continent,latitude,longitude);aliases=[]
                countries.append(current)
            else:
                done=line.endswith(';')
                if ';' in line[:-1] or ':' in line:raise ValueError('Country file has an incomplete entry.')
                for token in line.rstrip(';,').split(','):
                    if token.strip():aliases.append(token.strip())
                if done:finish();current=None
        if current is not None:raise ValueError('Country file has an incomplete entry.')
        if not count:raise ValueError('Country file has no prefixes.')
        return cls(prefixes,exact,countries)

    def filter_countries(self):
        """Return unambiguous primary prefixes suitable for node filter forms."""
        by_prefix={}
        for country in self.countries:
            prefix=country.prefix
            if not re.fullmatch(r'[A-Z0-9/\-]{1,16}',prefix) or not re.search(r'[A-Z]',prefix):continue
            identity=(country.name,country.continent)
            by_prefix.setdefault(prefix,set()).add(identity)
        return sorted(((prefix,*next(iter(values))) for prefix,values in by_prefix.items() if len(values)==1),
                      key=lambda item:(item[1].casefold(),item[0]))

    @classmethod
    def from_file(cls,path):
        with Path(path).open('rb') as source:data=source.read(MAX_FILE_BYTES+1)
        if len(data)>MAX_FILE_BYTES:raise ValueError('Country file exceeds 5 MB.')
        try:return cls.from_text(data.decode('utf-8-sig'))
        except UnicodeError as exc:raise ValueError('Country file must be UTF-8 or ASCII.') from exc

    def lookup(self,call):
        if not call.isascii():return None
        call=call.strip().upper()
        if not re.fullmatch(r'[A-Z0-9/]{1,32}(?:-\d{1,2})?',call):return None
        call=re.sub(r'-\d{1,2}$','',call)
        parts=call.split('/')
        if any(not part for part in parts) or parts[-1] in ('MM','AM'):return None
        if call in self.exact:
            value=self.exact[call]
            return replace(value,matched=call,exact=True) if value else None
        if len(parts)>1:
            # A recognized location prefix paired with a full callsign is an
            # estimate, not proof of where the operator is transmitting.
            # Strip /P or /QRP only when a separate location prefix is present.
            if len(parts)==3 and parts[-1] in ('P','QRP'):parts=parts[:-1]
            if len(parts)!=2:return None
            candidates=[]
            for location,base in ((parts[0],parts[1]),(parts[1],parts[0])):
                if (location in ('P','A','M','MM','AM','QRP','QRO') or len(location)>5 or
                    not re.fullmatch(r'[A-Z0-9]{3,32}',base) or not re.search(r'[A-Z]',base) or not re.search(r'[0-9]',base)):
                    continue
                if location in self.prefixes:
                    value=self.prefixes[location]
                    if value is None:return None
                    candidates.append((location,value))
            if len(candidates)!=1:return None
            key,value=candidates[0]
            return replace(value,matched=key)
        for length in range(len(call),0,-1):
            key=call[:length]
            if key in self.prefixes:
                value=self.prefixes[key]
                return replace(value,matched=key) if value else None
        return None
