"""
Build the pilot workbook (ML/outputs/demo_workbook.xlsx) from demo_patients.json, so the sheet the tester holds
and the records the loader puts in the database come from the same file.

Sheets
  Read me          how the live test works, step by step
  Patients         one row per demo patient: who they are, what is pre-loaded, the expected result per model
  Visits loaded    every visit already in the app (what the tester will see in the history)
  Final visit      the held-back visit to type in after predicting, with the expected outcome
  Results          one row per patient x model: expected probability/class, blank columns for what the app showed,
                   and a formula that says MATCH / MISMATCH

Run with the pinned venv: python make_demo_workbook.py
"""
import json
import math
from pathlib import Path

from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
patients = json.load(open(ROOT / 'outputs/demo_patients.json'))
MODELS = {'xgboost': 'XGBoost', 'svm': 'SVM', 'logistic_reg': 'Logistic Regression'}
LABS = [('hgb', 'Hb (g/dL)'), ('wbc', 'WBC (×10⁹/L)'), ('anc', 'ANC (×10⁹/L)'), ('platelet_count', 'Platelets (×10⁹/L)'),
        ('rbc', 'RBC (×10¹²/L)'), ('mcv', 'MCV (fL)'), ('hct', 'Hct (%)'), ('arc', 'ARC (×10⁹/L)')]
HEAD = Font(bold=True, color='FFFFFF')
HEAD_FILL = PatternFill('solid', fgColor='2F5597')
INPUT_FILL = PatternFill('solid', fgColor='FFF2CC')
BOLD = Font(bold=True)


def sheet(wb, title, headers, rows, widths=None, input_cols=()):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.font, c.fill, c.alignment = HEAD, HEAD_FILL, Alignment(wrap_text=True, vertical='center')
    for r in rows:
        ws.append(r)
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(h, max(12, min(40, len(str(h)) + 2)))
    for col in input_cols:
        for row in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            row[0].fill = INPUT_FILL
    ws.freeze_panes = 'A2'
    return ws


def pct(prob):
    """The app's figure: percentage to one decimal, rounded half up like JS Math.round (0.6374 -> 63.7)."""
    return math.floor(prob * 1000 + 0.5) / 10


def shown(prob):
    return f"{pct(prob):g}%"


def cls(c):
    return 'Likely responder' if c == 'responder' else 'Unlikely responder'


wb = Workbook()
readme = wb.active
readme.title = 'Read me'
lines = [
    ('SickleCare pilot test: 20 patients from the held-out test set', BOLD),
    ('', None),
    ('What is loaded', BOLD),
    ('The 20 patients on the Patients sheet are already in the app under the SickleCare Test Facility, folder numbers DEMO-001 to DEMO-020. '
     'They are real, de-identified study patients that the models never saw during training. Names are placeholders. '
     'Every clinic visit is loaded EXCEPT the most recent one, which is held back on the Final visit sheet.', None),
    ('', None),
    ('How to test one patient', BOLD),
    ('1. Open the patient in the app and check the visit history against the Visits loaded sheet.', None),
    ('2. Open the latest visit and tap Predict. Write what the app shows in the yellow columns of the Results sheet: type the probability, and pick Likely / Unlikely from the drop-down.', None),
    ('3. Add a new visit and type the values from the Final visit sheet for that patient. If the app asks you to confirm an out-of-range value, confirm it: the values are exactly as recorded in the study data.', None),
    ('4. The prediction history now shows whether the prediction was confirmed. Pick Confirmed / Not confirmed from the drop-down in the Results sheet.', None),
    ('5. Repeat in each of the three apps (XGBoost, SVM, Logistic Regression). Each app shows only its own model.', None),
    ('', None),
    ('What to expect', BOLD),
    ('A patient is a responder when the Hb at the final visit is at least 1.0 g/dL above the baseline Hb. '
     'Each model has its own threshold (XGBoost 0.572, SVM 0.309, Logistic Regression 0.678); a probability at or above it means Likely responder. '
     'On these 20 patients every model is expected to be right 15 times and wrong 5 times, which is the same rate as on the full test set in the thesis. '
     'The expected misses are listed on the Patients sheet; seeing them is part of the test.', None),
    ('', None),
    ('The app shows each probability as a percentage to one decimal place, for example 63.7%. The Expected probability column uses '
     'the same format, so the figure on screen should match the sheet exactly. Likely / Unlikely comes from comparing that probability '
     'with the model threshold, and the outcome comes from the final visit; both must match exactly too.', None),
]
for text, font in lines:
    readme.append([text])
    if font:
        readme.cell(readme.max_row, 1).font = font
    readme.cell(readme.max_row, 1).alignment = Alignment(wrap_text=True, vertical='top')
readme.column_dimensions['A'].width = 120

# Patients
rows = []
for p in patients:
    e = p['expected']
    misses = [MODELS[m] for m in MODELS if not e[m]['correct']]
    rows.append([p['folder_number'], p['full_name'], p['sex'], p['date_of_birth'], p['genotype'], p['baseline_hgb'],
                 p['hu_start_date'], len(p['visits']) - 1, p['visits'][-2]['lab_sample_date'],
                 'Responder' if e['observed_responder_at_last_visit'] else 'Non-responder']
                + [x for m in MODELS for x in (shown(e[m]['probability']), cls(e[m]['predicted_class']))]
                + [', '.join(misses) if misses else 'All three correct'])
