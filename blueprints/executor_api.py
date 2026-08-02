import logging
import re
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Blueprint, jsonify, request

from server.auth import require_api_key
from server.config import ServerConfig
from server.rate_limit import apply_rate_limit

executor_bp = Blueprint("executor_api", __name__, url_prefix="/api/v1")
logger = logging.getLogger(__name__)

# Complete Piston Language Registry with Aliases, Extensions & Example Code
PISTON_LANGUAGE_REGISTRY = {
    "python": {
        "language": "python",
        "version": "3.10.0",
        "extension": ".py",
        "aliases": ["py", "python3", "py3"],
        "example": "print('Hello, ToolPix Code Executor!')"
    },
    "javascript": {
        "language": "javascript",
        "version": "18.15.0",
        "extension": ".js",
        "aliases": ["js", "node", "nodejs"],
        "example": "console.log('Hello, ToolPix Code Executor!');"
    },
    "typescript": {
        "language": "typescript",
        "version": "5.0.3",
        "extension": ".ts",
        "aliases": ["ts"],
        "example": "const msg: string = 'Hello, TypeScript!';\nconsole.log(msg);"
    },
    "c": {
        "language": "c",
        "version": "10.2.0",
        "extension": ".c",
        "aliases": ["gcc"],
        "example": '#include <stdio.h>\nint main() {\n    printf("Hello from C!\\n");\n    return 0;\n}'
    },
    "cpp": {
        "language": "c++",
        "version": "10.2.0",
        "extension": ".cpp",
        "aliases": ["c++", "cxx", "g++"],
        "example": '#include <iostream>\nint main() {\n    std::cout << "Hello from C++!" << std::endl;\n    return 0;\n}'
    },
    "java": {
        "language": "java",
        "version": "15.0.2",
        "extension": ".java",
        "aliases": ["java"],
        "example": 'public class Main {\n    public static void main(String[] args) {\n        System.out.println("Hello from Java!");\n    }\n}'
    },
    "csharp": {
        "language": "csharp",
        "version": "6.12.0",
        "extension": ".cs",
        "aliases": ["cs", "c#", "dotnet"],
        "example": 'using System;\nclass Program {\n    static void Main() {\n        Console.WriteLine("Hello from C#!");\n    }\n}'
    },
    "go": {
        "language": "go",
        "version": "1.16.2",
        "extension": ".go",
        "aliases": ["golang"],
        "example": 'package main\nimport "fmt"\nfunc main() {\n    fmt.Println("Hello from Go!")\n}'
    },
    "rust": {
        "language": "rust",
        "version": "1.68.2",
        "extension": ".rs",
        "aliases": ["rs"],
        "example": 'fn main() {\n    println!("Hello from Rust!");\n}'
    },
    "php": {
        "language": "php",
        "version": "8.2.3",
        "extension": ".php",
        "aliases": ["php"],
        "example": '<?php\necho "Hello from PHP!\\n";'
    },
    "ruby": {
        "language": "ruby",
        "version": "3.0.1",
        "extension": ".rb",
        "aliases": ["rb"],
        "example": 'puts "Hello from Ruby!"'
    },
    "swift": {
        "language": "swift",
        "version": "5.3.3",
        "extension": ".swift",
        "aliases": ["swift"],
        "example": 'print("Hello from Swift!")'
    },
    "kotlin": {
        "language": "kotlin",
        "version": "1.4.20",
        "extension": ".kt",
        "aliases": ["kt", "kts"],
        "example": 'fun main() {\n    println("Hello from Kotlin!")\n}'
    },
    "perl": {
        "language": "perl",
        "version": "5.36.0",
        "extension": ".pl",
        "aliases": ["pl"],
        "example": 'print "Hello from Perl!\\n";'
    },
    "bash": {
        "language": "bash",
        "version": "5.2.0",
        "extension": ".sh",
        "aliases": ["sh", "shell", "zsh"],
        "example": 'echo "Hello from Bash!"'
    },
    "sql": {
        "language": "sqlite3",
        "version": "3.36.0",
        "extension": ".sql",
        "aliases": ["sqlite", "sqlite3"],
        "example": 'CREATE TABLE demo (id INT, name TEXT);\nINSERT INTO demo VALUES (1, "ToolPix");\nSELECT * FROM demo;'
    },
    "r": {
        "language": "r",
        "version": "4.1.1",
        "extension": ".r",
        "aliases": ["rscript"],
        "example": 'cat("Hello from R!\\n")'
    },
    "scala": {
        "language": "scala",
        "version": "3.0.0",
        "extension": ".scala",
        "aliases": ["scala"],
        "example": 'object Main extends App {\n  println("Hello from Scala!")\n}'
    },
    "haskell": {
        "language": "haskell",
        "version": "9.0.1",
        "extension": ".hs",
        "aliases": ["hs"],
        "example": 'main = putStrLn "Hello from Haskell!"'
    },
    "lua": {
        "language": "lua",
        "version": "5.4.4",
        "extension": ".lua",
        "aliases": ["lua"],
        "example": 'print("Hello from Lua!")'
    },
    "tcl": {
        "language": "tcl",
        "version": "8.6.12",
        "extension": ".tcl",
        "aliases": ["tcl"],
        "example": 'puts "Hello from Tcl!"'
    }
}

