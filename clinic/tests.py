from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from authentication.models import Facility
from .models import Patient, PredictionRun, SafetyFlag

User = get_user_model()
TODAY = timezone.localdate()


def ago(days):
    return (TODAY - timedelta(days=days)).isoformat()


def fake_predict(model_id, features):
    p = {'xgboost': 0.72, 'svm': 0.45, 'logistic_reg': 0.60}[model_id]
    t = {'xgboost': 0.5721, 'svm': 0.3094, 'logistic_reg': 0.6776}[model_id]
    return {'response_probability': p, 'predicted_class': 'responder' if p >= t else 'non_responder',
            'threshold': t, 'model_hash': f'sha256:{model_id}'}


class ClinicTestCase(APITestCase):
    def setUp(self):
        cache.clear()
        self.facility = Facility.objects.create(name='Komfo Anokye', prediction_enabled=True)
        self.other_facility = Facility.objects.create(name='Korle Bu')
        self.doc = User.objects.create_user('a@example.com', 'passw0rd123', full_name='Dr A', facility=self.facility)
        self.outsider = User.objects.create_user('b@example.com', 'passw0rd123', full_name='Dr B', facility=self.other_facility)
        self.client.force_authenticate(self.doc)

    def make_patient(self, **extra):
        data = {'folder_number': 'F-001', 'full_name': 'Ama Mensah', 'date_of_birth': '2012-05-01', 'sex': 'male',
                'genotype': 'HbSS', 'data_consent': True, 'research_consent': True, 'anc_flag_floor': 2.0}
        data.update(extra)
        res = self.client.post('/patients/', data, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()

    def set_baseline(self, pid, hgb=7.0, start=200, source='measured_pre_hu', reason=None):
        body = {'baseline_hgb': hgb, 'baseline_source': source, 'hu_start_date': ago(start)}
        if reason:
            body['reason'] = reason
        return self.client.put(f'/patients/{pid}/hu-baseline/', body, format='json')

    def add_visit(self, pid, days_ago=10, expect=201, **labs):
        data = {'lab_sample_date': ago(days_ago), 'transfused_last_3_months': 'no', 'hgb': 8.0, 'anc': 3.0,
                'platelet_count': 250, 'wbc': 8.0, 'rbc': 2.8, 'mcv': 85, 'hct': 24, 'arc': 150}
        data.update(labs)
        res = self.client.post(f'/patients/{pid}/visits/', data, format='json')
        self.assertEqual(res.status_code, expect, res.content)
        return res.json()

    def ready_patient(self):
        """Patient with a baseline 200 days ago and one eligible visit."""
        p = self.make_patient()
        self.set_baseline(p['id'])
        v = self.add_visit(p['id'])
        return p, v


class LookupTests(ClinicTestCase):
    def test_facilities_public(self):
        self.client.force_authenticate(None)
        res = self.client.get('/facilities/')
        self.assertEqual(res.status_code, 200)
        names = [f['name'] for f in res.json()]
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(names), 78 + 2)  # seeded list + this test's two
        self.assertIn('Tamale Teaching Hospital', names)
        self.assertEqual(set(res.json()[0]), {'id', 'name', 'region'})

    def test_clinicians_only_my_facility(self):
        self.assertEqual(self.client.get('/clinicians/').json(), [{'id': str(self.doc.pk), 'full_name': 'Dr A'}])

    def test_config_serves_ranges_thresholds_and_models(self):
        body = self.client.get('/config/').json()
        self.assertEqual(body['lab_ranges']['hgb'], {'min': 3, 'max': 16, 'unit': 'g/dL'})
        self.assertEqual(body['flag_thresholds']['anc_critical'], 1.0)
        models = body['prediction_models']
        self.assertEqual([m['id'] for m in models], ['xgboost', 'svm', 'logistic_reg'])
        self.assertEqual([m['is_default'] for m in models], [True, False, False])
        self.assertEqual([m['threshold'] for m in models], [0.5721, 0.3094, 0.6776])
        self.assertTrue(models[0]['model_hash'].startswith('sha256:'))


