"""Capture node directory responses using headers observed on CC and DXSpider."""
import re

WHO_HEADER = re.compile(r'^\s*Callsign\s+Type\s+', re.I)
CONFIG_HEADER = re.compile(r'^\s*(?:Cluster Configuration:|Node\s+(?:Callsigns|Connected Nodes))\s*$', re.I)
PROMPT = re.compile(r'\b(?:CCC|dxspider)\s*>', re.I)


class DirectoryCapture:
    def __init__(self, max_lines=2000, max_chars=200000):
        self.max_lines=max_lines; self.max_chars=max_chars
        self.lines=[]; self.chars=0; self.active=False; self.complete=False; self.truncated=False

    def feed(self, line):
        line=line.rstrip('\r')
        if WHO_HEADER.match(line) or CONFIG_HEADER.match(line):
            self.lines=[]; self.chars=0; self.active=True; self.complete=False; self.truncated=False
            self._append(line); return True
        if not self.active: return False
        if PROMPT.search(line):
            self.active=False; self.complete=True; return True
        self._append(line)
        return True

    def _append(self, line):
        if len(self.lines) >= self.max_lines or self.chars + len(line) + 1 > self.max_chars:
            self.truncated=True; return
        self.lines.append(line); self.chars += len(line) + 1

    def text(self):
        return '\n'.join(self.lines)
