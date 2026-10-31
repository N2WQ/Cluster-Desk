"""Cooperative, conflict-aware settings writes for desktop instances."""
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


class SettingsConflict(OSError):
    pass


def validate_settings_depth(value, maximum=64):
    """Reject unusually deep JSON before recursive copying or comparison."""
    pending=[(value,0)]
    while pending:
        item,depth=pending.pop()
        if depth>maximum:raise ValueError('Settings are nested too deeply.')
        if isinstance(item,dict):pending.extend((child,depth+1) for child in item.values())
        elif isinstance(item,list):pending.extend((child,depth+1) for child in item)


@contextmanager
def settings_lock(path, timeout=2):
    if os.name == 'nt':
        import msvcrt
    else:
        import fcntl
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(str(path.with_suffix(path.suffix+'.lock')),os.O_RDWR|os.O_CREAT,0o600)
    locked=False
    try:
        if os.fstat(fd).st_size==0:os.write(fd,b'\0')
        deadline=time.monotonic()+timeout
        while True:
            try:
                if os.name == 'nt':
                    os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
                else:
                    fcntl.flock(fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked=True;break
            except OSError:
                if time.monotonic()>=deadline:raise OSError('Another copy is saving settings; try again.')
                time.sleep(.05)
        yield
    finally:
        if locked:
            if os.name == 'nt':
                os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)


def save_settings(path, local, local_baseline, disk_baseline, groups=(), baseline_exists=None):
    """Merge changed top-level keys; reject conflicting concurrent edits."""
    path=Path(path);missing=object()
    with settings_lock(path):
        try:
            disk=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(disk,dict):raise ValueError('Settings must be an object.')
            validate_settings_depth(disk)
            exists=True
        except FileNotFoundError:
            if (baseline_exists if baseline_exists is not None else bool(disk_baseline)):
                raise SettingsConflict('Settings were removed by another copy; reopen the app before saving.')
            disk={};exists=False
        except (ValueError,UnicodeError,RecursionError) as exc:
            raise OSError('The settings file is invalid; it was not overwritten.') from exc
        changes={key:value for key,value in local.items() if value != local_baseline.get(key,missing)}
        for group in groups:
            if any(key in changes for key in group):
                for key in group:
                    if key in local:changes[key]=local[key]
        conflicts=[]
        for key,value in changes.items():
            current=disk.get(key,missing);original=disk_baseline.get(key,missing)
            if current != original and current != local_baseline.get(key,missing) and current != value:
                conflicts.append(key)
        if conflicts:
            raise SettingsConflict('Another copy changed: '+', '.join(sorted(conflicts))+'. Reopen the app to review those settings.')
        merged=dict(disk)
        if not exists:merged.update(local)
        else:merged.update(changes)
        fd,tmp=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=str(path.parent))
        try:
            with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as output:
                json.dump(merged,output,indent=2);output.write('\n');output.flush();os.fsync(output.fileno())
            os.replace(tmp,path)
        finally:
            try:os.unlink(tmp)
            except FileNotFoundError:pass
        return merged
