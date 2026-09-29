import os, json
from http.server import HTTPServer, SimpleHTTPRequestHandler

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')

class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=BASE, **k)

    def do_GET(self):
        if self.path == '/api/ping':
            b = json.dumps({'ok': True, 'app': 'Julia'}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        super().do_GET()

    def log_message(self, *a):
        pass

HTTPServer(('127.0.0.1', 5000), H).serve_forever()