class PatientTests(ClinicTestCase):
    def test_create_returns_detail_with_summary(self):
        p = self.make_patient(primary_clinician_id=str(self.doc.pk))
        for key in ('id', 'age_years', 'hu', 'latest_visit', 'active_flags', 'latest_prediction',
                    'prediction_eligibility', 'analytics', 'overdue'):
            self.assertIn(key, p)
        self.assertEqual(p['primary_clinician'], {'id': str(self.doc.pk), 'full_name': 'Dr A'})
        self.assertEqual(p['anc_flag_floor'], 2.0)
        self.assertIsNone(p['hu'])

    def test_consent_and_anc_floor_rules(self):
        base = {'folder_number': 'X', 'full_name': 'Y', 'date_of_birth': '2010-01-01', 'sex': 'male', 'genotype': 'HbSS',
                'research_consent': False}
        res = self.client.post('/patients/', {**base, 'data_consent': False}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('data_consent', res.json()['fields'])
        res = self.client.post('/patients/', {**base, 'data_consent': True, 'anc_flag_floor': 1.25}, format='json')
        self.assertIn('anc_flag_floor_reason', res.json()['fields'])
        res = self.client.post('/patients/', {**base, 'data_consent': True, 'primary_clinician_id': str(self.outsider.pk)},
                               format='json')
        self.assertIn('primary_clinician_id', res.json()['fields'])

    def test_duplicates(self):
        self.make_patient()
        base = {'date_of_birth': '2012-05-01', 'sex': 'male', 'genotype': 'HbSS', 'data_consent': True, 'research_consent': False}
        res = self.client.post('/patients/', {**base, 'folder_number': 'F-001', 'full_name': 'Kofi'}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'folder_number_taken'))
        res = self.client.post('/patients/', {**base, 'folder_number': 'F-002', 'full_name': 'ama mensah'}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'possible_duplicate'))
        self.assertEqual(res.json()['matches'][0]['folder_number'], 'F-001')
        self.make_patient(folder_number='F-002', confirm_duplicate=True)
        # The same folder number at another facility is fine
        self.client.force_authenticate(self.outsider)
        self.make_patient()

    def test_other_facility_gets_404_everywhere(self):
        p, v = self.ready_patient()
        flag = self.add_visit(p['id'], days_ago=5, anc=0.5)['flags'][0]
        self.client.force_authenticate(self.outsider)
        for url in [f'/patients/{p["id"]}/', f'/patients/{p["id"]}/visits/', f'/patients/{p["id"]}/hu-baseline/',
                    f'/patients/{p["id"]}/trends/', f'/patients/{p["id"]}/predictions/', f'/visits/{v["id"]}/',
                    f'/visits/{v["id"]}/prediction-eligibility/']:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 404, url)
            self.assertEqual(res.json()['code'], 'not_found')
        self.assertEqual(self.client.patch(f'/patients/{p["id"]}/', {'full_name': 'x'}, format='json').status_code, 404)
        self.assertEqual(self.client.patch(f'/visits/{v["id"]}/', {'hgb': 9, 'edit_reason': 'x'}, format='json').status_code, 404)
        self.assertEqual(self.client.post(f'/flags/{flag["id"]}/acknowledge/', {'action': 'refer'}).status_code, 404)
        self.assertEqual(self.client.post(f'/visits/{v["id"]}/predict/', {}).status_code, 404)
        self.assertEqual(self.client.get('/patients/').json()['count'], 0)
        self.assertEqual(self.client.get('/flags/?acknowledged=false').json()['count'], 0)

    def test_list_filters_and_shape(self):
        a = self.make_patient()
        self.set_baseline(a['id'])
        self.add_visit(a['id'], anc=0.5)
        self.make_patient(folder_number='F-009', full_name='Kwame Boateng', date_of_birth='2015-01-01')
        res = self.client.get('/patients/').json()
        self.assertEqual(set(res), {'count', 'next', 'previous', 'results'})
        self.assertEqual(res['count'], 2)
        row = res['results'][0]
        self.assertEqual(row['full_name'], 'Ama Mensah')
        self.assertEqual((row['active_flag_count'], row['max_flag_severity'], row['baseline_missing']), (1, 'critical', False))
        self.assertEqual(self.client.get('/patients/?search=F-009').json()['results'][0]['full_name'], 'Kwame Boateng')
        self.assertEqual(self.client.get('/patients/?has_flags=true').json()['count'], 1)
        self.assertEqual(self.client.get('/patients/?baseline_missing=true').json()['results'][0]['full_name'], 'Kwame Boateng')
        self.assertEqual(self.client.get('/patients/?ordering=-last_visit_date').json()['results'][0]['full_name'], 'Ama Mensah')

    def test_summary_and_analytics(self):
        p = self.make_patient()
        self.set_baseline(p['id'], hgb=7.0)
        self.add_visit(p['id'], days_ago=300, hgb=7.5, mcv=80, dose_mg=500, weight_kg=25, adherence='good')
        self.add_visit(p['id'], days_ago=100, hgb=8.5, mcv=90, dose_mg=750, weight_kg=25, adherence='poor',
                       pain_episodes=2, next_appointment_date=ago(5))
        s = self.client.get(f'/patients/{p["id"]}/').json()
        self.assertEqual(s['hu']['days_on_hu'], 200)
        self.assertEqual(s['observed_response'], {'delta_hgb': 1.5, 'meets_criterion': True})
        self.assertTrue(s['overdue'])
        a = s['analytics']
        self.assertEqual(a['visit_count'], 2)
        self.assertEqual((a['hgb']['min'], a['hgb']['max'], a['hgb']['mean']), (7.5, 8.5, 8.0))
        # The 300-days-ago visit is before HU start + 90 days, so only one visit is assessed
        self.assertEqual((a['hgb']['visits_assessed'], a['hgb']['visits_meeting_criterion']), (1, 1))
        self.assertEqual(a['mcv'], {'first': 80.0, 'latest': 90.0, 'change': 10.0})
        self.assertEqual(a['dose'], {'current_mg': 750, 'current_mg_per_kg': 30.0, 'changes': 1})
        self.assertEqual(a['adherence'], {'good': 1, 'partial': 0, 'poor': 1})
        self.assertEqual(a['pain_episodes'], {'total': 2, 'per_visit_avg': 2.0})
        six = self.client.get(f'/patients/{p["id"]}/?period=6m').json()['analytics']
        self.assertEqual(six['visit_count'], 1)

    def test_app_form_payload_with_nulls(self):
        # Exactly what components/PatientForm.tsx sends for a new patient with the default 2.0 floor
        body = {'folder_number': 'F-100', 'full_name': 'Kojo', 'date_of_birth': '2012-01-01', 'sex': 'male',
                'genotype': 'HbSS', 'phone_number': '', 'next_of_kin_name': '', 'next_of_kin_phone': '',
                'primary_clinician_id': None, 'data_consent': True, 'data_consent_date': ago(0),
                'research_consent': False, 'research_consent_date': None, 'anc_flag_floor': 2.0,
                'anc_flag_floor_reason': None}
        res = self.client.post('/patients/', body, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['anc_flag_floor_reason'], '')
        pid = res.json()['id']
        low = self.client.patch(f'/patients/{pid}/', {**body, 'anc_flag_floor': 1.25, 'anc_flag_floor_reason': 'Low ANC'},
                                format='json')
        self.assertEqual(low.status_code, 200, low.content)
        back = self.client.patch(f'/patients/{pid}/', body, format='json')
        self.assertEqual(back.status_code, 200, back.content)
        self.assertEqual((back.json()['anc_flag_floor'], back.json()['anc_flag_floor_reason']), (2.0, ''))

    def test_data_consent_required_on_create(self):
        body = {'folder_number': 'X', 'full_name': 'Y', 'date_of_birth': '2010-01-01', 'sex': 'male', 'genotype': 'HbSS'}
        res = self.client.post('/patients/', body, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('data_consent', res.json()['fields'])
        p = self.make_patient()
        self.assertEqual(self.client.patch(f'/patients/{p["id"]}/', {'full_name': 'Z'}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(f'/patients/{p["id"]}/', {'data_consent': False}, format='json').status_code, 400)

    def test_folder_number_race_is_409(self):
        p = self.make_patient()
        other = self.make_patient(folder_number='F-002', full_name='Other')
        # Simulate a concurrent insert slipping past the exists() pre-checks
        with mock.patch('django.db.models.QuerySet.exists', return_value=False):
            res = self.client.post('/patients/', {'folder_number': 'F-001', 'full_name': 'Kofi', 'date_of_birth': '2012-05-01',
                                                  'sex': 'male', 'genotype': 'HbSS', 'data_consent': True}, format='json')
            self.assertEqual((res.status_code, res.json()['code']), (409, 'folder_number_taken'))
            res = self.client.patch(f'/patients/{other["id"]}/', {'folder_number': p['folder_number']}, format='json')
            self.assertEqual((res.status_code, res.json()['code']), (409, 'folder_number_taken'))

    def test_last_visit_ordering_puts_no_visit_patients_last(self):
        self.make_patient(full_name='Aaron')  # no visits
        b = self.make_patient(folder_number='F-002', full_name='Zed')
        self.add_visit(b['id'])
        names = [r['full_name'] for r in self.client.get('/patients/?ordering=-last_visit_date').json()['results']]
        self.assertEqual(names, ['Zed', 'Aaron'])


class BaselineTests(ClinicTestCase):
    def test_versioning(self):
        p = self.make_patient()
        self.assertEqual(self.client.get(f'/patients/{p["id"]}/hu-baseline/').status_code, 404)
        first = self.set_baseline(p['id'], hgb=7.2).json()
        self.assertEqual(first['baseline_hgb'], 7.2)
        self.assertIsInstance(first['version_id'], int)
        self.assertIn('reason', self.set_baseline(p['id'], hgb=7.0).json()['fields'])
        self.assertEqual(self.set_baseline(p['id'], hgb=15.5, reason='x').status_code, 400)
        self.assertEqual(self.set_baseline(p['id'], hgb=7.0, source='from_folder', reason='Found folder value').status_code, 200)
        history = self.client.get(f'/patients/{p["id"]}/hu-baseline/history/').json()
        self.assertEqual([h['baseline_hgb'] for h in history], [7.0, 7.2])
        self.assertEqual(history[0]['reason'], 'Found folder value')
        self.assertEqual(history[0]['changed_by'], {'id': str(self.doc.pk), 'full_name': 'Dr A'})


class VisitTests(ClinicTestCase):
    def test_create_evaluates_flags_and_deltas(self):
        p = self.make_patient()
        self.set_baseline(p['id'])
        self.add_visit(p['id'], days_ago=40, hgb=8.0)
        v = self.add_visit(p['id'], days_ago=10, hgb=4.0, anc=0.8, platelet_count=60, arc=50)
        self.assertEqual({f['rule'] for f in v['flags']}, {'anc_critical', 'hgb_critical', 'platelets_low', 'arc_low', 'hgb_drop'})
        self.assertEqual(v['deltas']['hgb'], -4.0)
        self.assertEqual(v['observed_response'], {'delta_hgb': -3.0, 'meets_criterion': False})

    def test_anc_floor_override(self):
        p = self.make_patient(anc_flag_floor=1.25, anc_flag_floor_reason='Low baseline ANC')
        self.assertEqual(self.add_visit(p['id'], anc=1.5)['flags'], [])
        p2 = self.make_patient(folder_number='F-002', full_name='Other')
        self.assertEqual([f['rule'] for f in self.add_visit(p2['id'], anc=1.5)['flags']], ['anc_low'])

    def test_validation(self):
        p = self.make_patient()
        res = self.client.post(f'/patients/{p["id"]}/visits/', {'lab_sample_date': ago(1), 'transfused_last_3_months': 'no',
                                                                 'hgb': 25, 'anc': 1500}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'out_of_range'))
        self.assertEqual(set(res.json()['fields']), {'hgb', 'anc'})
        self.add_visit(p['id'], days_ago=1)
        body = {'lab_sample_date': ago(1), 'transfused_last_3_months': 'no'}
        dup = self.client.post(f'/patients/{p["id"]}/visits/', body, format='json')
        self.assertEqual((dup.status_code, dup.json()['code']), (409, 'duplicate_visit_date'))
        self.add_visit(p['id'], days_ago=1, confirm_duplicate=True)
        future = self.client.post(f'/patients/{p["id"]}/visits/', {**body, 'lab_sample_date': ago(-1)}, format='json')
        self.assertIn('lab_sample_date', future.json()['fields'])
        self.add_visit(p['id'], days_ago=3, hgb=17, confirm_out_of_range=True)

    def test_pregnancy_status_required_for_female_12_to_50(self):
        p = self.make_patient(sex='female', date_of_birth='2000-01-01')
        res = self.client.post(f'/patients/{p["id"]}/visits/', {'lab_sample_date': ago(1), 'transfused_last_3_months': 'no'},
                               format='json')
        self.assertIn('pregnancy_status', res.json()['fields'])
        v = self.add_visit(p['id'], pregnancy_status='pregnant')
        self.assertIn('pregnant', [f['rule'] for f in v['flags']])

    def test_edit_needs_reason_and_recomputes_flags(self):
        p = self.make_patient()
        v = self.add_visit(p['id'], anc=0.8, platelet_count=60)
        by_rule = {f['rule']: f for f in v['flags']}
        self.client.post(f'/flags/{by_rule["platelets_low"]["id"]}/acknowledge/', {'action': 'repeat_fbc'})
        self.assertIn('edit_reason', self.client.patch(f'/visits/{v["id"]}/', {'anc': 3.0}, format='json').json()['fields'])
        res = self.client.patch(f'/visits/{v["id"]}/', {'anc': 3.0, 'platelet_count': 250, 'edit_reason': 'Typo'}, format='json')
        self.assertEqual(res.status_code, 200)
        flags = {f['rule']: f for f in res.json()['flags']}
        self.assertNotIn('anc_critical', flags)  # unacknowledged → removed
        self.assertEqual(flags['platelets_low']['status'], 'resolved_by_edit')  # acknowledged → kept, resolved
        self.assertTrue(res.json()['edited'])
        latest = self.client.get(f'/visits/{v["id"]}/').json()
        self.assertEqual(latest['anc'], 3.0)

    def test_history_rows(self):
        p = self.make_patient()
        self.set_baseline(p['id'], hgb=7.0)
        self.add_visit(p['id'], days_ago=60, hgb=7.5, dose_mg=500, weight_kg=25)
        self.add_visit(p['id'], days_ago=30, hgb=8.0)
        self.add_visit(p['id'], days_ago=10, hgb=8.6, dose_mg=750, weight_kg=27)
        res = self.client.get(f'/patients/{p["id"]}/visits/').json()
        self.assertEqual(res['count'], 3)
        newest = res['results'][0]
        self.assertEqual(newest['delta_hgb_vs_previous'], 0.6)
        self.assertEqual(newest['delta_hgb_vs_baseline'], 1.6)
        self.assertEqual((newest['dose_changed'], newest['previous_dose_mg'], newest['dose_mg_per_kg']), (True, 500, 27.8))
        self.assertEqual(newest['created_by'], {'id': str(self.doc.pk), 'full_name': 'Dr A'})
        self.assertFalse(res['results'][1]['dose_changed'])
        self.assertEqual(len(self.client.get(f'/patients/{p["id"]}/visits/?limit=1').json()['results']), 1)

    def test_trends(self):
        p = self.make_patient()
        self.set_baseline(p['id'], hgb=7.0)
        self.add_visit(p['id'], days_ago=60, hgb=7.5, dose_mg=500)
        self.add_visit(p['id'], days_ago=10, hgb=8.6, dose_mg=750, anc=0.8, transfused_last_3_months='yes')
        res = self.client.get(f'/patients/{p["id"]}/trends/?labs=hgb,anc,bogus').json()
        self.assertEqual(set(res['series']), {'hgb', 'anc'})
        self.assertEqual([pt['value'] for pt in res['series']['hgb']], [7.5, 8.6])
        self.assertEqual(res['reference_lines']['hgb'], [{'label': 'Baseline', 'value': 7.0}, {'label': 'Target (+1)', 'value': 8.0}])
        self.assertEqual({e['type'] for e in res['events']}, {'dose_change', 'transfusion', 'flag'})
        recent = self.client.get(f'/patients/{p["id"]}/trends/?labs=hgb&from={ago(30)}').json()
        self.assertEqual(len(recent['series']['hgb']), 1)

    def rules_of(self, visit_id):
        return {f['rule'] for f in self.client.get(f'/visits/{visit_id}/').json()['flags']}

    def test_backdated_and_edited_visits_refresh_next_visit_hgb_drop(self):
        p = self.make_patient()
        self.add_visit(p['id'], days_ago=60, hgb=8.0)
        late = self.add_visit(p['id'], days_ago=10, hgb=7.8)
        self.assertNotIn('hgb_drop', self.rules_of(late['id']))
        # A back-dated visit between the two becomes the later visit's previous visit
        mid = self.add_visit(p['id'], days_ago=30, hgb=10.0)
        self.assertIn('hgb_drop', self.rules_of(late['id']))
        # Editing the middle visit's Hb clears it again
        self.client.patch(f'/visits/{mid["id"]}/', {'hgb': 8.0, 'edit_reason': 'Typo'}, format='json')
        self.assertNotIn('hgb_drop', self.rules_of(late['id']))
        # Moving the middle visit after the late one: late is compared with the first visit again, mid with late
        self.client.patch(f'/visits/{mid["id"]}/', {'hgb': 10.0, 'edit_reason': 'Typo'}, format='json')
        self.client.patch(f'/visits/{mid["id"]}/', {'lab_sample_date': ago(5), 'hgb': 5.0, 'edit_reason': 'Wrong date'},
                          format='json')
        self.assertNotIn('hgb_drop', self.rules_of(late['id']))
        self.assertIn('hgb_drop', self.rules_of(mid['id']))

    def test_flag_id_is_stable_across_edits(self):
        p = self.make_patient()
        flag = self.add_visit(p['id'], anc=0.5)['flags'][0]
        v = SafetyFlag.objects.get(pk=flag['id']).visit_id
        res = self.client.patch(f'/visits/{v}/', {'anc': 0.6, 'edit_reason': 'Typo'}, format='json').json()
        self.assertEqual([(f['id'], f['message'][:8]) for f in res['flags']], [(flag['id'], 'ANC 0.6:')])
        self.assertEqual(self.client.post(f'/flags/{flag["id"]}/acknowledge/', {'action': 'hold_hu'}).status_code, 204)

    def test_history_query_count_does_not_grow_per_visit(self):
        p = self.make_patient()
        self.set_baseline(p['id'])
        for d in (60, 50):
            self.add_visit(p['id'], days_ago=d)

        def count():
            with CaptureQueriesContext(connection) as q:
                self.client.get(f'/patients/{p["id"]}/visits/')
            return len(q)

        two = count()
        for d in (40, 30, 20, 10):
            self.add_visit(p['id'], days_ago=d)
        self.assertEqual(count(), two)


class FlagTests(ClinicTestCase):
    def test_today_list_and_acknowledge(self):
        p = self.make_patient()
        warn = self.add_visit(p['id'], days_ago=20, platelet_count=60)['flags'][0]
        crit = self.add_visit(p['id'], days_ago=10, anc=0.5)['flags'][0]
        res = self.client.get('/flags/?acknowledged=false').json()
        self.assertEqual([f['id'] for f in res['results']], [crit['id'], warn['id']])  # critical first
        self.assertEqual(set(res['results'][0]), {'id', 'rule', 'severity', 'message', 'patient', 'visit_id', 'created_at'})
        self.assertEqual(self.client.post(f'/flags/{crit["id"]}/acknowledge/', {'action': 'bogus'}).status_code, 400)
        self.assertEqual(self.client.post(f'/flags/{crit["id"]}/acknowledge/', {'action': 'hold_hu', 'note': 'Held'}).status_code, 204)
        self.assertEqual(self.client.post(f'/flags/{crit["id"]}/acknowledge/', {'action': 'hold_hu'}).status_code, 409)
        self.assertEqual(self.client.get('/flags/?acknowledged=false').json()['count'], 1)
        flag = SafetyFlag.objects.get(pk=crit['id'])
        self.assertEqual((flag.action, flag.note, flag.acknowledged_by), ('hold_hu', 'Held', self.doc))

    def test_archived_patients_and_resolved_flags_are_not_counted(self):
        p = self.make_patient()
        v = self.add_visit(p['id'], days_ago=20, platelet_count=60)
        self.client.post(f'/flags/{v["flags"][0]["id"]}/acknowledge/', {'action': 'repeat_fbc'})
        self.client.patch(f'/visits/{v["id"]}/', {'platelet_count': 250, 'edit_reason': 'Typo'}, format='json')
        detail = self.client.get(f'/patients/{p["id"]}/').json()
        self.assertEqual(detail['analytics']['toxicity']['flags_total'], 0)
        self.assertEqual(self.client.get(f'/patients/{p["id"]}/trends/').json()['events'], [])
        self.add_visit(p['id'], days_ago=10, anc=0.5)
        self.assertEqual(self.client.get('/flags/').json()['count'], 1)
        self.client.patch(f'/patients/{p["id"]}/', {'archived': True}, format='json')
        self.assertEqual(self.client.get('/flags/').json()['count'], 0)


class EligibilityTests(ClinicTestCase):
    def codes(self, visit_id):
        return [r['code'] for r in self.client.get(f'/visits/{visit_id}/prediction-eligibility/').json()['reasons']]

    def test_eligible_visit(self):
        p, v = self.ready_patient()
        e = self.client.get(f'/visits/{v["id"]}/prediction-eligibility/').json()
        self.assertEqual(e, {'eligible': True, 'reasons': [], 'reliability_note': None, 'imputed_lab_count': 0})

    def test_blocking_reasons(self):
        p = self.make_patient(research_consent=False, genotype='unknown')
        self.set_baseline(p['id'], start=61)
        v = self.add_visit(p['id'], days_ago=1, anc=None, transfused_last_3_months='unknown')
        res = self.client.get(f'/visits/{v["id"]}/prediction-eligibility/').json()
        self.assertEqual([r['code'] for r in res['reasons']],
                         ['no_research_consent', 'days_on_hu', 'core_labs_missing', 'transfused', 'genotype_unsupported'])
        days = next(r for r in res['reasons'] if r['code'] == 'days_on_hu')
        self.assertEqual(days['eligible_from'], ago(61 - 90))
        self.assertEqual(res['imputed_lab_count'], 1)

    def test_baseline_feature_and_latest_visit_rules(self):
        p = self.make_patient()
        v = self.add_visit(p['id'], days_ago=20)
        self.assertIn('baseline_missing', self.codes(v['id']))
        self.set_baseline(p['id'], source='unknown')
        self.assertIn('baseline_unknown', self.codes(v['id']))
        self.add_visit(p['id'], days_ago=5)
        self.assertIn('not_latest_visit', self.codes(v['id']))
        self.facility.prediction_enabled = False
        self.facility.save()
        self.assertIn('feature_disabled', self.codes(v['id']))

    def test_reliability_notes(self):
        p = self.make_patient(genotype='HbSC')
        self.set_baseline(p['id'])
        v = self.add_visit(p['id'])
        self.assertEqual(self.client.get(f'/visits/{v["id"]}/prediction-eligibility/').json()['reliability_note'], 'lower_reliability')
        old = self.make_patient(folder_number='F-2', full_name='Old', date_of_birth='1960-01-01')
        self.set_baseline(old['id'])
        v2 = self.add_visit(old['id'])
        self.assertEqual(self.client.get(f'/visits/{v2["id"]}/prediction-eligibility/').json()['reliability_note'], 'outside_training_age')


@mock.patch('ml_app.predictor.predict_one', side_effect=fake_predict)
class PredictTests(ClinicTestCase):
    def test_runs_all_models(self, _):
        p, v = self.ready_patient()
        res = self.client.post(f'/visits/{v["id"]}/predict/', {}, format='json')
        self.assertEqual(res.status_code, 201)
        run = res.json()
        self.assertEqual([r['model'] for r in run['results']], ['xgboost', 'svm', 'logistic_reg'])
        self.assertEqual([r['predicted_class'] for r in run['results']], ['responder', 'responder', 'non_responder'])
        self.assertEqual(run['agreement'], {'responder_votes': 2, 'of': 3, 'all_agree': False})
        self.assertEqual(run['status'], 'current')
        self.assertIsNone(run['observed_outcome'])
        self.assertEqual(run['observed_response'], {'delta_hgb': 1.0, 'meets_criterion': True})
        # Asking again returns the same run instead of a duplicate
        again = self.client.post(f'/visits/{v["id"]}/predict/', {}, format='json')
        self.assertEqual((again.status_code, again.json()['run_id']), (200, run['run_id']))
        self.assertEqual(self.client.get(f'/visits/{v["id"]}/').json()['prediction']['run_id'], run['run_id'])
        self.assertEqual(self.client.get(f'/patients/{p["id"]}/').json()['latest_prediction']['run_id'], run['run_id'])
        row = self.client.get(f'/patients/{p["id"]}/visits/').json()['results'][0]['prediction']
        self.assertEqual(row, {'predicted_class': 'responder', 'response_probability': 0.72, 'status': 'current', 'outcome': None})

    def test_disabled_and_ineligible(self, _):
        p, v = self.ready_patient()
        self.facility.prediction_enabled = False
        self.facility.save()
        res = self.client.post(f'/visits/{v["id"]}/predict/', {})
        self.assertEqual((res.status_code, res.json()['code']), (403, 'prediction_disabled'))
        self.facility.prediction_enabled = True
        self.facility.save()
        no_anc = self.add_visit(p['id'], days_ago=2, anc=None)
        res = self.client.post(f'/visits/{no_anc["id"]}/predict/', {})
        self.assertEqual((res.status_code, res.json()['code']), (409, 'not_eligible'))
        self.assertEqual([r['code'] for r in res.json()['reasons']], ['core_labs_missing'])
        self.assertEqual(self.client.post(f'/visits/{no_anc["id"]}/predict/', {'override_reason': 'Lab down'}).status_code, 201)

    def test_partial_failure_then_retry_fills_same_run(self, predict_one):
        p, v = self.ready_patient()

        def svm_down(model_id, features):
            if model_id == 'svm':
                raise RuntimeError('secret internal detail')
            return fake_predict(model_id, features)

        predict_one.side_effect = svm_down
        first = self.client.post(f'/visits/{v["id"]}/predict/', {}).json()
        self.assertEqual(first['results'][1]['error']['code'], 'model_unavailable')
        self.assertNotIn('secret', str(first))
        self.assertEqual(first['agreement']['of'], 2)
        predict_one.side_effect = fake_predict
        retry = self.client.post(f'/visits/{v["id"]}/predict/', {}).json()
        self.assertEqual(retry['run_id'], first['run_id'])
        self.assertNotIn('error', retry['results'][1])

    def test_all_models_down_is_503_and_leaves_no_run(self, predict_one):
        p, v = self.ready_patient()
        predict_one.side_effect = RuntimeError('boom')
        res = self.client.post(f'/visits/{v["id"]}/predict/', {})
        self.assertEqual((res.status_code, res.json()['code']), (503, 'prediction_unavailable'))
        self.assertFalse(PredictionRun.objects.exists())

    def test_outcome_check_and_outdating(self, _):
        p, v = self.ready_patient()  # baseline 7.0, visit Hb 8.0
        run_id = self.client.post(f'/visits/{v["id"]}/predict/', {}).json()['run_id']
        self.add_visit(p['id'], days_ago=2, hgb=8.4)  # next visit: +1.4 over baseline → confirmed
        run = self.client.get(f'/patients/{p["id"]}/predictions/').json()['results'][0]
        self.assertEqual(run['run_id'], run_id)
        self.assertEqual((run['observed_outcome']['meets_criterion'], run['observed_outcome']['delta_hgb']), (True, 1.4))
        self.assertEqual([r['correct'] for r in run['results']], [True, True, False])
        row = self.client.get(f'/patients/{p["id"]}/visits/').json()['results'][1]
        self.assertEqual(row['prediction']['outcome'], 'confirmed')
        # Editing the predicted visit outdates its run
        self.client.patch(f'/visits/{v["id"]}/', {'hgb': 8.1, 'edit_reason': 'Typo'}, format='json')
        self.assertEqual(PredictionRun.objects.get(pk=run_id).status, 'outdated')

    def test_outcome_uses_the_runs_original_baseline(self, _):
        p, v = self.ready_patient()  # baseline 7.0
        run_id = self.client.post(f'/visits/{v["id"]}/predict/', {}).json()['run_id']
        self.set_baseline(p['id'], hgb=7.6, reason='Corrected')
        self.add_visit(p['id'], days_ago=2, hgb=8.4)  # +1.4 over the run's 7.0, only +0.8 over the new 7.6
        run = self.client.get(f'/patients/{p["id"]}/predictions/').json()['results'][0]
        self.assertEqual(run['run_id'], run_id)
        self.assertEqual((run['observed_outcome']['delta_hgb'], run['observed_outcome']['meets_criterion']), (1.4, True))

    def test_non_object_body_is_not_a_500(self, _):
        p, v = self.ready_patient()
        res = self.client.post(f'/visits/{v["id"]}/predict/', [1, 2], format='json')
        self.assertEqual(res.status_code, 201)

    def test_transfused_next_visit_is_not_assessable(self, _):
        p, v = self.ready_patient()
        self.client.post(f'/visits/{v["id"]}/predict/', {})
        self.add_visit(p['id'], days_ago=2, hgb=9.0, transfused_last_3_months='yes')
        run = self.client.get(f'/patients/{p["id"]}/predictions/').json()['results'][0]
        self.assertFalse(run['observed_outcome']['assessable'])
        self.assertEqual([r['correct'] for r in run['results']], [None, None, None])

    def test_baseline_and_demographic_changes_outdate_runs(self, _):
        p, v = self.ready_patient()
        self.client.post(f'/visits/{v["id"]}/predict/', {})
        self.client.patch(f'/patients/{p["id"]}/', {'phone_number': '024'}, format='json')
        self.assertEqual(PredictionRun.objects.get().status, 'current')
        self.client.patch(f'/patients/{p["id"]}/', {'genotype': 'HbSC'}, format='json')
        self.assertEqual(PredictionRun.objects.get().status, 'outdated')
        new = self.client.post(f'/visits/{v["id"]}/predict/', {}).json()
        self.set_baseline(p['id'], hgb=7.5, reason='Corrected')
        self.assertEqual(PredictionRun.objects.get(pk=new['run_id']).status, 'outdated')


class RealModelTests(ClinicTestCase):
    def test_real_pipelines_predict(self):
        """Loads the deployed pickles end to end (needs the pinned scikit-learn and xgboost)."""
        p, v = self.ready_patient()
        run = self.client.post(f'/visits/{v["id"]}/predict/', {}).json()
        self.assertEqual(len(run['results']), 3)
        for r in run['results']:
            self.assertNotIn('error', r, r)
            self.assertTrue(0 <= r['response_probability'] <= 1)
