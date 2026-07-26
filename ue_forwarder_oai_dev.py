import json
import os
import socket
import threading
import fcntl
import struct

LISTEN_IP = "0.0.0.0"
LISTEN_PORT = 9200

# OAI dev edge target. edge-dev is attached to orbit3c-dev-core.
EDGE_IP = os.getenv("EDGE_IP", "192.168.83.150")
EDGE_PORT = int(os.getenv("EDGE_PORT", "9100"))

# OAI UE tunnel. The UE IP can change after restart, so the script can discover it.
UE_BIND_INTERFACE = os.getenv("UE_BIND_INTERFACE", "oaitun_ue1")
UE_BIND_IP = os.getenv("UE_BIND_IP", "")  # optional override, e.g., "12.1.1.131"
USE_BIND_TO_DEVICE = os.getenv("USE_BIND_TO_DEVICE", "1").lower() not in ("0", "false", "no")
SOCKET_TIMEOUT_SEC = float(os.getenv("SOCKET_TIMEOUT_SEC", "120.0"))


# def recv_line(sock):
#     buf = b""
#     while b"\n" not in buf:
#         chunk = sock.recv(65536)
#         if not chunk:
#             return None
#         buf += chunk
#     line, _, rest = buf.partition(b"\n")
#     return line.decode("utf-8"), rest



def get_interface_ipv4(ifname: str) -> str:
    """Return IPv4 address assigned to a Linux interface, or empty string if unavailable."""
    if not ifname:
        return ""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        ifreq = struct.pack("256s", ifname[:15].encode("utf-8"))
        res = fcntl.ioctl(s.fileno(), 0x8915, ifreq)  # SIOCGIFADDR
        return socket.inet_ntoa(res[20:24])
    except OSError:
        return ""
    finally:
        s.close()


def bind_socket_to_oai_tunnel(sock: socket.socket):
    """
    Force the edge-facing socket to use the OAI UE tunnel.
    Run this script with sudo if SO_BINDTODEVICE causes a permission error.
    """
    if USE_BIND_TO_DEVICE and UE_BIND_INTERFACE:
        SO_BINDTODEVICE = 25
        sock.setsockopt(
            socket.SOL_SOCKET,
            SO_BINDTODEVICE,
            (UE_BIND_INTERFACE + "\0").encode("utf-8"),
        )

    bind_ip = UE_BIND_IP or get_interface_ipv4(UE_BIND_INTERFACE)
    if bind_ip:
        sock.bind((bind_ip, 0))
        return bind_ip

    return ""

class LineReader:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def recv_line(self):
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                return None
            self.buf += chunk

        line, self.buf = self.buf.split(b"\n", 1)
        return line.decode("utf-8")
    

def recv_exact(sock, n, initial=b""):
    buf = initial
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError("Socket closed while receiving fixed-size payload")
        buf += chunk
    return buf[:n], buf[n:]


def send_line(sock, obj):
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


# def handle_client(client_sock, client_addr):
#     print(f"[UE-FWD] Host connection from {client_addr}")
#     edge_sock = None

#     try:
#         line, rest = recv_line(client_sock)
#         if line is None:
#             print("[UE-FWD] No data from host")
#             return

#         msg = json.loads(line)
#         print(f"[UE-FWD] Received from host: type={msg.get('type')}")

#         image_len = int(msg.get("image_len", 0))
#         image_bytes, _ = recv_exact(client_sock, image_len, initial=rest)

#         print(f"[UE-FWD] Connecting to edge {EDGE_IP}:{EDGE_PORT} from {UE_BIND_IP}")
#         edge_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#         edge_sock.settimeout(30.0)
#         edge_sock.bind((UE_BIND_IP, 0))
#         edge_sock.connect((EDGE_IP, EDGE_PORT))

#         local_ip, local_port = edge_sock.getsockname()
#         print(f"[UE-FWD] Edge socket local endpoint: {local_ip}:{local_port}")
#         peer_ip, peer_port = edge_sock.getpeername()
#         print(f"[UE-FWD] Edge socket peer endpoint: {peer_ip}:{peer_port}")
#         print("[UE-FWD] Connected to edge")

