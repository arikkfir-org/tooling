#!/usr/bin/env python3
"""Lets the review task's model read GitHub with the run's token, without the token: the github sidecar of the review
pod serves GitHub's REST API under /api/ and its git endpoints under /git/ on 127.0.0.1, and adds the token to each
request it forwards. Only reads go through: GET and HEAD on the API, and git fetches (info/refs for git-upload-pack,
and git-upload-pack); never a push, a write or GraphQL. Octomaton mints the token read-only (contents and pull
requests) for every repository of the organization and refreshes its file while the run lives, so it is read for each
request. The model's steps never mount it.

Usage: github_proxy.py --token-file FILE [--port 8080]
"""

import argparse
import base64
import http.client
import http.server
import re
import sys
import urllib.parse

API = "https://api.github.com"
GIT = "https://github.com"
PORT = 8080
TIMEOUT = 120
CHUNK = 64 * 1024
# Request headers the proxy drops: hop-by-hop ones (RFC 9110, 7.6.1), credentials, and those it sets itself.
DROPPED_REQUEST = {"authorization", "connection", "content-length", "cookie", "host", "keep-alive",
                   "proxy-authorization", "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade"}
# Response headers the proxy drops: hop-by-hop ones, cookies, and the framing it sets itself.
DROPPED_RESPONSE = {"connection", "content-length", "keep-alive", "proxy-authenticate", "set-cookie", "trailer",
                    "transfer-encoding", "upgrade"}
GIT_PATH = re.compile(r"^/[\w.-]+/[\w.-]+\.git/(info/refs|git-upload-pack)$")
USAGE = ("Serves GitHub with the run's read-only token: GET /api/<REST path> (for example /api/repos/OWNER/REPO/pulls/1)"
         " and git fetches from /git/OWNER/REPO.git.\n")


class Refusal(Exception):
    """A request the proxy doesn't forward."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def route(method, path, query):
    """Returns the upstream ("api" or "git") and path for a request the proxy forwards; raises Refusal otherwise."""
    if path.startswith("/api/"):
        if method not in ("GET", "HEAD"):
            raise Refusal(405, "The GitHub API is read-only here: GET and HEAD only.\n")
        return "api", path[len("/api"):]
    if path.startswith("/git/"):
        target = path[len("/git"):]
        match = GIT_PATH.match(target)
        if not match:
            raise Refusal(404, "Only git fetches go through: /git/OWNER/REPO.git.\n")
        if match.group(1) == "info/refs":
            services = urllib.parse.parse_qs(query).get("service")
            if method not in ("GET", "HEAD") or services != ["git-upload-pack"]:
                raise Refusal(403, "Only git fetches go through, never a push.\n")
        elif method != "POST":
            raise Refusal(405, "git-upload-pack takes POST.\n")
        return "git", target
    raise Refusal(404, USAGE)


def authorization(upstream, token):
    """The Authorization header GitHub reads the token from: a bearer token for the API, HTTP Basic for git."""
    if upstream == "api":
        return f"Bearer {token}"
    return "Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()


def local_location(location, bases):
    """Points a redirect to GitHub back at the proxy, so that following it keeps the token."""
    for prefix, base in bases.items():
        if location.startswith(base + "/"):
            return f"/{prefix}" + location[len(base):]
    return location


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "github-proxy"

    def do_GET(self):
        # Every response ends its connection, so a body without Content-Length ends with it too.
        self.close_connection = True
        path, _, query = self.path.partition("?")
        if path == "/healthz":
            self.reply(200, "ok\n")
            return
        try:
            upstream, target = route(self.command, path, query)
            token = self.token()
        except Refusal as refusal:
            self.reply(refusal.status, str(refusal))
            return
        try:
            body = self.body()
        except ValueError as error:
            self.reply(400, f"Unreadable request body: {error}\n")
            return
        self.forward(upstream, target + (f"?{query}" if query else ""), authorization(upstream, token), body)

    do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_GET

    def token(self):
        try:
            with open(self.server.token_file, encoding="utf-8") as f:
                token = f.read().strip()
        except OSError:
            token = ""
        if not token:
            raise Refusal(503, "No GitHub token yet.\n")
        return token

    def body(self):
        """The request's body, de-chunked, or None."""
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            parts = []
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip(), 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass  # trailers
                    return b"".join(parts)
                parts.append(self.rfile.read(size))
                self.rfile.readline()
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else None

    def forward(self, upstream, target, auth, body):
        base = self.server.bases[upstream]
        url = urllib.parse.urlsplit(base)
        connection_class = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(url.netloc, timeout=TIMEOUT)
        headers = {name: value for name, value in self.headers.items() if name.lower() not in DROPPED_REQUEST}
        headers.setdefault("User-Agent", "arikkfir-reviewer")
        headers["Authorization"] = auth
        try:
            connection.request(self.command, url.path + target, body=body, headers=headers)
            response = connection.getresponse()
        except (OSError, http.client.HTTPException) as error:
            connection.close()
            self.reply(502, f"GitHub is unreachable: {error}\n")
            return
        try:
            self.relay(response)
        finally:
            connection.close()

    def relay(self, response):
        self.send_response(response.status, response.reason)
        for name, value in response.getheaders():
            if name.lower() == "location":
                value = local_location(value, self.server.bases)
            if name.lower() not in DROPPED_RESPONSE:
                self.send_header(name, value)
        length = response.getheader("Content-Length")
        if length is not None and response.getheader("Transfer-Encoding") is None:
            self.send_header("Content-Length", length)
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command == "HEAD" or response.status in (204, 304):
            return
        while chunk := response.read(CHUNK):
            self.wfile.write(chunk)

    def reply(self, status, text):
        data = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def log_message(self, format, *args):
        self.server.log.write(format % args + "\n")
        self.server.log.flush()


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, token_file, port=PORT, api=API, git=GIT, log=sys.stderr):
        super().__init__(("127.0.0.1", port), Handler)
        self.token_file, self.log = token_file, log
        self.bases = {"api": api.rstrip("/"), "git": git.rstrip("/")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args(argv)
    server = Server(args.token_file, args.port)
    print(f"Serving GitHub on http://127.0.0.1:{server.server_port}", file=sys.stderr, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
