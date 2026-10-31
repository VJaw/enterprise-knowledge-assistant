"""Runs one snippet for `python_exec`. Started as `python -I sandbox_runner.py <cpu_s> <mem_mb>` in an empty
temp dir with an empty environment; the code arrives on stdin.

Order matters: limits first, then the audit hook, then the user's code. Audit hooks can't be removed once
installed. This is a best-effort sandbox, not a hardened one (§B17)."""
import ast
import builtins
import os
import resource
import sys

ALLOWED_IMPORTS = {
    "math", "cmath", "statistics", "decimal", "fractions", "random", "datetime", "calendar", "time", "json",
    "csv", "re", "string", "textwrap", "collections", "itertools", "functools", "operator", "heapq", "bisect",
    "dataclasses", "typing", "pprint", "io",
}
BLOCKED_MODULES = {
    "socket", "_socket", "ssl", "_ssl", "select", "selectors", "asyncio", "subprocess", "_posixsubprocess",
    "multiprocessing", "_multiprocessing", "ctypes", "_ctypes", "cffi", "pty", "fcntl", "mmap", "signal",
    "urllib", "http", "ftplib", "smtplib", "imaplib", "poplib", "telnetlib", "xmlrpc", "webbrowser", "shutil",
    "importlib",
}
BLOCKED_EVENTS = (
    "socket.", "subprocess.", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty",
    "os.kill", "os.killpg", "os.remove", "os.unlink", "os.rmdir", "os.rename", "os.chmod", "os.chown",
    "os.symlink", "os.link", "os.truncate", "os.putenv", "os.unsetenv", "os.chdir", "ctypes.", "pty.",
    "shutil.", "webbrowser.", "sys.addaudithook", "sys.setprofile", "sys.settrace",
)
PATH_EVENTS = ("open", "os.listdir", "os.scandir")
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def set_limits(cpu_s: int, mem_mb: int) -> None:
    for rlimit, value in ((resource.RLIMIT_CPU, cpu_s), (resource.RLIMIT_AS, mem_mb * 2**20),
                          (resource.RLIMIT_FSIZE, 10 * 2**20)):
        try:
            resource.setrlimit(rlimit, (value, value))
        except (ValueError, OSError):
            pass  # macOS rejects RLIMIT_AS below the current reservation; enforced on Linux (the container)


def check_imports(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            if name.split(".")[0] not in ALLOWED_IMPORTS:
                raise ImportError(f"import of {name!r} is not allowed in the sandbox")


def main() -> None:
    cpu_s, mem_mb = int(sys.argv[1]), int(sys.argv[2])
    code = sys.stdin.read()
    cwd = os.path.realpath(os.getcwd())
    # Reads are allowed in the temp dir and the Python install (stdlib imports), never e.g. the app's .env
    roots = (cwd, sys.prefix, sys.base_prefix, sys.exec_prefix)
    readable = tuple({os.path.realpath(p) + os.sep for p in roots})
    set_limits(cpu_s, mem_mb)

    def inside(path, roots: tuple[str, ...]) -> bool:
        return (os.path.realpath(path) + os.sep).startswith(roots)

    def hook(event: str, args: tuple) -> None:
        if event == "import" and args[0].split(".")[0] in BLOCKED_MODULES:
            raise ImportError(f"import of {args[0]!r} is blocked in the sandbox")
        if event.startswith(BLOCKED_EVENTS):
            raise PermissionError(f"{event} is blocked in the sandbox")
        if event in PATH_EVENTS and isinstance(args[0], (str, bytes)):
            path = os.fsdecode(args[0]) or "."
            writing = event == "open" and (any(ch in (args[1] or "r") for ch in "wax+")
                                           or (args[2] or 0) & WRITE_FLAGS)
            if not inside(path, (cwd + os.sep,) if writing else readable):
                raise PermissionError(f"access to {path!r} is blocked in the sandbox")

    tree = ast.parse(code, "<sandbox>")
    check_imports(tree)
    compiled = compile(tree, "<sandbox>", "exec")
    sys.addaudithook(hook)
    exec(compiled, {"__name__": "__main__", "__builtins__": builtins})  # noqa: S102


if __name__ == "__main__":
    main()
