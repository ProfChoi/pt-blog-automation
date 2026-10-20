from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.setup_gmail_oauth import upload_secrets


class SetupTests(unittest.TestCase):
    @patch("tools.setup_gmail_oauth.subprocess.run")
    def test_secrets_use_stdin_not_process_arguments(self, run):
        run.return_value = Mock(returncode=0)
        credentials = SimpleNamespace(
            client_id="private-id", client_secret="private-secret", refresh_token="private-token",
        )
        upload_secrets(credentials, "owner/repo")
        self.assertEqual(run.call_count, 3)
        for call, expected in zip(run.call_args_list, ("private-id", "private-secret", "private-token")):
            self.assertNotIn(expected, " ".join(call.args[0]))
            self.assertEqual(call.kwargs["input"], expected)
            self.assertTrue(call.kwargs["capture_output"])

    @patch("tools.setup_gmail_oauth.subprocess.run")
    def test_missing_refresh_token_does_not_partially_upload(self, run):
        credentials = SimpleNamespace(client_id="id", client_secret="secret", refresh_token=None)
        with self.assertRaises(RuntimeError):
            upload_secrets(credentials, "owner/repo")
        run.assert_not_called()
