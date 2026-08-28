from fastapi import FastAPI

from database.database import Base, engine, sync_missing_columns, test_db_connection
from routers.auth import router as auth_router
from routers.schools import router as school_router
from routers.users import router as user_router

Base.metadata.create_all(bind=engine)
sync_missing_columns()

app = FastAPI(
    title="School Management API",
    version="1.0.0"
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