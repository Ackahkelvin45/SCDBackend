"""
Load the pilot demo patients (ML/outputs/demo_patients.json, made by ML/scripts/select_demo_patients.py) into
one facility, with every HU visit except the last. The clinician then predicts on the latest visit in the app,
enters the held-back final visit from the sheet (demo_final_visits.csv) and sees the outcome confirmed or not.

  manage.py load_demo_patients --facility "SickleCare Test Facility" --clinician doctor@example.com
  manage.py load_demo_patients --facility "SickleCare Test Facility" --delete
"""
import csv
import json
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from authentication.models import Facility
from clinic.models import HUBaseline, Patient, Visit
from clinic.views import save_visit, stamp

DEFAULT_JSON = Path(settings.BASE_DIR) / 'ML' / 'outputs' / 'demo_patients.json'
LABS = ['hgb', 'anc', 'arc', 'platelet_count', 'wbc', 'rbc', 'mcv', 'hct']


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument('--facility', required=True, help='Facility name the demo patients belong to')
        parser.add_argument('--clinician', help='Email of the clinician recorded as creator (default: first in facility)')
        parser.add_argument('--json', default=str(DEFAULT_JSON))
        parser.add_argument('--keep-last', action='store_true', help='Load the final visit too (no held-back outcome)')
        parser.add_argument('--delete', action='store_true', help='Remove the DEMO-* patients of the facility instead')

    def handle(self, *args, **o):
        facility = Facility.objects.filter(name=o['facility']).first()
        if facility is None:
            raise CommandError(f"No facility named {o['facility']!r}")
        demo = Patient.objects.filter(facility=facility, folder_number__startswith='DEMO-')
        if o['delete']:
            n = demo.count()
            demo.delete()
            self.stdout.write(f'deleted {n} demo patients from {facility.name}')
            return
        if demo.exists():
            raise CommandError('Demo patients already loaded here. Run with --delete first.')
        users = get_user_model().objects.filter(facility=facility, is_active=True)
        user = users.filter(email__iexact=o['clinician']).first() if o['clinician'] else users.first()
        if user is None:
            raise CommandError('No active clinician in that facility to record as creator.')

        patients = json.load(open(o['json']))
        sheet = []
        with transaction.atomic():
            for p in patients:
                patient = Patient(facility=facility, folder_number=p['folder_number'], full_name=p['full_name'],
                                  date_of_birth=date.fromisoformat(p['date_of_birth']), sex=p['sex'],
                                  genotype=p['genotype'], data_consent=True, data_consent_date=date.today(),
                                  research_consent=True, research_consent_date=date.today(),
                                  created_by=user, updated_by=user, primary_clinician=user)
                stamp(patient, user, 'Pilot demo patient (held-out test set)')
                patient.save()
                baseline = HUBaseline(patient=patient, baseline_hgb=p['baseline_hgb'], baseline_source='from_folder',
                                      hu_start_date=date.fromisoformat(p['hu_start_date']), updated_by=user)
                stamp(baseline, user, 'Pilot demo baseline')
                baseline.save()
                visits = p['visits'] if o['keep_last'] else p['visits'][:-1]
                for v in visits:
                    self.add_visit(patient, user, v, p)
                if not o['keep_last']:
                    last = p['visits'][-1]
                    sheet.append({'folder_number': p['folder_number'], 'name': patient.full_name,
                                  'lab_sample_date': last['lab_sample_date'], **{c: last[c] for c in LABS},
                                  'observed_responder': p['expected']['observed_responder_at_last_visit'],
                                  **{f"{m}_probability": p['expected'][m]['probability'] for m in p['expected'] if isinstance(p['expected'][m], dict)}})
        if sheet:
            out = Path(o['json']).with_name('demo_final_visits.csv')
            with open(out, 'w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=list(sheet[0]))
                w.writeheader()
                w.writerows(sheet)
            self.stdout.write(f'final-visit sheet: {out}')
        self.stdout.write(f'loaded {len(patients)} demo patients into {facility.name} as {user.email}')

    @staticmethod
    def add_visit(patient, user, v, p):
        female_12_50 = patient.sex == 'female' and 12 <= (date.fromisoformat(v['lab_sample_date']) - patient.date_of_birth).days / 365.25 <= 50
        visit = Visit(patient=patient, lab_sample_date=date.fromisoformat(v['lab_sample_date']),
                      transfused_last_3_months='no', pregnancy_status='not_pregnant' if female_12_50 else None,
                      note='Pilot demo visit from the de-identified study dataset', created_by=user,
                      **{c: v[c] for c in LABS})
        save_visit(visit, user)
