"""WSGI support for the Tornado web framework.

WSGI is the Python standard for web servers, and allows for interoperability
between Tornado and other Python web frameworks and servers.

This module provides WSGI support via the `WSGIContainer` class, which
makes it possible to run applications using other WSGI frameworks on
the Tornado HTTP server. The reverse is not supported; the Tornado
`.Application` and `.RequestHandler` classes are designed for use with
the Tornado `.HTTPServer` and cannot be used in a generic WSGI
container.

"""
import concurrent.futures
from io import BytesIO
import tornado
import sys
from tornado.concurrent import dummy_executor
from tornado import escape
from tornado import httputil
from tornado.ioloop import IOLoop
from tornado.log import access_log
from typing import List, Tuple, Optional, Callable, Any, Dict, Text
from types import TracebackType
import typing
if typing.TYPE_CHECKING:
    from typing import Type
    from _typeshed.wsgi import WSGIApplication as WSGIAppType

class WSGIContainer(object):
    """Makes a WSGI-compatible application runnable on Tornado's HTTP server.

    .. warning::

       WSGI is a *synchronous* interface, while Tornado's concurrency model
       is based on single-threaded *asynchronous* execution.  Many of Tornado's
       distinguishing features are not available in WSGI mode, including efficient
       long-polling and websockets. The primary purpose of `WSGIContainer` is
       to support both WSGI applications and native Tornado ``RequestHandlers`` in
       a single process. WSGI-only applications are likely to be better off
       with a dedicated WSGI server such as ``gunicorn`` or ``uwsgi``.

    Wrap a WSGI application in a `WSGIContainer` to make it implement the Tornado
    `.HTTPServer` ``request_callback`` interface.  The `WSGIContainer` object can
    then be passed to classes from the `tornado.routing` module,
    `tornado.web.FallbackHandler`, or to `.HTTPServer` directly.

    This class is intended to let other frameworks (Django, Flask, etc)
    run on the Tornado HTTP server and I/O loop.

    Realistic usage will be more complicated, but the simplest possible example uses a
    hand-written WSGI application with `.HTTPServer`::

        def simple_app(environ, start_response):
            status = "200 OK"
            response_headers = [("Content-type", "text/plain")]
            start_response(status, response_headers)
            return [b"Hello world!\\n"]

        async def main():
            container = tornado.wsgi.WSGIContainer(simple_app)
            http_server = tornado.httpserver.HTTPServer(container)
            http_server.listen(8888)
            await asyncio.Event().wait()

        asyncio.run(main())

    The recommended pattern is to use the `tornado.routing` module to set up routing
    rules between your WSGI application and, typically, a `tornado.web.Application`.
    Alternatively, `tornado.web.Application` can be used as the top-level router
    and `tornado.web.FallbackHandler` can embed a `WSGIContainer` within it.

    If the ``executor`` argument is provided, the WSGI application will be executed
    on that executor. This must be an instance of `concurrent.futures.Executor`,
    typically a ``ThreadPoolExecutor`` (``ProcessPoolExecutor`` is not supported).
    If no ``executor`` is given, the application will run on the event loop thread in
    Tornado 6.3; this will change to use an internal thread pool by default in
    Tornado 7.0.

    .. warning::
       By default, the WSGI application is executed on the event loop's thread. This
       limits the server to one request at a time (per process), making it less scalable
       than most other WSGI servers. It is therefore highly recommended that you pass
       a ``ThreadPoolExecutor`` when constructing the `WSGIContainer`, after verifying
       that your application is thread-safe. The default will change to use a
       ``ThreadPoolExecutor`` in Tornado 7.0.

    .. versionadded:: 6.3
       The ``executor`` parameter.

    .. deprecated:: 6.3
       The default behavior of running the WSGI application on the event loop thread
       is deprecated and will change in Tornado 7.0 to use a thread pool by default.
    """

    def __init__(self, wsgi_application: 'WSGIAppType', executor: Optional[concurrent.futures.Executor]=None) -> None:
        self.wsgi_application = wsgi_application
        self.executor = dummy_executor if executor is None else executor

    def __call__(self, request: httputil.HTTPServerRequest) -> None:
        IOLoop.current().spawn_callback(self.handle_request, request)

    def environ(self, request: httputil.HTTPServerRequest) -> Dict[Text, Any]:
        """Converts a `tornado.httputil.HTTPServerRequest` to a WSGI environment.

        .. versionchanged:: 6.3
           No longer a static method.
        """
        pass
HTTPRequest = httputil.HTTPServerRequest