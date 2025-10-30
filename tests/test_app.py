import socket
import sys
import types
import unittest
from unittest import mock


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

from app import is_safe_url


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


if __name__ == "__main__":
    unittest.main()
