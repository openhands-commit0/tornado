"""``tornado.web`` provides a simple web framework with asynchronous
features that allow it to scale to large numbers of open connections,
making it ideal for `long polling
<http://en.wikipedia.org/wiki/Push_technology#Long_polling>`_.

Here is a simple "Hello, world" example app:

.. testcode::

    import asyncio
    import tornado

    class MainHandler(tornado.web.RequestHandler):
        def get(self):
            self.write("Hello, world")

    async def main():
        application = tornado.web.Application([
            (r"/", MainHandler),
        ])
        application.listen(8888)
        await asyncio.Event().wait()

    if __name__ == "__main__":
        asyncio.run(main())

.. testoutput::
   :hide:


See the :doc:`guide` for additional information.

Thread-safety notes
-------------------

In general, methods on `RequestHandler` and elsewhere in Tornado are
not thread-safe. In particular, methods such as
`~RequestHandler.write()`, `~RequestHandler.finish()`, and
`~RequestHandler.flush()` must only be called from the main thread. If
you use multiple threads it is important to use `.IOLoop.add_callback`
to transfer control back to the main thread before finishing the
request, or to limit your use of other threads to
`.IOLoop.run_in_executor` and ensure that your callbacks running in
the executor do not refer to Tornado objects.

"""
import base64
import binascii
import datetime
import email.utils
import functools
import gzip
import hashlib
import hmac
import http.cookies
from inspect import isclass
from io import BytesIO
import mimetypes
import numbers
import os.path
import re
import socket
import sys
import threading
import time
import warnings
import tornado
import traceback
import types
import urllib.parse
from urllib.parse import urlencode
from tornado.concurrent import Future, future_set_result_unless_cancelled
from tornado import escape
from tornado import gen
from tornado.httpserver import HTTPServer
from tornado import httputil
from tornado import iostream
from tornado import locale
from tornado.log import access_log, app_log, gen_log
from tornado import template
from tornado.escape import utf8, _unicode
from tornado.routing import AnyMatches, DefaultHostMatches, HostMatches, ReversibleRouter, Rule, ReversibleRuleRouter, URLSpec, _RuleList
from tornado.util import ObjectDict, unicode_type, _websocket_mask
url = URLSpec
from typing import Dict, Any, Union, Optional, Awaitable, Tuple, List, Callable, Iterable, Generator, Type, TypeVar, cast, overload
from types import TracebackType
import typing
if typing.TYPE_CHECKING:
    from typing import Set
_HeaderTypes = Union[bytes, unicode_type, int, numbers.Integral, datetime.datetime]
_CookieSecretTypes = Union[str, bytes, Dict[int, str], Dict[int, bytes]]
MIN_SUPPORTED_SIGNED_VALUE_VERSION = 1
'The oldest signed value version supported by this version of Tornado.\n\nSigned values older than this version cannot be decoded.\n\n.. versionadded:: 3.2.1\n'
MAX_SUPPORTED_SIGNED_VALUE_VERSION = 2
'The newest signed value version supported by this version of Tornado.\n\nSigned values newer than this version cannot be decoded.\n\n.. versionadded:: 3.2.1\n'
DEFAULT_SIGNED_VALUE_VERSION = 2
'The signed value version produced by `.RequestHandler.create_signed_value`.\n\nMay be overridden by passing a ``version`` keyword argument.\n\n.. versionadded:: 3.2.1\n'
DEFAULT_SIGNED_VALUE_MIN_VERSION = 1
'The oldest signed value accepted by `.RequestHandler.get_signed_cookie`.\n\nMay be overridden by passing a ``min_version`` keyword argument.\n\n.. versionadded:: 3.2.1\n'

class _ArgDefaultMarker:
    pass
_ARG_DEFAULT = _ArgDefaultMarker()

def _unimplemented_method(*args: Any, **kwargs: Any) -> None:
    raise HTTPError(405)

def _create_signature_v1(secret: Union[str, bytes], *parts: Union[str, bytes]) -> bytes:
    hash = hmac.new(utf8(secret), digestmod=hashlib.sha1)
    for part in parts:
        hash.update(utf8(part))
    return utf8(hash.hexdigest())

def get_signature_key_version(value: Union[None, str, bytes]) -> Optional[int]:
    """Extract the signature key version from the given signed value.

    Returns None if the version cannot be determined.
    """
    if value is None:
        return None
    value = utf8(value)
    parts = value.split(b"|")
    if len(parts) < 3:
        return None
    if len(parts) > 3:
        try:
            return int(parts[3])
        except ValueError:
            return None
    return None

def decode_signed_value(secret: _CookieSecretTypes, name: str, value: Union[None, str, bytes], max_age_days: Optional[float]=31, min_version: Optional[int]=None, clock: Callable[[], float]=None) -> Optional[bytes]:
    """Decode a signed value.

    Returns the decoded value if the signature is valid and the value is not
    expired, or None otherwise.
    """
    if min_version is None:
        min_version = DEFAULT_SIGNED_VALUE_MIN_VERSION
    if clock is None:
        clock = time.time
    if value is None:
        return None
    value = utf8(value)
    parts = value.split(b"|")
    if len(parts) < 3:
        return None
    signature = parts[-1]
    if not isinstance(secret, dict):
        expected_sig = _create_signature_v1(secret, name, parts[0], parts[1])
        if not hmac.compare_digest(utf8(expected_sig), utf8(signature)):
            return None
        timestamp = int(parts[1])
        if timestamp < int(clock()) - max_age_days * 86400:
            return None
        try:
            return base64.b64decode(parts[0])
        except Exception:
            return None
    else:
        version = 2
        if len(parts) < 4:
            return None
        key_version = int(parts[-1])
        if key_version not in secret:
            return None
        expected_sig = _create_signature_v1(secret[key_version], name, parts[0], parts[1])
        if not hmac.compare_digest(utf8(expected_sig), utf8(signature)):
            return None
        timestamp = int(parts[1])
        if timestamp < int(clock()) - max_age_days * 86400:
            return None
        try:
            return base64.b64decode(parts[0])
        except Exception:
            return None

def create_signed_value(secret: _CookieSecretTypes, name: str, value: Union[str, bytes], version: Optional[int]=None, clock: Callable[[], float]=None, key_version: Optional[int]=None) -> bytes:
    """Signs and timestamps a string so it cannot be forged.

    Stores signatures in the format: value|timestamp|signature[|key_version].
    Parts are joined with pipes, and may not contain pipes themselves.
    """
    if version is None:
        version = DEFAULT_SIGNED_VALUE_VERSION
    if clock is None:
        clock = time.time

    timestamp = utf8(str(int(clock())))
    value = base64.b64encode(utf8(value))

    if version == 1:
        if isinstance(secret, dict):
            raise ValueError("secret_dict cannot be used with version 1")
        signature = _create_signature_v1(secret, name, value, timestamp)
        value = b"|".join([value, timestamp, signature])
        return value
    elif version == 2:
        # Version 2 adds key versioning, but is otherwise the same.
        if isinstance(secret, dict):
            if key_version is None:
                key_version = max(secret.keys())
            assert key_version in secret
            secret = secret[key_version]
        signature = _create_signature_v1(secret, name, value, timestamp)
        value = b"|".join([value, timestamp, signature])
        if key_version is not None:
            value = b"|".join([value, utf8(str(key_version))])
        return value
    else:
        raise ValueError("Unsupported version %d" % version)

