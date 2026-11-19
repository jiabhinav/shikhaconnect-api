import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from database.database import initialize_database, test_db_connection
from routers.auth import router as auth_router
from routers.schools import router as school_router
from routers.super_admin import router as super_admin_router
from routers.users import router as user_router
from routers.teachers import router as teacher_router

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
    return HTMLResponse(page.body.decode("utf-8").replace("    presets: [", upload_confirmation + "    presets: [", 1))


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
