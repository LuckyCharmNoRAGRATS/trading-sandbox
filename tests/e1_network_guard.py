"""Fail-closed network guard for isolated E1 GitHub Actions pytest runs.

Loaded via pytest -p before tests are collected. Does not affect application
runtime or a normal local pytest invocation.
"""
import socket


def _forbidden_network(*args, **kwargs):
    raise AssertionError("E1 CI forbids all socket network connections")


socket.socket.connect = _forbidden_network
socket.socket.connect_ex = _forbidden_network
socket.create_connection = _forbidden_network
