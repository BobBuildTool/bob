from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread, Lock
from xml.etree.ElementTree import Element, SubElement, tostring
import os, time

def createHttpHandler(repoPath, args):

    class Handler(BaseHTTPRequestHandler):
        def addProps(self, path, response):
            # trying to create basic props
            href = SubElement(response, 'D:href')
            href_path = os.path.relpath(path, repoPath)
            if href_path == '.':
                href.text = '/'
            else:
                href.text = '/' + str(href_path).replace('\\', '/')
            propstat = SubElement(response, 'D:propstat')
            prop = SubElement(propstat, 'D:prop')
            stats = os.stat(path)
            length = SubElement(prop, 'D:getcontentlength')
            length.text = str(stats[6])
            last_mod = SubElement(prop, 'D:getlastmodified')
            last_mod.text = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(stats[8]))
            etag = SubElement(prop, 'D:getetag')
            # etag inspired by apache
            etag.text = f'{stats[6]:x}-{stats[1]:x}-{stats[8]:x}'
            resource_type = SubElement(prop, 'D:resourcetype')
            if os.path.isdir(path):
               collection = SubElement(resource_type, 'D:collection')
            status = SubElement(propstat, 'D:status')
            status.text = 'HTTP/1.1 200 OK'

        def getCommon(self, start=None, end=None):
            f = None
            path = repoPath + self.path
            try:
                if not os.path.exists(path):
                    raise FileNotFoundError
                if os.path.isfile(path):
                    f = open(path, "rb")
                    fs = os.fstat(f.fileno())
                    mtype = "application/octet-stream"
                    length = str(fs[6])
                else:
                    mtype = None
                    length = 0
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
            self.send_header("Content-type", mtype)
            # length is not the full size when a Range is requested
            if end is not None:
                if end >= int(length):
                    end = int(length) - 1
                length = str(end - start + 1)
            self.send_header("Content-Length", length)
            self.end_headers()
            return f

        def do_HEAD(self):
            self.stats.headRequests += 1
            if args.get('retryHead') and args.get('retries') > 0:
                self.send_error(500, "internal error (retries={})".format(args['retries']))
                args['retries'] = args.get('retries') - 1
                return
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
            # check if a Range was defined
            start = None
            end = None
            if self.headers['Range']:
                start, end = (int(x) for x in self.headers.get('Range').strip().strip('bytes=').split('-'))
            f = self.getCommon(start, end)
            if f:
                if end is not None:
                    f.seek(start)
                    data = f.read(end - start + 1)
                    self.wfile.write(data)
                else:
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

        def do_DELETE(self):
            self.stats.deleteRequests += 1
            if args.get('noResponse'):
                self.log_error("PUT: noResponse: drop connection!")
                self.close_connection = True
                return
            if args.get('retries') > 0:
                self.send_error(500, "internal error")
                args['retries'] = args.get('retries') - 1
                return
            path = repoPath + self.path
            if os.path.exists(path):
                os.unlink(path)
                self.send_response(200)
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

        def do_MKCOL(self):
            self.stats.mkcolRequests += 1
            if args.get('noResponse'):
                self.log_error("PUT: noResponse: drop connection!")
                self.close_connection = True
                return
            if args.get('retries') > 0:
                self.send_error(500, "internal error")
                args['retries'] = args.get('retries') - 1
                return
            path = repoPath + self.path
            # remove trailing slash
            if path[-1] == '/': path = path[:-1]
            # check if already exists, return 405 if it does
            if os.path.exists(path):
                self.send_response(405)
                self.end_headers()
                return
            # check if parent exists, return 409 if not
            h, t = os.path.split(path)
            if not os.path.exists(h):
                self.send_response(409)
                self.end_headers()
                return
            os.mkdir(path)
            self.send_response(201)
            self.end_headers()

        def do_PROPFIND(self):
            self.stats.propfindRequests += 1
            if args.get('noResponse'):
                self.log_error("PUT: noResponse: drop connection!")
                self.close_connection = True
                return
            if args.get('retries') > 0:
                self.send_error(500, "internal error")
                args['retries'] = args.get('retries') - 1
                return
            path = repoPath + self.path
            depth = str(self.headers.get('Depth', 'infinity'))
            et = Element('D:multistatus', {'xmlns:D' : 'DAV:'})
            if depth == '0' or depth == '1':
                response = SubElement(et, 'D:response')
                self.addProps(path, response)
                if depth == '1':
                    for x in os.listdir(path):
                        response = SubElement(et, 'D:response')
                        self.addProps(os.path.join(path, x), response)
                self.send_response(207)
                self.end_headers()
                self.wfile.write(tostring(et))
            else:
                # infinity or something else not allowed
                self.send_response(405)
                self.end_headers()

    return Handler

class HttpServerStats:
    def __init__(self, port):
        self.port = port
        self.headRequests = 0
        self.getRequests = 0
        self.putRequests = 0
        self.deleteRequests = 0
        self.mkcolRequests = 0
        self.propfindRequests = 0

class HttpServerMock():
    def __init__(self, repoPath, noResponse=False, retries=0, retryHead=False):
        if not repoPath.endswith(os.sep):
            repoPath += os.sep
        self.handler = createHttpHandler(repoPath,
            {'noResponse' : noResponse, 'retries' : retries, 'retryHead' : retryHead})
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
