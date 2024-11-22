"""Utilities for working with ``Future`` objects.

Tornado previously provided its own ``Future`` class, but now uses
`asyncio.Future`. This module contains utility functions for working
with `asyncio.Future` in a way that is backwards-compatible with
Tornado's old ``Future`` implementation.

While this module is an important part of Tornado's internal
implementation, applications rarely need to interact with it
directly.

"""
import asyncio
from concurrent import futures
import functools
import sys
import types
from tornado.log import app_log
import typing
from typing import Any, Callable, Optional, Tuple, Union
_T = typing.TypeVar('_T')

class ReturnValueIgnoredError(Exception):
    pass
Future = asyncio.Future
FUTURES = (futures.Future, Future)

class DummyExecutor(futures.Executor):
    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            future.set_exception(e)
        else:
            future.set_result(result)
        return future

    def shutdown(self, wait=True):
        pass

dummy_executor = DummyExecutor()

def run_on_executor(*args: Any, **kwargs: Any) -> Callable:
    """Decorator to run a synchronous method asynchronously on an executor.

    Returns a future.

    The executor to be used is determined by the ``executor``
    attributes of ``self``. To use a different attribute name, pass a
    keyword argument to the decorator::

        @run_on_executor(executor='_thread_pool')
        def foo(self):
            pass

    This decorator should not be confused with the similarly-named
    `.IOLoop.run_in_executor`. In general, using ``run_in_executor``
    when *calling* a blocking method is recommended instead of using
    this decorator when *defining* a method. If compatibility with older
    versions of Tornado is required, consider defining an executor
    and using ``executor.submit()`` at the call site.

    .. versionchanged:: 4.2
       Added keyword arguments to use alternative attributes.

    .. versionchanged:: 5.0
       Always uses the current IOLoop instead of ``self.io_loop``.

    .. versionchanged:: 5.1
       Returns a `.Future` compatible with ``await`` instead of a
       `concurrent.futures.Future`.

    .. deprecated:: 5.1

       The ``callback`` argument is deprecated and will be removed in
       6.0. The decorator itself is discouraged in new code but will
       not be removed in 6.0.

    .. versionchanged:: 6.0

       The ``callback`` argument was removed.
    """
    def run_on_executor_decorator(fn: Callable) -> Callable:
        executor = kwargs.get("executor", "executor")

        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            executor_obj = getattr(self, executor, dummy_executor)
            future = executor_obj.submit(fn, self, *args, **kwargs)
            return await asyncio.wrap_future(future)

        return wrapper

    if len(args) == 1 and len(kwargs) == 0 and callable(args[0]):
        return run_on_executor_decorator(args[0])
    return run_on_executor_decorator
_NO_RESULT = object()

def is_future(x: Any) -> bool:
    """Returns True if ``x`` is a `.Future`.

    This should work both for Tornado/asyncio `Future` objects and
    `concurrent.futures.Future`.
    """
    return isinstance(x, FUTURES)

def chain_future(a: 'Future[_T]', b: 'Future[_T]') -> None:
    """Chain two futures together so that when one completes, so does the other.

    The result (success or failure) of ``a`` will be copied to ``b``, unless
    ``b`` has already been completed or cancelled by the time ``a`` finishes.

    .. versionchanged:: 5.0

       Now accepts both Tornado/asyncio `Future` objects and
       `concurrent.futures.Future`.

    """
    def copy(future: 'Future[_T]') -> None:
        if b.done():
            return
        if future.cancelled():
            b.cancel()
        else:
            exc = future.exception()
            if exc is not None:
                b.set_exception(exc)
            else:
                b.set_result(future.result())

def future_set_result_unless_cancelled(future: 'Union[futures.Future[_T], Future[_T]]', value: _T) -> None:
    """Set the given ``value`` as the `Future`'s result, if not cancelled.

    Avoids ``asyncio.InvalidStateError`` when calling ``set_result()`` on
    a cancelled `asyncio.Future`.

    .. versionadded:: 5.0
    """
    if not future.cancelled():
        future.set_result(value)

def future_set_exception_unless_cancelled(future: 'Union[futures.Future[_T], Future[_T]]', exc: BaseException) -> None:
    """Set the given ``exc`` as the `Future`'s exception.

    If the Future is already canceled, logs the exception instead. If
    this logging is not desired, the caller should explicitly check
    the state of the Future and call ``Future.set_exception`` instead of
    this wrapper.

    Avoids ``asyncio.InvalidStateError`` when calling ``set_exception()`` on
    a cancelled `asyncio.Future`.

    .. versionadded:: 6.0

    """
    if future.cancelled():
        app_log.error("Exception after Future was cancelled", exc_info=exc)
    else:
        future.set_exception(exc)

def future_set_exc_info(future: 'Union[futures.Future[_T], Future[_T]]', exc_info: Tuple[Optional[type], Optional[BaseException], Optional[types.TracebackType]]) -> None:
    """Set the given ``exc_info`` as the `Future`'s exception.

    Understands both `asyncio.Future` and the extensions in older
    versions of Tornado to enable better tracebacks on Python 2.

    .. versionadded:: 5.0

    .. versionchanged:: 6.0

       If the future is already cancelled, this function is a no-op.
       (previously ``asyncio.InvalidStateError`` would be raised)

    """
    if exc_info[1] is not None:
        future_set_exception_unless_cancelled(future, exc_info[1])

def future_add_done_callback(future: 'Union[futures.Future[_T], Future[_T]]', callback: Callable[..., None]) -> None:
    """Arrange to call ``callback`` when ``future`` is complete.

    ``callback`` is invoked with one argument, the ``future``.

    If ``future`` is already done, ``callback`` is invoked immediately.
    This may differ from the behavior of ``Future.add_done_callback``,
    which makes no such guarantee.

    .. versionadded:: 5.0
    """
    if future.done():
        callback(future)
    else:
        future.add_done_callback(callback)