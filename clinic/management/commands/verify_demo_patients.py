"""
Play the pilot test for every demo patient through the same API the app calls, then compare with the workbook.

For each DEMO-* patient and each model (as the three APKs do): predict on the latest loaded visit with ?model=<id>,
enter the held-back final visit, read the prediction history, and check probability (as the app prints it),
class, outcome and correctness against demo_patients.json. Everything runs in one transaction that is rolled
back, so the demo patients are left exactly as loaded. Safe on the live server.

  manage.py verify_demo_patients --facility "SickleCare Test Facility"
"""
import json
import math
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from rest_framework.test import APIClient

from authentication.models import Facility
from clinic.models import Patient

from .load_demo_patients import DEFAULT_JSON, LABS


def shown(p):
    """The app's display: percentage to one decimal, rounded half up (JS Math.round)."""
    return math.floor(p * 1000 + 0.5) / 10


class Rollback(Exception):
    pass


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument('--facility', required=True)
        parser.add_argument('--json', default=str(DEFAULT_JSON))

    def handle(self, *args, **o):
        facility = Facility.objects.filter(name=o['facility']).first()
        if facility is None:
            raise CommandError(f"No facility named {o['facility']!r}")
        if not facility.prediction_enabled:
            raise CommandError('Prediction is disabled for this facility; the app would get 403.')
        expected = {p['folder_number']: p for p in json.load(open(o['json']))}
        patients = {p.folder_number: p for p in Patient.objects.filter(facility=facility, folder_number__in=expected)}
        missing = sorted(set(expected) - set(patients))
        if missing:
            raise CommandError(f'Not loaded: {", ".join(missing)}')
        user = patients[min(patients)].created_by

        rows = []
        try:
            with transaction.atomic():
                client = APIClient()
                client.force_authenticate(user)
                for folder in sorted(expected):
                    rows += self.play(client, patients[folder], expected[folder])
                raise Rollback
        except Rollback:
            pass

        cols = ['folder', 'model', 'exp_%', 'app_%', 'exp_class', 'app_class', 'exp_outcome', 'app_outcome', 'result']
        self.stdout.write(' | '.join(cols))
        for r in rows:
            self.stdout.write(' | '.join(str(r[c]) for c in cols))
        bad = [r for r in rows if r['result'] != 'MATCH']
        right = {m['id']: sum(r['app_correct'] is True for r in rows if r['model'] == m['id']) for m in settings.PREDICTION_MODELS}
        self.stdout.write(f'\n{len(rows) - len(bad)} of {len(rows)} rows match the workbook. '
                          f'Models right on the real outcome: {right} (expected 15 of 20 each). Database rolled back.')
        if bad:
            raise CommandError(f'{len(bad)} mismatches')

    def play(self, client, patient, p):
        out = []
        last_loaded, final = p['visits'][-2], p['visits'][-1]
        visit = patient.visits.filter(lab_sample_date=last_loaded['lab_sample_date']).first()
        if visit is None:
            raise CommandError(f'{p["folder_number"]}: visit of {last_loaded["lab_sample_date"]} not loaded')
        preds = {}
        for spec in settings.PREDICTION_MODELS:
            r = client.post(f'/visits/{visit.pk}/predict/?model={spec["id"]}', {}, format='json')
            if r.status_code not in (200, 201):
                raise CommandError(f'{p["folder_number"]} {spec["id"]}: predict returned {r.status_code} {r.content[:300]}')
            preds[spec['id']] = r.json()['results'][0]

        # The clinician enters the held-back final visit from the workbook
        dob = date.fromisoformat(p['date_of_birth'])
        age = (date.fromisoformat(final['lab_sample_date']) - dob).days / 365.25
        body = {'lab_sample_date': final['lab_sample_date'], 'transfused_last_3_months': 'no',
                'confirm_out_of_range': True, 'confirm_duplicate': True,
                **{c: final[c] for c in LABS if final[c] is not None}}
        if p['sex'] == 'female' and 12 <= age <= 50:
            body['pregnancy_status'] = 'not_pregnant'
        r = client.post(f'/patients/{patient.pk}/visits/', body, format='json')
        if r.status_code != 201:
            raise CommandError(f'{p["folder_number"]}: final visit returned {r.status_code} {r.content[:300]}')

        for spec in settings.PREDICTION_MODELS:
            m, e = spec['id'], p['expected'][spec['id']]
            r = client.get(f'/patients/{patient.pk}/predictions/?model={m}')
            run = next(x for x in r.json()['results'] if x['visit_id'] == str(visit.pk))
            res, outcome = run['results'][0], run.get('observed_outcome') or {}
            row = {'folder': p['folder_number'], 'model': m,
                   'exp_%': shown(e['probability']), 'app_%': shown(preds[m]['response_probability']),
                   'exp_class': e['predicted_class'], 'app_class': preds[m]['predicted_class'],
                   'exp_outcome': p['expected']['observed_responder_at_last_visit'], 'app_outcome': outcome.get('meets_criterion'),
                   'exp_correct': e['correct'], 'app_correct': res.get('correct')}
            ok = (row['exp_%'] == row['app_%'] and row['exp_class'] == row['app_class']
                  and row['exp_outcome'] == row['app_outcome'] and row['exp_correct'] == row['app_correct'])
            row['result'] = 'MATCH' if ok else 'MISMATCH'
            out.append(row)
        return out
