"""`python_exec` (§B9.3, §B17): a separate limited process; sockets, subprocesses and the app's files are out
of reach."""
from pathlib import Path

import pytest

from app.tools.python_exec import make_python_exec, run_code


async def call(code: str) -> str:
    msg = await make_python_exec().ainvoke({"name": "python_exec", "args": {"code": code}, "id": "1",
                                            "type": "tool_call"})
    return msg.content


async def test_calculations_work():
    assert await call("import statistics\nprint(round(statistics.mean([3, 5, 17]), 2))") == "8.33\n"


async def test_import_socket_fails():
    assert await call("import socket\nprint('connected')") == \
        "error: ImportError: import of 'socket' is not allowed in the sandbox"


def test_an_infinite_loop_is_killed():
    ok, out = run_code("while True:\n    pass", cpu_s=1, wall_s=5)
    assert not ok and out == "killed: CPU time limit of 1 s exceeded"


def test_wall_clock_limit():
    ok, out = run_code("import time\ntime.sleep(10)", wall_s=1)
    assert not ok and out == "killed: wall-clock limit of 1 s exceeded"


@pytest.mark.parametrize(("code", "message"), [
    ("__import__('socket').create_connection(('example.com', 80))", "import of 'socket' is blocked"),
    ("__import__('subprocess').run(['ls'])", "import of 'subprocess' is blocked"),
    ("__import__('os').system('echo pwned')", "os.system is blocked"),
    ("__import__('os').execv('/bin/sh', ['sh'])", "os.exec is blocked"),
    ("import os", "import of 'os' is not allowed"),
    (f"print(open({str(Path('.env').resolve())!r}).read())", "is blocked in the sandbox"),
    ("print(open('../../etc/passwd').read())", "is blocked in the sandbox"),
    ("open('/tmp/pyexec-escape.txt', 'w').write('x')", "is blocked in the sandbox"),
    ("print(__import__('os').listdir('/'))", "access to '/' is blocked"),
])
def test_escapes_are_blocked(code, message):
    ok, out = run_code(code)
    assert not ok and message in out


def test_no_secrets_in_the_environment():
    ok, out = run_code("print(sorted(__import__('os').environ))")
    # empty apart from what the OS / interpreter add on their own (macOS: __CF_USER_TEXT_ENCODING, LC_CTYPE)
    assert ok and set(eval(out)) <= {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}


def test_files_in_its_own_temp_dir_are_fine():
    assert run_code("open('t.txt', 'w').write('hi')\nprint(open('t.txt').read())") == (True, "hi\n")