class RequestHandler(object):
    """Base class for HTTP request handlers.

    Subclasses must define at least one of the methods defined in the
    "Entry points" section below.

    Applications should not construct `RequestHandler` objects
    directly and subclasses should not override ``__init__`` (override
    `~RequestHandler.initialize` instead).

    """
    SUPPORTED_METHODS = ('GET', 'HEAD', 'POST', 'DELETE', 'PATCH', 'PUT', 'OPTIONS')
    _template_loaders = {}
    _template_loader_lock = threading.Lock()
    _remove_control_chars_regex = re.compile('[\\x00-\\x08\\x0e-\\x1f]')
    _stream_request_body = False
    _transforms = None
    path_args = None
    path_kwargs = None

    def __init__(self, application: 'Application', request: httputil.HTTPServerRequest, **kwargs: Any) -> None:
        super().__init__()
        self.application = application
        self.request = request
        self._headers_written = False
        self._finished = False
        self._auto_finish = True
        self._prepared_future = None
        self.ui = ObjectDict(((n, self._ui_method(m)) for n, m in application.ui_methods.items()))
        self.ui['_tt_modules'] = _UIModuleNamespace(self, application.ui_modules)
        self.ui['modules'] = self.ui['_tt_modules']
        self.clear()
        assert self.request.connection is not None
        self.request.connection.set_close_callback(self.on_connection_close)
        self.initialize(**kwargs)

    def _initialize(self) -> None:
        """Hook for subclass initialization.

        A dictionary passed as the third argument of a ``URLSpec`` will be
        supplied as keyword arguments to initialize().

        Example::

            class ProfileHandler(RequestHandler):
                def initialize(self, database):
                    self.database = database

                def get(self, username):
                    ...

            app = Application([
                (r'/user/(.*)', ProfileHandler, dict(database=database)),
                ])
        """
        pass

    initialize = _initialize
    "Hook for subclass initialization. Called for each request.\n\n    A dictionary passed as the third argument of a ``URLSpec`` will be\n    supplied as keyword arguments to ``initialize()``.\n\n    Example::\n\n        class ProfileHandler(RequestHandler):\n            def initialize(self, database):\n                self.database = database\n\n            def get(self, username):\n                ...\n\n        app = Application([\n            (r'/user/(.*)', ProfileHandler, dict(database=database)),\n            ])\n    "

    @property
    def settings(self) -> Dict[str, Any]:
        """An alias for `self.application.settings <Application.settings>`."""
        pass
    head = _unimplemented_method
    get = _unimplemented_method
    post = _unimplemented_method
    delete = _unimplemented_method
    patch = _unimplemented_method
    put = _unimplemented_method
    options = _unimplemented_method

    def prepare(self) -> Optional[Awaitable[None]]:
        """Called at the beginning of a request before  `get`/`post`/etc.

        Override this method to perform common initialization regardless
        of the request method.

        Asynchronous support: Use ``async def`` or decorate this method with
        `.gen.coroutine` to make it asynchronous.
        If this method returns an  ``Awaitable`` execution will not proceed
        until the ``Awaitable`` is done.

        .. versionadded:: 3.1
           Asynchronous support.
        """
        pass

    def on_finish(self) -> None:
        """Called after the end of a request.

        Override this method to perform cleanup, logging, etc.
        This method is a counterpart to `prepare`.  ``on_finish`` may
        not produce any output, as it is called after the response
        has been sent to the client.
        """
        pass

    def on_connection_close(self) -> None:
        """Called in async handlers if the client closed the connection.

        Override this to clean up resources associated with
        long-lived connections.  Note that this method is called only if
        the connection was closed during asynchronous processing; if you
        need to do cleanup after every request override `on_finish`
        instead.

        Proxies may keep a connection open for a time (perhaps
        indefinitely) after the client has gone away, so this method
        may not be called promptly after the end user closes their
        connection.
        """
        pass

    def clear(self) -> None:
        """Resets all headers and content for this response."""
        pass

    def set_default_headers(self) -> None:
        """Override this to set HTTP headers at the beginning of the request.

        For example, this is the place to set a custom ``Server`` header.
        Note that setting such headers in the normal flow of request
        processing may not do what you want, since headers may be reset
        during error handling.
        """
        pass

    def set_status(self, status_code: int, reason: Optional[str]=None) -> None:
        """Sets the status code for our response.

        :arg int status_code: Response status code.
        :arg str reason: Human-readable reason phrase describing the status
            code. If ``None``, it will be filled in from
            `http.client.responses` or "Unknown".

        .. versionchanged:: 5.0

           No longer validates that the response code is in
           `http.client.responses`.
        """
        pass

    def get_status(self) -> int:
        """Returns the status code for our response."""
        pass

    def set_header(self, name: str, value: _HeaderTypes) -> None:
        """Sets the given response header name and value.

        All header values are converted to strings (`datetime` objects
        are formatted according to the HTTP specification for the
        ``Date`` header).

        """
        pass

    def add_header(self, name: str, value: _HeaderTypes) -> None:
        """Adds the given response header and value.

        Unlike `set_header`, `add_header` may be called multiple times
        to return multiple values for the same header.
        """
        pass

    def clear_header(self, name: str) -> None:
        """Clears an outgoing header, undoing a previous `set_header` call.

        Note that this method does not apply to multi-valued headers
        set by `add_header`.
        """
        pass
    _INVALID_HEADER_CHAR_RE = re.compile('[\\x00-\\x1f]')

    def get_argument(self, name: str, default: Union[None, str, _ArgDefaultMarker]=_ARG_DEFAULT, strip: bool=True) -> Optional[str]:
        """Returns the value of the argument with the given name.

        If default is not provided, the argument is considered to be
        required, and we raise a `MissingArgumentError` if it is missing.

        If the argument appears in the request more than once, we return the
        last value.

        This method searches both the query and body arguments.
        """
        pass

    def get_arguments(self, name: str, strip: bool=True) -> List[str]:
        """Returns a list of the arguments with the given name.

        If the argument is not present, returns an empty list.

        This method searches both the query and body arguments.
        """
        pass

    def get_body_argument(self, name: str, default: Union[None, str, _ArgDefaultMarker]=_ARG_DEFAULT, strip: bool=True) -> Optional[str]:
        """Returns the value of the argument with the given name
        from the request body.

        If default is not provided, the argument is considered to be
        required, and we raise a `MissingArgumentError` if it is missing.

        If the argument appears in the url more than once, we return the
        last value.

        .. versionadded:: 3.2
        """
        pass

    def get_body_arguments(self, name: str, strip: bool=True) -> List[str]:
        """Returns a list of the body arguments with the given name.

        If the argument is not present, returns an empty list.

        .. versionadded:: 3.2
        """
        pass

    def get_query_argument(self, name: str, default: Union[None, str, _ArgDefaultMarker]=_ARG_DEFAULT, strip: bool=True) -> Optional[str]:
        """Returns the value of the argument with the given name
        from the request query string.

        If default is not provided, the argument is considered to be
        required, and we raise a `MissingArgumentError` if it is missing.

        If the argument appears in the url more than once, we return the
        last value.

        .. versionadded:: 3.2
        """
        pass

    def get_query_arguments(self, name: str, strip: bool=True) -> List[str]:
        """Returns a list of the query arguments with the given name.

        If the argument is not present, returns an empty list.

        .. versionadded:: 3.2
        """
        pass

    def decode_argument(self, value: bytes, name: Optional[str]=None) -> str:
        """Decodes an argument from the request.

        The argument has been percent-decoded and is now a byte string.
        By default, this method decodes the argument as utf-8 and returns
        a unicode string, but this may be overridden in subclasses.

        This method is used as a filter for both `get_argument()` and for
        values extracted from the url and passed to `get()`/`post()`/etc.

        The name of the argument is provided if known, but may be None
        (e.g. for unnamed groups in the url regex).
        """
        pass

    @property
    def cookies(self) -> Dict[str, http.cookies.Morsel]:
        """An alias for
        `self.request.cookies <.httputil.HTTPServerRequest.cookies>`."""
        pass

    def get_cookie(self, name: str, default: Optional[str]=None) -> Optional[str]:
        """Returns the value of the request cookie with the given name.

        If the named cookie is not present, returns ``default``.

        This method only returns cookies that were present in the request.
        It does not see the outgoing cookies set by `set_cookie` in this
        handler.
        """
        pass

    def set_cookie(self, name: str, value: Union[str, bytes], domain: Optional[str]=None, expires: Optional[Union[float, Tuple, datetime.datetime]]=None, path: str='/', expires_days: Optional[float]=None, *, max_age: Optional[int]=None, httponly: bool=False, secure: bool=False, samesite: Optional[str]=None, **kwargs: Any) -> None:
        """Sets an outgoing cookie name/value with the given options.

        Newly-set cookies are not immediately visible via `get_cookie`;
        they are not present until the next request.

        Most arguments are passed directly to `http.cookies.Morsel` directly.
        See https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Set-Cookie
        for more information.

        ``expires`` may be a numeric timestamp as returned by `time.time`,
        a time tuple as returned by `time.gmtime`, or a
        `datetime.datetime` object. ``expires_days`` is provided as a convenience
        to set an expiration time in days from today (if both are set, ``expires``
        is used).

        .. deprecated:: 6.3
           Keyword arguments are currently accepted case-insensitively.
           In Tornado 7.0 this will be changed to only accept lowercase
           arguments.
        """
        pass

    def clear_cookie(self, name: str, **kwargs: Any) -> None:
        """Deletes the cookie with the given name.

        This method accepts the same arguments as `set_cookie`, except for
        ``expires`` and ``max_age``. Clearing a cookie requires the same
        ``domain`` and ``path`` arguments as when it was set. In some cases the
        ``samesite`` and ``secure`` arguments are also required to match. Other
        arguments are ignored.

        Similar to `set_cookie`, the effect of this method will not be
        seen until the following request.

        .. versionchanged:: 6.3

           Now accepts all keyword arguments that ``set_cookie`` does.
           The ``samesite`` and ``secure`` flags have recently become
           required for clearing ``samesite="none"`` cookies.
        """
        pass

    def clear_all_cookies(self, **kwargs: Any) -> None:
        """Attempt to delete all the cookies the user sent with this request.

        See `clear_cookie` for more information on keyword arguments. Due to
        limitations of the cookie protocol, it is impossible to determine on the
        server side which values are necessary for the ``domain``, ``path``,
        ``samesite``, or ``secure`` arguments, this method can only be
        successful if you consistently use the same values for these arguments
        when setting cookies.

        Similar to `set_cookie`, the effect of this method will not be seen
        until the following request.

        .. versionchanged:: 3.2

           Added the ``path`` and ``domain`` parameters.

        .. versionchanged:: 6.3

           Now accepts all keyword arguments that ``set_cookie`` does.

        .. deprecated:: 6.3

           The increasingly complex rules governing cookies have made it
           impossible for a ``clear_all_cookies`` method to work reliably
           since all we know about cookies are their names. Applications
           should generally use ``clear_cookie`` one at a time instead.
        """
        pass

    def set_signed_cookie(self, name: str, value: Union[str, bytes], expires_days: Optional[float]=30, version: Optional[int]=None, **kwargs: Any) -> None:
        """Signs and timestamps a cookie so it cannot be forged.

        You must specify the ``cookie_secret`` setting in your Application
        to use this method. It should be a long, random sequence of bytes
        to be used as the HMAC secret for the signature.

        To read a cookie set with this method, use `get_signed_cookie()`.

        Note that the ``expires_days`` parameter sets the lifetime of the
        cookie in the browser, but is independent of the ``max_age_days``
        parameter to `get_signed_cookie`.
        A value of None limits the lifetime to the current browser session.

        Secure cookies may contain arbitrary byte values, not just unicode
        strings (unlike regular cookies)

        Similar to `set_cookie`, the effect of this method will not be
        seen until the following request.

        .. versionchanged:: 3.2.1

           Added the ``version`` argument.  Introduced cookie version 2
           and made it the default.

        .. versionchanged:: 6.3

           Renamed from ``set_secure_cookie`` to ``set_signed_cookie`` to
           avoid confusion with other uses of "secure" in cookie attributes
           and prefixes. The old name remains as an alias.
        """
        pass
    set_secure_cookie = set_signed_cookie

    def create_signed_value(self, name: str, value: Union[str, bytes], version: Optional[int]=None) -> bytes:
        """Signs and timestamps a string so it cannot be forged.

        Normally used via set_signed_cookie, but provided as a separate
        method for non-cookie uses.  To decode a value not stored
        as a cookie use the optional value argument to get_signed_cookie.

        .. versionchanged:: 3.2.1

           Added the ``version`` argument.  Introduced cookie version 2
           and made it the default.
        """
        pass

    def get_signed_cookie(self, name: str, value: Optional[str]=None, max_age_days: float=31, min_version: Optional[int]=None) -> Optional[bytes]:
        """Returns the given signed cookie if it validates, or None.

        The decoded cookie value is returned as a byte string (unlike
        `get_cookie`).

        Similar to `get_cookie`, this method only returns cookies that
        were present in the request. It does not see outgoing cookies set by
        `set_signed_cookie` in this handler.

        .. versionchanged:: 3.2.1

           Added the ``min_version`` argument.  Introduced cookie version 2;
           both versions 1 and 2 are accepted by default.

         .. versionchanged:: 6.3

           Renamed from ``get_secure_cookie`` to ``get_signed_cookie`` to
           avoid confusion with other uses of "secure" in cookie attributes
           and prefixes. The old name remains as an alias.

        """
        pass
    get_secure_cookie = get_signed_cookie

    def get_signed_cookie_key_version(self, name: str, value: Optional[str]=None) -> Optional[int]:
        """Returns the signing key version of the secure cookie.

        The version is returned as int.

        .. versionchanged:: 6.3

           Renamed from ``get_secure_cookie_key_version`` to
           ``set_signed_cookie_key_version`` to avoid confusion with other
           uses of "secure" in cookie attributes and prefixes. The old name
           remains as an alias.

        """
        pass
    get_secure_cookie_key_version = get_signed_cookie_key_version

    def redirect(self, url: str, permanent: bool=False, status: Optional[int]=None) -> None:
        """Sends a redirect to the given (optionally relative) URL.

        If the ``status`` argument is specified, that value is used as the
        HTTP status code; otherwise either 301 (permanent) or 302
        (temporary) is chosen based on the ``permanent`` argument.
        The default is 302 (temporary).
        """
        pass

    def write(self, chunk: Union[str, bytes, dict]) -> None:
        """Writes the given chunk to the output buffer.

        To write the output to the network, use the `flush()` method below.

        If the given chunk is a dictionary, we write it as JSON and set
        the Content-Type of the response to be ``application/json``.
        (if you want to send JSON as a different ``Content-Type``, call
        ``set_header`` *after* calling ``write()``).

        Note that lists are not converted to JSON because of a potential
        cross-site security vulnerability.  All JSON output should be
        wrapped in a dictionary.  More details at
        http://haacked.com/archive/2009/06/25/json-hijacking.aspx/ and
        https://github.com/facebook/tornado/issues/1009
        """
        pass

    def render(self, template_name: str, **kwargs: Any) -> 'Future[None]':
        """Renders the template with the given arguments as the response.

        ``render()`` calls ``finish()``, so no other output methods can be called
        after it.

        Returns a `.Future` with the same semantics as the one returned by `finish`.
        Awaiting this `.Future` is optional.

        .. versionchanged:: 5.1

           Now returns a `.Future` instead of ``None``.
        """
        pass

    def render_linked_js(self, js_files: Iterable[str]) -> str:
        """Default method used to render the final js links for the
        rendered webpage.

        Override this method in a sub-classed controller to change the output.
        """
        pass

    def render_embed_js(self, js_embed: Iterable[bytes]) -> bytes:
        """Default method used to render the final embedded js for the
        rendered webpage.

        Override this method in a sub-classed controller to change the output.
        """
        pass

    def render_linked_css(self, css_files: Iterable[str]) -> str:
        """Default method used to render the final css links for the
        rendered webpage.

        Override this method in a sub-classed controller to change the output.
        """
        pass

    def render_embed_css(self, css_embed: Iterable[bytes]) -> bytes:
        """Default method used to render the final embedded css for the
        rendered webpage.

        Override this method in a sub-classed controller to change the output.
        """
        pass

    def render_string(self, template_name: str, **kwargs: Any) -> bytes:
        """Generate the given template with the given arguments.

        We return the generated byte string (in utf8). To generate and
        write a template as a response, use render() above.
        """
        pass

    def get_template_namespace(self) -> Dict[str, Any]:
        """Returns a dictionary to be used as the default template namespace.

        May be overridden by subclasses to add or modify values.

        The results of this method will be combined with additional
        defaults in the `tornado.template` module and keyword arguments
        to `render` or `render_string`.
        """
        pass

    def create_template_loader(self, template_path: str) -> template.BaseLoader:
        """Returns a new template loader for the given path.

        May be overridden by subclasses.  By default returns a
        directory-based loader on the given path, using the
        ``autoescape`` and ``template_whitespace`` application
        settings.  If a ``template_loader`` application setting is
        supplied, uses that instead.
        """
        pass

    def flush(self, include_footers: bool=False) -> 'Future[None]':
        """Flushes the current output buffer to the network.

        .. versionchanged:: 4.0
           Now returns a `.Future` if no callback is given.

        .. versionchanged:: 6.0

           The ``callback`` argument was removed.
        """
        pass

    def finish(self, chunk: Optional[Union[str, bytes, dict]]=None) -> 'Future[None]':
        """Finishes this response, ending the HTTP request.

        Passing a ``chunk`` to ``finish()`` is equivalent to passing that
        chunk to ``write()`` and then calling ``finish()`` with no arguments.

        Returns a `.Future` which may optionally be awaited to track the sending
        of the response to the client. This `.Future` resolves when all the response
        data has been sent, and raises an error if the connection is closed before all
        data can be sent.

        .. versionchanged:: 5.1

           Now returns a `.Future` instead of ``None``.
        """
        pass

    def detach(self) -> iostream.IOStream:
        """Take control of the underlying stream.

        Returns the underlying `.IOStream` object and stops all
        further HTTP processing. Intended for implementing protocols
        like websockets that tunnel over an HTTP handshake.

        This method is only supported when HTTP/1.1 is used.

        .. versionadded:: 5.1
        """
        pass

    def send_error(self, status_code: int=500, **kwargs: Any) -> None:
        """Sends the given HTTP error code to the browser.

        If `flush()` has already been called, it is not possible to send
        an error, so this method will simply terminate the response.
        If output has been written but not yet flushed, it will be discarded
        and replaced with the error page.

        Override `write_error()` to customize the error page that is returned.
        Additional keyword arguments are passed through to `write_error`.
        """
        pass

    def write_error(self, status_code: int, **kwargs: Any) -> None:
        """Override to implement custom error pages.

        ``write_error`` may call `write`, `render`, `set_header`, etc
        to produce output as usual.

        If this error was caused by an uncaught exception (including
        HTTPError), an ``exc_info`` triple will be available as
        ``kwargs["exc_info"]``.  Note that this exception may not be
        the "current" exception for purposes of methods like
        ``sys.exc_info()`` or ``traceback.format_exc``.
        """
        pass

    @property
    def locale(self) -> tornado.locale.Locale:
        """The locale for the current session.

        Determined by either `get_user_locale`, which you can override to
        set the locale based on, e.g., a user preference stored in a
        database, or `get_browser_locale`, which uses the ``Accept-Language``
        header.

        .. versionchanged: 4.1
           Added a property setter.
        """
        pass

    def get_user_locale(self) -> Optional[tornado.locale.Locale]:
        """Override to determine the locale from the authenticated user.

        If None is returned, we fall back to `get_browser_locale()`.

        This method should return a `tornado.locale.Locale` object,
        most likely obtained via a call like ``tornado.locale.get("en")``
        """
        pass

    def get_browser_locale(self, default: str='en_US') -> tornado.locale.Locale:
        """Determines the user's locale from ``Accept-Language`` header.

        See http://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html#sec14.4
        """
        pass

    @property
    def current_user(self) -> Any:
        """The authenticated user for this request.

        This is set in one of two ways:

        * A subclass may override `get_current_user()`, which will be called
          automatically the first time ``self.current_user`` is accessed.
          `get_current_user()` will only be called once per request,
          and is cached for future access::

              def get_current_user(self):
                  user_cookie = self.get_signed_cookie("user")
                  if user_cookie:
                      return json.loads(user_cookie)
                  return None

        * It may be set as a normal variable, typically from an overridden
          `prepare()`::

              @gen.coroutine
              def prepare(self):
                  user_id_cookie = self.get_signed_cookie("user_id")
                  if user_id_cookie:
                      self.current_user = yield load_user(user_id_cookie)

        Note that `prepare()` may be a coroutine while `get_current_user()`
        may not, so the latter form is necessary if loading the user requires
        asynchronous operations.

        The user object may be any type of the application's choosing.
        """
        pass

    def get_current_user(self) -> Any:
        """Override to determine the current user from, e.g., a cookie.

        This method may not be a coroutine.
        """
        pass

    def get_login_url(self) -> str:
        """Override to customize the login URL based on the request.

        By default, we use the ``login_url`` application setting.
        """
        pass

    def get_template_path(self) -> Optional[str]:
        """Override to customize template path for each handler.

        By default, we use the ``template_path`` application setting.
        Return None to load templates relative to the calling file.
        """
        pass

    @property
    def xsrf_token(self) -> bytes:
        """The XSRF-prevention token for the current user/session.

        To prevent cross-site request forgery, we set an '_xsrf' cookie
        and include the same '_xsrf' value as an argument with all POST
        requests. If the two do not match, we reject the form submission
        as a potential forgery.

        See http://en.wikipedia.org/wiki/Cross-site_request_forgery

        This property is of type `bytes`, but it contains only ASCII
        characters. If a character string is required, there is no
        need to base64-encode it; just decode the byte string as
        UTF-8.

        .. versionchanged:: 3.2.2
           The xsrf token will now be have a random mask applied in every
           request, which makes it safe to include the token in pages
           that are compressed.  See http://breachattack.com for more
           information on the issue fixed by this change.  Old (version 1)
           cookies will be converted to version 2 when this method is called
           unless the ``xsrf_cookie_version`` `Application` setting is
           set to 1.

        .. versionchanged:: 4.3
           The ``xsrf_cookie_kwargs`` `Application` setting may be
           used to supply additional cookie options (which will be
           passed directly to `set_cookie`). For example,
           ``xsrf_cookie_kwargs=dict(httponly=True, secure=True)``
           will set the ``secure`` and ``httponly`` flags on the
           ``_xsrf`` cookie.
        """
        pass

    def _get_raw_xsrf_token(self) -> Tuple[Optional[int], bytes, float]:
        """Read or generate the xsrf token in its raw form.

        The raw_xsrf_token is a tuple containing:

        * version: the version of the cookie from which this token was read,
          or None if we generated a new token in this request.
        * token: the raw token data; random (non-ascii) bytes.
        * timestamp: the time this token was generated (will not be accurate
          for version 1 cookies)
        """
        pass

    def _decode_xsrf_token(self, cookie: str) -> Tuple[Optional[int], Optional[bytes], Optional[float]]:
        """Convert a cookie string into a the tuple form returned by
        _get_raw_xsrf_token.
        """
        pass

    def check_xsrf_cookie(self) -> None:
        """Verifies that the ``_xsrf`` cookie matches the ``_xsrf`` argument.

        To prevent cross-site request forgery, we set an ``_xsrf``
        cookie and include the same value as a non-cookie
        field with all ``POST`` requests. If the two do not match, we
        reject the form submission as a potential forgery.

        The ``_xsrf`` value may be set as either a form field named ``_xsrf``
        or in a custom HTTP header named ``X-XSRFToken`` or ``X-CSRFToken``
        (the latter is accepted for compatibility with Django).

        See http://en.wikipedia.org/wiki/Cross-site_request_forgery

        .. versionchanged:: 3.2.2
           Added support for cookie version 2.  Both versions 1 and 2 are
           supported.
        """
        pass

    def xsrf_form_html(self) -> str:
        """An HTML ``<input/>`` element to be included with all POST forms.

        It defines the ``_xsrf`` input value, which we check on all POST
        requests to prevent cross-site request forgery. If you have set
        the ``xsrf_cookies`` application setting, you must include this
        HTML within all of your HTML forms.

        In a template, this method should be called with ``{% module
        xsrf_form_html() %}``

        See `check_xsrf_cookie()` above for more information.
        """
        pass

    def static_url(self, path: str, include_host: Optional[bool]=None, **kwargs: Any) -> str:
        """Returns a static URL for the given relative static file path.

        This method requires you set the ``static_path`` setting in your
        application (which specifies the root directory of your static
        files).

        This method returns a versioned url (by default appending
        ``?v=<signature>``), which allows the static files to be
        cached indefinitely.  This can be disabled by passing
        ``include_version=False`` (in the default implementation;
        other static file implementations are not required to support
        this, but they may support other options).

        By default this method returns URLs relative to the current
        host, but if ``include_host`` is true the URL returned will be
        absolute.  If this handler has an ``include_host`` attribute,
        that value will be used as the default for all `static_url`
        calls that do not pass ``include_host`` as a keyword argument.

        """
        pass

    def require_setting(self, name: str, feature: str='this feature') -> None:
        """Raises an exception if the given app setting is not defined."""
        pass

    def reverse_url(self, name: str, *args: Any) -> str:
        """Alias for `Application.reverse_url`."""
        pass

    def compute_etag(self) -> Optional[str]:
        """Computes the etag header to be used for this request.

        By default uses a hash of the content written so far.

        May be overridden to provide custom etag implementations,
        or may return None to disable tornado's default etag support.
        """
        pass

    def set_etag_header(self) -> None:
        """Sets the response's Etag header using ``self.compute_etag()``.

        Note: no header will be set if ``compute_etag()`` returns ``None``.

        This method is called automatically when the request is finished.
        """
        pass

    def check_etag_header(self) -> bool:
        """Checks the ``Etag`` header against requests's ``If-None-Match``.

        Returns ``True`` if the request's Etag matches and a 304 should be
        returned. For example::

            self.set_etag_header()
            if self.check_etag_header():
                self.set_status(304)
                return

        This method is called automatically when the request is finished,
        but may be called earlier for applications that override
        `compute_etag` and want to do an early check for ``If-None-Match``
        before completing the request.  The ``Etag`` header should be set
        (perhaps with `set_etag_header`) before calling this method.
        """
        pass

    async def _execute(self, transforms: List['OutputTransform'], *args: bytes, **kwargs: bytes) -> None:
        """Executes this request with the given output transforms."""
        pass

    def data_received(self, chunk: bytes) -> Optional[Awaitable[None]]:
        """Implement this method to handle streamed request data.

        Requires the `.stream_request_body` decorator.

        May be a coroutine for flow control.
        """
        pass

    def _log(self) -> None:
        """Logs the current request.

        Sort of deprecated since this functionality was moved to the
        Application, but left in place for the benefit of existing apps
        that have overridden this method.
        """
        pass

    def log_exception(self, typ: 'Optional[Type[BaseException]]', value: Optional[BaseException], tb: Optional[TracebackType]) -> None:
        """Override to customize logging of uncaught exceptions.

        By default logs instances of `HTTPError` as warnings without
        stack traces (on the ``tornado.general`` logger), and all
        other exceptions as errors with stack traces (on the
        ``tornado.application`` logger).

        .. versionadded:: 3.1
        """
        pass
