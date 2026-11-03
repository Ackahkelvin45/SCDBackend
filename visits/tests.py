from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import Visit

User = get_user_model()


class VisitTests(APITestCase):
    def setUp(self):
        self.a = User.objects.create_user("a@example.com", "passw0rd123", full_name="A")
        self.b = User.objects.create_user("b@example.com", "passw0rd123", full_name="B")
        self.visit = Visit.objects.create(patient=self.a, visit_date="2026-01-01")
        self.client.force_authenticate(self.b)

    def test_other_users_visit_is_hidden(self):
        self.assertEqual(self.client.get(f"/visits/{self.visit.id}/").status_code, 404)
        self.assertEqual(self.client.get("/visits/").json()["count"], 0)

    def test_negative_offset(self):
        self.assertEqual(self.client.get("/visits/?offset=-1").status_code, 400)
        self.assertEqual(self.client.get("/visits/?limit=-1").status_code, 400)

    def test_trends_days_capped(self):
        res = self.client.get("/visits/trends/?days=100000")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["days"]), 365)
