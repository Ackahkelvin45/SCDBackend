from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import MedicationLog

User = get_user_model()


class MedicationLogTests(APITestCase):
    def setUp(self):
        self.a = User.objects.create_user("a@example.com", "passw0rd123", full_name="A")
        self.b = User.objects.create_user("b@example.com", "passw0rd123", full_name="B")

    def log(self, user, **data):
        self.client.force_authenticate(user)
        return self.client.post("/medications/log/", {"date": "2026-01-01", "hu_taken": True, "dose_mg": 500, **data})

    def test_bad_date(self):
        self.assertEqual(self.log(self.a, date="not-a-date").status_code, 400)

    def test_other_users_log_not_overwritten(self):
        self.assertEqual(self.log(self.a).status_code, 201)
        self.assertEqual(self.log(self.b, dose_mg=1000).status_code, 201)
        self.assertEqual(MedicationLog.objects.get(user=self.a).dose_mg, 500)

    def test_second_post_same_day_updates(self):
        self.assertEqual(self.log(self.a).status_code, 201)
        res = self.log(self.a, dose_mg=750)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(MedicationLog.objects.get(user=self.a).dose_mg, 750)