_RequestHandlerType = TypeVar('_RequestHandlerType', bound=RequestHandler)

def stream_request_body(cls: Type[_RequestHandlerType]) -> Type[_RequestHandlerType]:
    """Apply to `RequestHandler` subclasses to enable streaming body support.

    This decorator implies the following changes:

    * `.HTTPServerRequest.body` is undefined, and body arguments will not
      be included in `RequestHandler.get_argument`.
    * `RequestHandler.prepare` is called when the request headers have been
      read instead of after the entire body has been read.
    * The subclass must define a method ``data_received(self, data):``, which
      will be called zero or more times as data is available.  Note that
      if the request has an empty body, ``data_received`` may not be called.
    * ``prepare`` and ``data_received`` may return Futures (such as via
      ``@gen.coroutine``, in which case the next method will not be called
      until those futures have completed.
    * The regular HTTP method (``post``, ``put``, etc) will be called after
      the entire body has been read.

    See the `file receiver demo <https://github.com/tornadoweb/tornado/tree/stable/demos/file_upload/>`_
    for example usage.
    """
    pass

def removeslash(method: Callable[..., Optional[Awaitable[None]]]) -> Callable[..., Optional[Awaitable[None]]]:
    """Use this decorator to remove trailing slashes from the request path.

    For example, a request to ``/foo/`` would redirect to ``/foo`` with this
    decorator. Your request handler mapping should use a regular expression
    like ``r'/foo/*'`` in conjunction with using the decorator.
    """
    pass

