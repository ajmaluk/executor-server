import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from flask import Blueprint, jsonify, request

try:
    from auth import require_api_key
    from config import ServerConfig
    from rate_limit import apply_rate_limit
except ImportError:
    from server.auth import require_api_key
    from server.config import ServerConfig
    from server.rate_limit import apply_rate_limit

executor_bp = Blueprint("executor_api", __name__)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cached binary lookups — resolved once at import, not per-request
# ---------------------------------------------------------------------------
_BINARY_CACHE = {}
_BINARY_CACHE_LOCK = threading.Lock()


def _find_binary(*names):
    """Find a binary by trying multiple names and fallback system paths, cache the result."""
    cache_key = tuple(names)
    if cache_key in _BINARY_CACHE:
        return _BINARY_CACHE[cache_key]
    with _BINARY_CACHE_LOCK:
        if cache_key in _BINARY_CACHE:
            return _BINARY_CACHE[cache_key]
        for name in names:
            path = shutil.which(name)
            if path:
                _BINARY_CACHE[cache_key] = path
                return path
            # Fallback search in standard system locations (e.g. Docker container paths)
            for prefix in ("/usr/bin", "/usr/local/bin", "/bin", "/usr/lib/jvm/default-java/bin", "/usr/lib/jvm/java-17-openjdk-amd64/bin"):
                candidate = os.path.join(prefix, name)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    _BINARY_CACHE[cache_key] = candidate
                    return candidate
        _BINARY_CACHE[cache_key] = None
        return None


# Concurrency limiter — prevent resource exhaustion on small instances.
_MAX_CONCURRENT_EXECUTIONS = int(os.environ.get("EXECUTOR_MAX_CONCURRENT", "8"))
_execution_semaphore = threading.Semaphore(_MAX_CONCURRENT_EXECUTIONS)

# Max time (seconds) a request waits in the execution queue before being
# rejected as busy. Kept well under the gunicorn worker timeout so queued
# requests cannot pile up and stall worker threads indefinitely.
_EXECUTION_QUEUE_WAIT_S = ServerConfig.EXECUTOR_QUEUE_WAIT_S
# Internal sentinel: the request was rejected because the execution queue was full.
_EXIT_SERVER_BUSY = 429

# Reject JSON bodies far larger than the bounded code/stdin fields they can
# legally carry. Code is capped at EXECUTOR_MAX_CODE_BYTES and stdin at
# EXECUTOR_MAX_STDIN_BYTES, so a body bigger than the sum (+ JSON overhead)
# can only be junk. Kept small to avoid paying for needless JSON parsing.
_MAX_EXECUTE_BODY_BYTES = (
    ServerConfig.EXECUTOR_MAX_CODE_BYTES
    + ServerConfig.EXECUTOR_MAX_STDIN_BYTES
    + 16384  # JSON keys/escaping/whitespace headroom
)

# Observable concurrency metrics (per worker process).
# `rejected` counts requests turned away because the queue was full (never
# executed) — kept distinct from `failed` (code ran but errored), so the two
# are not conflated.
_EXECUTION_STATS_LOCK = threading.Lock()
_EXECUTION_STATS = {
    "running": 0,
    "waiting": 0,
    "completed": 0,
    "failed": 0,
    "timed_out": 0,
    "rejected": 0,
}


def get_execution_stats():
    """Return a copy of the live concurrency metrics."""
    with _EXECUTION_STATS_LOCK:
        return dict(_EXECUTION_STATS)

