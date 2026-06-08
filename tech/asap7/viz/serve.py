"""Static file server that never serves a stale mesh.

Python's `http.server` sends no Cache-Control/ETag, so browsers heuristically cache big .glb
files and don't revalidate -> stale geometry (e.g. an old skew mesh keeps its removed gate boxes).
This sends `Cache-Control: no-cache` (always revalidate) + a strong `ETag` (size+mtime), and
answers `If-None-Match` with 304 when unchanged -> fresh every load, no needless re-download.

Usage: python serve.py [port]      (default 8017, binds 0.0.0.0)
"""
import http.server, os, sys

class Handler(http.server.SimpleHTTPRequestHandler):
    def _etag(self):
        try:
            st = os.stat(self.translate_path(self.path))
            return f'"{st.st_size:x}-{int(st.st_mtime):x}"'
        except OSError:
            return None

    def send_head(self):
        etag = self._etag()
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.end_headers()
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        etag = self._etag()
        if etag:
            self.send_header("ETag", etag)
        super().end_headers()

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8017
print(f"serving {os.getcwd()} on 0.0.0.0:{PORT}  (no-cache + ETag revalidation)")
http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
