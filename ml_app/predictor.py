"""
Loads the trained XGBoost pipeline and constructs the feature vector from a Visit + patient record.

Feature order (must match training):
  age, gender_encoded, hb_genotype_encoded, baseline_hgb,
  prev_hgb, prev_anc, prev_arc, prev_platelet_count,
  prev_wbc, prev_rbc, prev_mcv, prev_hct, prev_days_on_hu
"""
from pathlib import Path
import numpy as np
import joblib

BASE_DIR = Path(__file__).resolve().parent.parent

_MODEL_PATH = BASE_DIR / 'ML' / 'outputs' / 'xgboost.pkl'
_THRESHOLD_PATH = BASE_DIR / 'ML' / 'outputs' / 'best_thresholds.pkl'

_model = None
_thresholds = None


def _load_artifacts():
    global _model, _thresholds
    if _model is None:
        _model = joblib.load(_MODEL_PATH)
    if _thresholds is None:
        _thresholds = joblib.load(_THRESHOLD_PATH)


GENDER_MAP = {
    'male': 0.0,
    'female': 1.0,
}

GENOTYPE_MAP = {
    'HbSS': 0.0,
    'HbSC': 1.0,
    'HbSβ0': 2.0,
    'HbSβ+': 3.0,
    'not_sure': 3.0,
}


def _compute_age(date_of_birth, reference_date):
    if date_of_birth is None:
        return np.nan
    delta = reference_date - date_of_birth
    return delta.days / 365.25


def _encode_gender(gender):
    return GENDER_MAP.get(gender, np.nan)


def _encode_genotype(genotype):
    if genotype is None:
        return np.nan
    return GENOTYPE_MAP.get(genotype, np.nan)


def _to_float(value):
    if value is None:
        return np.nan
    return float(value)


def build_feature_vector(visit, hu_baseline):
    patient = visit.patient
    visit_date = visit.visit_date
    hu_start_date = hu_baseline.hu_start_date

    age = _compute_age(patient.date_of_birth, visit_date)
    gender_encoded = _encode_gender(patient.gender)
    genotype_encoded = _encode_genotype(patient.genotype)
    baseline_hgb = _to_float(hu_baseline.baseline_hgb)
    prev_days_on_hu = float((visit_date - hu_start_date).days)

    features = np.array([[
        age,
        gender_encoded,
        genotype_encoded,
        baseline_hgb,
        _to_float(visit.hgb),
        _to_float(visit.anc),
        _to_float(visit.arc),
        _to_float(visit.platelet_count),
        _to_float(visit.wbc),
        _to_float(visit.rbc),
        _to_float(visit.mcv),
        _to_float(visit.hct),
        prev_days_on_hu,
    ]], dtype=float)

    return features


def predict(visit, hu_baseline, threshold_type='Best F1'):
    _load_artifacts()

    features = build_feature_vector(visit, hu_baseline)
    probability = float(_model.predict_proba(features)[0][1])

    threshold = _thresholds['XGBoost'][threshold_type]
    predicted_class = 'responder' if probability >= threshold else 'non_responder'

    return {
        'response_probability': round(probability, 4),
        'predicted_class': predicted_class,
        'model_version': '1.0',
        'target_definition': 'hgb improvement >= 1.0 g/dL from HU baseline at next eligible visit',
        'threshold': round(threshold, 4),
        'threshold_type': threshold_type,
    }
