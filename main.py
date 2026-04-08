import socket
import threading
import struct
import argparse
import time
import json
from datetime import datetime

TCP_PORT = 50000
UDP_PORT = 50001
BUFFER_SIZE = 4096

MSG_TEXT = 1
MSG_NAME = 2
MSG_REQ_HISTORY = 3
MSG_HISTORY_DATA = 4
MSG_SHARE_PEERS = 5

class P2PChat:
    def __init__(self, ip, name):
        self.is_running = True
        self.ip = ip
        self.name = name
        self.peers = {}
        self.notified_peers = set()
        self.lock = threading.Lock()
        self.history = []
        self.history_synced = False
        self.add_history(f"Узел запущен. IP: {self.ip}, Имя: {self.name}", is_system=True)

    def add_history(self, message, is_system=False):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"--- {message} ---" if is_system else message
        full_log = f"[{timestamp}] {log_entry}"
        with self.lock:
            self.history.append(full_log)
            print(f"\r{full_log}\n> ", end="", flush=True)

    def pack_msg(self, msg_type, payload=""):
        payload_bytes = payload.encode('utf-8')
        header = struct.pack('!BI', msg_type, len(payload_bytes))
        return header + payload_bytes

    def send_to_peer(self, sock, msg_type, payload=""):
        try:
            sock.sendall(self.pack_msg(msg_type, payload))
        except:
            pass

    def broadcast_udp(self):

        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        msg = f"{self.ip}:{self.name}".encode('utf-8')
        try:
            udp_sock.sendto(msg, ('255.255.255.255', UDP_PORT))
        except Exception as e:
            pass
        finally:
            udp_sock.close()

    def listen_udp(self):

        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:

            udp_sock.bind(('0.0.0.0', UDP_PORT))
        except:
            return

        while self.is_running:
            data, addr = udp_sock.recvfrom(BUFFER_SIZE)
            try:
                peer_info = data.decode('utf-8').split(":", 1)
                peer_ip = peer_info[0]

                if peer_ip == self.ip:
                    continue

                with self.lock:
                    known = peer_ip in self.peers

                if not known:
                    threading.Thread(target=self.connect_to_peer, args=(peer_ip,), daemon=True).start()
            except:
                pass

    def start_tcp_server(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.ip, TCP_PORT))
        server.listen(10)
        while True:
            client_sock, addr = server.accept()
            threading.Thread(target=self.handle_tcp_client, args=(client_sock, False), daemon=True).start()

    def connect_to_peer(self, peer_ip):
        with self.lock:
            if peer_ip in self.peers:
                return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect((peer_ip, TCP_PORT))
            sock.settimeout(None)
            self.send_to_peer(sock, MSG_NAME, f"{self.ip}:{self.name}")
            self.handle_tcp_client(sock, True)
        except:
            pass

    def handle_tcp_client(self, sock, is_outbound):
        peer_ip = None
        peer_name = "Unknown"
        try:
            while True:
                header = sock.recv(5)
                if not header or len(header) < 5: break
                msg_type, msg_length = struct.unpack('!BI', header)

                payload_bytes = b""
                while len(payload_bytes) < msg_length:
                    chunk = sock.recv(msg_length - len(payload_bytes))
                    if not chunk: break
                    payload_bytes += chunk
                payload = payload_bytes.decode('utf-8')

                if msg_type == MSG_NAME:
                    p_ip, p_name = payload.split(":", 1)
                    peer_ip, peer_name = p_ip, p_name

                    with self.lock:
                        if peer_ip in self.peers:
                            if (self.ip < peer_ip and not is_outbound) or (self.ip > peer_ip and is_outbound):
                                sock.close()
                                return

                        if not is_outbound:
                            self.send_to_peer(sock, MSG_NAME, f"{self.ip}:{self.name}")

                        self.peers[peer_ip] = {"socket": sock, "name": p_name}

                        if peer_ip not in self.notified_peers:
                            self.notified_peers.add(peer_ip)
                            should_log = True
                        else:
                            should_log = False

                    if should_log:
                        self.add_history(f"Связь установлена: {p_name} ({peer_ip})", is_system=True)

                    if not self.history_synced:
                        self.history_synced = True
                        self.send_to_peer(sock, MSG_REQ_HISTORY)

                        #обмен пирами
                        with self.lock:
                            all_ips = list(self.peers.keys())
                            if self.ip not in all_ips: all_ips.append(self.ip)
                        self.send_to_peer(sock, MSG_SHARE_PEERS, json.dumps(all_ips))

                elif msg_type == MSG_TEXT:
                    self.add_history(f"[{peer_name} ({peer_ip})]: {payload}")

                elif msg_type == MSG_REQ_HISTORY:
                    with self.lock:
                        clean_history = [line.replace(f"[Вы ({self.ip})]:", f"[{self.name} ({self.ip})]:") for line in self.history]
                    self.send_to_peer(sock, MSG_HISTORY_DATA, json.dumps(clean_history))

                elif msg_type == MSG_HISTORY_DATA:
                    data = json.loads(payload)
                    with self.lock:
                        print(f"\r\n--- АРХИВ ЧАТА ({len(data)} строк) ---")
                        for line in data: print(f"  {line}")
                        print(f"--- КОНЕЦ АРХИВА ---\n> ", end="", flush=True)

                elif msg_type == MSG_SHARE_PEERS:
                        received_ips = json.loads(payload)
                        for r_ip in received_ips:
                            with self.lock:
                                known = r_ip in self.peers
                            if r_ip != self.ip and not known:
                                threading.Thread(target=self.connect_to_peer, args=(r_ip,), daemon=True).start()
        except:
            pass
        finally:
            if peer_ip:
                with self.lock:
                    if peer_ip in self.peers: del self.peers[peer_ip]
                    if peer_ip in self.notified_peers: self.notified_peers.remove(peer_ip)
                self.add_history(f"{peer_name} покинул чат", is_system=True)
            sock.close()

    def run(self):
        threading.Thread(target=self.start_tcp_server, daemon=True).start()
        threading.Thread(target=self.listen_udp, daemon=True).start()

        def br_loop():
            while self.is_running:
                self.broadcast_udp()
                for _ in range(50):
                    if not self.is_running: break
                    time.sleep(0.1)

        threading.Thread(target=br_loop, daemon=True).start()
        time.sleep(1)
        while True:
            try:
                msg = input("")
                if not msg.strip():
                    print("> ", end="", flush=True)
                    continue

                if msg.lower() in ['exit', 'quit']:
                    self.is_running = False
                    print("\rЗавершение работы...")

                    with self.lock:
                        for p_ip in list(self.peers.keys()):
                            sock = self.peers[p_ip]["socket"]
                            try:
                                sock.shutdown(socket.SHUT_WR)
                                sock.close()
                            except:
                                pass
                        self.peers.clear()
                    time.sleep(1.5)
                    break

                self.add_history(f"[Вы ({self.ip})]: {msg}")
                with self.lock:
                    for p in list(self.peers.values()):
                        self.send_to_peer(p["socket"], MSG_TEXT, msg)
            except KeyboardInterrupt:
                break

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip")
    parser.add_argument("--name")
    args = parser.parse_args()

    my_ip = args.ip if args.ip else input("Ваш IP: ")
    my_name = args.name if args.name else input("Ваше имя: ")
    P2PChat(my_ip, my_name).run()
