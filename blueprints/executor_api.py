import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
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

executor_bp = Blueprint("executor_api", __name__, url_prefix="/api/v1")
logger = logging.getLogger(__name__)

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


def _execute_sqlite_natively(sql_code: str):
    """Executes SQLite queries natively using Python's built-in sqlite3 engine."""
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    output_lines = []
    
    try:
        statements = [stmt.strip() for stmt in sql_code.split(";") if stmt.strip()]
        for stmt in statements:
            cursor.execute(stmt)
            if cursor.description:
                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchall()
                output_lines.append(f"Query: {stmt};")
                output_lines.append(" | ".join(columns))
                output_lines.append("-" * 30)
                for row in rows:
                    output_lines.append(" | ".join(str(val) for val in row))
                output_lines.append("")
        
        conn.commit()
        conn.close()
        stdout = "\n".join(output_lines) if output_lines else "Query executed successfully."
        return stdout, "", 0
    except Exception as e:
        conn.close()
        return "", f"SQLite Error: {str(e)}", 1


def _execute_native_subprocess(canonical_key: str, code: str, stdin_str: str, args: list, timeout_s: int):
    """Executes code using native server interpreters and compilers inside a temporary directory sandbox."""
    if canonical_key == "sql":
        return _execute_sqlite_natively(code)

    with tempfile.TemporaryDirectory() as tmpdir:
        spec = NATIVE_LANGUAGE_REGISTRY[canonical_key]
        ext = spec["extension"]
        src_path = os.path.join(tmpdir, f"main{ext}")

        with open(src_path, "w", encoding="utf-8") as f:
            f.write(code)

        compile_cmd = None
        run_cmd = []

        if canonical_key == "python":
            run_cmd = [sys.executable, src_path] + args

        elif canonical_key == "javascript":
            node_path = shutil.which("node") or shutil.which("nodejs")
            if not node_path:
                return "", "JavaScript Runtime Error: Node.js is not installed on server.", 127
            run_cmd = [node_path, src_path] + args

        elif canonical_key == "bash":
            sh_path = shutil.which("bash") or shutil.which("sh")
            run_cmd = [sh_path, src_path] + args

        elif canonical_key == "c":
            gcc_path = shutil.which("gcc") or shutil.which("clang")
            if not gcc_path:
                return "", "C Compiler Error: gcc or clang is not installed on server.", 127
            bin_path = os.path.join(tmpdir, "main.out")
            compile_cmd = [gcc_path, src_path, "-o", bin_path]
            run_cmd = [bin_path] + args

        elif canonical_key == "cpp":
            gpp_path = shutil.which("g++") or shutil.which("clang++")
            if not gpp_path:
                return "", "C++ Compiler Error: g++ or clang++ is not installed on server.", 127
            bin_path = os.path.join(tmpdir, "main.out")
            compile_cmd = [gpp_path, src_path, "-o", bin_path]
            run_cmd = [bin_path] + args

        elif canonical_key == "php":
            php_path = shutil.which("php")
            if not php_path:
                return "", "PHP Error: php CLI is not installed on server.", 127
            run_cmd = [php_path, src_path] + args

        elif canonical_key == "ruby":
            ruby_path = shutil.which("ruby")
            if not ruby_path:
                return "", "Ruby Error: ruby is not installed on server.", 127
            run_cmd = [ruby_path, src_path] + args

        elif canonical_key == "perl":
            perl_path = shutil.which("perl")
            if not perl_path:
                return "", "Perl Error: perl is not installed on server.", 127
            run_cmd = [perl_path, src_path] + args

        # Step 1: Compile if compilation step is required
        compile_output = ""
        if compile_cmd:
            comp_proc = subprocess.run(
                compile_cmd,
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=timeout_s
            )
            compile_output = comp_proc.stdout + comp_proc.stderr
            if comp_proc.returncode != 0:
                return "", f"Compilation Failed:\n{compile_output}", comp_proc.returncode

        # Step 2: Execute runner process
        run_proc = subprocess.run(
            run_cmd,
            cwd=tmpdir,
            input=stdin_str,
            capture_output=True,
            text=True,
            timeout=timeout_s
        )

        return run_proc.stdout, run_proc.stderr, run_proc.returncode


@executor_bp.route("/languages", methods=["GET", "OPTIONS"])
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
@require_api_key
@apply_rate_limit
def execute_code():
    """Executes code natively on the server without external API dependencies."""
    data = request.get_json(silent=True) or {}
    
    lang_input = (data.get("language") or "").strip()
    code_raw = data.get("code")
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

    # Process STDIN
    stdin_str = str(stdin_raw)
    stdin_bytes = len(stdin_str.encode("utf-8"))
    stdin_truncated = False
    if stdin_bytes > ServerConfig.EXECUTOR_MAX_STDIN_BYTES:
        stdin_str = stdin_str[:ServerConfig.EXECUTOR_MAX_STDIN_BYTES]
        stdin_truncated = True

    sanitized_args = sanitize_args(args_raw)
    spec = NATIVE_LANGUAGE_REGISTRY[canonical_key]

    start_t = time.time()
    try:
        stdout, stderr, exit_code = _execute_native_subprocess(
            canonical_key=canonical_key,
            code=code_str,
            stdin_str=stdin_str,
            args=sanitized_args,
            timeout_s=ServerConfig.EXECUTOR_TIMEOUT_S
        )

        elapsed_ms = round((time.time() - start_t) * 1000, 2)
        output = stdout + stderr

        output_truncated = False
        if len(output.encode("utf-8")) > ServerConfig.EXECUTOR_MAX_OUTPUT_BYTES:
            output = output[:ServerConfig.EXECUTOR_MAX_OUTPUT_BYTES] + "\n...[Output Truncated]"
            output_truncated = True

        return jsonify({
            "status": "success",
            "engine": "native_standalone",
            "language": canonical_key,
            "runtime": spec["language"],
            "filename": f"main{spec['extension']}",
            "output": output,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "execution_time_ms": elapsed_ms,
            "stdin_truncated": stdin_truncated,
            "output_truncated": output_truncated
        }), 200

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timeout", "message": f"Code execution timed out after {ServerConfig.EXECUTOR_TIMEOUT_S} seconds."}), 504
    except Exception as e:
        logger.exception("Native code execution error")
        return jsonify({"error": "Internal Server Error", "message": str(e)}), 500
