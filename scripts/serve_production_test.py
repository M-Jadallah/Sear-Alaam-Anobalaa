"""HTTPS test fixture for browser QA; always uses a disposable SQLite snapshot."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.deployment import snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        api_port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="siyar-production-browser-") as temporary:
        folder = Path(temporary)
        database = folder / "library.sqlite3"
        snapshot(ROOT / "data/library.sqlite3", database)
        env = {**os.environ, "SIYAR_MODE": "production", "SIYAR_DB": str(database), "SIYAR_AUTH_DB": str(folder / "auth.sqlite3"),
               "SIYAR_PUBLIC_ORIGIN": f"https://localhost:{args.port}", "SIYAR_ADMIN_USERNAME": "browser-admin", "SIYAR_ADMIN_PASSWORD": "synthetic-browser-password-only"}
        openssl = shutil.which("openssl") or "C:/Program Files/Git/usr/bin/openssl.exe"
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(folder / "key.pem"), "-out", str(folder / "cert.pem"), "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"], check=True, capture_output=True)
        api = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", str(api_port)], cwd=ROOT, env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            until = time.monotonic() + 45
            while True:
                try:
                    with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{api_port}/api/live", headers={"Host": "localhost"}), timeout=2):
                        break
                except (OSError, urllib.error.HTTPError):
                    if api.poll() is not None or time.monotonic() > until:
                        raise RuntimeError("Production test API did not start")
                    time.sleep(.1)
            class Handler(BaseHTTPRequestHandler):
                def handle_request(self):
                    if self.path == "/__test_shutdown":
                        import threading
                        self.send_response(200); self.end_headers()
                        threading.Thread(target=server.shutdown, daemon=True).start()
                        return
                    if self.path.startswith("/api/"):
                        size = int(self.headers.get("Content-Length", "0"))
                        if size > 32768:
                            self.send_error(413); return
                        data = self.rfile.read(size) if self.command == "POST" else None
                        req = urllib.request.Request(f"http://127.0.0.1:{api_port}" + self.path, data=data, method=self.command,
                            headers={key: value for key, value in self.headers.items() if key.lower() not in {"connection", "accept-encoding"}})
                        try:
                            response = urllib.request.urlopen(req, timeout=65)
                        except urllib.error.HTTPError as error:
                            response = error
                        body = response.read()
                        self.send_response(response.status)
                        for key, value in response.headers.items():
                            if key.lower() not in {"content-length", "transfer-encoding", "connection"}:
                                self.send_header(key, value)
                        self.send_header("Content-Length", str(len(body))); self.end_headers()
                        if self.command != "HEAD": self.wfile.write(body)
                        return
                    root = (ROOT / "frontend/dist").resolve()
                    target = (root / unquote(urlsplit(self.path).path).lstrip("/")).resolve()
                    if not target.is_relative_to(root):
                        self.send_error(403); return
                    if not target.is_file(): target = root / "index.html"
                    import mimetypes
                    body = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(target)[0] or "application/octet-stream")
                    self.send_header("Content-Length", str(len(body))); self.end_headers()
                    if self.command != "HEAD": self.wfile.write(body)
                do_GET = do_POST = do_HEAD = handle_request
                def log_message(self, *args): pass
            server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(folder / "cert.pem", folder / "key.pem")
            server.socket = context.wrap_socket(server.socket, server_side=True)
            print("READY", flush=True)
            server.serve_forever()
        finally:
            api.terminate()
            api.wait(timeout=15)


if __name__ == "__main__":
    main()
