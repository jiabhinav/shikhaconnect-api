import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.database import Base
from database.table_init import register_models
from dependencies.db import get_db_session
from dependencies.auth import get_current_user
from models.user import LoginUser, User, UserAddress, UserRole
from routers.users import router
from utils.passwords import verify_password


class LoginUserTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        register_models()
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db_session] = lambda: self.db
        self.actor = SimpleNamespace(id=999, role=UserRole.SUPER_ADMIN)
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def test_registration_stores_credentials_in_login_user(self):
        payload = dict(first_name="Test", last_name="User", email="test@example.com",
                       mobile="1234567890", designation="Principal")
        response = self.client.post("/users/", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        profile = self.db.get(User, response.json()["data"]["id"])
        account = profile.login_user
        self.assertEqual(account.email, payload["email"])
        self.assertTrue(verify_password(payload["mobile"], account.password))
        self.assertEqual(profile.designation, "Principal")
        self.assertNotIn("password", User.__table__.columns)
        self.assertNotIn("email", User.__table__.columns)
        self.assertNotIn("password", response.json()["data"])
        self.assertEqual(self.client.post("/users/", json=payload).status_code, 400)
        self.assertEqual(self.db.query(LoginUser).count(), 1)
        self.assertEqual(self.db.query(User).count(), 1)

    def test_account_without_profile_reserves_identity(self):
        self.db.add(User(first_name="Other", last_name="Role",
                              email="other@example.com", mobile="999", password="hash"))
        self.db.commit()
        response = self.client.post("/users/", json=dict(
            first_name="Test", last_name="User", email="other@example.com", mobile="888"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.db.query(User).count(), 1)

    def test_get_uses_foreign_key_and_preserves_flat_response(self):
        # Account IDs need not match profile IDs.
        self.db.add(LoginUser(id=50, first_name="Other", last_name="Role",
                              email="other@example.com", mobile="999", password="hash"))
        self.db.commit()
        created = self.client.post("/users/", json=dict(
            first_name="Test", middle_name="Middle", last_name="User",
            email="test@example.com", mobile="1234567890",
            designation="Principal", city="Delhi", role="Sub Admin",
        ))
        self.assertEqual(created.status_code, 200, created.text)
        expected = created.json()["data"]
        profile = self.db.get(User, expected["id"])
        self.db.expire_all()

        single = self.client.get(f"/users/{profile.id}")
        self.assertEqual(single.status_code, 200, single.text)
        self.assertEqual(single.json(), {
            "status": "success", "message": "User fetched successfully",
            "data": {**expected, "schools": []},
        })
        listing = self.client.get("/users/")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.json(), {
            "status": "success", "message": "Users fetched successfully",
            "data": [{**expected, "schools": []}],
        })
        self.assertEqual(self.client.get("/users/99999").status_code, 404)

    def test_get_empty_user_list(self):
        response = self.client.get("/users/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], [])

    def _create_profile(self):
        self.db.add(User(id=50, first_name="Other", last_name="Role",
                              email="other@example.com", mobile="999", password="hash"))
        self.db.commit()
        response = self.client.post("/users/", json=dict(
            first_name="Test", last_name="User", email="test@example.com",
            mobile="1234567890", designation="Principal",
        ))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["id"]

    def test_update_changes_account_and_profile(self):
        user_id = self._create_profile()
        response = self.client.put(f"/users/{user_id}", json=dict(
            first_name="Updated", email="updated@example.com", mobile="777",
            password="new-secret", role="Sub Admin", city="Delhi",
        ))
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        profile = self.db.get(User, user_id)
        self.assertEqual(profile.first_name, "Updated")
        self.assertEqual(profile.email, "updated@example.com")
        self.assertEqual(profile.mobile, "777")
        self.assertEqual(profile.role, UserRole.SUB_ADMIN)
        self.assertTrue(verify_password("new-secret", profile.password))
        self.assertEqual(profile.city, "Delhi")
        self.assertEqual(profile.address.city, "Delhi")
        self.assertNotIn("city", User.__table__.columns)
        self.assertEqual({**response.json()["data"], "schools": []}, self.client.get(f"/users/{user_id}").json()["data"])
        self.assertNotIn("password", response.json()["data"])

    def test_update_conflict_keeps_both_records_unchanged(self):
        user_id = self._create_profile()
        for duplicate in ({"email": "other@example.com"}, {"mobile": "999"}):
            response = self.client.put(f"/users/{user_id}", json={
                **duplicate, "first_name": "Changed", "city": "Changed",
            })
            self.assertEqual(response.status_code, 400, response.text)
            self.db.expire_all()
            profile = self.db.get(User, user_id)
            self.assertEqual(profile.first_name, "Test")
            self.assertIsNone(profile.city)

    def test_delete_removes_only_linked_account(self):
        user_id = self._create_profile()
        account_id = self.db.get(User, user_id).login_user_id
        response = self.client.delete(f"/users/{user_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"status": "success", "message": "User deleted successfully"})
        self.db.expire_all()
        self.assertIsNone(self.db.get(User, user_id))
        self.assertEqual(self.db.query(UserAddress).filter_by(login_user_id=account_id).count(), 0)
        self.assertIsNotNone(self.db.get(User, 50))

    def test_update_and_delete_require_ownership_or_super_admin(self):
        user_id = self._create_profile()
        self.actor.role = UserRole.ADMIN
        self.assertEqual(self.client.put(f"/users/{user_id}", json={"city": "Delhi"}).status_code, 403)
        self.assertEqual(self.client.delete(f"/users/{user_id}").status_code, 403)
        self.assertIsNotNone(self.db.get(User, user_id))
        self.assertEqual(self.client.put("/users/99999", json={"city": "Delhi"}).status_code, 404)
        self.assertEqual(self.client.delete("/users/99999").status_code, 404)

    def test_update_address_preserves_omitted_fields_and_allows_null(self):
        user_id = self._create_profile()
        response = self.client.put(f"/users/{user_id}", json={
            "line_1": "First street", "city": "Delhi", "pin_code": "110001",
        })
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.put(f"/users/{user_id}", json={"city": "Mumbai", "line_1": None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["city"], "Mumbai")
        self.assertEqual(response.json()["data"]["pin_code"], "110001")
        self.assertIsNone(response.json()["data"]["line_1"])
        self.db.expire_all()
        account_id = self.db.get(User, user_id).login_user_id
        address = self.db.query(UserAddress).filter_by(login_user_id=account_id).one()
        self.assertEqual(address.city, "Mumbai")
        self.assertIsNone(address.line_1)
