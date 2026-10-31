"""Check the generated map's coverage and freshness behavior."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CodeMapTests(unittest.TestCase):
    def test_generation_and_stale_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'tools').mkdir()
            shutil.copyfile(Path(__file__).with_name('code_map.py'), root / 'tools' / 'code_map.py')
            (root / 'alpha.py').write_text('"""Example module."""\nimport beta\ndef run():\n    """Start the task."""\n', encoding='utf-8')
            (root / 'beta.py').write_text('class Task:\n    def stop(self):\n        pass\n', encoding='utf-8')
            (root / 'test_hidden.py').write_text('def hidden(): pass\n', encoding='utf-8')
            command = [sys.executable, str(root / 'tools' / 'code_map.py')]
            subprocess.run(command, check=True, capture_output=True, text=True)
            output = (root / 'docs' / 'code-maps' / 'cluster-desk.md').read_text(encoding='utf-8')
            self.assertIn('`beta`', output)
            self.assertIn('Start the task.', output)
            self.assertIn('`Task.stop`', output)
            self.assertNotIn('test_hidden.py', output)
            self.assertEqual(subprocess.run(command + ['--check'], capture_output=True).returncode, 0)
            source = (root / 'alpha.py').read_bytes().replace(b'\r\n', b'\n')
            (root / 'alpha.py').write_bytes(source.replace(b'\n', b'\r\n'))
            self.assertEqual(subprocess.run(command + ['--check'], capture_output=True).returncode, 0)
            (root / 'beta.py').write_text('class Task: pass\n', encoding='utf-8')
            self.assertNotEqual(subprocess.run(command + ['--check'], capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