# Native Language Registry
NATIVE_LANGUAGE_REGISTRY = {
    "python": {
        "language": "python",
        "extension": ".py",
        "aliases": ["py", "python3", "py3"],
        "example": "print('Hello from Native Python Execution!')"
    },
    "javascript": {
        "language": "javascript",
        "extension": ".js",
        "aliases": ["js", "node", "nodejs"],
        "example": "console.log('Hello from Native JavaScript Execution!');"
    },
    "bash": {
        "language": "bash",
        "extension": ".sh",
        "aliases": ["sh", "shell", "zsh"],
        "example": 'echo "Hello from Native Bash Execution!"'
    },
    "c": {
        "language": "c",
        "extension": ".c",
        "aliases": ["gcc"],
        "example": '#include <stdio.h>\nint main() {\n    printf("Hello from Native C!\\n");\n    return 0;\n}'
    },
    "cpp": {
        "language": "c++",
        "extension": ".cpp",
        "aliases": ["c++", "cxx", "g++"],
        "example": '#include <iostream>\nint main() {\n    std::cout << "Hello from Native C++!" << std::endl;\n    return 0;\n}'
    },
    "java": {
        "language": "java",
        "extension": ".java",
        "aliases": ["java", "jdk"],
        "example": 'public class Main {\n    public static void main(String[] args) {\n        System.out.println("Hello from Native Java!");\n    }\n}'
    },
    "go": {
        "language": "go",
        "extension": ".go",
        "aliases": ["go", "golang"],
        "example": 'package main\nimport "fmt"\nfunc main() {\n    fmt.Println("Hello from Native Go!")\n}'
    },
    "rust": {
        "language": "rust",
        "extension": ".rs",
        "aliases": ["rust", "rs"],
        "example": 'fn main() {\n    println!("Hello from Native Rust!");\n}'
    },
    "swift": {
        "language": "swift",
        "extension": ".swift",
        "aliases": ["swift"],
        "example": 'print("Hello from Native Swift!")'
    },
    "kotlin": {
        "language": "kotlin",
        "extension": ".kt",
        "aliases": ["kt", "kotlin"],
        "example": 'fun main() {\n    println("Hello from Native Kotlin!")\n}'
    },
    "php": {
        "language": "php",
        "extension": ".php",
        "aliases": ["php"],
        "example": '<?php\necho "Hello from Native PHP!\\n";'
    },
    "ruby": {
        "language": "ruby",
        "extension": ".rb",
        "aliases": ["rb"],
        "example": 'puts "Hello from Native Ruby!"'
    },
    "perl": {
        "language": "perl",
        "extension": ".pl",
        "aliases": ["pl"],
        "example": 'print "Hello from Native Perl!\\n";'
    },
    "sql": {
        "language": "sqlite3",
        "extension": ".sql",
        "aliases": ["sqlite", "sqlite3"],
        "example": 'CREATE TABLE demo (id INT, name TEXT);\nINSERT INTO demo VALUES (1, "ToolPix");\nSELECT * FROM demo;'
    }
}

# Alias mapping
ALIAS_TO_KEY = {}
for key, info in NATIVE_LANGUAGE_REGISTRY.items():
    ALIAS_TO_KEY[key.lower()] = key
    for alias in info.get("aliases", []):
        ALIAS_TO_KEY[alias.lower()] = key


def resolve_language_key(lang_str: str):
    """Resolves language key or alias to canonical registry key."""
    if not lang_str:
        return None
    normalized = lang_str.strip().lower()
    return ALIAS_TO_KEY.get(normalized)


def sanitize_args(args_raw) -> list:
    """Sanitizes arguments array to string format."""
    if not args_raw:
        return []
    if isinstance(args_raw, (list, tuple)):
        return [str(item) for item in args_raw if item is not None]
    if isinstance(args_raw, str):
        return [args_raw]
    return []


