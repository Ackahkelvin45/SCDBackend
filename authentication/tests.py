from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase

User = get_user_model()


class UserScopingTests(APITestCase):
    def setUp(self):
        self.a = User.objects.create_user("a@example.com", "passw0rd123", full_name="A")
        self.b = User.objects.create_user("b@example.com", "passw0rd123", full_name="B")
        self.client.force_authenticate(self.a)

    def test_list_shows_only_self(self):
        res = self.client.get("/auth/users/")
        self.assertEqual([u["email"] for u in res.json()], ["a@example.com"])

    def test_cannot_touch_other_user(self):
        url = f"/auth/users/{self.b.pk}/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.patch(url, {"full_name": "hacked"}).status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.b.refresh_from_db()
        self.assertEqual(self.b.full_name, "B")

    def test_can_delete_self(self):
        self.assertEqual(self.client.delete(f"/auth/users/{self.a.pk}/").status_code, 204)

    def test_register_still_public(self):
        self.client.force_authenticate(None)
        res = self.client.post("/auth/users/", {
            "email": "c@example.com", "full_name": "C",
            "password": "Str0ngPassw0rd!", "password2": "Str0ngPassw0rd!",
        })
        self.assertEqual(res.status_code, 201)

    def test_put_self_and_other(self):
        data = {"email": "a@example.com", "full_name": "A2"}
        self.assertEqual(self.client.put(f"/auth/users/{self.a.pk}/", data).status_code, 200)
        self.assertEqual(self.client.put(f"/auth/users/{self.b.pk}/", data).status_code, 404)

    def test_me_returns_self_and_patch_ignores_protected_fields(self):
        self.assertEqual(self.client.get("/auth/users/me/").json()["email"], "a@example.com")
        patient_id = self.a.patient_id
        res = self.client.patch("/auth/users/me/", {
            "full_name": "A2", "is_staff": True, "is_superuser": True,
            "patient_id": "SCD-HACKED", "password": "newpassw0rd",
        })
        self.assertEqual(res.status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.full_name, "A2")
        self.assertFalse(self.a.is_staff)
        self.assertFalse(self.a.is_superuser)
        self.assertEqual(self.a.patient_id, patient_id)
        self.assertTrue(self.a.check_password("passw0rd123"))

    def test_unauthenticated_gets_401(self):
        self.client.force_authenticate(None)
        for url in ["/auth/users/", "/auth/users/me/", f"/auth/users/{self.a.pk}/"]:
            self.assertEqual(self.client.get(url).status_code, 401)


class RegisterTests(APITestCase):
    def setUp(self):
        cache.clear()  # anon throttle state lives in the cache

    def register(self, **extra):
        data = {"email": "c@example.com", "full_name": "C",
                "password": "Str0ngPassw0rd!", "password2": "Str0ngPassw0rd!"}
        data.update(extra)
        return self.client.post("/auth/users/", data)

    def test_register_ignores_privilege_fields(self):
        res = self.register(is_staff=True, is_superuser=True)
        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.json()["patient_id"])
        self.assertNotIn("password", res.json())
        user = User.objects.get(email="c@example.com")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_mismatched_passwords(self):
        self.assertEqual(self.register(password2="Other0ne!!").status_code, 400)

    def test_password_without_digit(self):
        res = self.register(password="NoDigitsHere!", password2="NoDigitsHere!")
        self.assertEqual(res.status_code, 400)
        self.assertIn("password", res.json())


class TokenTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("a@example.com", "passw0rd123", full_name="A")
        res = self.client.post("/auth/login/", {"email": "a@example.com", "password": "passw0rd123"})
        self.access, self.refresh = res.json()["access"], res.json()["refresh"]

    def test_refresh_then_logout_rejects_old_refresh(self):
        res = self.client.post("/auth/refresh/", {"refresh": self.refresh})
        self.assertEqual(res.status_code, 200)
        new_refresh = res.json()["refresh"]
        self.assertEqual(self.client.post("/auth/logout/", {"refresh": new_refresh}).status_code, 205)
        self.assertEqual(self.client.post("/auth/refresh/", {"refresh": new_refresh}).status_code, 401)

    def test_rotated_refresh_cannot_be_reused(self):
        self.assertEqual(self.client.post("/auth/refresh/", {"refresh": self.refresh}).status_code, 200)
        self.assertEqual(self.client.post("/auth/refresh/", {"refresh": self.refresh}).status_code, 401)

    def test_tokens_rejected_after_self_delete(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        self.assertEqual(self.client.delete(f"/auth/users/{self.user.pk}/").status_code, 204)
        self.assertEqual(self.client.get("/auth/users/me/").status_code, 401)
        self.client.credentials()
        self.assertEqual(self.client.post("/auth/refresh/", {"refresh": self.refresh}).status_code, 401)
