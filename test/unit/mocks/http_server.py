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
                return None
            except OSError:
                self.send_error(500, "internal error")
                return None

            if "If-Modified-Since" in self.headers:
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self.end_headers()
                f.close()
                return None

            self.send_response(200)
            self.send_header("Content-type", "application/octet-stream")
            self.send_header("Content-Length", str(fs[6]))
            self.end_headers()
            return f

        def do_HEAD(self):
            self.stats.headRequests += 1
            f = self.getCommon()
            if f: f.close()

        def do_GET(self):
            self.stats.getRequests += 1
            if args.get('noResponse'):
                self.log_error("GET: noResponse: drop connection!")
                self.close_connection = True
                return
            if args.get('retries') > 0:
                self.send_error(500, "internal error (retries={})".format(args['retries']))
                args['retries'] = args.get('retries') - 1
                return

            f = self.getCommon()
            if f:
                self.wfile.write(f.read())
                f.close()

        def do_PUT(self):
            self.stats.putRequests += 1
            if args.get('noResponse'):
                self.log_error("PUT: noResponse: drop connection!")
                self.close_connection = True
                return
            if args.get('retries') > 0:
                self.send_error(500, "internal error")
                args['retries'] = args.get('retries') - 1
                return

            path = repoPath + self.path
            if os.path.exists(path) and ("If-None-Match" in self.headers):
                self.send_response(412)
                self.end_headers()
                return

            os.makedirs(os.path.dirname(path), exist_ok=True)
            length = int(self.headers['Content-Length'])
            with open(path, "wb") as f:
                f.write(self.rfile.read(length))
            self.send_response(200)
            self.end_headers()

    return Handler

class HttpServerStats:
    def __init__(self, port):
        self.port = port
        self.headRequests = 0
        self.getRequests = 0
        self.putRequests = 0

class HttpServerMock():
    def __init__(self, repoPath, noResponse=False, retries=0):
        self.handler = createHttpHandler(repoPath,
            {'noResponse' : noResponse, 'retries' : retries })
        self.server = HTTPServer(('localhost', 0), self.handler)

    def __enter__(self):
        self.thread = Thread(target=self.server.serve_forever)
        self.thread.start()
        stats = HttpServerStats(self.server.server_address[1])
        self.handler.stats = stats
        return stats

    def __exit__(self, exc_type, exc_value, traceback):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        return False