# Runs SQLite in a fresh Python subprocess so a runaway query (e.g. an unbounded
# recursive CTE) can be hard-killed by the process-group timeout and can never
# exhaust CPU/memory inside the server process itself.
_SQLITE_HELPER_SRC = r"""
import sqlite3
import sys


def split_statements(sql):
    # Naive split(";") breaks when a string literal contains a semicolon
    # (e.g. INSERT INTO t VALUES ('foo;bar')). Track quotes/identifiers so
    # we only split on a ';' that is outside any string.
    statements = []
    current = []
    i = 0
    n = len(sql)
    quote = None  # None | "'" | '"' | '`'
    in_bracket = False
    line_comment = False
    block_comment = False
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if line_comment:
            current.append(ch)
            if ch == "\n":
                line_comment = False
            i += 1
            continue
        if block_comment:
            current.append(ch)
            if ch == "*" and nxt == "/":
                current.append(nxt)
                i += 2
                block_comment = False
                continue
            i += 1
            continue
        if quote:
            current.append(ch)
            if ch == quote:
                if nxt == quote:  # escaped quote ''
                    current.append(nxt)
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if in_bracket:
            current.append(ch)
            if ch == "]":
                in_bracket = False
            i += 1
            continue
        if ch == "'" or ch == '"' or ch == "`":
            quote = ch
            current.append(ch)
            i += 1
            continue
        if ch == "[":
            in_bracket = True
            current.append(ch)
            i += 1
            continue
        if ch == "-" and nxt == "-":
            line_comment = True
            current.append(ch)
            current.append(nxt)
            i += 2
            continue
        if ch == "/" and nxt == "*":
            block_comment = True
            current.append(ch)
            current.append(nxt)
            i += 2
            continue
        if ch == ";":
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    stmt = "".join(current).strip()
    if stmt:
        statements.append(stmt)
    return statements


conn = sqlite3.connect(":memory:")
output_lines = []
try:
    for stmt in split_statements(sys.stdin.read()):
        cursor = conn.cursor()
        cursor.execute(stmt)
        if cursor.description:
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            output_lines.append("Query: %s;" % stmt)
            output_lines.append(" | ".join(columns))
            output_lines.append("-" * 30)
            for row in rows:
                output_lines.append(" | ".join(str(val) for val in row))
            output_lines.append("")
    conn.commit()
    sys.stdout.write("\n".join(output_lines) if output_lines else "Query executed successfully.")
except Exception as e:
    sys.stderr.write("SQLite Error: %s" % e)
    sys.exit(1)
"""


def _execute_sqlite_natively(sql_code: str, timeout_s: int = ServerConfig.EXECUTOR_TIMEOUT_S):
    """Executes SQLite queries in an isolated subprocess with a hard timeout.

    The query runs in its own process group, so a runaway query is killed on
    timeout instead of leaking a thread inside the server. Returns
    (stdout, stderr, exit_code, stdout_truncated, stderr_truncated).
    """
    return _run_process(
        [sys.executable, "-c", _SQLITE_HELPER_SRC],
        cwd=tempfile.gettempdir(),
        stdin_str=sql_code,
        timeout_s=timeout_s,
    )


