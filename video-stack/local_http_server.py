"""Exclusive loopback HTTP listeners on Windows; ordinary behavior elsewhere."""
from http.server import ThreadingHTTPServer
import os
import socket


class LocalHttpServer(ThreadingHTTPServer):
    allow_reuse_address = False if os.name == "nt" else ThreadingHTTPServer.allow_reuse_address

    def server_bind(self):
        if os.name == "nt":
            # Windows SO_REUSEADDR permits a second process to bind the same
            # endpoint. Set exclusivity before bind so tasks have one owner.
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()
