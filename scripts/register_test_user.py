from database.database import SessionLocal
from models.user import User, UserRole, UserStatus


def main():
    db = SessionLocal()
    try:
        user = User(
            first_name="Test",
            last_name="User",
            email="test@example.com",
            mobile="9876543211",
            school_name="Bright Future School",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(
            "Inserted user:",
            user.id,
            user.first_name,
            user.last_name,
            user.email,
            user.mobile,
            user.school_name,
            user.role.value,
            user.status.value,
        )
    except Exception as e:
        db.rollback()
        print("Error inserting user:", e)
    finally:
        db.close()


if __name__ == "__main__":
    main()
