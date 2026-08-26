from database.database import SessionLocal
from models.user import UserRole, UserStatus
from routers.users import create_user
from schemas.user import UserCreate


def run_test():
    db = SessionLocal()
    try:
        u = UserCreate(
            name="Script User",
            email="script@example.com",
            mobile="9876543210",
            school_name="Central Academy",
            role=UserRole.SUPER_ADMIN,
            status=UserStatus.ACTIVE,
        )
        user = create_user(u, db=db)
        print("Created:", user.id, user.name, user.email, user.mobile, user.school_name, user.role.value, user.status.value)

        try:
            create_user(u, db=db)
        except Exception as e:
            print("Duplicate error (expected):", type(e), e)
    finally:
        db.close()


if __name__ == '__main__':
    run_test()
