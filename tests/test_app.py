import os
import socket
import sys
import types
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _install_stub_module(name, **attributes):
    module = types.ModuleType(name)
    for attr_name, attr_value in attributes.items():
        setattr(module, attr_name, attr_value)
    sys.modules[name] = module
    return module


class _StubResponse:
    def __init__(self):
        self.status_code = 200
        self.text = ""
        self.content = b""
        self.url = "https://example.com/playlist.m3u8"

    def raise_for_status(self):
        pass


def _fake_requests_get(*args, **kwargs):
    raise RuntimeError("network calls are disabled in tests")


_install_stub_module("requests", get=_fake_requests_get)
_install_stub_module("m3u8", loads=lambda data: None)

crypto_module = _install_stub_module("Crypto")
cipher_module = _install_stub_module("Crypto.Cipher")


class _StubCipher:
    def __init__(self, *args, **kwargs):
        pass

    def decrypt(self, data):
        return data


_install_stub_module("Crypto.Cipher.AES", new=lambda *a, **k: _StubCipher())
setattr(cipher_module, "AES", sys.modules["Crypto.Cipher.AES"])
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
    jsonify=lambda payload: payload,
    render_template=lambda *a, **k: None,
    request=None,
    send_from_directory=lambda *a, **k: None,
)

_install_stub_module("werkzeug.utils", secure_filename=lambda filename: filename)

from downloader import DownloadTaskOptions
import app
from app import derive_title_from_url, is_safe_url


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


class CreateTaskTests(unittest.TestCase):
    def test_repeated_submissions_generate_unique_uuid_task_ids(self):
        fake_uuid_values = [
            uuid.UUID("12345678-1234-5678-1234-567812345678"),
            uuid.UUID("87654321-4321-8765-4321-876543218765"),
        ]

        payload = {
            "url": "https://example.com/video.m3u8",
            "title": "Demo",
            "output_format": "mp4",
        }

        with mock.patch.object(app, "manager") as manager_mock:
            manager_mock.create_task.return_value = mock.Mock()
            with mock.patch("app.uuid.uuid4", side_effect=fake_uuid_values):
                responses = []
                for _ in range(2):
                    with mock.patch("app.request", types.SimpleNamespace(get_json=lambda **k: payload)):
                        response = app.create_task()
                    responses.append(response)

        self.assertEqual(len(responses), 2)
        self.assertNotEqual(responses[0]["task_id"], responses[1]["task_id"])
        first_call = manager_mock.create_task.call_args_list[0]
        second_call = manager_mock.create_task.call_args_list[1]
        self.assertEqual(str(fake_uuid_values[0]), first_call.args[0])
        self.assertEqual(str(fake_uuid_values[1]), second_call.args[0])
        options = first_call.args[1]
        self.assertIsInstance(options, DownloadTaskOptions)
        self.assertEqual(options.url, payload["url"])
        self.assertEqual(options.output_format, payload["output_format"])

    def test_missing_url_returns_error(self):
        with mock.patch("app.request", types.SimpleNamespace(get_json=lambda **k: {"url": ""})):
            response, status = app.create_task()
        self.assertEqual(status, 400)
        self.assertIn("error", response)


class UtilityTests(unittest.TestCase):
    def test_title_fallback(self):
        self.assertEqual(derive_title_from_url("https://example.com/video/index.m3u8"), "index")
        self.assertEqual(derive_title_from_url("https://example.com/"), "video")

    def test_download_task_options_validation(self):
        options = DownloadTaskOptions(url="https://example.com/file.m3u8", title="demo")
        options.validate()
        with self.assertRaises(ValueError):
            DownloadTaskOptions(url="https://example.com", title="demo", output_format="avi").validate()
        with self.assertRaises(ValueError):
            DownloadTaskOptions(url="https://example.com", title="demo", start_segment=5, end_segment=2).validate()


class TaskControlEndpointTests(unittest.TestCase):
    def test_start_task_endpoint_returns_payload(self):
        task_payload = {"id": "abc"}
        task_mock = mock.Mock()
        task_mock.to_dict.return_value = task_payload
        with mock.patch.object(app, "manager") as manager_mock:
            manager_mock.start_task.return_value = task_mock
            response = app.start_task("abc")
        self.assertEqual(response, task_payload)
        manager_mock.start_task.assert_called_once_with("abc")

    def test_pause_task_endpoint_fetches_task_state(self):
        task_payload = {"id": "abc", "status": "paused"}
        task_mock = mock.Mock()
        task_mock.to_dict.return_value = task_payload
        with mock.patch.object(app, "manager") as manager_mock:
            manager_mock.get_task.return_value = task_mock
            response = app.pause_task("abc")
        self.assertEqual(response, task_payload)
        manager_mock.pause_task.assert_called_once_with("abc")
        manager_mock.get_task.assert_called_once_with("abc")

    def test_resume_task_endpoint(self):
        task_payload = {"id": "abc", "status": "downloading"}
        task_mock = mock.Mock()
        task_mock.to_dict.return_value = task_payload
        with mock.patch.object(app, "manager") as manager_mock:
            manager_mock.resume_task.return_value = task_mock
            response = app.resume_task("abc")
        self.assertEqual(response, task_payload)
        manager_mock.resume_task.assert_called_once_with("abc")

    def test_delete_task_honours_remove_flag(self):
        with mock.patch.object(app, "manager") as manager_mock:
            request_obj = types.SimpleNamespace(
                get_json=lambda **_: {"remove_files": True},
                args={},
            )
            with mock.patch("app.request", request_obj):
                response = app.delete_task("abc")
        self.assertEqual(response, {"status": "ok"})
        manager_mock.delete_task.assert_called_once_with("abc", remove_files=True)


if __name__ == "__main__":
    unittest.main()
