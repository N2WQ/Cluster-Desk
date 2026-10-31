"""Launch the packaged app with an isolated profile and close its own window."""
import ctypes
import json
import socket
import threading
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

exe = (Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / 'dist' / 'ClusterDesk.exe').resolve()
user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_window():
    found = []
    @callback_type
    def visit(hwnd, _):
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, len(title))
        if title.value != 'Cluster Desk by N2WQ': return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if handle:
            try:
                path = ctypes.create_unicode_buffer(32768); length = wintypes.DWORD(len(path))
                if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)) and Path(path.value).resolve() == exe:
                    found.append(hwnd)
            finally: kernel32.CloseHandle(handle)
        return True
    user32.EnumWindows(visit, 0)
    return found


with tempfile.TemporaryDirectory() as profile:
    local_node = '--local-node' in sys.argv[2:]
    ready = threading.Event(); release = threading.Event(); server_errors = []; commands = []
    listener = None; worker = None
    if local_node:
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(20)
        port = listener.getsockname()[1]
        settings_dir = Path(profile) / 'ClusterDesk'; settings_dir.mkdir()
        (settings_dir / 'settings.json').write_text(json.dumps({'host':'127.0.0.1','port':str(port),'call':'LZ3ZZ','auto_start':True}), encoding='utf-8')
        def serve():
            try:
                sock, _ = listener.accept()
                with sock:
                    sock.settimeout(10); stream = sock.makefile('rb')
                    sock.sendall(b'login: '); commands.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n')
                    commands.extend((stream.readline(), stream.readline()))
                    sock.sendall(b'CC11^14074.0^K1ABC^30-Oct-2026^1234Z^TEST^W1AW^226\r\n')
                    ready.set(); release.wait(20)
            except Exception as exc:
                server_errors.append(exc); ready.set()
            finally: listener.close()
        worker = threading.Thread(target=serve, daemon=True); worker.start()
    env = os.environ.copy()
    env['LOCALAPPDATA'] = profile
    env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
    before = set(find_window())
    process = subprocess.Popen([str(exe)], cwd=profile, env=env)
    try:
        deadline = time.monotonic() + 30
        window = None
        while time.monotonic() < deadline:
            windows = set(find_window()) - before
            if windows:
                window = windows.pop(); break
            if process.poll() is not None: raise RuntimeError(f'Application exited early: {process.returncode}')
            time.sleep(.1)
        if not window: raise RuntimeError('Application window did not appear')
        if local_node:
            if not ready.wait(20): raise RuntimeError('Local node did not receive the startup exchange')
            if server_errors: raise RuntimeError(f'Local node failed: {server_errors[0]}')
            if commands != [b'LZ3ZZ\r\n', b'set/ve7cc\r\n', b'sh/mydx/100\r\n']:
                raise RuntimeError(f'Unexpected local-node startup exchange: {commands!r}')
        user32.PostMessageW(window, 0x0010, 0, 0)  # WM_CLOSE
        process.wait(timeout=15)
        if process.returncode: raise RuntimeError(f'Application exited with {process.returncode}')
        print('Packaged application opened and closed successfully with an isolated profile and no Python on PATH.' + (' Local simulated node received login and startup commands.' if local_node else ''))
    finally:
        release.set()
        if process.poll() is None:
            process.terminate(); process.wait(timeout=10)
        if worker: worker.join(timeout=2)
