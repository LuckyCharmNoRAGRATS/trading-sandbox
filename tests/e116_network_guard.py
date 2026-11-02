"""Prevent any real network connection from the E1.16 offline pytest process."""
import socket


def pytest_configure(config):
    def blocked(*args, **kwargs):
        raise AssertionError("External network connection forbidden in E1.16 tests")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
