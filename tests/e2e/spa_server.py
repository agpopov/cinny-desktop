import http.server, os, sys
root = sys.argv[1]
class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=root, **k)
    def send_head(self):
        path = self.translate_path(self.path.split('?')[0])
        if not os.path.exists(path): self.path = '/index.html'
        return super().send_head()
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(('127.0.0.1', 8080), H).serve_forever()
