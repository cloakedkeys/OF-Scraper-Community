import subprocess
import sys
import logging
import asyncio
import ofscraper.utils.of_env.of_env as of_env


def _format_subprocess_output(text_output):
    """
    Sanitizes subprocess output. Crucial for FFmpeg, which uses \r to overwrite
    terminal lines, causing massive garbled strings in standard log files.
    """
    if not text_output:
        return ""
    if isinstance(text_output, bytes):
        text_output = text_output.decode("utf-8", errors="ignore")
    # Replace carriage returns with proper newlines so log files can read it
    return text_output.replace("\r", "\n").strip()


def run(
    *args,
    log=None,
    stdout=None,
    stderr=None,
    capture_output=True,
    level=None,
    name=None,
    timeout=600,
    **kwargs,
):
    log = log or logging.getLogger("shared")
    cmd_args = list(args[0])

    if (
        cmd_args
        and isinstance(cmd_args[0], str)
        and cmd_args[0].lower().endswith(".py")
    ):
        cmd_args.insert(0, sys.executable)

    name = name or " ".join(cmd_args)

    level = (
        int(level)
        if level is not None
        else int(of_env.getattr("LOG_SUBPROCESS_LEVEL") or 0)
    )

    final_args = (cmd_args,) + args[1:]

    # If capture_output is True, subprocess.run handles the pipes automatically.
    if capture_output:
        kwargs["capture_output"] = True
    else:
        # If False, we respect whatever custom pipes were passed (or let them leak to terminal if None)
        kwargs["stdout"] = stdout
        kwargs["stderr"] = stderr
    # -----------------------

    try:
        t = subprocess.run(
            *final_args,
            timeout=timeout,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"Subprocess {name} timed out after {timeout} seconds.")

    if level <= 0:
        return t

    if getattr(t, "stdout", None):
        log.log(level, f"{name} stdout:\n{_format_subprocess_output(t.stdout)}")
    if getattr(t, "stderr", None):
        log.log(level, f"{name} stderr:\n{_format_subprocess_output(t.stderr)}")

    return t


KILL_WAIT = 2  # seconds to wait for a killed process to be reaped
OUTPUT_GRACE = 2  # seconds to wait for the rest of the output after exit
EXIT_POLL = 0.1  # seconds between checks for the script's own exit


async def _kill(process):
    """Kill the process if it still runs, and close its pipes without waiting
    on its children.

    A program the process started in the background can hold its stdout and
    stderr open. Python 3.11's process.wait() also waits for those pipes to
    close, so close them ourselves and bound the wait (#389). Close them even
    when the process has already exited (#391).
    """
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    process._transport.close()
    try:
        await asyncio.wait_for(process.wait(), timeout=KILL_WAIT)
    except asyncio.TimeoutError:
        pass


async def _wait_for_exit(process, wait_task):
    """Return once the process itself has exited.

    On Python 3.11, process.wait() (wait_task) also waits for the stdout and
    stderr pipes to close, which a program the process started in the
    background can hold open (#391). process.returncode is set as soon as the
    process exits, on every Python version and on uvloop, so check it too.
    """
    while process.returncode is None and not wait_task.done():
        await asyncio.wait({wait_task}, timeout=EXIT_POLL)


