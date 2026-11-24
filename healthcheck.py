import os
import socket
import sys

def tls_on():

    return os.environ.get("CRM_TLS", "").strip().lower() not in ("off", "0", "no", "false")

def status(port, tls, timeout=4):

    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    if tls:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        sock = ctx.wrap_socket(sock)
    with sock:
        sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n\r\n" % port)
        head = b""
        while b"\r\n" not in head and len(head) < 256:
            chunk = sock.recv(256)
            if not chunk:
                break
            head += chunk
    parts = head.split(b" ", 2)
    return int(parts[1]) if len(parts) > 1 and parts[0].startswith(b"HTTP/") and parts[1].isdigit() else 0

def main():
    try:
        return 0 if status(int(os.environ.get("CRM_PORT", "8780")), tls_on()) == 200 else 1
    except Exception:
        return 1

if __name__ == "__main__":
    sys.exit(main())