sheet(wb, 'Patients',
      ['Folder no.', 'Name in app', 'Sex', 'Date of birth', 'Genotype', 'Baseline Hb (g/dL)', 'HU start date', 'Visits loaded',
       'Latest loaded visit (predict here)', 'Observed outcome at final visit']
      + [h for m in MODELS.values() for h in (f'{m}: expected probability', f'{m}: expected class')] + ['Expected to be wrong'],
      rows, widths={'Name in app': 18, 'Expected to be wrong': 30, 'Latest loaded visit (predict here)': 20})

# Visits loaded
rows = [[p['folder_number'], p['full_name'], i, v['lab_sample_date']] + [v[c] for c, _ in LABS]
        for p in patients for i, v in enumerate(p['visits'][:-1], 1)]
sheet(wb, 'Visits loaded', ['Folder no.', 'Name in app', 'Visit no.', 'Lab sample date'] + [h for _, h in LABS], rows)

# Final visit
rows = []
for p in patients:
    last = p['visits'][-1]
    delta = round(last['hgb'] - p['baseline_hgb'], 2)
    rows.append([p['folder_number'], p['full_name'], last['lab_sample_date']] + [last[c] for c, _ in LABS]
                + ['No', 'Not pregnant' if p['sex'] == 'female' else 'n/a', p['baseline_hgb'], delta,
                   'Confirmed (responder)' if delta >= 1.0 else 'Not confirmed (non-responder)'])
sheet(wb, 'Final visit', ['Folder no.', 'Name in app', 'Lab sample date to enter'] + [h for _, h in LABS]
      + ['Transfused last 3 months', 'Pregnancy status (females 12–50)', 'Baseline Hb', 'Hb change vs baseline', 'Expected outcome in app'],
      rows, widths={'Expected outcome in app': 30})

# Results: one row per patient x model, with blanks for the app's values and MATCH formulas
headers = ['Folder no.', 'Name in app', 'App / model', 'Expected probability', 'Expected class', 'Expected outcome',
           'App showed: probability', 'App showed: class (Likely / Unlikely)', 'App showed: outcome (Confirmed / Not confirmed)',
           'Probability matches?', 'Class matches?', 'Outcome matches?', 'Notes']
rows = []
for p in patients:
    e = p['expected']
    outcome = 'Confirmed' if e['observed_responder_at_last_visit'] else 'Not confirmed'
    for m, name in MODELS.items():
        rows.append([p['folder_number'], p['full_name'], name, pct(e[m]['probability']), cls(e[m]['predicted_class']),
                     outcome] + [None] * 7)
ws = sheet(wb, 'Results', headers, rows, widths={h: 22 for h in headers}, input_cols=(7, 8, 9, 13))
for r in range(2, ws.max_row + 1):
    ws[f'D{r}'].number_format = ws[f'G{r}'].number_format = '0.0"%"'
    # typed "63.7%" is stored by Excel as 0.637, typed "63.7" as 63.7: accept either
    ws[f'J{r}'] = f'=IF(G{r}="","",IF(OR(ROUND(G{r},1)=D{r},ROUND(G{r}*100,1)=D{r}),"MATCH","MISMATCH"))'
    ws[f'K{r}'] = f'=IF(H{r}="","",IF(H{r}=E{r},"MATCH","MISMATCH"))'
    ws[f'L{r}'] = f'=IF(I{r}="","",IF(I{r}=F{r},"MATCH","MISMATCH"))'
n = ws.max_row
# Drop-downs, so the tester picks the exact wording and the checks are a plain comparison
for col, choices in (('H', 'Likely responder,Unlikely responder'), ('I', 'Confirmed,Not confirmed')):
    dv = DataValidation(type='list', formula1=f'"{choices}"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f'{col}2:{col}{n}')
ws.append([])
ws.append(['Summary', None, 'Probability matches', f'=COUNTIF(J2:J{n},"MATCH")', 'of', f'=COUNT(G2:G{n})'])
ws.append([None, None, 'Class matches', f'=COUNTIF(K2:K{n},"MATCH")', 'of', f'=COUNTA(H2:H{n})'])
ws.append([None, None, 'Outcome matches', f'=COUNTIF(L2:L{n},"MATCH")', 'of', f'=COUNTA(I2:I{n})'])
for m, name in MODELS.items():
    ws.append([None, None, f'{name}: class matches', f'=COUNTIFS(C2:C{n},"{name}",K2:K{n},"MATCH")', 'of', f'=COUNTIFS(C2:C{n},"{name}",H2:H{n},"<>")'])
ws.cell(n + 2, 1).font = BOLD

out = ROOT / 'outputs/demo_workbook.xlsx'
wb.save(out)
print('saved', out, '|', len(patients), 'patients,', sum(len(p['visits']) - 1 for p in patients), 'visits loaded,', len(rows), 'result rows')
