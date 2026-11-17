"""Optional standalone host for a privately installed card runtime.

No MO2 session, GameSync app, mod-maintenance API or public private-source bundle.
The fixed production origin preserves browser recovery journals across restarts.
"""
from __future__ import annotations
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import webbrowser
from .shared_cards import shared_cards_path
from .shared_card_runtime import runtime_installation, invoke_shared_card_runtime, REQUEST_BYTES

DEFAULT_PORT = 47771
HTML_BUDGET = 16 * 1024 * 1024


def runtime_html() -> str:
    executable, manifest = runtime_installation()
    folder = executable.parent
    page = folder/'cardgame.html'
    if page.is_symlink():
        raise ValueError('Symlink page is not supported')
    with page.open('rb') as source:
        raw = source.read(HTML_BUDGET + 1)
    if len(raw) > HTML_BUDGET or manifest.get('htmlSha256') != hashlib.sha256(raw).hexdigest():
        raise ValueError('Private runtime page integrity check failed')
    html = raw.decode('utf-8')
    if html.count('<!--__CARD_HOST_BOOT__-->') != 1:
        raise ValueError('Private runtime page has invalid boot contract')
    return html


def create_server(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    # Never silently fall back to another port/origin on a collision.
    html = runtime_html()
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def origin(self): return f'http://127.0.0.1:{self.server.server_port}'
        def reply(self, data, status=200, html_page=False):
            raw = data.encode() if html_page else json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if html_page else 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            if html_page:
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: https:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers();self.wfile.write(raw)
        def valid_host(self): return self.headers.get('Host') == self.origin()[7:]
        def do_GET(self):
            if not self.valid_host():return self.reply({'error':'Invalid host'},403)
            if self.path != '/':return self.reply({'error':'Not found'},404)
            boot='<script>window.__CARD_HOST_BOOT__='+json.dumps({'token':token})+';</script>'
            return self.reply(html.replace('<!--__CARD_HOST_BOOT__-->',boot),html_page=True)
        def do_POST(self):
            if not self.valid_host() or self.headers.get('Origin') != self.origin() or not hmac.compare_digest(self.headers.get('X-Arsenal-Token',''),token):
                return self.reply({'error':'Unauthorized card session'},403)
            if self.path != '/api/shared-cards/invoke':return self.reply({'error':'Not found'},404)
            if self.headers.get('Content-Type','').split(';')[0] != 'application/json':return self.reply({'error':'JSON required'},415)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0 < size <= REQUEST_BYTES:return self.reply({'error':'Request byte budget exceeded'},413)
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):raise ValueError('Object required')
            except (ValueError, RecursionError):return self.reply({'error':'Invalid JSON'},400)
            return self.reply(invoke_shared_card_runtime(data))
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    return server


def main():
    try:server=create_server()
    except (OSError, ValueError, TypeError, AttributeError, RecursionError) as error:
        raise SystemExit('Card host did not start. Check the private runtime installation and fixed port; no fallback origin or save reset.') from error
    print(f'ModHunter private card runtime: http://127.0.0.1:{DEFAULT_PORT}/',flush=True)
    webbrowser.open(f'http://127.0.0.1:{DEFAULT_PORT}/')
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
