# ToolPix Dedicated Code Executor Backend Server (Render Deployment)

This is a dedicated, production-ready Code Execution Engine service designed for continuous deployment on [Render](https://render.com). It provides a secure API endpoint (`/api/v1/execute`) that allows your frontend websites to compile and run code in 20+ programming languages securely.

---

## Key Features

1. **Dedicated Code Execution**:
   - Compiles and executes code snippets in 20+ supported programming languages (Python, JavaScript, TypeScript, C, C++, Java, Rust, Go, PHP, Ruby, Bash, SQL, Swift, Kotlin, Perl, R, Scala, Haskell, Lua, Tcl).
   - Handles standard input (`stdin`), command-line arguments (`args`), compilation output, stdout, stderr, and exit codes.
   - Enforces configurable output byte limits (`EXECUTOR_MAX_OUTPUT_BYTES`) and code size limits (`EXECUTOR_MAX_CODE_BYTES`).

2. **API Secret Authentication**:
   - Every execution request must include a secret key in:
     - Header: `X-API-Key: <your-secret-key>`
     - Header: `Authorization: Bearer <your-secret-key>`
   - Supports multiple secret keys (`SERVER_API_KEYS="secret1,secret2"`) for multi-website integration.

3. **CORS Multi-Website Support**:
   - `ALLOWED_ORIGINS` allows specified domains or wildcard `*`.
   - Handles `OPTIONS` preflight requests seamlessly.

4. **Defined Endpoints**:
   - `GET /` & `GET /healthz` - Public Render health checks.
   - `GET /api/v1/status` - Executor engine status and configuration limits.
   - `GET /api/v1/languages` - List of supported languages and runtimes.
   - `POST /api/v1/auth/verify` - Validate API key secret.
   - `POST /api/v1/execute` - Execute code snippet.

---

## API Request Format

### Request (`POST /api/v1/execute`)

Headers:
```http
Content-Type: application/json
X-API-Key: your-secret-api-key-here
```

Body:
```json
{
  "language": "python",
  "code": "print('Hello from Render Code Executor!')\nfor i in range(3):\n    print(f'Count: {i}')",
  "stdin": "",
  "args": []
}
```

### Response (`200 OK`)
```json
{
  "status": "success",
  "language": "python",
  "runtime": "python",
  "version": "3.10.0",
  "output": "Hello from Render Code Executor!\nCount: 0\nCount: 1\nCount: 2\n",
  "stdout": "Hello from Render Code Executor!\nCount: 0\nCount: 1\nCount: 2\n",
  "stderr": "",
  "compile_output": "",
  "exit_code": 0
}
```

---

## Deploying to Render

1. Log into [Render Dashboard](https://dashboard.render.com).
2. Click **New +** -> **Web Service**.
3. Select your GitHub repository.
4. Set configuration:
   - **Root Directory**: `server`
   - **Environment**: `Python`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4`
   - **Health Check Path**: `/healthz`
5. Add Environment Variables:
   - `SERVER_API_KEYS` = `your-secret-key-1,your-secret-key-2`
   - `ALLOWED_ORIGINS` = `https://site1.com,https://site2.com`
   - `PISTON_API_URL` = `https://emkc.org/api/v2/piston/execute`

---

## Frontend Integration Example (JavaScript)

```javascript
async function executeCodeOnRender(language, code, stdin = "") {
  const RENDER_EXECUTOR_URL = "https://your-executor-app.onrender.com";
  const API_SECRET = "your-secret-key-1";

  const response = await fetch(`${RENDER_EXECUTOR_URL}/api/v1/execute`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_SECRET,
    },
    body: JSON.stringify({ language, code, stdin }),
  });

  if (!response.ok) {
    const err = await response.json();
    throw new Error(err.message || "Execution failed");
  }

  return await response.json();
}

// Example usage:
executeCodeOnRender("python", "print(10 + 20)")
  .then(result => console.log("Output:", result.output))
  .catch(err => console.error("Error:", err));
```