def addslash(method: Callable[..., Optional[Awaitable[None]]]) -> Callable[..., Optional[Awaitable[None]]]:
    """Use this decorator to add a missing trailing slash to the request path.

    For example, a request to ``/foo`` would redirect to ``/foo/`` with this
    decorator. Your request handler mapping should use a regular expression
    like ``r'/foo/?'`` in conjunction with using the decorator.
    """
    pass

class _ApplicationRouter(ReversibleRuleRouter):
    """Routing implementation used internally by `Application`.

    Provides a binding between `Application` and `RequestHandler`.
    This implementation extends `~.routing.ReversibleRuleRouter` in a couple of ways:
        * it allows to use `RequestHandler` subclasses as `~.routing.Rule` target and
        * it allows to use a list/tuple of rules as `~.routing.Rule` target.
        ``process_rule`` implementation will substitute this list with an appropriate
        `_ApplicationRouter` instance.
    """

    def __init__(self, application: 'Application', rules: Optional[_RuleList]=None) -> None:
        assert isinstance(application, Application)
        self.application = application
        super().__init__(rules)

class Application(ReversibleRouter):
    """A collection of request handlers that make up a web application.

    Instances of this class are callable and can be passed directly to
    HTTPServer to serve the application::

        application = web.Application([
            (r"/", MainPageHandler),
        ])
        http_server = httpserver.HTTPServer(application)
        http_server.listen(8080)

    The constructor for this class takes in a list of `~.routing.Rule`
    objects or tuples of values corresponding to the arguments of
    `~.routing.Rule` constructor: ``(matcher, target, [target_kwargs], [name])``,
    the values in square brackets being optional. The default matcher is
    `~.routing.PathMatches`, so ``(regexp, target)`` tuples can also be used
    instead of ``(PathMatches(regexp), target)``.

    A common routing target is a `RequestHandler` subclass, but you can also
    use lists of rules as a target, which create a nested routing configuration::

        application = web.Application([
            (HostMatches("example.com"), [
                (r"/", MainPageHandler),
                (r"/feed", FeedHandler),
            ]),
        ])

    In addition to this you can use nested `~.routing.Router` instances,
    `~.httputil.HTTPMessageDelegate` subclasses and callables as routing targets
    (see `~.routing` module docs for more information).

    When we receive requests, we iterate over the list in order and
    instantiate an instance of the first request class whose regexp
    matches the request path. The request class can be specified as
    either a class object or a (fully-qualified) name.

    A dictionary may be passed as the third element (``target_kwargs``)
    of the tuple, which will be used as keyword arguments to the handler's
    constructor and `~RequestHandler.initialize` method. This pattern
    is used for the `StaticFileHandler` in this example (note that a
    `StaticFileHandler` can be installed automatically with the
    static_path setting described below)::

        application = web.Application([
            (r"/static/(.*)", web.StaticFileHandler, {"path": "/var/www"}),
        ])

    We support virtual hosts with the `add_handlers` method, which takes in
    a host regular expression as the first argument::

        application.add_handlers(r"www\\.myhost\\.com", [
            (r"/article/([0-9]+)", ArticleHandler),
        ])

    If there's no match for the current request's host, then ``default_host``
    parameter value is matched against host regular expressions.


    .. warning::

       Applications that do not use TLS may be vulnerable to :ref:`DNS
       rebinding <dnsrebinding>` attacks. This attack is especially
       relevant to applications that only listen on ``127.0.0.1`` or
       other private networks. Appropriate host patterns must be used
       (instead of the default of ``r'.*'``) to prevent this risk. The
       ``default_host`` argument must not be used in applications that
       may be vulnerable to DNS rebinding.

    You can serve static files by sending the ``static_path`` setting
    as a keyword argument. We will serve those files from the
    ``/static/`` URI (this is configurable with the
    ``static_url_prefix`` setting), and we will serve ``/favicon.ico``
    and ``/robots.txt`` from the same directory.  A custom subclass of
    `StaticFileHandler` can be specified with the
    ``static_handler_class`` setting.

    .. versionchanged:: 4.5
       Integration with the new `tornado.routing` module.

    """

    def __init__(self, handlers: Optional[_RuleList]=None, default_host: Optional[str]=None, transforms: Optional[List[Type['OutputTransform']]]=None, **settings: Any) -> None:
        if transforms is None:
            self.transforms = []
            if settings.get('compress_response') or settings.get('gzip'):
                self.transforms.append(GZipContentEncoding)
        else:
            self.transforms = transforms
        self.default_host = default_host
        self.settings = settings
        self.ui_modules = {'linkify': _linkify, 'xsrf_form_html': _xsrf_form_html, 'Template': TemplateModule}
        self.ui_methods = {}
        self._load_ui_modules(settings.get('ui_modules', {}))
        self._load_ui_methods(settings.get('ui_methods', {}))
        if self.settings.get('static_path'):
            path = self.settings['static_path']
            handlers = list(handlers or [])
            static_url_prefix = settings.get('static_url_prefix', '/static/')
            static_handler_class = settings.get('static_handler_class', StaticFileHandler)
            static_handler_args = settings.get('static_handler_args', {})
            static_handler_args['path'] = path
            for pattern in [re.escape(static_url_prefix) + '(.*)', '/(favicon\\.ico)', '/(robots\\.txt)']:
                handlers.insert(0, (pattern, static_handler_class, static_handler_args))
        if self.settings.get('debug'):
            self.settings.setdefault('autoreload', True)
            self.settings.setdefault('compiled_template_cache', False)
            self.settings.setdefault('static_hash_cache', False)
            self.settings.setdefault('serve_traceback', True)
        self.wildcard_router = _ApplicationRouter(self, handlers)
        self.default_router = _ApplicationRouter(self, [Rule(AnyMatches(), self.wildcard_router)])
        if self.settings.get('autoreload'):
            from tornado import autoreload
            autoreload.start()

    def listen(self, port: int, address: Optional[str]=None, *, family: socket.AddressFamily=socket.AF_UNSPEC, backlog: int=tornado.netutil._DEFAULT_BACKLOG, flags: Optional[int]=None, reuse_port: bool=False, **kwargs: Any) -> HTTPServer:
        """Starts an HTTP server for this application on the given port.

        This is a convenience alias for creating an `.HTTPServer` object and
        calling its listen method.  Keyword arguments not supported by
        `HTTPServer.listen <.TCPServer.listen>` are passed to the `.HTTPServer`
        constructor.  For advanced uses (e.g. multi-process mode), do not use
        this method; create an `.HTTPServer` and call its
        `.TCPServer.bind`/`.TCPServer.start` methods directly.

        Note that after calling this method you still need to call
        ``IOLoop.current().start()`` (or run within ``asyncio.run``) to start
        the server.

        Returns the `.HTTPServer` object.

        .. versionchanged:: 4.3
           Now returns the `.HTTPServer` object.

        .. versionchanged:: 6.2
           Added support for new keyword arguments in `.TCPServer.listen`,
           including ``reuse_port``.
        """
        pass

    def add_handlers(self, host_pattern: str, host_handlers: _RuleList) -> None:
        """Appends the given handlers to our handler list.

        Host patterns are processed sequentially in the order they were
        added. All matching patterns will be considered.
        """
        pass

    def __call__(self, request: httputil.HTTPServerRequest) -> Optional[Awaitable[None]]:
        dispatcher = self.find_handler(request)
        return dispatcher.execute()

    def get_handler_delegate(self, request: httputil.HTTPServerRequest, target_class: Type[RequestHandler], target_kwargs: Optional[Dict[str, Any]]=None, path_args: Optional[List[bytes]]=None, path_kwargs: Optional[Dict[str, bytes]]=None) -> '_HandlerDelegate':
        """Returns `~.httputil.HTTPMessageDelegate` that can serve a request
        for application and `RequestHandler` subclass.

        :arg httputil.HTTPServerRequest request: current HTTP request.
        :arg RequestHandler target_class: a `RequestHandler` class.
        :arg dict target_kwargs: keyword arguments for ``target_class`` constructor.
        :arg list path_args: positional arguments for ``target_class`` HTTP method that
            will be executed while handling a request (``get``, ``post`` or any other).
        :arg dict path_kwargs: keyword arguments for ``target_class`` HTTP method.
        """
        pass

    def reverse_url(self, name: str, *args: Any) -> str:
        """Returns a URL path for handler named ``name``

        The handler must be added to the application as a named `URLSpec`.

        Args will be substituted for capturing groups in the `URLSpec` regex.
        They will be converted to strings if necessary, encoded as utf8,
        and url-escaped.
        """
        pass

    def log_request(self, handler: RequestHandler) -> None:
        """Writes a completed HTTP request to the logs.

        By default writes to the python root logger.  To change
        this behavior either subclass Application and override this method,
        or pass a function in the application settings dictionary as
        ``log_function``.
        """
        pass

