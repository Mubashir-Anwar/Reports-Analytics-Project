"""The child program that runs one Analytics Python script (started by pyscript.py, never imported by the service).

    python -I -X utf8 pysandbox.py <work folder>

The work folder holds job.json, script.py and the input datasets (pandas pickles written by the service). The script
gets `df` (the first input), `dfs` (every input by dataset name and ID), `pd` (pandas) and `np` (numpy); it may import
scikit-learn, scipy and the standard library. What it leaves in `result` (or `df`) is the output: a pandas DataFrame
(a Series, a list of records or a dict of columns are turned into one). The output is written as out.ndjson and the
report as result.json; the service reads both, never a pickle the script made.

Guard rails, set up before the script runs (an audit hook: Python tells it about every sensitive operation):
    files        read only Python's own folders and the work folder; write only in the work folder
    network      every socket operation is refused
    programs     subprocess, os.system, exec and spawn are refused
    native code  ctypes, and loading a compiled module from the work folder, are refused
    memory       ANALYTICS_PYTHON_MEMORY_MB (a Windows job object, or RLIMIT_AS elsewhere)
The service also starts the process with an empty environment (no secrets), in the work folder, with a time limit.
This guards against mistakes and curious scripts. It is not a security boundary like a container: give the Python
right to people you trust with the data, or run the service in Docker (see deploy/).
"""
from __future__ import annotations

import io
import json
import os
import sys
import traceback

MAX_OUTPUT = 64_000


