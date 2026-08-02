# ToolPix Standalone Code Executor Backend Server (100% Native & Free)

This is a standalone, high-performance **Native Code Execution Engine** designed for continuous deployment on [Render](https://render.com). It runs 100% natively on your server with **zero external API dependencies (No Piston API key required, 100% free forever)**.

---

## Key Features

1. **100% Native & Self-Contained Engine**:
   - Compiles and runs code snippets natively on the server (Python, JavaScript/Node.js, Bash, C, C++, PHP, Ruby, Perl, SQLite) inside isolated temporary sandboxes.
   - Zero external third-party API dependencies (No Piston API keys or paid external limits!).
   - In-memory SQLite execution engine built-in.

2. **API Secret Authentication**:
   - Every request must include a valid key in:
     - Header: `X-API-Key: <your-secret-key>`
     - Header: `Authorization: Bearer <your-secret-key>`

3. **CORS Multi-Website Support**:
   - Automatically permits requests from `toolpix.pythonanywhere.com` and all subdomains of `uthakkan.in` (`*.uthakkan.in`).

4. **Defined Endpoints**:
   - `GET /` & `GET /healthz` - Liveness health checks.
   - `GET /readyz` - Server readiness probe.
   - `GET /api/v1/status` - Executor engine status and configuration limits.
   - `GET /api/v1/languages` - Supported programming runtimes.
   - `POST /api/v1/execute` - Execute code snippet natively.

---

## API Usage Example (JavaScript)

```javascript
async function executeCodeOnRender(language, code, stdin = "") {
  const RENDER_URL = "https://executor-server.onrender.com";
  const API_SECRET = "your-secret-key-here";

  const response = await fetch(`${RENDER_URL}/api/v1/execute`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_SECRET,
    },
    body: JSON.stringify({ language, code, stdin }),
  });

  return await response.json();
}

// Example Python execution:
executeCodeOnRender("py", "print(100 + 200)")
  .then(res => console.log("Result:", res.stdout))
  .catch(err => console.error("Error:", err));
```
