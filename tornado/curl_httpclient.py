"""Non-blocking HTTP client implementation using pycurl."""
import collections
import functools
import logging
import pycurl
import re
import threading
import time
from io import BytesIO
from tornado import httputil
from tornado import ioloop
from tornado.escape import utf8, native_str
from tornado.httpclient import HTTPRequest, HTTPResponse, HTTPError, AsyncHTTPClient, main
from tornado.log import app_log
from typing import Dict, Any, Callable, Union, Optional
import typing
if typing.TYPE_CHECKING:
    from typing import Deque, Tuple
curl_log = logging.getLogger('tornado.curl_httpclient')
CR_OR_LF_RE = re.compile(b'\r|\n')

class CurlAsyncHTTPClient(AsyncHTTPClient):

    def _handle_socket(self, event: int, fd: int, multi: Any, data: bytes) -> None:
        """Called by libcurl when it wants to change the file descriptors
        it cares about.
        """
        pass

    def _set_timeout(self, msecs: int) -> None:
        """Called by libcurl to schedule a timeout."""
        pass

    def _handle_events(self, fd: int, events: int) -> None:
        """Called by IOLoop when there is activity on one of our
        file descriptors.
        """
        pass

    def _handle_timeout(self) -> None:
        """Called by IOLoop when the requested timeout has passed."""
        pass

    def _handle_force_timeout(self) -> None:
        """Called by IOLoop periodically to ask libcurl to process any
        events it may have forgotten about.
        """
        pass

    def _finish_pending_requests(self) -> None:
        """Process any requests that were completed by the last
        call to multi.socket_action.
        """
        pass

class CurlError(HTTPError):

    def __init__(self, errno: int, message: str) -> None:
        HTTPError.__init__(self, 599, message)
        self.errno = errno
if __name__ == '__main__':
    AsyncHTTPClient.configure(CurlAsyncHTTPClient)
    main()