# Build Alias lookup mapping
ALIAS_TO_KEY = {}
for key, info in PISTON_LANGUAGE_REGISTRY.items():
    ALIAS_TO_KEY[key.lower()] = key
    for alias in info.get("aliases", []):
        ALIAS_TO_KEY[alias.lower()] = key


def _create_pooled_session() -> requests.Session:
    """Creates a high-performance HTTP Session with connection pooling and retries."""
    s = requests.Session()
    retries = Retry(
        total=2,
        backoff_factor=0.3,
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(
        pool_connections=50,
        pool_maxsize=100,
        max_retries=retries,
        pool_block=False
    )
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


_session = _create_pooled_session()

# Enhanced Java public class regex pattern
PUBLIC_JAVA_CLASS_PATTERN = re.compile(
    r"(?:public\s+)+(?:final\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE
)


def resolve_language_key(lang_str: str):
    """Resolves language key or alias to canonical registry key."""
    if not lang_str:
        return None
    normalized = lang_str.strip().lower()
    return ALIAS_TO_KEY.get(normalized)


def get_filename_for_code(canonical_key: str, code: str) -> str:
    """Generates proper filename for code file (handles Java public class requirement)."""
    spec = PISTON_LANGUAGE_REGISTRY[canonical_key]
    ext = spec["extension"]

    if canonical_key == "java":
        match = PUBLIC_JAVA_CLASS_PATTERN.search(code)
        if match:
            class_name = match.group(1)
            return f"{class_name}{ext}"

    return f"main{ext}"


def sanitize_args(args_raw) -> list:
    """Sanitizes arguments array to ensure all items are string formatted."""
    if not args_raw:
        return []
    if isinstance(args_raw, (list, tuple)):
        return [str(item) for item in args_raw if item is not None]
    if isinstance(args_raw, str):
        return [args_raw]
    return []


@executor_bp.route("/languages", methods=["GET", "OPTIONS"])
def list_languages():
    """Lists all supported programming languages, aliases, and versions."""
    return jsonify({
        "status": "success",
        "count": len(PISTON_LANGUAGE_REGISTRY),
        "aliases_mapped": len(ALIAS_TO_KEY),
        "languages": PISTON_LANGUAGE_REGISTRY
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

    info = PISTON_LANGUAGE_REGISTRY[key]
    return jsonify({
        "status": "success",
        "canonical_name": key,
        "details": info
    }), 200


@executor_bp.route("/execute", methods=["POST", "OPTIONS"])
@require_api_key
@apply_rate_limit
def execute_code():
    """Executes code snippet in Piston sandbox with auto-alias resolution, Java class detection, and input handling."""
    data = request.get_json(silent=True) or {}
    
    lang_input = (data.get("language") or "").strip()
    code_raw = data.get("code")
    files_input = data.get("files")
    stdin_raw = data.get("stdin") or ""
    args_raw = data.get("args")

    if not lang_input:
        return jsonify({"error": "Bad Request", "message": "Field 'language' is required."}), 400

    canonical_key = resolve_language_key(lang_input)
    if not canonical_key:
        return jsonify({
            "error": "Bad Request",
            "message": f"Unsupported language '{lang_input}'. Call /api/v1/languages for supported runtimes."
        }), 400

    spec = PISTON_LANGUAGE_REGISTRY[canonical_key]

    # Process files array or single code string
    files_to_send = []
    total_code_bytes = 0

    if files_input and isinstance(files_input, list):
        for item in files_input:
            if isinstance(item, dict) and "content" in item:
                fname = item.get("name") or get_filename_for_code(canonical_key, item["content"])
                fcontent = str(item["content"])
                total_code_bytes += len(fcontent.encode("utf-8"))
                files_to_send.append({"name": fname, "content": fcontent})

    if not files_to_send:
        if not code_raw or not str(code_raw).strip():
            return jsonify({"error": "Bad Request", "message": "Field 'code' or non-empty 'files' is required."}), 400
        
        code_str = str(code_raw)
        total_code_bytes = len(code_str.encode("utf-8"))
        fname = get_filename_for_code(canonical_key, code_str)
        files_to_send.append({"name": fname, "content": code_str})

    if total_code_bytes > ServerConfig.EXECUTOR_MAX_CODE_BYTES:
        return jsonify({
            "error": "Payload Too Large",
            "message": f"Total code size ({total_code_bytes} bytes) exceeds maximum limit of {ServerConfig.EXECUTOR_MAX_CODE_BYTES} bytes."
        }), 413

    # Process & truncate STDIN if needed
    stdin_str = str(stdin_raw)
    stdin_bytes = len(stdin_str.encode("utf-8"))
    stdin_truncated = False
    if stdin_bytes > ServerConfig.EXECUTOR_MAX_STDIN_BYTES:
        stdin_str = stdin_str[:ServerConfig.EXECUTOR_MAX_STDIN_BYTES]
        stdin_truncated = True

    sanitized_args = sanitize_args(args_raw)

    payload = {
        "language": spec["language"],
        "version": spec["version"],
        "files": files_to_send,
        "stdin": stdin_str,
        "args": sanitized_args,
        "compile_timeout": 10000,
        "run_timeout": 10000,
    }

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if ServerConfig.PISTON_API_KEY:
        headers["Authorization"] = ServerConfig.PISTON_API_KEY

    start_t = time.time()
    try:
        resp = _session.post(
            ServerConfig.PISTON_EXECUTE_URL,
            json=payload,
            headers=headers,
            timeout=ServerConfig.EXECUTOR_TIMEOUT_S
        )

        elapsed_ms = round((time.time() - start_t) * 1000, 2)

        if resp.status_code != 200:
            logger.error("Upstream executor returned status %d: %s", resp.status_code, resp.text)
            return jsonify({
                "error": "Execution Error",
                "message": f"Upstream Piston API error status ({resp.status_code})",
                "details": resp.text[:500]
            }), 502

        res_json = resp.json()
        run_stage = res_json.get("run") or {}
        compile_stage = res_json.get("compile") or {}

        stdout = run_stage.get("stdout") or ""
        stderr = run_stage.get("stderr") or ""
        output = run_stage.get("output") or (stdout + stderr)
        exit_code = run_stage.get("code", 0)

        # Truncate output if exceeding limits
        output_truncated = False
        if len(output.encode("utf-8")) > ServerConfig.EXECUTOR_MAX_OUTPUT_BYTES:
            output = output[:ServerConfig.EXECUTOR_MAX_OUTPUT_BYTES] + "\n...[Output Truncated]"
            output_truncated = True

        primary_filename = files_to_send[0]["name"] if files_to_send else f"main{spec['extension']}"

        return jsonify({
            "status": "success",
            "language": canonical_key,
            "runtime": spec["language"],
            "version": spec["version"],
            "filename": primary_filename,
            "files_executed": [f["name"] for f in files_to_send],
            "output": output,
            "stdout": stdout,
            "stderr": stderr,
            "compile_output": compile_stage.get("output", ""),
            "exit_code": exit_code,
            "execution_time_ms": elapsed_ms,
            "stdin_truncated": stdin_truncated,
            "output_truncated": output_truncated
        }), 200

    except requests.exceptions.Timeout:
        return jsonify({"error": "Timeout", "message": "Code execution timed out."}), 504
    except Exception as e:
        logger.exception("Error executing code snippet")
        return jsonify({"error": "Internal Server Error", "message": str(e)}), 500