class _HandlerDelegate(httputil.HTTPMessageDelegate):

    def __init__(self, application: Application, request: httputil.HTTPServerRequest, handler_class: Type[RequestHandler], handler_kwargs: Optional[Dict[str, Any]], path_args: Optional[List[bytes]], path_kwargs: Optional[Dict[str, bytes]]) -> None:
        self.application = application
        self.connection = request.connection
        self.request = request
        self.handler_class = handler_class
        self.handler_kwargs = handler_kwargs or {}
        self.path_args = path_args or []
        self.path_kwargs = path_kwargs or {}
        self.chunks = []
        self.stream_request_body = _has_stream_request_body(self.handler_class)

class HTTPError(Exception):
    """An exception that will turn into an HTTP error response.

    Raising an `HTTPError` is a convenient alternative to calling
    `RequestHandler.send_error` since it automatically ends the
    current function.

    To customize the response sent with an `HTTPError`, override
    `RequestHandler.write_error`.

    :arg int status_code: HTTP status code.  Must be listed in
        `httplib.responses <http.client.responses>` unless the ``reason``
        keyword argument is given.
    :arg str log_message: Message to be written to the log for this error
        (will not be shown to the user unless the `Application` is in debug
        mode).  May contain ``%s``-style placeholders, which will be filled
        in with remaining positional parameters.
    :arg str reason: Keyword-only argument.  The HTTP "reason" phrase
        to pass in the status line along with ``status_code``.  Normally
        determined automatically from ``status_code``, but can be used
        to use a non-standard numeric code.
    """

    def __init__(self, status_code: int=500, log_message: Optional[str]=None, *args: Any, **kwargs: Any) -> None:
        self.status_code = status_code
        self.log_message = log_message
        self.args = args
        self.reason = kwargs.get('reason', None)
        if log_message and (not args):
            self.log_message = log_message.replace('%', '%%')

    def __str__(self) -> str:
        message = 'HTTP %d: %s' % (self.status_code, self.reason or httputil.responses.get(self.status_code, 'Unknown'))
        if self.log_message:
            return message + ' (' + self.log_message % self.args + ')'
        else:
            return message

