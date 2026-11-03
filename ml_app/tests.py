from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from visits.models import Visit
from .models import HUBaseline

User = get_user_model()


class PredictTests(APITestCase):
    def setUp(self):
        self.a = User.objects.create_user("a@example.com", "passw0rd123", full_name="A")
        self.b = User.objects.create_user("b@example.com", "passw0rd123", full_name="B")
        self.visit = Visit.objects.create(patient=self.a, visit_date=date(2026, 6, 1))
        HUBaseline.objects.create(patient=self.a, baseline_hgb="7.5", hu_start_date=date(2026, 1, 1))

    def test_other_users_visit_404(self):
        self.client.force_authenticate(self.b)
        res = self.client.post("/ml/predict-hu-response/", {"visit_id": str(self.visit.id)})
        self.assertEqual(res.status_code, 404)

    def test_predict_error_is_not_leaked(self):
        self.client.force_authenticate(self.a)
        with patch("ml_app.predictor.predict", side_effect=RuntimeError("secret path /srv/model.pkl")), \
                self.assertLogs("ml_app.views", level="ERROR"):
            res = self.client.post("/ml/predict-hu-response/", {"visit_id": str(self.visit.id)})
        self.assertEqual(res.status_code, 500)
        self.assertNotIn("secret path", res.content.decode())