async def async_run(
    *args,
    log=None,
    level=None,
    name=None,
    timeout=600,
    capture_output=True,
    input=None,
    text=False,
    check=False,
    **kwargs,
):
    """
    Asynchronously executes a command without blocking the main event loop.
    Reads streams natively to avoid 'communicate()' closing stdin prematurely,
    ensuring stdout is ALWAYS perfectly captured.
    """
    log = log or logging.getLogger("shared")
    cmd_args = list(args[0])

    if (
        cmd_args
        and isinstance(cmd_args[0], str)
        and cmd_args[0].lower().endswith(".py")
    ):
        cmd_args.insert(0, sys.executable)

    name = name or " ".join(cmd_args)

    if level is None:
        level = int(of_env.getattr("LOG_SUBPROCESS_LEVEL", "10"))

    if capture_output:
        kwargs["stdout"] = asyncio.subprocess.PIPE
        kwargs["stderr"] = asyncio.subprocess.PIPE

    if input is not None:
        kwargs["stdin"] = asyncio.subprocess.PIPE
        if text and isinstance(input, str):
            input = input.encode("utf-8")

    process = await asyncio.create_subprocess_exec(*cmd_args, **kwargs)

    # Read into buffers, so output read so far is kept if reading stops early.
    out_buf = bytearray()
    err_buf = bytearray()

    async def _read_stream(stream, buf):
        if stream is None:
            return
        while chunk := await stream.read(65536):
            buf += chunk

    async def _feed_stdin(stream, data):
        if stream is None or data is None:
            return
        try:
            stream.write(data)
            await stream.drain()
        except Exception:
            pass  # Silently swallow Broken Pipe errors if script ignores stdin
        finally:
            try:
                stream.close()
            except Exception:
                pass

    # Keep a reference to every task: asyncio holds tasks only weakly, so an
    # unreferenced task can be garbage-collected before it finishes.
    tasks = []
    try:
        # 1. Start feeding stdin in the background
        stdin_task = None
        if input is not None:
            stdin_task = asyncio.create_task(_feed_stdin(process.stdin, input))
            tasks.append(stdin_task)

        # 2. Read stdout and stderr concurrently
        out_task = asyncio.create_task(_read_stream(process.stdout, out_buf))
        err_task = asyncio.create_task(_read_stream(process.stderr, err_buf))
        tasks.extend([out_task, err_task])

        # 3. Wait for the process itself to exit, bounded by timeout
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        wait_task = asyncio.create_task(process.wait())
        tasks.append(wait_task)
        await asyncio.wait_for(_wait_for_exit(process, wait_task), timeout=timeout)

        # 4. Gather the rest of the output. A program the script started in
        # the background can keep its pipes open after it exits (#391): wait
        # a short while, then use the script's result and stop reading.
        io_tasks = [out_task, err_task] + ([stdin_task] if stdin_task else [])
        grace = max(0, min(OUTPUT_GRACE, deadline - loop.time()))
        _, pending = await asyncio.wait(io_tasks, timeout=grace)
        if pending:
            log.warning(
                f"Subprocess {name} exited, but a program it started is still "
                "running and holding its output open; its output may be cut off."
            )
            await _kill(process)
            # Closing the pipes ends the reads; take what they had buffered.
            await asyncio.wait(io_tasks, timeout=KILL_WAIT)
        for task in io_tasks:
            if task.done():
                task.result()  # raise a read error, as before

    except asyncio.TimeoutError:
        await _kill(process)
        raise TimeoutError(
            f"Subprocess {name} timed out after {timeout} seconds and was forcefully killed."
        )
    except BaseException:
        # Cancelled (or failed): don't leave the script running, and close
        # its pipes even if it has exited (#391).
        await _kill(process)
        raise
    finally:
        # Leave nothing running: cancel unfinished tasks and wait for them.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    stdout = bytes(out_buf)
    stderr = bytes(err_buf)
    if text:
        if stdout is not None:
            stdout = stdout.decode("utf-8", errors="ignore")
        if stderr is not None:
            stderr = stderr.decode("utf-8", errors="ignore")

    if check and process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode, cmd_args, output=stdout, stderr=stderr
        )

    class ProcessResult:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    t = ProcessResult(process.returncode, stdout, stderr)

    if level == 0:
        return t

    if t.stdout:
        log.log(level, f"{name} stdout:\n{_format_subprocess_output(t.stdout)}")
    if t.stderr:
        log.log(level, f"{name} stderr:\n{_format_subprocess_output(t.stderr)}")

    return t