#         send_line(edge_sock, msg)
#         edge_sock.sendall(image_bytes)
#         print("[UE-FWD] Sent header+image to edge, waiting for ACK")

#         ack_line, _ = recv_line(edge_sock)
#         if ack_line is None:
#             print("[UE-FWD] No ACK from edge")
#             return

#         ack = json.loads(ack_line)
#         print(f"[UE-FWD] ACK from edge: {ack}")
#         send_line(client_sock, ack)

#         result_line, _ = recv_line(edge_sock)
#         if result_line is None:
#             print("[UE-FWD] No result from edge")
#             return

#         result = json.loads(result_line)
#         print(f"[UE-FWD] Result from edge: {result}")
#         send_line(client_sock, result)

#     except Exception as e:
#         print(f"[UE-FWD] Error: {e}")
#     finally:
#         try:
#             if edge_sock is not None:
#                 edge_sock.close()
#         except Exception:
#             pass
#         try:
#             client_sock.close()
#         except Exception:
#             pass

def handle_client(client_sock, client_addr):
    print(f"[UE-FWD] Host connection from {client_addr}")
    edge_sock = None

    try:
        host_reader = LineReader(client_sock)

        line = host_reader.recv_line()
        if line is None:
            print("[UE-FWD] No data from host")
            return

        msg = json.loads(line)
        print(f"[UE-FWD] Received from host: type={msg.get('type')}")

        image_len = int(msg.get("image_len", 0))
        image_bytes, _ = recv_exact(client_sock, image_len, initial=host_reader.buf)
        host_reader.buf = b""

        print(f"[UE-FWD-OAI-DEV] Connecting to edge {EDGE_IP}:{EDGE_PORT} via interface={UE_BIND_INTERFACE}")
        edge_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        edge_sock.settimeout(SOCKET_TIMEOUT_SEC)
        bound_ip = bind_socket_to_oai_tunnel(edge_sock)
        print(f"[UE-FWD-OAI-DEV] Bound edge socket to ip={bound_ip or 'auto'} interface={UE_BIND_INTERFACE}")
        edge_sock.connect((EDGE_IP, EDGE_PORT))

        edge_reader = LineReader(edge_sock)

        local_ip, local_port = edge_sock.getsockname()
        print(f"[UE-FWD] Edge socket local endpoint: {local_ip}:{local_port}")
        peer_ip, peer_port = edge_sock.getpeername()
        print(f"[UE-FWD] Edge socket peer endpoint: {peer_ip}:{peer_port}")
        print("[UE-FWD] Connected to edge")

        send_line(edge_sock, msg)
        edge_sock.sendall(image_bytes)
        print("[UE-FWD] Sent header+image to edge, waiting for ACK")

        ack_line = edge_reader.recv_line()
        if ack_line is None:
            print("[UE-FWD] No ACK from edge")
            return

        ack = json.loads(ack_line)
        print(f"[UE-FWD] ACK from edge: {ack}")
        send_line(client_sock, ack)

        result_line = edge_reader.recv_line()
        if result_line is None:
            print("[UE-FWD] No result from edge")
            return

        result = json.loads(result_line)
        print(f"[UE-FWD] Result from edge: {result}")
        send_line(client_sock, result)

    except Exception as e:
        print(f"[UE-FWD] Error: {e}")
    finally:
        try:
            if edge_sock is not None:
                edge_sock.close()
        except Exception:
            pass
        try:
            client_sock.close()
        except Exception:
            pass


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_IP, LISTEN_PORT))
    server.listen(5)
    print(f"[UE-FWD-OAI-DEV] Listening on {LISTEN_IP}:{LISTEN_PORT} | edge={EDGE_IP}:{EDGE_PORT} | tunnel={UE_BIND_INTERFACE}")

    while True:
        client_sock, client_addr = server.accept()
        threading.Thread(
            target=handle_client,
            args=(client_sock, client_addr),
            daemon=True,
        ).start()


if __name__ == "__main__":
    main()