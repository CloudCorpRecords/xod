"""A pretend company careers page for the apply test: serves apply.html and records what was submitted."""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).parent
OUT = Path(sys.argv[2])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = (HERE / "apply.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        text = raw.decode("utf-8", "replace")
        fields = {}
        for part in text.split("--")[1:]:
            if 'name="' in part:
                name = part.split('name="', 1)[1].split('"', 1)[0]
                filename = part.split('filename="', 1)[1].split('"', 1)[0] if 'filename="' in part else None
                value = part.split("\r\n\r\n", 1)[1].rsplit("\r\n", 1)[0] if "\r\n\r\n" in part else ""
                fields[name] = {"filename": filename, "bytes": len(value)} if filename is not None else value
        OUT.write_text(json.dumps(fields, indent=1))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h1>Thanks! Your application was received.</h1>")


HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
