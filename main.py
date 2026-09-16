import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from database.database import engine, sync_missing_columns, test_db_connection
from routers.auth import router as auth_router
from routers.schools import router as school_router
from routers.super_admin import router as super_admin_router
from routers.users import router as user_router

from database.table_init import ensure_all_tables

logger = logging.getLogger(__name__)

try:
    with engine.begin() as connection:
        ensure_all_tables(connection)
    sync_missing_columns()
except Exception:
    logger.exception("Database initialization failed; database requests will retry table creation")

app = FastAPI(
    title="School Management API",
    version="1.0.0"
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


@app.get("/")
def home():
    return {
        "message": "School Management API is working",
        "database_connected": test_db_connection()
    }


@app.get("/health")
def health_check():
    return {
        "database_connected": test_db_connection()
    }
