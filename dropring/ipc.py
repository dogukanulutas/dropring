import json
import socket

from .store import runtime_dir


def socket_path():
    return runtime_dir() / "dropring.sock"


def send(message, timeout=2.0):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(socket_path()))
            client.sendall(json.dumps(message).encode() + b"\n")
            return client.recv(16).startswith(b"ok")
    except OSError:
        return False
