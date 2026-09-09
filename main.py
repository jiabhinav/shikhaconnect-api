import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database.database import Base, engine, sync_missing_columns, test_db_connection
from routers.auth import router as auth_router
from routers.schools import router as school_router
from routers.users import router as user_router

logger = logging.getLogger(__name__)

try:
    Base.metadata.create_all(bind=engine)
    sync_missing_columns()
except Exception as exc:
    logger.warning("Database initialization failed during startup: %s", exc)

app = FastAPI(
    title="School Management API",
    version="1.0.0"
)

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
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(user_router)
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
