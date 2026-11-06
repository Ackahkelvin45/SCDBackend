"""
Pick the demo patients for the clinician-app pilot from the HELD-OUT TEST SET (never seen in training).

Reproduces the dataset and the GroupShuffleSplit of ml_pipeline.py / evaluate_deployed.py exactly, scores
every test row with the three deployed pipelines, then chooses N patients (half observed responders at their
last visit, half non-responders) that are:
  * known genotype (ss/sc/sbo), eligible for prediction in the app (>= 90 days on HU, age >= 2)
  * at least MIN_VISITS laboratory visits in the HU period, so the app shows a real history
  * few median-filled labs at the prediction visit (the fill constant is the column mode)
  * ranked by how many of the three models are correct, so the demo is representative, not cherry-picked
    (the responder/non-responder balance and the "correct" column keep the expected ~80% hit rate honest)

Writes demo_patients.json (patients + every HU visit, in the dataset's own units) and demo_summary.csv.
Run with the pinned venv (sklearn 1.8.0, xgboost 3.2.0): python select_demo_patients.py [N]
"""
import json, sys, warnings
from pathlib import Path

warnings.filterwarnings('ignore')
import joblib, numpy as np, pandas as pd
from sklearn.model_selection import GroupShuffleSplit

ROOT = Path(__file__).resolve().parents[1]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
MIN_VISITS = 4
MIN_DAYS, RESPONSE = 90, 1.0
FEATURES = ['age', 'gender_encoded', 'hb_genotype_encoded', 'baseline_hgb', 'prev_hgb', 'prev_anc', 'prev_arc',
            'prev_platelet_count', 'prev_wbc', 'prev_rbc', 'prev_mcv', 'prev_hct', 'prev_days_on_hu']
LABS = ['hgb', 'anc', 'arc', 'platelet_count', 'wbc', 'rbc', 'mcv', 'hct']
MODELS = {'xgboost': 'XGBoost', 'svm': 'SVM', 'logistic_reg': 'Logistic Reg'}
GENOTYPE = {'ss': 'HbSS', 'sc': 'HbSC', 'sbo': 'HbSβ0'}
# Placeholder names for the app (the dataset is de-identified and has none)
NAMES = ['Kofi Mensah', 'Ama Owusu', 'Yaw Boateng', 'Akosua Asante', 'Kwame Darko', 'Abena Appiah', 'Kojo Agyemang',
         'Adwoa Bonsu', 'Kwabena Ofori', 'Efua Addo', 'Nana Yeboah', 'Esi Quaye', 'Fiifi Antwi', 'Araba Tetteh',
         'Kwesi Amoah', 'Afia Osei', 'Ebo Larbi', 'Maame Frimpong', 'Paa Kwesi Baah', 'Serwaa Dapaah']

# ── dataset, exactly as in evaluate_deployed.py ─────────────────────────────
d = pd.read_csv(ROOT / 'data/hu_clean_data.csv')
hu = d[d['Hu_Start'] == 1].copy()
hu['Lab_Visit_Date'] = pd.to_datetime(hu['Lab_Visit_Date'])
hu = hu.sort_values(['Case_ID', 'Lab_Visit_Date']).reset_index(drop=True)
base = hu.groupby('Case_ID').first()[['Lab_Visit_Date', 'hgb']].rename(
    columns={'Lab_Visit_Date': 'hu_start_date', 'hgb': 'baseline_hgb'})
hu = hu.merge(base, on='Case_ID')
hu['days_on_hu'] = (hu['Lab_Visit_Date'] - hu['hu_start_date']).dt.days
df = hu[hu['days_on_hu'] >= MIN_DAYS].copy().reset_index(drop=True)
df['Responder'] = ((df['hgb'] - df['baseline_hgb']) >= RESPONSE).astype(int)
df = df.sort_values(['Case_ID', 'Lab_Visit_Date']).reset_index(drop=True)
# The app stores labs as decimals (hgb/wbc/anc/rbc 2 dp; platelets/mcv/hct/arc 1 dp), so score what the app
# will actually send to the models, not the raw CSV values.
DP = {'hgb': 2, 'wbc': 2, 'anc': 2, 'rbc': 2, 'platelet_count': 1, 'mcv': 1, 'hct': 1, 'arc': 1}
for c in LABS:
    hu[c] = hu[c].round(DP[c])
    df[c] = df[c].round(DP[c])
