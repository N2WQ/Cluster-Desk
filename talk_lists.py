"""Locally saved recipient lists for ordinary cluster TALK commands."""
from announcements import message_commands

MAX_LISTS = 20


def validate_talk_list(name, recipients):
    if not isinstance(name, str) or not isinstance(recipients, str):
        raise ValueError('Enter a list name and recipient callsigns.')
    name = name.strip()
    if not name or len(name) > 40 or not name.isprintable():
        raise ValueError('Enter a list name of up to 40 printable characters.')
    if any(ord(char) < 32 or ord(char) == 127 for char in recipients):
        raise ValueError('Enter recipients on one line without control characters.')
    commands = message_commands('CC Cluster', 'Direct message', 'x', recipients)
    return {'name': name, 'recipients': ','.join(command.split(' ')[1] for command in commands)}


def load_talk_lists(value):
    if not isinstance(value, list): return []
    result = []; seen = set()
    for item in value[:MAX_LISTS]:
        if not isinstance(item, dict): continue
        try: record = validate_talk_list(item.get('name'), item.get('recipients'))
        except ValueError: continue
        key = record['name'].casefold()
        if key not in seen: result.append(record); seen.add(key)
    return result
