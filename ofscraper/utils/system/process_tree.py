"""Keep track of every program a command starts, so they can all be stopped.

A command (a user script, ffmpeg) can start programs of its own. When the
command exits or is killed, those programs are left running unless something
stops them. ProcessTree puts the command and everything it starts into one
group the operating system tracks:

- POSIX: a new session, so the command leads its own process group and its
  children join it. A program that starts its own session (a daemon) leaves
  the group and isn't stopped.
- Windows: a Job Object. Children join the job and can't leave it, and the
  job is closed with KILL_ON_JOB_CLOSE, so its programs are also killed if
  ofscraper exits without cleaning up. The command is added to the job right
  after it starts; a program it starts before that isn't tracked, but a
  script can't start one that quickly.
"""

import asyncio
import os
import signal
import sys

import psutil

STOP_WAIT = 2  # seconds a program gets to exit after it is asked to stop
STOP_POLL = 0.05  # seconds between checks that the programs have exited


class ProcessTree:
    """The programs a command starts. Use as a context manager around the
    command's lifetime: pass popen_kwargs() when starting it, attach() it
    once started, then stop() what is left once it has exited."""

    def __init__(self):
        self._pgid = None
        self._job = None

    def popen_kwargs(self):
        if sys.platform == "win32":
            return {}
        return {"start_new_session": True}

    def attach(self, pid):
        if sys.platform == "win32":
            self._job = _windows_job(pid)
        else:
            # With start_new_session the command leads its own process group.
            self._pgid = pid

    def running(self):
        """Whether any program in the tree is still running."""
        if self._job is not None:
            return _windows_job_active(self._job)
        if self._pgid is not None:
            # The signal check is cheap and settles the usual case, an empty
            # group. A program that has exited but hasn't been reaped (a
            # zombie) stays in the group: in Docker, ofscraper is PID 1 and
            # inherits orphans it never reaps. So look for a live member.
            return _signal_group(self._pgid, 0) and _live_member(self._pgid)
        return False

    async def stop(self):
        """Stop every program still running in the tree.

        On POSIX they get SIGTERM and STOP_WAIT seconds to exit, then SIGKILL.
        Windows has no equivalent of SIGTERM for these programs, so they are
        killed at once.
        """
        if self._job is not None:
            _windows_job_kill(self._job)
            return
        if self._pgid is None or not _signal_group(self._pgid, signal.SIGTERM):
            return
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + STOP_WAIT
            while self.running() and loop.time() < deadline:
                await asyncio.sleep(STOP_POLL)
        finally:
            # Also on a cancel during the wait: don't leave anything running.
            _signal_group(self._pgid, signal.SIGKILL)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._job is not None:
            _windows_close(self._job)
            self._job = None


def _signal_group(pgid, sig):
    """Send sig to the process group; False if the group no longer exists.

    The command's pid can't be reused while its group still has members, so
    this can't reach an unrelated group.
    """
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A member changed its user; it can't be signalled from here.
        return True
    return True


def _live_member(pgid):
    """Whether a process in the group is running (not a zombie)."""
    for process in psutil.process_iter(["status"]):
        if process.info["status"] == psutil.STATUS_ZOMBIE:
            continue
        try:
            if os.getpgid(process.pid) == pgid:
                return True
        except OSError:
            continue  # it exited meanwhile, or can't be inspected
    return False


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (field, ctypes.c_uint64)
            for field in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _Accounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPDWORD,
    ]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def _check(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    def _windows_job(pid):
        job = _check(_kernel32.CreateJobObjectW(None, None))
        try:
            info = _ExtendedLimit()
            info.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            _check(
                _kernel32.SetInformationJobObject(
                    job,
                    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(info),
                    ctypes.sizeof(info),
                )
            )
            process = _check(
                _kernel32.OpenProcess(
                    _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid
                )
            )
            try:
                _check(_kernel32.AssignProcessToJobObject(job, process))
            finally:
                _kernel32.CloseHandle(process)
        except BaseException:
            _kernel32.CloseHandle(job)
            raise
        return job

    def _windows_job_active(job):
        info = _Accounting()
        _check(
            _kernel32.QueryInformationJobObject(
                job,
                _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
                None,
            )
        )
        return info.ActiveProcesses > 0

    def _windows_job_kill(job):
        _check(_kernel32.TerminateJobObject(job, 1))

    def _windows_close(job):
        _kernel32.CloseHandle(job)
