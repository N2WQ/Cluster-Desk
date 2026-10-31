"""Validation for a small, user-editable list of cluster endpoints."""
import re

DEFAULT_NODES = (
    {'name': 'CC Cluster', 'host': 'dxc.n2wq.com', 'port': 7300},
    {'name': 'DXSpider', 'host': 'dxs.n2wq.com', 'port': 7300},
)


def validate_login_call(value):
    """Normalize a user-entered login callsign and reject unsafe characters."""
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/-]{0,31}',value.strip()):
        raise ValueError('Enter a callsign starting with a letter or number, using only letters, numbers, / or -.')
    return value.strip().upper()


def validate_node(name, host, port):
    """Return a validated saved endpoint, or explain why it cannot be used."""
    name = name.strip(); host = host.strip()
    if not name or len(name) > 60 or any(ord(c) < 32 for c in name):
        raise ValueError('Enter a name of up to 60 characters.')
    if not host or len(host) > 253 or any(not c.isascii() or c.isspace() or ord(c) < 33 for c in host):
        raise ValueError('Enter a valid host name or IP address.')
    try:
        if isinstance(port, bool) or not isinstance(port, (int, str)) or not str(port).isdigit(): raise ValueError()
        port = int(port)
    except (TypeError, ValueError): raise ValueError('Enter a port from 1 to 65535.') from None
    if not 1 <= port <= 65535: raise ValueError('Enter a port from 1 to 65535.')
    return {'name': name, 'host': host, 'port': port}


def load_nodes(value):
    """Restore a bounded list of distinct valid endpoints from saved settings."""
    if not isinstance(value, list): return [dict(item) for item in DEFAULT_NODES]
    result = []; names = set()
    for item in value[:50]:
        if not isinstance(item, dict): continue
        try:
            if not isinstance(item.get('name'), str) or not isinstance(item.get('host'), str): continue
            node = validate_node(item['name'], item['host'], item.get('port'))
        except ValueError: continue
        key = node['name'].casefold()
        if key not in names: result.append(node); names.add(key)
    return result


def load_startup_nodes(value):
    """Accept at most three distinct, valid saved additional connections."""
    if not isinstance(value, list): return []
    result=[];seen=set()
    for item in value[:3]:
        if not isinstance(item,dict) or not isinstance(item.get('host'),str) or not isinstance(item.get('call'),str):continue
        try:
            node=validate_node('Additional node',item['host'],item.get('port'))
            call=validate_login_call(item['call'])
        except ValueError:continue
        key=(node['host'].casefold(),node['port'],call)
        if key in seen:continue
        seen.add(key);result.append({'host':node['host'],'port':node['port'],'call':call,'auto_reconnect':item.get('auto_reconnect') is True})
    return result
