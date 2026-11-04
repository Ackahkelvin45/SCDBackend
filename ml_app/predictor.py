"""
Loads the trained pipelines listed in settings.PREDICTION_MODELS and builds the feature vector
from a Visit + Patient + HU baseline.

Feature order (must match training):
  age, gender_encoded, hb_genotype_encoded, baseline_hgb,
  prev_hgb, prev_anc, prev_arc, prev_platelet_count,
  prev_wbc, prev_rbc, prev_mcv, prev_hct, prev_days_on_hu

Each pipeline has its own median imputer, so missing labs are filled the same way as in training.
"""
import hashlib
from pathlib import Path

import joblib
import numpy as np
from django.conf import settings

BASE_DIR = Path(__file__).resolve().parent.parent
_OUTPUTS = BASE_DIR / 'ML' / 'outputs'

_models = {}
_hashes = {}
_thresholds = None

GENDER_MAP = {
    'male': 0.0,
    'female': 1.0,
}

# Training vocabulary was ss/sc/sbo/dont_know → 0–3. HbSβ+ never appeared in training and shares
# the "unknown" code. [CONFIRM with the ML owner]; other/unknown genotypes are blocked by eligibility.
GENOTYPE_MAP = {
    'HbSS': 0.0,
    'HbSC': 1.0,
    'HbSβ0': 2.0,
    'HbSβ+': 3.0,
}

LAB_FIELDS = ['hgb', 'anc', 'arc', 'platelet_count', 'wbc', 'rbc', 'mcv', 'hct']


def _spec(model_id):
    return next(m for m in settings.PREDICTION_MODELS if m['id'] == model_id)


def model_hash(model_id):
    if model_id not in _hashes:
        digest = hashlib.sha256((_OUTPUTS / _spec(model_id)['file']).read_bytes()).hexdigest()
        _hashes[model_id] = f'sha256:{digest}'
    return _hashes[model_id]


def threshold(model_id):
    global _thresholds
    if _thresholds is None:
        _thresholds = joblib.load(_OUTPUTS / 'best_thresholds.pkl')
    return float(_thresholds[_spec(model_id)['threshold_key']]['Best F1'])


def _model(model_id):
    if model_id not in _models:
        _models[model_id] = joblib.load(_OUTPUTS / _spec(model_id)['file'])
    return _models[model_id]


def _to_float(value):
    return np.nan if value is None else float(value)


def build_feature_vector(visit, baseline):
    patient = visit.patient
    lab_date = visit.lab_sample_date
    age = (lab_date - patient.date_of_birth).days / 365.25
    return np.array([[
        age,
        GENDER_MAP.get(patient.sex, np.nan),
        GENOTYPE_MAP.get(patient.genotype, np.nan),
        _to_float(baseline.baseline_hgb),
        _to_float(visit.hgb),
        _to_float(visit.anc),
        _to_float(visit.arc),
        _to_float(visit.platelet_count),
        _to_float(visit.wbc),
        _to_float(visit.rbc),
        _to_float(visit.mcv),
        _to_float(visit.hct),
        float((lab_date - baseline.hu_start_date).days),
    ]], dtype=float)


def predict_one(model_id, features):
    """Probability of response for one model. Raises on any load/inference failure; the caller handles it."""
    probability = float(_model(model_id).predict_proba(features)[0][1])
    cut = threshold(model_id)
    return {
        'response_probability': round(probability, 4),
        'predicted_class': 'responder' if probability >= cut else 'non_responder',
        'threshold': round(cut, 4),
        'model_hash': model_hash(model_id),
    }
