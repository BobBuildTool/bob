from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import http.client
import re
import socket
from threading import Thread

import requests

CRUMBDATA=b"""
<defaultCrumbIssuer>
<crumb>0123456789abcdef</crumb><crumbRequestField>.crumb</crumbRequestField>
</defaultCrumbIssuer>
"""
PLUGINS=b"""
{"plugins":[{"active":True,"backupVersion":"1.3.1","bundled":False,"deleted":False,"dependencies":[{},{},{},{},{},{},{},{},{}],"downgradable":True,"enabled":True,"hasUpdate":True,"longName":"conditional-buildstep","pinned":False,"shortName":"conditional-buildstep","supportsDynamicLoad":"MAYBE","url":"https://wiki.jenkins-ci.org/display/JENKINS/Conditional+BuildStep+Plugin","version":"1.3.3"},{"active":True,"backupVersion":"2.2.2","bundled":False,"deleted":False,"dependencies":[{},{},{},{},{},{},{},{},{},{},{}],"downgradable":True,"enabled":True,"hasUpdate":True,"longName":"Jenkins GIT plugin","pinned":False,"shortName":"git","supportsDynamicLoad":"MAYBE","url":"http://wiki.jenkins-ci.org/display/JENKINS/Git+Plugin","version":"2.2.7"},{"active":True,"backupVersion":"2.4","bundled":True,"deleted":False,"dependencies":[{},{},{},{},{},{},{},{},{}],"downgradable":True,"enabled":True,"hasUpdate":True,"longName":"Jenkins Subversion Plug-in","pinned":True,"shortName":"subversion","supportsDynamicLoad":"MAYBE","url":"http://wiki.jenkins-ci.org/display/JENKINS/Subversion+Plugin","version":"2.4.4"},{"active":True,"backupVersion":"2.4","bundled":True,"deleted":False,"dependencies":[{},{},{},{},{},{},{},{},{}],"downgradable":True,"enabled":True,"hasUpdate":True,"longName":"Copy Artifact Plug-in","pinned":True,"shortName":"copyartifact","supportsDynamicLoad":"MAYBE","url":"","version":"1.2.3"},{"active":True,"backupVersion":"2.4","bundled":True,"deleted":False,"dependencies":[{},{},{},{},{},{},{},{},{}],"downgradable":True,"enabled":True,"hasUpdate":True,"longName":"Jenkins Multiple SCMs plugin","pinned":True,"shortName":"multiple-scms","supportsDynamicLoad":"MAYBE","url":"","version":"1.2.3"},{"active":True,"backupVersion":"2.4","bundled":True,"deleted":False,"dependencies":[{},{},{},{},{},{},{},{},{}],"downgradable":True,"enabled":True,"hasUpdate":True,"longName":"Jenkins Workspace Cleanup Plugin","pinned":True,"shortName":"ws-cleanup","supportsDynamicLoad":"MAYBE","url":"","version":"1.2.3"}]}
"""

class MockServerRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if 'crumbIssuer' in self.path:
            self.send_response(requests.codes.ok)
            self.end_headers()
            self.wfile.write(CRUMBDATA)
            return
        if 'pluginManager' in self.path:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(PLUGINS)
            return

        try:
            txData = self.server.getTxData()
            txData=txData[self.path]
            self.send_response(requests.codes.ok)
            self.end_headers()
            self.wfile.write(txData)
        except KeyError:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if 'job' in self.path:
            if 'build' in self.path or 'description' in self.path :
                length = int(self.headers.get('content-length',0))
                fstream = self.rfile.read(length)
                self.server.rxJenkinsData((self.path , fstream))
                self.send_response(requests.codes.created)
                self.end_headers()
                return
            if 'config.xml' in self.path:
                length = int(self.headers.get('content-length',0))
                fstream = self.rfile.read(length)
                self.server.rxJenkinsData((self.path , fstream))
                self.send_response(requests.codes.ok)
                self.end_headers()
                return
        if 'doDelete' in self.path:
            self.send_response(302)
            self.end_headers()
            self.server.rxJenkinsData((self.path , ''))
            return
        if 'createItem' in self.path:
            length = int(self.headers.get('content-length',0))
            fstream = self.rfile.read(length)
            self.server.rxJenkinsData((self.path , fstream))
            self.send_response(200)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()
        return

    def do_QUIT(self):
        self.send_response(200)
        self.end_headers()
        return

    def log_message(self, format, *args):
        return

class StoppableHttpServer (HTTPServer):
    recieveBuf  = []
    transmitBuf = {}

    def serve_forever (self):
        self.stop = False
        while not self.stop:
            self.handle_request()

    def stop_server(self):
        self.stop = True

    def rxJenkinsData(self, data):
        self.recieveBuf.append(data)

    def getTxData(self):
        return self.transmitBuf

    def getJenkinsData(self):
        ret = self.recieveBuf.copy()
        self.recieveBuf = []
        return ret

    def addJenkinsData(self, request, data):
        self.transmitBuf[request] = data

class JenkinsMock():
    def start_mock_server(self, port):
        self.mock_server = StoppableHttpServer(('localhost', port), MockServerRequestHandler)
        self.mock_server_thread = Thread(target=self.mock_server.serve_forever)
        self.mock_server_thread.setDaemon(True)
        self.mock_server_thread.start()

    def stop_mock_server(self, port):
        self.mock_server.stop_server()
        # send a dummy request to break the while loop...
        conn = http.client.HTTPConnection("localhost", port)
        conn.request("QUIT", "/")
        conn.getresponse()
        self.mock_server.socket.close()

    def addServerData(self, request, data):
        self.mock_server.addJenkinsData(request, data)

    def getServerData(self):
        return self.mock_server.getJenkinsData()
