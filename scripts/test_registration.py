from database.database import SessionLocal
from models.user import UserRole, UserStatus
from routers.users import create_user
from schemas.user import UserCreate


def run_test():
    db = SessionLocal()
    try:
        u = UserCreate(
            first_name="Script",
            last_name="User",
            email="script@example.com",
            mobile="9876543210",
            school_name="Central Academy",
            role=UserRole.SUPER_ADMIN,
            status=UserStatus.ACTIVE,
        )
        user = create_user(u, db=db)
        print("Created:", user["data"]["id"], user["data"]["first_name"], user["data"]["last_name"], user["data"]["email"], user["data"]["mobile"], user["data"]["school_name"], user["data"]["role"], user["data"]["status"])

        try:
            create_user(u, db=db)
        except Exception as e:
            print("Duplicate error (expected):", type(e), e)
    finally:
        db.close()


if __name__ == '__main__':
    run_test()