class Finish(Exception):
    """An exception that ends the request without producing an error response.

    When `Finish` is raised in a `RequestHandler`, the request will
    end (calling `RequestHandler.finish` if it hasn't already been
    called), but the error-handling methods (including
    `RequestHandler.write_error`) will not be called.

    If `Finish()` was created with no arguments, the pending response
    will be sent as-is. If `Finish()` was given an argument, that
    argument will be passed to `RequestHandler.finish()`.

    This can be a more convenient way to implement custom error pages
    than overriding ``write_error`` (especially in library code)::

        if self.current_user is None:
            self.set_status(401)
            self.set_header('WWW-Authenticate', 'Basic realm="something"')
            raise Finish()

    .. versionchanged:: 4.3
       Arguments passed to ``Finish()`` will be passed on to
       `RequestHandler.finish`.
    """
    pass

class MissingArgumentError(HTTPError):
    """Exception raised by `RequestHandler.get_argument`.

    This is a subclass of `HTTPError`, so if it is uncaught a 400 response
    code will be used instead of 500 (and a stack trace will not be logged).

    .. versionadded:: 3.1
    """

    def __init__(self, arg_name: str) -> None:
        super().__init__(400, 'Missing argument %s' % arg_name)
        self.arg_name = arg_name

class ErrorHandler(RequestHandler):
    """Generates an error response with ``status_code`` for all requests."""

