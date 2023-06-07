from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread, Lock
import os,sys

def createHttpHandler(repoPath, args):

    class Handler(BaseHTTPRequestHandler):

        def getCommon(self):
            path = repoPath + self.path
            try:
                f = open(path, "rb")
                fs = os.fstat(f.fileno())
            except FileNotFoundError:
                self.send_error(404, "not found")
                self.end_headers()
                return None
            except OSError:
                self.send_error(500, "internal error")
                self.end_headers()
                return None

            if "If-Modified-Since" in self.headers:
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self.end_headers()
                return None

            self.send_response(200)
            self.send_header("Content-type", "application/octet-stream")
            self.send_header("Content-Length", str(fs[6]))
            self.end_headers()
            return f

        def do_HEAD(self):
            f = self.getCommon()
            if f: f.close()

        def do_GET(self):
            if args.get('noResponse'):
                self.close_connection = True
                return
            if args.get('retries') > 0:
                self.send_error(500, "internal error")
                self.end_headers()
                args['retries'] = args.get('retries') - 1
                return

            f = self.getCommon()
            if f:
                self.wfile.write(f.read())
                f.close()

    return Handler

class HttpServerMock():
    def __init__(self, repoPath, noResponse=False, retries=0):
        self.server = HTTPServer(('localhost', 0), createHttpHandler(repoPath,
            {'noResponse' : noResponse, 'retries' : retries }))

    def __enter__(self):
        self.thread = Thread(target=self.server.serve_forever)
        self.thread.start()
        return self.server.server_address[1]

    def __exit__(self, exc_type, exc_value, traceback):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        return False
