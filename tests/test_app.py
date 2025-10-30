import os
import socket
import sys
import types
import unittest
from unittest import mock
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _install_stub_module(name, **attributes):
    module = types.ModuleType(name)
    for attr_name, attr_value in attributes.items():
        setattr(module, attr_name, attr_value)
    sys.modules[name] = module
    return module


# Stub third-party dependencies imported by app.py so tests can run without them.
_install_stub_module("requests", get=None)
_install_stub_module("m3u8", loads=None)

crypto_module = _install_stub_module("Crypto")
cipher_module = _install_stub_module("Crypto.Cipher")
aes_module = _install_stub_module("Crypto.Cipher.AES")
setattr(cipher_module, "AES", aes_module)
setattr(crypto_module, "Cipher", cipher_module)

class _StubFlask:
    def __init__(self, *args, **kwargs):
        pass

    def route(self, *args, **kwargs):
        def decorator(func):
            return func

        return decorator


_install_stub_module(
    "flask",
    Flask=_StubFlask,
    render_template=lambda *args, **kwargs: None,
    request=None,
    jsonify=lambda *args, **kwargs: None,
    send_from_directory=lambda *args, **kwargs: None,
    abort=lambda *args, **kwargs: None,
)

werkzeug_module = _install_stub_module("werkzeug")
werkzeug_utils_module = _install_stub_module(
    "werkzeug.utils", secure_filename=lambda filename: filename
)
setattr(werkzeug_module, "utils", werkzeug_utils_module)

import app
from app import is_safe_url


class _DummyThread:
    def __init__(self, *args, **kwargs):
        self.daemon = False

    def start(self):
        pass


class IsSafeUrlTests(unittest.TestCase):
    def test_ipv6_only_public_host_allowed(self):
        url = "http://ipv6-only.example/path"
        addr_info = [
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2001:4860::1", 0, 0, 0)),
        ]

        with mock.patch("app.socket.getaddrinfo", return_value=addr_info):
            self.assertTrue(is_safe_url(url))

    def test_private_ipv6_host_rejected(self):
        url = "http://private-v6.example/path"
        addr_info = [
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("fd00::1", 0, 0, 0)),
        ]

        with mock.patch("app.socket.getaddrinfo", return_value=addr_info):
            self.assertFalse(is_safe_url(url))


class StartDownloadTests(unittest.TestCase):
    def test_repeated_submissions_generate_unique_uuid_task_ids(self):
        fake_uuid_values = [
            uuid.UUID("12345678-1234-5678-1234-567812345678"),
            uuid.UUID("87654321-4321-8765-4321-876543218765"),
        ]

        request_payload = {
            "url": "https://example.com/video.m3u8",
            "filename": "video.mp4",
        }

        with mock.patch("app.jsonify", side_effect=lambda payload: payload):
            with mock.patch("app.threading.Thread", side_effect=lambda *a, **k: _DummyThread()):
                with mock.patch("app.uuid.uuid4", side_effect=fake_uuid_values):
                    task_ids = []
                    for _ in range(2):
                        with mock.patch("app.request", types.SimpleNamespace(json=request_payload.copy())):
                            response = app.start_download()
                        task_ids.append(response["task_id"])

        self.assertEqual(len(task_ids), 2)
        self.assertNotEqual(task_ids[0], task_ids[1])
        for task_id in task_ids:
            self.assertIsInstance(task_id, str)
            # Ensure the returned identifier is a valid UUID string
            self.assertEqual(task_id, str(uuid.UUID(task_id)))


if __name__ == "__main__":
    unittest.main()