def limit_memory(megabytes: int) -> str:
    """Cap this process's memory. Returns "" or why it could not be capped."""
    if megabytes <= 0:
        return ""
    limit = megabytes * 1024 * 1024
    try:
        if os.name == "nt":
            import ctypes  # noqa: PLC0415 - before the guard; the script itself may not use ctypes
            from ctypes import wintypes  # noqa: PLC0415

            class BasicLimits(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

            class IoCounters(ctypes.Structure):
                _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class ExtendedLimits(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            job = kernel.CreateJobObjectW(None, None)
            info = ExtendedLimits()
            info.BasicLimitInformation.LimitFlags = 0x100 | 0x2000 | 0x8   # process memory, kill on close, one process
            info.BasicLimitInformation.ActiveProcessLimit = 1
            info.ProcessMemoryLimit = limit
            if not job or not kernel.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
                return f"no memory limit (Windows error {ctypes.get_last_error()})"
            if not kernel.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(kernel.GetCurrentProcess())):
                return f"no memory limit (Windows error {ctypes.get_last_error()})"
            return ""
        import resource  # noqa: PLC0415
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        return ""
    except Exception as exc:  # noqa: BLE001 - the time limit still applies
        return f"no memory limit ({type(exc).__name__})"


# ctypes only for these library files (scikit-learn sets the number of BLAS threads through threadpoolctl)
CTYPES_USERS = ("threadpoolctl.py",)
BLOCKED_EVENTS = ("socket.", "subprocess.", "_posixsubprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork",
                  "os.forkpty", "os.startfile", "os.kill", "os.killpg", "os.putenv", "os.unsetenv", "os.chdir", "ctypes.",
                  "_winapi.", "winreg.", "msvcrt.", "urllib.Request", "http.client.", "ftplib.", "smtplib.", "poplib.", "imaplib.",
                  "nntplib.", "telnetlib.", "webbrowser.", "sqlite3.", "mmap.", "sys.remote_exec", "os.link", "os.symlink",
                  "pdb.", "cpython.run_", "resource.setrlimit", "resource.prlimit", "os.setuid", "os.setgid")
BLOCKED_MODULES = {"socket", "_socket", "ssl", "_ssl", "ctypes", "_ctypes", "subprocess", "_posixsubprocess", "multiprocessing",
                   "_multiprocessing", "_winapi", "winreg", "_overlapped", "duckdb", "_duckdb", "sqlite3", "_sqlite3", "mmap",
                   "pymongo", "bson", "psycopg", "pyodbc", "pymssql", "pymysql", "oracledb", "sqlalchemy", "requests", "urllib3",
                   "paramiko", "backend", "_pyrepl", "pty", "telnetlib", "ftplib", "smtplib", "webbrowser", "asyncio"}
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
WRITE_EVENTS = ("os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.truncate", "os.utime", "os.chmod", "os.chown",
                "shutil.copyfile", "shutil.copymode", "shutil.copystat", "shutil.copytree", "shutil.move", "shutil.rmtree",
                "shutil.make_archive", "shutil.unpack_archive", "tempfile.mkstemp", "tempfile.mkdtemp")
# Linux system files scikit-learn's helpers read (CPU count, loaded libraries): read-only, nothing secret in them
SYSTEM_FILES = ("/sys/fs/cgroup/cpu.max", "/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "/sys/fs/cgroup/cpu/cpu.cfs_period_us",
                "/proc/self/maps", "/proc/self/status", "/proc/self/cgroup", "/proc/self/mountinfo", "/proc/cpuinfo",
                "/proc/meminfo", "/proc/stat", "/sys/devices/system/cpu/online", "/sys/devices/system/cpu/possible")
LIST_EVENTS = ("os.listdir", "os.scandir", "glob.glob", "glob.glob/2", "os.walk", "os.fwalk")


def install_guard(work: str) -> None:
    norm = os.path.normcase
    absolute = os.path.abspath
    sep = os.sep

    def under(path: str, roots: tuple[str, ...]) -> bool:
        return any(path == root or path.startswith(root.rstrip(sep) + sep) for root in roots)

    work_root = norm(absolute(work))
    readable = tuple({norm(absolute(p)) for p in (sys.prefix, sys.base_prefix, sys.exec_prefix, *sys.path) if p and os.path.isdir(p)}
                     | {work_root})
    native_roots = tuple(root for root in readable if root != work_root)
    system_files = frozenset(norm(name) for name in SYSTEM_FILES) if os.name != "nt" else frozenset()

    def text(value: object) -> str | None:
        if isinstance(value, int):
            return None                                    # an already open file descriptor
        try:
            raw = os.fspath(value)                         # type: ignore[arg-type]
        except TypeError:
            return ""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        return norm(absolute(raw))

    def refuse(what: str) -> None:
        raise PermissionError(f"Not allowed in an Analytics script: {what}")

    def trusted_ctypes(event: str, args: tuple) -> bool:
        """A ctypes call made by threadpoolctl (a library file in Python's folders), not by the script."""
        frame = sys._getframe(2)
        while frame is not None and os.path.basename(os.path.dirname(frame.f_code.co_filename)) == "ctypes":
            frame = frame.f_back
        if frame is None or os.path.basename(frame.f_code.co_filename) not in CTYPES_USERS:
            return False
        if not under(norm(absolute(frame.f_code.co_filename)), native_roots):
            return False
        if event == "ctypes.dlopen" and args and isinstance(args[0], str) and (os.sep in args[0] or "/" in args[0]):
            return under(norm(absolute(args[0])), native_roots) or not under(norm(absolute(args[0])), (work_root,))
        return True

    def hook(event: str, args: tuple) -> None:
        if event.startswith("ctypes.") and trusted_ctypes(event, args):
            return
        if event.startswith(BLOCKED_EVENTS):
            refuse(event.split(".")[0] if event.startswith(("socket.", "subprocess.", "ctypes.")) else event)
        if event == "open":
            path = text(args[0])
            if path is None:
                return
            mode = str(args[1] or "") if len(args) > 1 else ""
            flags = int(args[2] or 0) if len(args) > 2 and isinstance(args[2], int) else 0
            writing = any(ch in mode for ch in "wax+") or bool(flags & WRITE_FLAGS)
            if writing and not under(path, (work_root,)):
                refuse("writing files outside the script's own folder")
            if not writing and not under(path, readable) and path not in system_files:
                refuse(f"reading files of the server ({path}; use the datasets in dfs)")
        elif event in WRITE_EVENTS:
            for arg in args[:2]:
                path = text(arg)
                if path and not under(path, (work_root,)):
                    refuse("changing files outside the script's own folder")
        elif event in LIST_EVENTS:
            path = text(args[0]) if args and args[0] is not None else work_root
            if path and not under(path, readable):
                refuse("listing the server's folders")
        elif event == "import":
            name = str(args[0] or "")
            if name.split(".")[0] in BLOCKED_MODULES:
                refuse(f"import {name.split('.')[0]}")
            path = text(args[1]) if len(args) > 1 and args[1] else ""
            if path and not under(path, native_roots):
                refuse("loading compiled code from outside Python's own folders")

    sys.addaudithook(hook)


class Capped(io.TextIOBase):
    """print() output, kept up to MAX_OUTPUT characters."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.size = 0
        self.cut = False

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if self.size < MAX_OUTPUT:
            keep = text[: MAX_OUTPUT - self.size]
            self.parts.append(keep)
            self.size += len(keep)
        if len(text) and self.size >= MAX_OUTPUT:
            self.cut = True
        return len(text)

    def value(self) -> str:
        return "".join(self.parts) + ("\n… (output cut)" if self.cut else "")


def as_frame(pd: object, value: object) -> object:
    if isinstance(value, pd.DataFrame):          # type: ignore[attr-defined]
        return value
    if isinstance(value, pd.Series):             # type: ignore[attr-defined]
        return value.to_frame(name=str(value.name) if value.name is not None else "value")
    if isinstance(value, (list, dict)):
        return pd.DataFrame(value)               # type: ignore[attr-defined]
    raise TypeError(f"`result` must be a pandas DataFrame (it is a {type(value).__name__})")


def script_traceback(exc: BaseException) -> str:
    """The error with only the lines of the script (not of this program)."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "<script>"]
    lines = [f"  line {f.lineno}" + (f": {f.line}" if f.line else "") for f in frames[-6:]]
    message = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    return "\n".join([*lines, message]) if lines else message


def main() -> int:
    work = os.path.abspath(sys.argv[1])
    with open(os.path.join(work, "job.json"), encoding="utf-8") as handle:
        job = json.load(handle)
    report: dict[str, object] = {"ok": False}
    notes = []
    memory_note = limit_memory(int(job.get("memoryMb") or 0))
    if memory_note:
        notes.append(memory_note)
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415
    for name in job.get("preload") or ():                    # modules scripts often need, loaded before the guard
        try:
            __import__(name)
        except Exception:  # noqa: BLE001 - the script reports it when it really needs one
            pass
    dfs = {}
    for item in job["inputs"]:
        frame = pd.read_pickle(os.path.join(work, item["file"]))
        dfs[item["name"]] = frame
        dfs[item["id"]] = frame
    with open(os.path.join(work, "script.py"), encoding="utf-8") as handle:
        source = handle.read()
    output = Capped()
    try:
        code = compile(source, "<script>", "exec")
    except SyntaxError as exc:
        report["error"] = f"line {exc.lineno}: {exc.msg}"
        write_report(work, report, output, notes)
        return 0
    namespace = {"__name__": "__main__", "pd": pd, "np": np, "dfs": dfs,
                 "df": dfs[job["inputs"][0]["name"]] if job["inputs"] else None}
    install_guard(work)
    sys.stdout = sys.stderr = output
    try:
        exec(code, namespace)  # noqa: S102 - running the user's script is the point of this program
        value = namespace.get("result", namespace.get("df"))
        if value is None:
            raise ValueError("Leave the cleaned data in `result` (or change `df`)")
        frame = as_frame(pd, value)
        if len(frame) > int(job["maxRows"]):
            raise ValueError(f"The result has {len(frame):,} rows; at most {int(job['maxRows']):,}")
        if len(frame.columns) > 400:
            raise ValueError("The result has more than 400 columns")
        if not isinstance(frame.index, pd.RangeIndex):    # a group-by result: its keys become columns again
            frame = frame.reset_index() if any(n is not None for n in frame.index.names) else frame.reset_index(drop=True)
        names: list[str] = []
        for column in frame.columns:
            name = (" ".join(str(c) for c in column if str(c)) if isinstance(column, tuple) else str(column)).strip() or "column"
            base, n = name[:120], 2
            while name in names:
                name = f"{base}_{n}"
                n += 1
            names.append(name)
        frame.columns = names
        frame.to_json(os.path.join(work, "out.ndjson"), orient="records", lines=True, date_format="iso", date_unit="ms",
                      force_ascii=False, default_handler=str)
        report.update({"ok": True, "rowCount": int(len(frame)), "columns": names,
                       "dtypes": {name: str(kind) for name, kind in zip(names, frame.dtypes)}})
    except MemoryError:
        report["error"] = "The script ran out of memory (ANALYTICS_PYTHON_MEMORY_MB)"
    except BaseException as exc:  # noqa: BLE001 - every error of the script goes back to the person who wrote it
        report["error"] = script_traceback(exc)
    write_report(work, report, output, notes)
    return 0


def write_report(work: str, report: dict[str, object], output: Capped, notes: list[str]) -> None:
    sys.stdout = sys.__stdout__
    report["output"] = output.value()
    report["notes"] = notes
    with open(os.path.join(work, "result.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, default=str)


if __name__ == "__main__":
    sys.exit(main())
