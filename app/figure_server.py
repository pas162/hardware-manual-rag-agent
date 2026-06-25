"""
Lightweight HTTPS server that serves extracted figure images.

Runs in a background daemon thread inside the MCP server process.
Uses a self-signed certificate (auto-generated, stored in data/store/).

Base URL: https://127.0.0.1:7477
Route:    /figures/<doc_id>/<filename>
          e.g. /figures/R01UH0890EJ0160/p294_fig13_2.png

Start with:
    from app.figure_server import start_figure_server, figure_url
    start_figure_server()
    url = figure_url("data/figures/R01UH0890EJ0160/p294_fig13_2.png")
"""

import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote
import mimetypes
import datetime

_ROOT = Path(__file__).resolve().parent.parent
_FIGURES_DIR = _ROOT / "data" / "figures"
_CERT_DIR = _ROOT / "data" / "store"
_CERT_FILE = _CERT_DIR / "figure_server.crt"
_KEY_FILE = _CERT_DIR / "figure_server.key"
_PORT = 7477
_HOST = "127.0.0.1"

_server_started = False
_lock = threading.Lock()


def _ensure_cert() -> tuple[Path, Path]:
    """Generate a self-signed cert if it doesn't exist yet."""
    if _CERT_FILE.exists() and _KEY_FILE.exists():
        return _CERT_FILE, _KEY_FILE

    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )

    _CERT_DIR.mkdir(parents=True, exist_ok=True)
    _KEY_FILE.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    _CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return _CERT_FILE, _KEY_FILE


class _FigureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Expect: /figures/<doc_id>/<filename>
        path = unquote(self.path.lstrip("/"))
        parts = path.split("/", 1)
        if len(parts) != 2 or parts[0] != "figures":
            self._send(404, b"Not Found")
            return

        file_path = _FIGURES_DIR / parts[1]
        # Prevent path traversal
        try:
            file_path.resolve().relative_to(_FIGURES_DIR.resolve())
        except ValueError:
            self._send(403, b"Forbidden")
            return

        if not file_path.is_file():
            self._send(404, b"Not Found")
            return

        mime, _ = mimetypes.guess_type(str(file_path))
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def _send(self, code: int, body: bytes):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silence request logs


def start_figure_server(port: int = _PORT) -> int:
    """Start the figure HTTPS server in a background daemon thread.

    Safe to call multiple times — only one server is started.
    Returns the port the server is listening on.
    """
    global _server_started, _PORT
    with _lock:
        if _server_started:
            return _PORT

        cert_file, key_file = _ensure_cert()

        for p in [port, 0]:
            try:
                server = HTTPServer((_HOST, p), _FigureHandler)
                break
            except OSError:
                continue

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)

        _PORT = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        _server_started = True
        return _PORT


def figure_url(image_path: str) -> str | None:
    """Convert a relative image_path to an HTTPS URL served by the local figure server.

    image_path format: "data/figures/<doc_id>/<filename>"
    Returns None if image_path is empty or doesn't contain 'figures/'.
    """
    if not image_path:
        return None
    p = Path(image_path)
    parts = p.parts
    try:
        idx = parts.index("figures")
    except ValueError:
        return None
    relative = "/".join(parts[idx + 1:])  # <doc_id>/<filename>
    return f"https://{_HOST}:{_PORT}/figures/{relative}"
