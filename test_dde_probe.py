import os
from pathlib import Path
import subprocess
import sys
import time
import unittest


@unittest.skipUnless(os.name == 'nt', 'DDE is a Windows interface')
class DDEProbeTests(unittest.TestCase):
    def test_real_local_dde_transaction_and_rejections(self):
        script=Path(__file__).resolve().parent/'tools'/'dde_probe.py'
        # DDE discovery broadcasts to top-level windows. Previous Tk tests can
        # leave Windows-owned windows on this thread, so keep its queue moving
        # while the separate probe process establishes its conversation.
        import ctypes
        from ctypes import wintypes
        user=ctypes.WinDLL('user32',use_last_error=True)
        user.PeekMessageW.argtypes=[ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT,wintypes.UINT]
        user.TranslateMessage.argtypes=[ctypes.POINTER(wintypes.MSG)]
        user.DispatchMessageW.argtypes=[ctypes.POINTER(wintypes.MSG)]
        process=subprocess.Popen([sys.executable,str(script),'--self-test'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        deadline=time.monotonic()+15;message=wintypes.MSG()
        while process.poll() is None and time.monotonic()<deadline:
            while user.PeekMessageW(ctypes.byref(message),None,0,0,1):
                user.TranslateMessage(ctypes.byref(message));user.DispatchMessageW(ctypes.byref(message))
            time.sleep(.01)
        if process.poll() is None:process.kill()
        stdout,stderr=process.communicate(timeout=5)
        self.assertEqual(process.returncode,0,stderr)
        self.assertIn('self-test passed',stdout)

    def test_service_must_be_explicit(self):
        script=Path(__file__).resolve().parent/'tools'/'dde_probe.py'
        result=subprocess.run([sys.executable,str(script)],capture_output=True,text=True,timeout=5)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Specify --service',result.stderr)


if __name__=='__main__':unittest.main()