def _kill_process_group(proc):
    """Kill the process and every child it spawned (entire process group)."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


def _read_capped(proc, timeout_s, max_output_bytes):
    """Read a subprocess's stdout/stderr with a hard byte cap and timeout.

    Returns (stdout_bytes, stderr_bytes, timed_out, out_truncated, err_truncated).
    Output beyond ``max_output_bytes`` per stream is discarded (never buffered),
    so a runaway program printing gigabytes cannot exhaust server memory.
    Reads use ``select`` so a stream that stops producing output never blocks.
    """
    import select

    start = time.time()
    fd_map = {proc.stdout.fileno(): "out", proc.stderr.fileno(): "err"}
    bufs = {"out": [], "err": []}
    lens = {"out": 0, "err": 0}
    truncated = {"out": False, "err": False}

    def feed(key, data):
        if not data:
            return
        space = max_output_bytes - lens[key]
        if space <= 0:
            truncated[key] = True
            return
        if len(data) > space:
            truncated[key] = True
            data = data[:space]
        bufs[key].append(data)
        lens[key] += len(data)

    def drain(stream):
        # Non-blocking drain after the process has exited.
        while True:
            try:
                chunk = stream.read(65536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            feed(fd_map[stream.fileno()], chunk)

    while True:
        remaining = timeout_s - (time.time() - start)
        if remaining <= 0:
            _kill_process_group(proc)
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
            out = b"".join(bufs["out"]).decode("utf-8", errors="replace")
            err = b"".join(bufs["err"]).decode("utf-8", errors="replace")
            return out, err, True, truncated["out"], truncated["err"]

        if proc.poll() is not None:
            drain(proc.stdout)
            drain(proc.stderr)
            proc.wait()
            break

        rlist, _, _ = select.select([proc.stdout, proc.stderr], [], [], min(remaining, 0.5))
        for stream in rlist:
            try:
                chunk = os.read(stream.fileno(), 65536)
            except (OSError, ValueError):
                chunk = b""
            if not chunk:
                continue
            feed(fd_map[stream.fileno()], chunk)

    out = b"".join(bufs["out"]).decode("utf-8", errors="replace")
    err = b"".join(bufs["err"]).decode("utf-8", errors="replace")
    try:
        proc.stdout.close()
    except Exception:
        pass
    try:
        proc.stderr.close()
    except Exception:
        pass
    return out, err, False, truncated["out"], truncated["err"]


def _run_process(cmd, cwd, stdin_str="", timeout_s=30, max_output_bytes=None):
    """Run a subprocess via Popen with a hard timeout and bounded output.

    Returns (stdout, stderr, returncode, stdout_truncated, stderr_truncated).
    The process group is killed if it exceeds the timeout (prevents orphaned
    child processes). Output is decoded as UTF-8 with replacement, so non-UTF-8
    output can never crash the request. Output is capped at
    ``max_output_bytes`` per stream while reading, so huge outputs never
    accumulate in memory.
    """
    if max_output_bytes is None:
        max_output_bytes = ServerConfig.EXECUTOR_MAX_OUTPUT_BYTES
    if not stdin_str:
        stdin_str = ""

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as e:
        return "", f"Failed to start process: {e}", 127, False, False

    if stdin_str:
        try:
            proc.stdin.write(stdin_str.encode("utf-8", errors="replace"))
        except (BrokenPipeError, OSError):
            pass
    try:
        proc.stdin.close()
    except Exception:
        pass

    stdout, stderr, timed_out, out_trunc, err_trunc = _read_capped(
        proc, timeout_s, max_output_bytes
    )
    if timed_out:
        err = (stderr + f"Execution timed out after {timeout_s} seconds.") if stderr else f"Execution timed out after {timeout_s} seconds."
        return stdout, err, -1, out_trunc, True
    return stdout, stderr, proc.returncode, out_trunc, err_trunc


def _execute_in_tempdir(canonical_key, code, stdin_str, run_cmd, compile_cmd, timeout_s, filename=None):
    """Run interpreted/compiled code inside an isolated temporary directory.

    Returns (stdout, stderr, exit_code). The compile step gets half the timeout
    budget; the run step gets whatever remains.
    """
    ext = NATIVE_LANGUAGE_REGISTRY[canonical_key]["extension"]
    filename = filename or f"main{ext}"
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, filename)
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(code)

        def _resolve(cmd):
            resolved = []
            for i, part in enumerate(cmd):
                if part == filename or part == f"main{ext}":
                    resolved.append(src_path)
                elif canonical_key in ("c", "cpp", "rust") and part == "main.out":
                    resolved.append(os.path.join(tmpdir, "main.out"))
                elif canonical_key == "kotlin" and part == "main.jar":
                    resolved.append(os.path.join(tmpdir, "main.jar"))
                elif i == 0:
                    resolved.append(part)  # binary path stays as-is
                else:
                    resolved.append(part)
            return resolved

        run_cmd_full = _resolve(run_cmd)
        compile_cmd_full = _resolve(compile_cmd) if compile_cmd else None

        start_ts = time.time()

        # Step 1: Compile if required — give compilation up to max(20, timeout_s - 5) seconds
        compile_output = ""
        if compile_cmd_full:
            compile_timeout = max(20, timeout_s - 5)
            comp_out, comp_err, comp_rc, _, _ = _run_process(
                compile_cmd_full, cwd=tmpdir, timeout_s=compile_timeout
            )
            compile_output = comp_out + comp_err
            if comp_rc != 0:
                return "", f"Compilation Failed:\n{compile_output}", comp_rc, False, False

        # Step 2: Execute — give it the full remaining time budget
        elapsed = time.time() - start_ts
        exec_timeout = max(5, timeout_s - int(elapsed))

        return _run_process(
            run_cmd_full, cwd=tmpdir, stdin_str=stdin_str, timeout_s=exec_timeout
        )


def _record_result(result):
    """Classify an execution result into the concurrency metrics."""
    with _EXECUTION_STATS_LOCK:
        rc = result[2] if isinstance(result, tuple) else -1
        if rc == 0:
            _EXECUTION_STATS["completed"] += 1
        elif rc == -1:
            _EXECUTION_STATS["timed_out"] += 1
        else:
            _EXECUTION_STATS["failed"] += 1


def _execute_native_subprocess(canonical_key: str, code: str, stdin_str: str, args: list, timeout_s: int):
    """Executes code using native server interpreters and compilers inside a temporary directory sandbox."""
    # Resolve compile/run commands before acquiring the semaphore
    compile_cmd = None
    run_cmd = []
    spec = NATIVE_LANGUAGE_REGISTRY[canonical_key]
    ext = spec["extension"]
    filename = f"main{ext}"

    if canonical_key == "python":
        run_cmd = [sys.executable, filename] + args
    elif canonical_key == "javascript":
        node_path = _find_binary("node", "nodejs")
        if not node_path:
            return "", "JavaScript Runtime Error: Node.js is not installed on server.", 127, False, False
        run_cmd = [node_path, filename] + args
    elif canonical_key == "bash":
        sh_path = _find_binary("bash", "sh")
        if not sh_path:
            return "", "Bash Error: bash or sh is not installed on server.", 127, False, False
        run_cmd = [sh_path, filename] + args
    elif canonical_key == "c":
        gcc_path = _find_binary("gcc", "clang")
        if not gcc_path:
            return "", "C Compiler Error: gcc or clang is not installed on server.", 127, False, False
        compile_cmd = [gcc_path, filename, "-o", "main.out"]
        run_cmd = ["main.out"] + args
    elif canonical_key == "cpp":
        gpp_path = _find_binary("g++", "clang++")
        if not gpp_path:
            return "", "C++ Compiler Error: g++ or clang++ is not installed on server.", 127, False, False
        compile_cmd = [gpp_path, filename, "-o", "main.out"]
        run_cmd = ["main.out"] + args
    elif canonical_key == "java":
        javac_path = _find_binary("javac")
        java_path = _find_binary("java")
        if not javac_path or not java_path:
            return "", "Java Compiler Error: javac or java JDK is not installed on server.", 127, False, False
        class_match = re.search(r"public\s+class\s+([A-Za-z_][A-Za-z0-9_]*)", code)
        if not class_match:
            class_match = re.search(r"class\s+([A-Za-z_][A-Za-z0-9_]*)", code)
        class_name = class_match.group(1) if class_match else "Main"
        filename = f"{class_name}.java"
        compile_cmd = [javac_path, filename]
        run_cmd = [java_path, class_name] + args
    elif canonical_key == "go":
        go_path = _find_binary("go")
        if not go_path:
            return "", "Go Error: go binary is not installed on server.", 127, False, False
        filename = "main.go"
        run_cmd = [go_path, "run", filename] + args
    elif canonical_key == "rust":
        rustc_path = _find_binary("rustc")
        if not rustc_path:
            return "", "Rust Compiler Error: rustc is not installed on server.", 127, False, False
        filename = "main.rs"
        compile_cmd = [rustc_path, filename, "-o", "main.out"]
        run_cmd = ["main.out"] + args
    elif canonical_key == "swift":
        swift_path = _find_binary("swift", "swiftc")
        if not swift_path:
            return "", "Swift Error: swift binary is not installed on server.", 127, False, False
        filename = "main.swift"
        run_cmd = [swift_path, filename] + args
    elif canonical_key == "kotlin":
        kotlinc_path = _find_binary("kotlinc")
        java_path = _find_binary("java")
        if not kotlinc_path or not java_path:
            return "", "Kotlin Compiler Error: kotlinc or java is not installed on server.", 127, False, False
        filename = "main.kt"
        compile_cmd = [kotlinc_path, filename, "-include-runtime", "-d", "main.jar"]
        run_cmd = [java_path, "-jar", "main.jar"] + args
    elif canonical_key == "php":
        php_path = _find_binary("php")
        if not php_path:
            return "", "PHP Error: php CLI is not installed on server.", 127, False, False
        run_cmd = [php_path, filename] + args
    elif canonical_key == "ruby":
        ruby_path = _find_binary("ruby")
        if not ruby_path:
            return "", "Ruby Error: ruby is not installed on server.", 127, False, False
        run_cmd = [ruby_path, filename] + args
    elif canonical_key == "perl":
        perl_path = _find_binary("perl")
        if not perl_path:
            return "", "Perl Error: perl is not installed on server.", 127, False, False
        run_cmd = [perl_path, filename] + args

    # Queue up: wait up to _EXECUTION_QUEUE_WAIT_S for a free execution slot.
    with _EXECUTION_STATS_LOCK:
        _EXECUTION_STATS["waiting"] += 1
    acquired = _execution_semaphore.acquire(timeout=_EXECUTION_QUEUE_WAIT_S)
    if not acquired:
        with _EXECUTION_STATS_LOCK:
            _EXECUTION_STATS["waiting"] -= 1
            _EXECUTION_STATS["rejected"] += 1
        return "", "Server busy: too many concurrent executions. Try again later.", _EXIT_SERVER_BUSY, False, False

    with _EXECUTION_STATS_LOCK:
        _EXECUTION_STATS["waiting"] -= 1
        _EXECUTION_STATS["running"] += 1
    try:
        if canonical_key == "sql":
            result = _execute_sqlite_natively(code, timeout_s)
        else:
            result = _execute_in_tempdir(
                canonical_key, code, stdin_str, run_cmd, compile_cmd, timeout_s, filename=filename
            )
        _record_result(result)
        return result
    finally:
        with _EXECUTION_STATS_LOCK:
            _EXECUTION_STATS["running"] -= 1
        _execution_semaphore.release()


@executor_bp.route("/languages", methods=["GET", "OPTIONS"])
@executor_bp.route("/api/v1/languages", methods=["GET", "OPTIONS"])
@executor_bp.route("/api/v2/runtimes", methods=["GET", "OPTIONS"])
@executor_bp.route("/v2/runtimes", methods=["GET", "OPTIONS"])
def list_languages():
    """Lists all supported native programming languages."""
    return jsonify({
        "status": "success",
        "engine": "native_standalone",
        "count": len(NATIVE_LANGUAGE_REGISTRY),
        "aliases_mapped": len(ALIAS_TO_KEY),
        "languages": NATIVE_LANGUAGE_REGISTRY
    }), 200


@executor_bp.route("/languages/<string:lang_query>", methods=["GET", "OPTIONS"])
@executor_bp.route("/api/v1/languages/<string:lang_query>", methods=["GET", "OPTIONS"])
def get_language_detail(lang_query):
    """Returns details for a specific language or alias."""
    key = resolve_language_key(lang_query)
    if not key:
        return jsonify({
            "error": "Not Found",
            "message": f"Language or alias '{lang_query}' is not supported."
        }), 404

    info = NATIVE_LANGUAGE_REGISTRY[key]
    return jsonify({
        "status": "success",
        "canonical_name": key,
        "details": info
    }), 200


@executor_bp.route("/execute", methods=["POST", "OPTIONS"])
@executor_bp.route("/api/v1/execute", methods=["POST", "OPTIONS"])
@executor_bp.route("/api/v2/execute", methods=["POST", "OPTIONS"])
@executor_bp.route("/v2/execute", methods=["POST", "OPTIONS"])
@require_api_key
@apply_rate_limit
def execute_code():
    """Executes code natively on the server with Piston API v2 & ToolPix response contract."""
    # Reject oversized bodies before parsing JSON — the useful fields (code, stdin)
    # are strictly bounded, so anything larger is wasted CPU on JSON parsing.
    if request.content_length and request.content_length > _MAX_EXECUTE_BODY_BYTES:
        return jsonify({
            "error": "Payload Too Large",
            "message": f"Request body exceeds limit of {_MAX_EXECUTE_BODY_BYTES} bytes."
        }), 413

    data = request.get_json(silent=True) or {}

    lang_input = (data.get("language") or "").strip()
    code_raw = data.get("code")
    if not code_raw and isinstance(data.get("files"), list) and len(data["files"]) > 0:
        code_raw = data["files"][0].get("content")
    stdin_raw = data.get("stdin") or ""
    args_raw = data.get("args")

    if not lang_input:
        return jsonify({"error": "Bad Request", "message": "Field 'language' is required."}), 400

    if not code_raw or not str(code_raw).strip():
        return jsonify({"error": "Bad Request", "message": "Field 'code' cannot be empty."}), 400

    canonical_key = resolve_language_key(lang_input)
    if not canonical_key:
        return jsonify({
            "error": "Bad Request",
            "message": f"Unsupported language '{lang_input}'. Call /api/v1/languages for supported runtimes."
        }), 400

    code_str = str(code_raw)
    code_bytes = len(code_str.encode("utf-8"))
    if code_bytes > ServerConfig.EXECUTOR_MAX_CODE_BYTES:
        return jsonify({
            "error": "Payload Too Large",
            "message": f"Code size ({code_bytes} bytes) exceeds limit of {ServerConfig.EXECUTOR_MAX_CODE_BYTES} bytes."
        }), 413

    # Process STDIN — truncate by bytes, not characters (multi-byte safety)
    stdin_str = str(stdin_raw)
    stdin_bytes = len(stdin_str.encode("utf-8"))
    stdin_truncated = False
    if stdin_bytes > ServerConfig.EXECUTOR_MAX_STDIN_BYTES:
        stdin_str = stdin_str.encode("utf-8")[:ServerConfig.EXECUTOR_MAX_STDIN_BYTES].decode("utf-8", errors="ignore")
        stdin_truncated = True

    sanitized_args = sanitize_args(args_raw)
    spec = NATIVE_LANGUAGE_REGISTRY[canonical_key]

    start_t = time.time()
    try:
        stdout, stderr, exit_code, stdout_truncated, stderr_truncated = _execute_native_subprocess(
            canonical_key=canonical_key,
            code=code_str,
            stdin_str=stdin_str,
            args=sanitized_args,
            timeout_s=ServerConfig.EXECUTOR_TIMEOUT_S
        )

        elapsed_ms = round((time.time() - start_t) * 1000, 2)

        if exit_code == _EXIT_SERVER_BUSY:
            return jsonify({
                "status": "error",
                "error": "Service Unavailable",
                "message": "Server busy: too many concurrent executions. Try again later."
            }), 503

        is_success = exit_code == 0
        output_combined = (stdout + "\n" + stderr) if (stdout and stderr) else (stdout + stderr)
        response_body = {
            "status": "success" if is_success else "error",
            "engine": "native_standalone",
            "language": canonical_key,
            "runtime": spec["language"],
            "filename": f"main{spec['extension']}",
            "output": output_combined,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "execution_time_ms": elapsed_ms,
            "stdin_truncated": stdin_truncated,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "run": {
                "stdout": stdout,
                "stderr": stderr,
                "code": exit_code,
                "signal": None,
                "output": output_combined
            }
        }
        if not is_success:
            response_body["error"] = stderr.strip() or f"Process exited with code {exit_code}"
        return jsonify(response_body), 200

    except Exception:
        logger.exception("Native code execution error")
        return jsonify({"error": "Internal Server Error", "message": "An unexpected server error occurred during execution."}), 500
