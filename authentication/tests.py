from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from .models import Facility

User = get_user_model()
PASSWORD = "passw0rd123"


class AuthTests(APITestCase):
    def setUp(self):
        cache.clear()  # anon throttle state lives in the cache
        self.facility = Facility.objects.create(name="Komfo Anokye", region="Ashanti", prediction_enabled=True)
        self.user = User.objects.create_user("doc@example.com", PASSWORD, full_name="Dr A", facility=self.facility,
                                             license_number="MDC/1", phone_number="0240000000")

    def signup(self, **extra):
        data = {"full_name": "Dr B", "email": "b@example.com", "phone_number": "0241111111", "license_number": "MDC/2",
                "facility_id": str(self.facility.pk), "password": "Str0ngPassw0rd!", "password2": "Str0ngPassw0rd!"}
        data.update(extra)
        return self.client.post("/auth/users/", data)

    def login(self, email="doc@example.com", password=PASSWORD):
        return self.client.post("/auth/login/", {"email": email, "password": password})

    def test_signup_returns_clinician_and_can_log_in(self):
        res = self.signup(is_staff=True, genotype="HbSS")
        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertEqual(body["status"], "active")
        self.assertEqual(body["facility"], {"id": str(self.facility.pk), "name": "Komfo Anokye", "region": "Ashanti",
                                            "prediction_enabled": True})
        self.assertNotIn("password", body)
        self.assertFalse(User.objects.get(email="b@example.com").is_staff)
        self.assertEqual(self.login("b@example.com", "Str0ngPassw0rd!").status_code, 200)

    def test_signup_errors_use_one_shape(self):
        res = self.signup(password2="Different1!", facility_id="00000000-0000-0000-0000-000000000000")
        self.assertEqual(res.status_code, 400)
        body = res.json()
        self.assertEqual(body["code"], "invalid")
        self.assertIn("facility_id", body["fields"])
        self.assertIsInstance(body["fields"]["facility_id"], list)

    def test_signup_rejects_duplicate_email_any_case_and_weak_password(self):
        self.assertIn("email", self.signup(email="DOC@example.com").json()["fields"])
        self.assertIn("password", self.signup(password="NoDigitsHere!", password2="NoDigitsHere!").json()["fields"])

    def test_login_includes_user_and_email_is_case_insensitive(self):
        res = self.login("Doc@Example.com")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.json()), {"access", "refresh", "user"})
        self.assertEqual(res.json()["user"]["license_number"], "MDC/1")

    def test_wrong_password_is_401_with_code(self):
        res = self.login(password="wrong")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(set(res.json()), {"detail", "code", "fields"})

    def test_deactivated_account(self):
        refresh = self.login().json()["refresh"]
        self.user.is_active = False
        self.user.save()
        res = self.login()
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "account_deactivated")
        self.assertEqual(self.client.post("/auth/refresh/", {"refresh": refresh}).status_code, 401)
        # A wrong password doesn't reveal that the account exists
        self.assertEqual(self.login(password="wrong").status_code, 401)

    def test_me_get_and_patch_only_name_and_phone(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get("/auth/users/me/").json()["email"], "doc@example.com")
        res = self.client.patch("/auth/users/me/", {"full_name": "Dr A2", "phone_number": "0249999999",
                                                    "license_number": "HACK", "is_staff": True, "email": "x@x.com"})
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual((self.user.full_name, self.user.phone_number), ("Dr A2", "0249999999"))
        self.assertEqual((self.user.license_number, self.user.is_staff, self.user.email), ("MDC/1", False, "doc@example.com"))

    def test_password_change(self):
        self.client.force_authenticate(self.user)
        bad = self.client.post("/auth/password/change/", {"old_password": "nope", "new_password": "NewPassw0rd!",
                                                          "new_password2": "NewPassw0rd!"})
        self.assertIn("old_password", bad.json()["fields"])
        ok = self.client.post("/auth/password/change/", {"old_password": PASSWORD, "new_password": "NewPassw0rd!",
                                                         "new_password2": "NewPassw0rd!"})
        self.assertEqual(ok.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewPassw0rd!"))

    def test_password_change_ends_other_sessions(self):
        refresh = self.login().json()["refresh"]
        self.client.force_authenticate(self.user)
        res = self.client.post("/auth/password/change/", {"old_password": PASSWORD, "new_password": "NewPassw0rd!",
                                                          "new_password2": "NewPassw0rd!"})
        self.assertEqual(res.json(), {"detail": "Password changed."})
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post("/auth/refresh/", {"refresh": refresh}).status_code, 401)

    def test_non_object_body_is_400(self):
        for url in ["/auth/login/", "/auth/logout/"]:
            res = self.client.post(url, ["x"], format="json")
            self.assertEqual(res.status_code, 400, url)
            self.assertEqual(set(res.json()), {"detail", "code", "fields"})

    def test_refresh_rotation_and_logout(self):
        refresh = self.login().json()["refresh"]
        new = self.client.post("/auth/refresh/", {"refresh": refresh}).json()["refresh"]
        self.assertEqual(self.client.post("/auth/refresh/", {"refresh": refresh}).status_code, 401)
        self.assertEqual(self.client.post("/auth/logout/", {"refresh": new}).status_code, 205)
        self.assertEqual(self.client.post("/auth/refresh/", {"refresh": new}).status_code, 401)

    @override_settings(STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                                 "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
    def test_password_reset_page_exists_and_sends_email(self):
        self.assertEqual(self.client.get("/accounts/password_reset/").status_code, 200)
        self.client.post("/accounts/password_reset/", {"email": "doc@example.com"})
        self.assertEqual(len(mail.outbox), 1)

    def test_unauthenticated_requests_are_401(self):
        for url in ["/auth/users/me/", "/patients/", "/flags/", "/config/", "/clinicians/"]:
            self.assertEqual(self.client.get(url).status_code, 401, url)
