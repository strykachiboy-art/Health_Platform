"""Synthetic-only CHAT-F3.S1 provider tests (stdlib unittest, no DB/Redis)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROVIDER = Path(__file__).resolve().parents[3] / "modules" / "chat" / "services" / "chat_media_security_provider.py"
spec = importlib.util.spec_from_file_location("chatf3_media_provider", PROVIDER)
module = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(module)


class FakeSocket:
    def __init__(self, response):
        self.response = response
        self.sent = []
        self.timeout = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def connect(self, endpoint):
        self.endpoint = endpoint

    def sendall(self, chunk):
        self.sent.append(chunk)

    def recv(self, maximum):
        return self.response

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ChatMediaProviderTests(unittest.TestCase):
    DATA = b"synthetic bytes - NO patient data"

    def test_clamd_clean_stream_only(self):
        fake = FakeSocket(b"stream: OK\0")
        with patch.object(module.socket, "create_connection", return_value=fake) as connect:
            self.assertTrue(module.scan_with_clamd(self.DATA, "image/png", host="clamd.private"))
        connect.assert_called_once_with(("clamd.private", 3310), timeout=8.0)
        self.assertEqual(fake.sent[0], b"zINSTREAM\0")
        self.assertEqual(fake.sent[-1], b"\0\0\0\0")
        self.assertEqual(fake.sent[1], len(self.DATA).to_bytes(4, "big") + self.DATA)

    def test_clamd_malware_hit_denied(self):
        with patch.object(module.socket, "create_connection", return_value=FakeSocket(b"stream: Eicar-Test-Signature FOUND\0")):
            self.assertFalse(module.scan_with_clamd(self.DATA, "image/png", host="clamd.private"))

    def test_clamd_scan_error_denied(self):
        with patch.object(module.socket, "create_connection", return_value=FakeSocket(b"stream: Size limit reached. ERROR\0")):
            self.assertFalse(module.scan_with_clamd(self.DATA, "image/png", host="clamd.private"))

    def test_clamd_incomplete_response_denied(self):
        with patch.object(module.socket, "create_connection", return_value=FakeSocket(b"stream: OK")):
            self.assertFalse(module.scan_with_clamd(self.DATA, "image/png", host="clamd.private"))

    def test_clamd_missing_endpoint_denied_without_socket(self):
        with patch.object(module.socket, "create_connection") as connect:
            self.assertFalse(module.scan_with_clamd(self.DATA, "image/png"))
            self.assertFalse(module.scan_with_clamd(self.DATA, "image/png", host="a", socket_path="/tmp/clamd.sock"))
            self.assertFalse(module.scan_with_clamd(b"", "image/png", host="a"))
            connect.assert_not_called()

    def test_clamd_network_failure_denied(self):
        with patch.object(module.socket, "create_connection", side_effect=TimeoutError):
            self.assertFalse(module.scan_with_clamd(self.DATA, "image/png", host="clamd.private"))

    def test_clamd_local_unix_socket(self):
        fake = FakeSocket(b"stream: OK\0")
        unix_family = getattr(module.socket, "AF_UNIX", None)
        with patch.object(module.socket, "socket", return_value=fake) as sock:
            accepted = module.scan_with_clamd(
                self.DATA, "image/png", socket_path="/run/clamd.sock"
            )
        if unix_family is None:
            self.assertFalse(accepted)
            sock.assert_not_called()
        else:
            self.assertTrue(accepted)
            sock.assert_called_once_with(unix_family, module.socket.SOCK_STREAM)
            self.assertTrue(fake.closed)

        # Exercise fail-closed Windows behavior on every platform, including Linux.
        with patch.object(module.socket, "AF_UNIX", None, create=True):
            with patch.object(module.socket, "socket") as unsupported_socket:
                self.assertFalse(module.scan_with_clamd(
                    self.DATA, "image/png", socket_path="/run/clamd.sock"
                ))
                unsupported_socket.assert_not_called()

    def test_ffprobe_good_audio_duration(self):
        content = json.dumps({"streams": [{"codec_type": "audio"}], "format": {"duration": "12.5"}}).encode()
        completed = subprocess.CompletedProcess([], 0, stdout=content, stderr=b"")
        with patch.object(module.subprocess, "run", return_value=completed) as runner:
            self.assertEqual(module.probe_audio_with_ffprobe(self.DATA, "audio/ogg"), 12.5)
        args = runner.call_args
        self.assertEqual(args.kwargs["input"], self.DATA)
        self.assertEqual(args.args[0][-1], "pipe:0")
        self.assertFalse(args.kwargs.get("shell", False))

    def test_ffprobe_non_audio_stream_denied(self):
        content = json.dumps({"streams": [{"codec_type": "video"}], "format": {"duration": "12"}}).encode()
        completed = subprocess.CompletedProcess([], 0, stdout=content, stderr=b"")
        with patch.object(module.subprocess, "run", return_value=completed):
            self.assertIsNone(module.probe_audio_with_ffprobe(self.DATA, "audio/mp4"))

    def test_ffprobe_unknown_nan_and_overlong_duration_denied(self):
        for duration in ("N/A", "nan", "infinity", "181", "0", "-1"):
            with self.subTest(duration=duration):
                content = json.dumps({"streams": [{"codec_type": "audio"}], "format": {"duration": duration}}).encode()
                completed = subprocess.CompletedProcess([], 0, stdout=content, stderr=b"")
                with patch.object(module.subprocess, "run", return_value=completed):
                    self.assertIsNone(module.probe_audio_with_ffprobe(self.DATA, "audio/ogg"))

    def test_ffprobe_timeout_missing_binary_and_invalid_mime_denied(self):
        with patch.object(module.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffprobe", 8)):
            self.assertIsNone(module.probe_audio_with_ffprobe(self.DATA, "audio/ogg"))
        with patch.object(module.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(module.probe_audio_with_ffprobe(self.DATA, "audio/ogg"))
        with patch.object(module.subprocess, "run") as runner:
            self.assertIsNone(module.probe_audio_with_ffprobe(self.DATA, "image/png"))
            runner.assert_not_called()

    def test_ffprobe_nonzero_exit_denied(self):
        completed = subprocess.CompletedProcess([], 1, stdout=b"{}", stderr=b"error")
        with patch.object(module.subprocess, "run", return_value=completed):
            self.assertIsNone(module.probe_audio_with_ffprobe(self.DATA, "audio/ogg"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
