import socket
import threading
import time
import unittest
from unittest.mock import patch
from app import App

class PrefixLookupTransportTest(unittest.TestCase):
    def test_lookup_and_spot_reach_ui_through_real_local_connection(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(5)
        port=listener.getsockname()[1];seen=[];errors=[];release=threading.Event()
        def server():
            try:
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(5);stream=sock.makefile('rb');sock.sendall(b'login: ');seen.append(stream.readline())
                    sock.sendall(b'LZ3ZZ de NODE dxspider >\r\n');seen.extend([stream.readline(),stream.readline()])
                    seen.append(stream.readline())
                    sock.sendall(b'K CC: 226 IZ: 8 CZ: 5 LL: 38N 97W EM18 (K, United States)\r\n')
                    sock.sendall(b'CC11^14074.0^K1ABC^30-Oct-2026^1234Z^TEST^W1AW^226^226^NODE^^^^^^^^^^^\r\n')
                    release.wait(5)
            except Exception as exc:errors.append(exc)
            finally:listener.close()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        app=App();app.withdraw();app._save_settings=lambda:None
        try:
            app.host.set('127.0.0.1');app.port.set(str(port));app.call.set('LZ3ZZ');app.connect()
            deadline=time.monotonic()+5
            while app.status.get()!='Connected' and time.monotonic()<deadline:app.update();time.sleep(.01)
            self.assertEqual(app.status.get(),'Connected')
            with patch('app.simpledialog.askstring',return_value='K'):app.lookup_main_prefix()
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                app.update()
                if 'CC: 226' in app.transcript.get('1.0','end') and any(spot.dx=='K1ABC' for _,spot in app.rows):break
                time.sleep(.01)
            self.assertIn('CC: 226',app.transcript.get('1.0','end'))
            self.assertTrue(any(spot.dx=='K1ABC' for _,spot in app.rows))
            self.assertEqual(seen,[b'LZ3ZZ\r\n',b'set/ve7cc\r\n',b'sh/mydx/100\r\n',b'show/prefix K\r\n'])
            self.assertEqual(errors,[])
        finally:
            release.set();app.quit_app();worker.join(5);listener.close()
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors,[])

if __name__=='__main__':unittest.main()