class RedirectHandler(RequestHandler):
    """Redirects the client to the given URL for all GET requests.

    You should provide the keyword argument ``url`` to the handler, e.g.::

        application = web.Application([
            (r"/oldpath", web.RedirectHandler, {"url": "/newpath"}),
        ])

    `RedirectHandler` supports regular expression substitutions. E.g., to
    swap the first and second parts of a path while preserving the remainder::

        application = web.Application([
            (r"/(.*?)/(.*?)/(.*)", web.RedirectHandler, {"url": "/{1}/{0}/{2}"}),
        ])

    The final URL is formatted with `str.format` and the substrings that match
    the capturing groups. In the above example, a request to "/a/b/c" would be
    formatted like::

        str.format("/{1}/{0}/{2}", "a", "b", "c")  # -> "/b/a/c"

    Use Python's :ref:`format string syntax <formatstrings>` to customize how
    values are substituted.

    .. versionchanged:: 4.5
       Added support for substitutions into the destination URL.

    .. versionchanged:: 5.0
       If any query arguments are present, they will be copied to the
       destination URL.
    """

class StaticFileHandler(RequestHandler):
    """A simple handler that can serve static content from a directory.

    A `StaticFileHandler` is configured automatically if you pass the
    ``static_path`` keyword argument to `Application`.  This handler
    can be customized with the ``static_url_prefix``, ``static_handler_class``,
    and ``static_handler_args`` settings.

    To map an additional path to this handler for a static data directory
    you would add a line to your application like::

        application = web.Application([
            (r"/content/(.*)", web.StaticFileHandler, {"path": "/var/www"}),
        ])

    The handler constructor requires a ``path`` argument, which specifies the
    local root directory of the content to be served.

    Note that a capture group in the regex is required to parse the value for
    the ``path`` argument to the get() method (different than the constructor
    argument above); see `URLSpec` for details.

    To serve a file like ``index.html`` automatically when a directory is
    requested, set ``static_handler_args=dict(default_filename="index.html")``
    in your application settings, or add ``default_filename`` as an initializer
    argument for your ``StaticFileHandler``.

    To maximize the effectiveness of browser caching, this class supports
    versioned urls (by default using the argument ``?v=``).  If a version
    is given, we instruct the browser to cache this file indefinitely.
    `make_static_url` (also available as `RequestHandler.static_url`) can
    be used to construct a versioned url.

    This handler is intended primarily for use in development and light-duty
    file serving; for heavy traffic it will be more efficient to use
    a dedicated static file server (such as nginx or Apache).  We support
    the HTTP ``Accept-Ranges`` mechanism to return partial content (because
    some browsers require this functionality to be present to seek in
    HTML5 audio or video).

    **Subclassing notes**

    This class is designed to be extensible by subclassing, but because
    of the way static urls are generated with class methods rather than
    instance methods, the inheritance patterns are somewhat unusual.
    Be sure to use the ``@classmethod`` decorator when overriding a
    class method.  Instance methods may use the attributes ``self.path``
    ``self.absolute_path``, and ``self.modified``.

    Subclasses should only override methods discussed in this section;
    overriding other methods is error-prone.  Overriding
    ``StaticFileHandler.get`` is particularly problematic due to the
    tight coupling with ``compute_etag`` and other methods.

    To change the way static urls are generated (e.g. to match the behavior
    of another server or CDN), override `make_static_url`, `parse_url_path`,
    `get_cache_time`, and/or `get_version`.

    To replace all interaction with the filesystem (e.g. to serve
    static content from a database), override `get_content`,
    `get_content_size`, `get_modified_time`, `get_absolute_path`, and
    `validate_absolute_path`.

    .. versionchanged:: 3.1
       Many of the methods for subclasses were added in Tornado 3.1.
    """
    CACHE_MAX_AGE = 86400 * 365 * 10
    _static_hashes = {}
    _lock = threading.Lock()

    def compute_etag(self) -> Optional[str]:
        """Sets the ``Etag`` header based on static url version.

        This allows efficient ``If-None-Match`` checks against cached
        versions, and sends the correct ``Etag`` for a partial response
        (i.e. the same ``Etag`` as the full file).

        .. versionadded:: 3.1
        """
        pass

    def set_headers(self) -> None:
        """Sets the content and caching headers on the response.

        .. versionadded:: 3.1
        """
        pass

    def should_return_304(self) -> bool:
        """Returns True if the headers indicate that we should return 304.

        .. versionadded:: 3.1
        """
        pass

    @classmethod
    def get_absolute_path(cls, root: str, path: str) -> str:
        """Returns the absolute location of ``path`` relative to ``root``.

        ``root`` is the path configured for this `StaticFileHandler`
        (in most cases the ``static_path`` `Application` setting).

        This class method may be overridden in subclasses.  By default
        it returns a filesystem path, but other strings may be used
        as long as they are unique and understood by the subclass's
        overridden `get_content`.

        .. versionadded:: 3.1
        """
        pass

    def validate_absolute_path(self, root: str, absolute_path: str) -> Optional[str]:
        """Validate and return the absolute path.

        ``root`` is the configured path for the `StaticFileHandler`,
        and ``path`` is the result of `get_absolute_path`

        This is an instance method called during request processing,
        so it may raise `HTTPError` or use methods like
        `RequestHandler.redirect` (return None after redirecting to
        halt further processing).  This is where 404 errors for missing files
        are generated.

        This method may modify the path before returning it, but note that
        any such modifications will not be understood by `make_static_url`.

        In instance methods, this method's result is available as
        ``self.absolute_path``.

        .. versionadded:: 3.1
        """
        pass

    @classmethod
    def get_content(cls, abspath: str, start: Optional[int]=None, end: Optional[int]=None) -> Generator[bytes, None, None]:
        """Retrieve the content of the requested resource which is located
        at the given absolute path.

        This class method may be overridden by subclasses.  Note that its
        signature is different from other overridable class methods
        (no ``settings`` argument); this is deliberate to ensure that
        ``abspath`` is able to stand on its own as a cache key.

        This method should either return a byte string or an iterator
        of byte strings.  The latter is preferred for large files
        as it helps reduce memory fragmentation.

        .. versionadded:: 3.1
        """
        pass

    @classmethod
    def get_content_version(cls, abspath: str) -> str:
        """Returns a version string for the resource at the given path.

        This class method may be overridden by subclasses.  The
        default implementation is a SHA-512 hash of the file's contents.

        .. versionadded:: 3.1
        """
        pass

    def get_content_size(self) -> int:
        """Retrieve the total size of the resource at the given path.

        This method may be overridden by subclasses.

        .. versionadded:: 3.1

        .. versionchanged:: 4.0
           This method is now always called, instead of only when
           partial results are requested.
        """
        pass

    def get_modified_time(self) -> Optional[datetime.datetime]:
        """Returns the time that ``self.absolute_path`` was last modified.

        May be overridden in subclasses.  Should return a `~datetime.datetime`
        object or None.

        .. versionadded:: 3.1

        .. versionchanged:: 6.4
           Now returns an aware datetime object instead of a naive one.
           Subclasses that override this method may return either kind.
        """
        pass

    def get_content_type(self) -> str:
        """Returns the ``Content-Type`` header to be used for this request.

        .. versionadded:: 3.1
        """
        pass

    def set_extra_headers(self, path: str) -> None:
        """For subclass to add extra headers to the response"""
        pass

    def get_cache_time(self, path: str, modified: Optional[datetime.datetime], mime_type: str) -> int:
        """Override to customize cache control behavior.

        Return a positive number of seconds to make the result
        cacheable for that amount of time or 0 to mark resource as
        cacheable for an unspecified amount of time (subject to
        browser heuristics).

        By default returns cache expiry of 10 years for resources requested
        with ``v`` argument.
        """
        pass

    @classmethod
    def make_static_url(cls, settings: Dict[str, Any], path: str, include_version: bool=True) -> str:
        """Constructs a versioned url for the given path.

        This method may be overridden in subclasses (but note that it
        is a class method rather than an instance method).  Subclasses
        are only required to implement the signature
        ``make_static_url(cls, settings, path)``; other keyword
        arguments may be passed through `~RequestHandler.static_url`
        but are not standard.

        ``settings`` is the `Application.settings` dictionary.  ``path``
        is the static path being requested.  The url returned should be
        relative to the current host.

        ``include_version`` determines whether the generated URL should
        include the query string containing the version hash of the
        file corresponding to the given ``path``.

        """
        pass

    def parse_url_path(self, url_path: str) -> str:
        """Converts a static URL path into a filesystem path.

        ``url_path`` is the path component of the URL with
        ``static_url_prefix`` removed.  The return value should be
        filesystem path relative to ``static_path``.

        This is the inverse of `make_static_url`.
        """
        pass

    @classmethod
    def get_version(cls, settings: Dict[str, Any], path: str) -> Optional[str]:
        """Generate the version string to be used in static URLs.

        ``settings`` is the `Application.settings` dictionary and ``path``
        is the relative location of the requested asset on the filesystem.
        The returned value should be a string, or ``None`` if no version
        could be determined.

        .. versionchanged:: 3.1
           This method was previously recommended for subclasses to override;
           `get_content_version` is now preferred as it allows the base
           class to handle caching of the result.
        """
        pass

