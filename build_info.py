"""Identify a packaged executable without exposing local profile data."""
import hashlib
from pathlib import Path
import sys


def packaged_executable():
    return Path(sys.executable) if getattr(sys, 'frozen', False) else None


def executable_sha256(path):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest().upper()