df['baseline_hgb'] = df['baseline_hgb'].round(2)
for c in LABS + ['days_on_hu']:
    df[f'prev_{c}'] = df.groupby('Case_ID')[c].shift(1)
df = df[df['prev_days_on_hu'].notna()].copy().reset_index(drop=True)
df['gender_encoded'] = df['gender'].map({'male': 0, 'female': 1}).fillna(-1).astype(int)
df['hb_genotype_encoded'] = df['hb_genotype'].map({'ss': 0, 'sc': 1, 'sbo': 2, 'dont_know': 3}).fillna(3).astype(int)
X, y = df[FEATURES], df['Responder']
gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
_, te_idx = next(gss.split(X, y, groups=df['Case_ID']))
assert len(te_idx) == 939, len(te_idx)
test = df.iloc[te_idx].copy()

# ── score every test row with the deployed pipelines ────────────────────────
thresholds = joblib.load(ROOT / 'outputs/best_thresholds.pkl')
for mid, key in MODELS.items():
    pipe = joblib.load(ROOT / f'outputs/{mid}.pkl')
    test[f'p_{mid}'] = pipe.predict_proba(test[FEATURES])[:, 1]
    test[f'ok_{mid}'] = ((test[f'p_{mid}'] >= thresholds[key]['Best F1']).astype(int) == test['Responder'])
test['n_correct'] = test[[f'ok_{m}' for m in MODELS]].sum(axis=1)

# ── the demo prediction is made on each patient's LAST row (features = second-to-last visit) ──
fill = {c: hu[c].mode().iloc[0] for c in LABS}        # the cleaning step's median fill constant
last = test.groupby('Case_ID').tail(1).copy()
last['n_visits'] = last['Case_ID'].map(hu.groupby('Case_ID').size())
last['n_filled'] = sum((last[f'prev_{c}'] == fill[c]).astype(int) for c in LABS)
# The source columns mix units (ANC/WBC sometimes in cells/µL, ARC sometimes ×1000). Keep patients whose every
# HU visit is on the main scale, so the values fit the app's fields and read sensibly on screen. ARC below the
# app's plausible range (the dataset's usual tenth-scale) and Hct as a fraction are allowed but counted.
RANGE = {'hgb': (3, 16), 'wbc': (0.5, 60), 'anc': (0.1, 40), 'platelet_count': (10, 1500), 'rbc': (1, 7),
         'mcv': (50, 140), 'hct': (10, 60), 'arc': (5, 800)}
bad_scale = ((hu['anc'] > 40) | (hu['wbc'] > 60) | (hu['arc'] > 800)).groupby(hu['Case_ID']).any()
n_oor = sum(((hu[c] < lo) | (hu[c] > hi)).astype(int) for c, (lo, hi) in RANGE.items()).groupby(hu['Case_ID']).sum()
last['bad_scale'] = last['Case_ID'].map(bad_scale)
last['n_oor'] = last['Case_ID'].map(n_oor)
ok = last[(last['hb_genotype'].isin(GENOTYPE)) & (last['gender'].isin(['male', 'female'])) &
          (last['n_visits'] >= MIN_VISITS) & (last['age'] >= 2) & (last['n_filled'] <= 2) & ~last['bad_scale']]
print(f'test set: {test.Case_ID.nunique()} patients; eligible for the demo: {len(ok)} '
      f'({int(ok.Responder.sum())} responders at their last visit)')

# Representative by construction: within each observed class, the share of patients the XGBoost model gets
# right is set to its test-set hit rate for that class, so the demo reproduces Chapter 5 rather than flattering
# or sandbagging the models. Within each cell the best-documented patients come first.
def pick(frame, k):
    return frame.sort_values(['n_filled', 'n_oor', 'n_visits'], ascending=[True, True, False]).head(k)