class FallbackHandler(RequestHandler):
    """A `RequestHandler` that wraps another HTTP server callback.

    The fallback is a callable object that accepts an
    `~.httputil.HTTPServerRequest`, such as an `Application` or
    `tornado.wsgi.WSGIContainer`.  This is most useful to use both
    Tornado ``RequestHandlers`` and WSGI in the same server.  Typical
    usage::

        wsgi_app = tornado.wsgi.WSGIContainer(
            django.core.handlers.wsgi.WSGIHandler())
        application = tornado.web.Application([
            (r"/foo", FooHandler),
            (r".*", FallbackHandler, dict(fallback=wsgi_app)),
        ])
    """

class OutputTransform(object):
    """A transform modifies the result of an HTTP request (e.g., GZip encoding)

    Applications are not expected to create their own OutputTransforms
    or interact with them directly; the framework chooses which transforms
    (if any) to apply.
    """

    def __init__(self, request: httputil.HTTPServerRequest) -> None:
        pass

class GZipContentEncoding(OutputTransform):
    """Applies the gzip content encoding to the response.

    See http://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html#sec14.11

    .. versionchanged:: 4.0
        Now compresses all mime types beginning with ``text/``, instead
        of just a whitelist. (the whitelist is still used for certain
        non-text mime types).
    """
    CONTENT_TYPES = set(['application/javascript', 'application/x-javascript', 'application/xml', 'application/atom+xml', 'application/json', 'application/xhtml+xml', 'image/svg+xml'])
    GZIP_LEVEL = 6
    MIN_LENGTH = 1024

    def __init__(self, request: httputil.HTTPServerRequest) -> None:
        self._gzipping = 'gzip' in request.headers.get('Accept-Encoding', '')

def authenticated(method: Callable[..., Optional[Awaitable[None]]]) -> Callable[..., Optional[Awaitable[None]]]:
    """Decorate methods with this to require that the user be logged in.

    If the user is not logged in, they will be redirected to the configured
    `login url <RequestHandler.get_login_url>`.

    If you configure a login url with a query parameter, Tornado will
    assume you know what you're doing and use it as-is.  If not, it
    will add a `next` parameter so the login page knows where to send
    you once you're logged in.
    """
    pass

class UIModule(object):
    """A re-usable, modular UI unit on a page.

    UI modules often execute additional queries, and they can include
    additional CSS and JavaScript that will be included in the output
    page, which is automatically inserted on page render.

    Subclasses of UIModule must override the `render` method.
    """

    def __init__(self, handler: RequestHandler) -> None:
        self.handler = handler
        self.request = handler.request
        self.ui = handler.ui
        self.locale = handler.locale

    def render(self, *args: Any, **kwargs: Any) -> str:
        """Override in subclasses to return this module's output."""
        pass

    def embedded_javascript(self) -> Optional[str]:
        """Override to return a JavaScript string
        to be embedded in the page."""
        pass

    def javascript_files(self) -> Optional[Iterable[str]]:
        """Override to return a list of JavaScript files needed by this module.

        If the return values are relative paths, they will be passed to
        `RequestHandler.static_url`; otherwise they will be used as-is.
        """
        pass

    def embedded_css(self) -> Optional[str]:
        """Override to return a CSS string
        that will be embedded in the page."""
        pass

    def css_files(self) -> Optional[Iterable[str]]:
        """Override to returns a list of CSS files required by this module.

        If the return values are relative paths, they will be passed to
        `RequestHandler.static_url`; otherwise they will be used as-is.
        """
        pass

    def html_head(self) -> Optional[str]:
        """Override to return an HTML string that will be put in the <head/>
        element.
        """
        pass

    def html_body(self) -> Optional[str]:
        """Override to return an HTML string that will be put at the end of
        the <body/> element.
        """
        pass

    def render_string(self, path: str, **kwargs: Any) -> bytes:
        """Renders a template and returns it as a string."""
        pass

class _linkify(UIModule):
    pass

class _xsrf_form_html(UIModule):
    pass

class TemplateModule(UIModule):
    """UIModule that simply renders the given template.

    {% module Template("foo.html") %} is similar to {% include "foo.html" %},
    but the module version gets its own namespace (with kwargs passed to
    Template()) instead of inheriting the outer template's namespace.

    Templates rendered through this module also get access to UIModule's
    automatic JavaScript/CSS features.  Simply call set_resources
    inside the template and give it keyword arguments corresponding to
    the methods on UIModule: {{ set_resources(js_files=static_url("my.js")) }}
    Note that these resources are output once per template file, not once
    per instantiation of the template, so they must not depend on
    any arguments to the template.
    """

    def __init__(self, handler: RequestHandler) -> None:
        super().__init__(handler)
        self._resource_list = []
        self._resource_dict = {}

class _UIModuleNamespace(object):
    """Lazy namespace which creates UIModule proxies bound to a handler."""

    def __init__(self, handler: RequestHandler, ui_modules: Dict[str, Type[UIModule]]) -> None:
        self.handler = handler
        self.ui_modules = ui_modules

    def __getitem__(self, key: str) -> Callable[..., str]:
        return self.handler._ui_module(key, self.ui_modules[key])

    def __getattr__(self, key: str) -> Callable[..., str]:
        try:
            return self[key]
        except KeyError as e:
            raise AttributeError(str(e))
_signed_value_version_re = re.compile(b'^([1-9][0-9]*)\\|(.*)$')