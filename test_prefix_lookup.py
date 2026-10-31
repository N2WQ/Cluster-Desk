import unittest
from unittest.mock import Mock, patch
from app import App

class PrefixLookupTests(unittest.TestCase):
    def test_valid_lookup_uses_selected_connection(self):
        connection, check, activity = Mock(), Mock(), Mock()
        with patch('app.simpledialog.askstring', return_value=' k1abc '):
            App.lookup_prefix(None, None, connection, check, activity)
        connection.send.assert_called_once_with('show/prefix K1ABC')
        self.assertEqual(check.call_count, 2)
        activity.assert_called_once()

    def test_cancel_and_invalid_do_not_send(self):
        for value in (None, 'K\nshow/users', 'é', 'ß1', 'A'*33):
            with self.subTest(value=value):
                connection, activity = Mock(), Mock()
                with patch('app.simpledialog.askstring', return_value=value), patch('app.messagebox.showerror'):
                    App.lookup_prefix(None, None, connection, Mock(), activity)
                connection.send.assert_not_called()
                activity.assert_not_called()

    def test_connection_change_during_prompt_does_not_send(self):
        connection, activity = Mock(), Mock()
        check = Mock(side_effect=[None, OSError('changed')])
        with patch('app.simpledialog.askstring', return_value='K'), patch('app.messagebox.showerror'):
            App.lookup_prefix(None, None, connection, check, activity)
        connection.send.assert_not_called()
        activity.assert_not_called()

if __name__ == '__main__': unittest.main()