parts = []
for cls, k in ((1, N // 2), (0, N - N // 2)):
    pool = ok[ok['Responder'] == cls]
    rate = last.loc[last['Responder'] == cls, 'ok_xgboost'].mean()      # sensitivity / specificity on the test set
    k_right = int(round(rate * k))
    parts += [pick(pool[pool['ok_xgboost']], k_right), pick(pool[~pool['ok_xgboost']], k - k_right)]
chosen = pd.concat(parts)
assert len(chosen) == N, len(chosen)

# The deployed server feeds the model exact age on the predicted visit's date ((lab date - dob) / 365.25), while the
# dataset's age column is one whole number per patient. Re-score the chosen rows the way the server will, so the
# workbook shows what the app shows. The selection above is unchanged, and so must every class be.
prev_date = chosen['Case_ID'].map(lambda c: hu.loc[hu['Case_ID'] == c, 'Lab_Visit_Date'].sort_values().iloc[-2])
chosen['age'] = (prev_date - pd.to_datetime(chosen['dob'])).dt.days / 365.25
for mid, key in MODELS.items():
    before = chosen[f'p_{mid}'] >= thresholds[key]['Best F1']
    chosen[f'p_{mid}'] = joblib.load(ROOT / f'outputs/{mid}.pkl').predict_proba(chosen[FEATURES])[:, 1]
    assert ((chosen[f'p_{mid}'] >= thresholds[key]['Best F1']) == before).all(), f'{mid}: a class changed with exact age'

# ── export ──────────────────────────────────────────────────────────────────
patients, rows = [], []
for i, (_, r) in enumerate(chosen.iterrows(), 1):
    visits = hu[hu['Case_ID'] == r['Case_ID']].sort_values('Lab_Visit_Date')
    patients.append({
        'folder_number': f'DEMO-{i:03d}', 'full_name': NAMES[(i - 1) % len(NAMES)], 'case_id': r['Case_ID'],
        'sex': r['gender'], 'date_of_birth': str(r['dob']),
        'genotype': GENOTYPE[r['hb_genotype']],
        'baseline_hgb': float(r['baseline_hgb']), 'hu_start_date': str(r['hu_start_date'].date()),
        'visits': [{'lab_sample_date': str(v['Lab_Visit_Date'].date()),
                    **{c: (None if pd.isna(v[c]) else float(v[c])) for c in LABS}} for _, v in visits.iterrows()],
        'expected': {
            'observed_responder_at_last_visit': bool(r['Responder']),
            **{m: {'probability': round(float(r[f'p_{m}']), 4), 'threshold': float(thresholds[MODELS[m]]['Best F1']),
                   'predicted_class': 'responder' if r[f'p_{m}'] >= thresholds[MODELS[m]]['Best F1'] else 'non_responder',
                   'correct': bool(r[f'ok_{m}'])} for m in MODELS},
        },
    })
    rows.append({'folder_number': f'DEMO-{i:03d}', 'age': int(r['age']), 'sex': r['gender'], 'genotype': GENOTYPE[r['hb_genotype']],
                 'visits': int(r['n_visits']), 'filled_labs': int(r['n_filled']), 'out_of_range_values': int(r['n_oor']), 'baseline_hgb': r['baseline_hgb'],
                 'last_hgb': r['hgb'], 'observed_responder': int(r['Responder']),
                 **{f'p_{m}': round(float(r[f'p_{m}']), 3) for m in MODELS},
                 **{f'ok_{m}': int(r[f'ok_{m}']) for m in MODELS}})
out = ROOT / 'outputs'
json.dump(patients, open(out / 'demo_patients.json', 'w'), indent=1)
summary = pd.DataFrame(rows)
summary.to_csv(out / 'demo_summary.csv', index=False)
print(summary.to_string(index=False))
print('\nmodels correct on these', N, 'patients:', {m: int(summary[f"ok_{m}"].sum()) for m in MODELS})
print('test-set rate for comparison:', {m: round(float(last[f"ok_{m}"].mean()), 2) for m in MODELS})
