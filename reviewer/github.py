"""A small GitHub API client (standard library only): REST with pagination, GraphQL, and retries."""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
TRIES = 3
TIMEOUT = 60
# GitHub asks clients to wait at least a minute after a secondary rate limit that names no delay.
SECONDARY_WAIT = 60
MAX_WAIT = 120
NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubError(Exception):
    """A request GitHub refused, or that kept failing."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Client:
    def __init__(self, token, api=API, opener=None, sleep=time.sleep):
        self.api = api.rstrip("/")
        self.opener = opener or urllib.request.build_opener()
        self.sleep = sleep
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "arikkfir-reviewer",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def rest(self, method, path, params=None, body=None):
        """Sends one REST request and returns its decoded JSON body, or None when it has none."""
        _, payload = self._send(method, self._url(path, params), body)
        return json.loads(payload) if payload.strip() else None

    def paginate(self, path, params=None):
        """GETs every page of a list endpoint, following the Link header's rel="next"."""
        url = self._url(path, {**(params or {}), "per_page": 100})
        items = []
        while url:
            headers, payload = self._send("GET", url)
            items.extend(json.loads(payload))
            match = NEXT_LINK.search(headers.get("Link") or "")
            url = match[1] if match else None
        return items

    def graphql(self, query, variables=None):
        """Runs a GraphQL query or mutation and returns its data. GraphQL errors raise GitHubError."""
        _, payload = self._send("POST", f"{self.api}/graphql", {"query": query, "variables": variables or {}})
        result = json.loads(payload)
        if result.get("errors"):
            messages = "; ".join(str(error.get("message", error)) for error in result["errors"])
            raise GitHubError(f"GraphQL: {messages}")
        return result["data"]

    def _url(self, path, params):
        url = path if path.startswith("https://") else self.api + path
        return f"{url}?{urllib.parse.urlencode(params)}" if params else url

    def _send(self, method, url, body=None):
        data = None if body is None else json.dumps(body).encode()
        headers = dict(self.headers, **({"Content-Type": "application/json"} if data is not None else {}))
        for attempt in range(1, TRIES + 1):
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with self.opener.open(request, timeout=TIMEOUT) as response:
                    return response.headers, response.read()
            except urllib.error.HTTPError as error:
                payload = error.read()
                wait = retry_delay(error.code, error.headers, payload, attempt)
                if wait is None or attempt == TRIES:
                    raise GitHubError(f"{method} {url}: HTTP {error.code}: {describe(payload)}", error.code) from None
            except OSError as error:  # connection failures and timeouts (URLError is an OSError)
                if attempt == TRIES:
                    raise GitHubError(f"{method} {url}: {error}") from None
                wait = 2 ** (attempt - 1)
            self.sleep(wait)


def retry_delay(status, headers, payload, attempt):
    """Seconds to wait before retrying a failed request, or None when a retry can't help."""
    if status >= 500:
        return 2 ** (attempt - 1)
    if status not in (403, 429):
        return None
    retry_after = headers.get("Retry-After")
    if retry_after:
        return min(int(retry_after), MAX_WAIT) if retry_after.isdigit() else SECONDARY_WAIT
    if headers.get("X-RateLimit-Remaining") == "0":
        reset = headers.get("X-RateLimit-Reset") or "0"
        return min(max(int(reset) - int(time.time()), 1), MAX_WAIT) if reset.isdigit() else SECONDARY_WAIT
    if status == 429 or b"secondary rate limit" in payload.lower():
        return SECONDARY_WAIT
    return None


def describe(payload):
    """GitHub's error message from a response body, or the start of the body."""
    try:
        return json.loads(payload)["message"]
    except (ValueError, KeyError, TypeError):
        return payload[:200].decode("utf-8", "replace")
