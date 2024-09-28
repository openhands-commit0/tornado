"""Utilities for working with multiple processes, including both forking
the server into multiple processes and managing subprocesses.
"""
import asyncio
import os
import multiprocessing
import signal
import subprocess
import sys
import time
from binascii import hexlify
from tornado.concurrent import Future, future_set_result_unless_cancelled, future_set_exception_unless_cancelled
from tornado import ioloop
from tornado.iostream import PipeIOStream
from tornado.log import gen_log
import typing
from typing import Optional, Any, Callable
if typing.TYPE_CHECKING:
    from typing import List
CalledProcessError = subprocess.CalledProcessError

def cpu_count() -> int:
    """Returns the number of processors on this machine."""
    pass
_task_id = None

def fork_processes(num_processes: Optional[int], max_restarts: Optional[int]=None) -> int:
    """Starts multiple worker processes.

    If ``num_processes`` is None or <= 0, we detect the number of cores
    available on this machine and fork that number of child
    processes. If ``num_processes`` is given and > 0, we fork that
    specific number of sub-processes.

    Since we use processes and not threads, there is no shared memory
    between any server code.

    Note that multiple processes are not compatible with the autoreload
    module (or the ``autoreload=True`` option to `tornado.web.Application`
    which defaults to True when ``debug=True``).
    When using multiple processes, no IOLoops can be created or
    referenced until after the call to ``fork_processes``.

    In each child process, ``fork_processes`` returns its *task id*, a
    number between 0 and ``num_processes``.  Processes that exit
    abnormally (due to a signal or non-zero exit status) are restarted
    with the same id (up to ``max_restarts`` times).  In the parent
    process, ``fork_processes`` calls ``sys.exit(0)`` after all child
    processes have exited normally.

    max_restarts defaults to 100.

    Availability: Unix
    """
    pass

def task_id() -> Optional[int]:
    """Returns the current task id, if any.

    Returns None if this process was not created by `fork_processes`.
    """
    pass

class Subprocess(object):
    """Wraps ``subprocess.Popen`` with IOStream support.

    The constructor is the same as ``subprocess.Popen`` with the following
    additions:

    * ``stdin``, ``stdout``, and ``stderr`` may have the value
      ``tornado.process.Subprocess.STREAM``, which will make the corresponding
      attribute of the resulting Subprocess a `.PipeIOStream`. If this option
      is used, the caller is responsible for closing the streams when done
      with them.

    The ``Subprocess.STREAM`` option and the ``set_exit_callback`` and
    ``wait_for_exit`` methods do not work on Windows. There is
    therefore no reason to use this class instead of
    ``subprocess.Popen`` on that platform.

    .. versionchanged:: 5.0
       The ``io_loop`` argument (deprecated since version 4.1) has been removed.

    """
    STREAM = object()
    _initialized = False
    _waiting = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.io_loop = ioloop.IOLoop.current()
        pipe_fds = []
        to_close = []
        if kwargs.get('stdin') is Subprocess.STREAM:
            in_r, in_w = os.pipe()
            kwargs['stdin'] = in_r
            pipe_fds.extend((in_r, in_w))
            to_close.append(in_r)
            self.stdin = PipeIOStream(in_w)
        if kwargs.get('stdout') is Subprocess.STREAM:
            out_r, out_w = os.pipe()
            kwargs['stdout'] = out_w
            pipe_fds.extend((out_r, out_w))
            to_close.append(out_w)
            self.stdout = PipeIOStream(out_r)
        if kwargs.get('stderr') is Subprocess.STREAM:
            err_r, err_w = os.pipe()
            kwargs['stderr'] = err_w
            pipe_fds.extend((err_r, err_w))
            to_close.append(err_w)
            self.stderr = PipeIOStream(err_r)
        try:
            self.proc = subprocess.Popen(*args, **kwargs)
        except:
            for fd in pipe_fds:
                os.close(fd)
            raise
        for fd in to_close:
            os.close(fd)
        self.pid = self.proc.pid
        for attr in ['stdin', 'stdout', 'stderr']:
            if not hasattr(self, attr):
                setattr(self, attr, getattr(self.proc, attr))
        self._exit_callback = None
        self.returncode = None

    def set_exit_callback(self, callback: Callable[[int], None]) -> None:
        """Runs ``callback`` when this process exits.

        The callback takes one argument, the return code of the process.

        This method uses a ``SIGCHLD`` handler, which is a global setting
        and may conflict if you have other libraries trying to handle the
        same signal.  If you are using more than one ``IOLoop`` it may
        be necessary to call `Subprocess.initialize` first to designate
        one ``IOLoop`` to run the signal handlers.

        In many cases a close callback on the stdout or stderr streams
        can be used as an alternative to an exit callback if the
        signal handler is causing a problem.

        Availability: Unix
        """
        pass

    def wait_for_exit(self, raise_error: bool=True) -> 'Future[int]':
        """Returns a `.Future` which resolves when the process exits.

        Usage::

            ret = yield proc.wait_for_exit()

        This is a coroutine-friendly alternative to `set_exit_callback`
        (and a replacement for the blocking `subprocess.Popen.wait`).

        By default, raises `subprocess.CalledProcessError` if the process
        has a non-zero exit status. Use ``wait_for_exit(raise_error=False)``
        to suppress this behavior and return the exit status without raising.

        .. versionadded:: 4.2

        Availability: Unix
        """
        pass

    @classmethod
    def initialize(cls) -> None:
        """Initializes the ``SIGCHLD`` handler.

        The signal handler is run on an `.IOLoop` to avoid locking issues.
        Note that the `.IOLoop` used for signal handling need not be the
        same one used by individual Subprocess objects (as long as the
        ``IOLoops`` are each running in separate threads).

        .. versionchanged:: 5.0
           The ``io_loop`` argument (deprecated since version 4.1) has been
           removed.

        Availability: Unix
        """
        pass

    @classmethod
    def uninitialize(cls) -> None:
        """Removes the ``SIGCHLD`` handler."""
        pass