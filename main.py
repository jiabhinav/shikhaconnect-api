import json
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from database.database import initialize_database, test_db_connection
from routers.auth import router as auth_router
from routers.student_promotions import StudentPromotionWrite
from routers.schools import router as school_router
from routers.super_admin import router as super_admin_router
from routers.users import router as user_router
from routers.teachers import router as teacher_router
from schemas.enums import StudentStatus

logger = logging.getLogger(__name__)

try:
    initialize_database()
except Exception:
    logger.exception("Database initialization failed; database requests will retry table creation")

app = FastAPI(
    title="School Management API",
    version="1.0.0",
    docs_url=None,
)
uploads_dir = Path(__file__).resolve().parent / "uploads"
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],  # Allows all origins, you can restrict this to specific domains if needed (e.g., ["http://127.0.0.1:5173", "https://yourfrontend.com"])
#     allow_credentials=True,
#     allow_methods=["*"],  # Allows all methods
#     allow_headers=["*"],  # Allows all headers
# )

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:4173",
        "http://localhost:4173",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "https://shikshaconnect.com",
        "https://www.shikshaconnect.com",
        "https://dev.shikshaconnect.com",
        "https://www.school.shikshaconnect.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(user_router)
app.include_router(super_admin_router)
app.include_router(school_router)

app.include_router(teacher_router)


@app.get("/docs", include_in_schema=False)
def swagger_docs(request: Request):
    root_path = request.scope.get("root_path", "").rstrip("/")
    page = get_swagger_ui_html(
        openapi_url=root_path + app.openapi_url,
        title=f"{app.title} - Swagger UI",
        oauth2_redirect_url=root_path + app.swagger_ui_oauth2_redirect_url,
    )
    upload_confirmation = r"""
    requestInterceptor: async (request) => {
        const path = new URL(request.url, window.location.href).pathname.replace(/\/$/, "");
        const uploadPath = "/schools/student_carry_forwards/bulk-upload";
        const isExcelUpload = request.method?.toUpperCase() === "POST"
            && (path.endsWith(uploadPath) || path.endsWith(uploadPath + "/excel"));

        if (isExcelUpload) {
            const filename = request.body?.get?.("file")?.name;
            const selectedFile = filename ? `"${filename}"` : "this Excel file";
            const message = `Upload ${selectedFile}?\n\n`
                + "Amounts in the file will replace existing carry-forward fees for matching students.";
            if (!window.confirm(message)) {
                throw new Error("Upload cancelled.");
            }
        }

        return request;
    },
"""
    status_schema = StudentPromotionWrite.model_json_schema()["properties"]["status"]
    status_dropdown = r"""
<script>
(() => {
    const promotionStatuses = PROMOTION_STATUS_OPTIONS;
    const studentStatuses = STUDENT_STATUS_OPTIONS;
    const swagger = document.getElementById("swagger-ui");
    const selector = 'textarea.body-param__text';
    const readBody = (editor, nested) => {
        try {
            const body = JSON.parse(editor.value);
            const rows = nested ? [body?.student_info] : Array.isArray(body) ? body : [body];
            return rows.every(row => row && typeof row === "object" && !Array.isArray(row))
                ? {body, rows} : null;
        } catch {
            return null;
        }
    };

    const renderEditor = (operation, statuses, nested) => {
        const editor = operation.querySelector(selector);
        if (!editor) return;
        let controls = editor.parentElement.querySelector(".student-status-controls");
        if (!controls) {
            controls = document.createElement("div");
            controls.className = "student-status-controls";
            editor.before(controls);
        }

        const rows = readBody(editor, nested)?.rows ?? [];
        const signature = JSON.stringify(rows.map(row => [row.student_id, row.status]));
        if (controls.dataset.rows === signature) return;
        controls.dataset.rows = signature;
        controls.replaceChildren();

        rows.forEach((row, index) => {
            const label = document.createElement("label");
            label.style.display = "block";
            label.textContent = nested ? "Student status " : `Row ${index + 1} status `;
            const select = document.createElement("select");
            if (nested) {
                select.add(new Option(operation.classList.contains("opblock-put") ? "Keep current" : "Default (active)", ""));
            }
            statuses.forEach(status => select.add(new Option(status, status)));
            select.value = row.status ?? (nested ? "" : PROMOTION_DEFAULT_STATUS);
            select.addEventListener("change", () => {
                const current = readBody(editor, nested);
                if (!current?.rows[index]) return;
                if (select.value) {
                    current.rows[index].status = select.value;
                } else {
                    delete current.rows[index].status;
                }
                // Invoke the native setter so Swagger UI's React editor sees the change.
                const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
                setter.call(editor, JSON.stringify(current.body, null, 2));
                editor.dispatchEvent(new Event("input", {bubbles: true}));
            });
            label.append(select);
            controls.append(label);
        });
    };

    const render = () => {
        for (const path of swagger.querySelectorAll('[data-path="/schools/students/promote"]')) {
            renderEditor(path.closest(".opblock"), promotionStatuses, false);
        }
        for (const path of swagger.querySelectorAll('[data-path="/schools/students"]')) {
            const operation = path.closest(".opblock");
            if (operation.matches(".opblock-post, .opblock-put")) {
                renderEditor(operation, studentStatuses, true);
            }
        }
    };

    swagger.addEventListener("input", render);
    new MutationObserver(render).observe(swagger, {childList: true, subtree: true});
    render();
})();
</script>
""".replace("PROMOTION_STATUS_OPTIONS", json.dumps(status_schema["enum"])).replace(
        "STUDENT_STATUS_OPTIONS", json.dumps([status.value for status in StudentStatus]),
    ).replace("PROMOTION_DEFAULT_STATUS", json.dumps(status_schema["default"]))
    html = page.body.decode("utf-8").replace("    presets: [", upload_confirmation + "    presets: [", 1)
    return HTMLResponse(html.replace("</body>", status_dropdown + "</body>", 1))


@app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
def swagger_oauth2_redirect():
    return get_swagger_ui_oauth2_redirect_html()


@app.get("/")
def home():
    return {
        "message": "School Management API is working",
        "database_connected": test_db_connection()
    }


@app.get("/health")
def health_check():
    try:
        initialize_database()
    except Exception:
        logger.exception("Database schema health check failed")
        return JSONResponse(
            status_code=503,
            content={"database_connected": test_db_connection(), "schema_initialized": False},
        )
    if not test_db_connection():
        return JSONResponse(
            status_code=503,
            content={"database_connected": False, "schema_initialized": False},
        )
    return {"database_connected": True, "schema_initialized": True}
