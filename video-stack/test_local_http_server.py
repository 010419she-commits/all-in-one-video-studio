"""Temporary socket checks only; never bind the real video service ports."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import socket
import unittest
from unittest.mock import Mock, patch

from local_http_server import LocalHttpServer


class ExclusiveHttpServerTests(unittest.TestCase):
    def test_second_server_cannot_bind_same_temporary_endpoint(self):
        first = LocalHttpServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self.addCleanup(first.server_close)
        self.assertNotIn(first.server_port, (8188, 8899, 8900, 8922))
        with self.assertRaises(OSError):
            second = LocalHttpServer(first.server_address, BaseHTTPRequestHandler)
            second.server_close()

    @unittest.skipUnless(os.name == "nt", "Windows socket exclusivity")
    def test_windows_sets_exclusive_address_use_and_disables_reuse(self):
        server = LocalHttpServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self.addCleanup(server.server_close)
        self.assertFalse(server.allow_reuse_address)
        self.assertEqual(server.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
        self.assertEqual(server.socket.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR), 0)

    def test_non_windows_bind_keeps_standard_base_behavior(self):
        fake = LocalHttpServer.__new__(LocalHttpServer)
        fake.socket = Mock()
        with patch("local_http_server.os.name", "posix"), \
             patch.object(ThreadingHTTPServer, "server_bind") as original:
            LocalHttpServer.server_bind(fake)
        fake.socket.setsockopt.assert_not_called()
        original.assert_called_once()


if __name__ == "__main__":
    unittest.main()
