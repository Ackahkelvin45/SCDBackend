# Machine Learning Guide
## Hydroxyurea Response Prediction Model
**Ahodwo Facility — Labs & Biomarkers Dataset**

---

> **Note on model evolution**
>
> This guide was originally written for a *Hydroxyurea Initiation* prediction model — predicting which patients would be started on HU. That approach was abandoned after analysis revealed a fundamental structural problem: the target variable (`Hu_Start`) was a running visit-level label, meaning every negative example was simply a patient's first visit and `days_in_care = 0` perfectly separated the two classes. All three models scored AUC = 1.0, not because of clinical signal, but because they learned visit timing. See Section 1.2 for the full explanation.
>
> **The model now predicts HU Response** — but as a **true prediction task**:
> using what is known at a patient's **previous** HU visit, predict whether they will be a Responder at their **next** eligible visit (≥ 90 days on HU), defined as a haemoglobin improvement of ≥ 1 g/dL from their HU baseline. This avoids the methodological flaw of using current-visit haemoglobin to directly reconstruct the label.

---

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [Why the Original Framing Was Wrong](#2-why-the-original-framing-was-wrong)
3. [Dataset Summary](#3-dataset-summary)
4. [Step 1 — Data Cleaning](#4-step-1--data-cleaning)
5. [Step 2 — Target Construction](#5-step-2--target-construction)
6. [Step 3 — Feature Engineering](#6-step-3--feature-engineering)
7. [Step 4 — Train/Test Split & Imputation](#7-step-4--traintest-split--imputation)
8. [Step 5 — Handling Class Imbalance](#8-step-5--handling-class-imbalance)
9. [Step 6 — Model Training](#9-step-6--model-training)
10. [Step 7 — Evaluation](#10-step-7--evaluation)
11. [Full Pre-Modelling Checklist](#11-full-pre-modelling-checklist)
12. [Final Feature Set Reference](#12-final-feature-set-reference)

---

## 1. Project Overview

**Goal:** Build a binary classification model to predict whether a patient with Sickle Cell Disease who is already on Hydroxyurea will be a Responder at their **next eligible visit**, defined as a haemoglobin improvement of at least 1 g/dL compared to their baseline at HU initiation.

| Property | Detail |
|---|---|
| **Task** | Binary Classification |
| **Population** | Patients on Hydroxyurea, assessed at ≥ 90 days after initiation |
| **Target variable** | `Responder_next_visit` (1 = responder at next eligible visit, 0 = not) |
| **Data type** | Longitudinal (repeated clinical visits per patient) |
| **Primary language** | Python (sklearn pipelines) |
| **Key concern** | Class imbalance (29% Responders) + patient-level data leakage prevention |

### Fixed Study Parameters

| Parameter | Value | Rationale |
|---|---|---|
| Minimum time on HU | **90 days** | HU takes 2–6 months to show effect; 90 days is a defensible lower bound that retains 5,869 usable visits |
| Response threshold | **≥ 1.0 g/dL** | Clinically established minimum for a meaningful HU response; widely cited in SCD literature |

---

## 2. Why the Original Framing Was Wrong

The first version of this pipeline attempted to predict `Hu_Start` — whether a patient would be initiated on Hydroxyurea. Three issues made this approach invalid.

### 2.1 The target variable encoded visit position, not clinical risk

`Hu_Start` is a running label in the dataset. Once a patient starts HU, every subsequent visit is recorded as `Hu_Start = 1`. The first visit before HU is `Hu_Start = 0`. This means:

- Every `Hu_Start = 0` row had `days_in_care = 0` — without exception
- The model simply learned: *first visit → 0, any later visit → 1*
- This single rule explained AUC = 1.0 across all three models

The model had no clinical insight. It was detecting visit timing.

### 2.2 There were effectively no true negatives

After data cleaning, 1,545 of 1,556 patients (99.3%) eventually started HU. Only 11 patients never started. A classifier cannot be trained to distinguish two groups when one group has 11 members.

### 2.3 The clinical question was wrong

Clinicians already know the guidelines for initiating HU. The decision support they need is not about initiation — it is about whether the drug is working once the patient is on it. A patient on HU for 6 months with no haemoglobin improvement needs a different management decision than one who has responded well.

### 2.4 Summary of the shift

| | Original (abandoned) | Current (correct) |
|---|---|---|
| Question | Will this patient start HU? | Is this patient responding to HU? |
| Population | All patients | Patients already on HU, ≥ 90 days |
| Target | `Hu_Start` (visit-level running label) | `Responder` (hgb_improvement ≥ 1 g/dL) |
| Why it failed | days_in_care = 0 perfectly separated classes | N/A |
| Test AUC | 1.0 (artefact) | Reflects genuine clinical signal |
| Rows | 10,594 | 5,869 |

---

## 3. Dataset Summary

The raw dataset is a single-sheet Excel workbook (`Append1`):

| Property | Value |
|---|---|
| Total rows (raw) | 22,052 |
| Total columns | 39 |
| Unique patients (`Case ID`) | 1,574 |
| Average visits per patient | ~14 visits |
| **Usable rows (after cleaning)** | **~10,594** |
| **Rows for modelling (HU response)** | **5,869** |
| **Patients in modelling dataset** | **1,087** |

> The modelling dataset is restricted to HU visits at ≥ 90 days after initiation. This is a deliberate study design choice, not a data quality issue.

---

## 4. Step 1 — Data Cleaning

Run: `Rscript ML/scripts/data_cleaning.r`
Output: `ML/data/hu_clean_data.csv`

### 4.1 Drop Phantom Rows (first action after loading)

5,204 rows at the bottom of the sheet have no `Case ID` and contain only `Hu_Start = 1`. They are a data export artefact.

```r
df <- df %>% filter(!is.na(`Case ID`) & `Case ID` != "")
```

### 4.2 Filter to Lab Visit Rows Only

~23.6% of rows are registration-only entries with no lab data.

```r
df <- df %>% filter(`Visit type` == "Lab")
```

### 4.3 Drop Columns That Must Never Enter the Model

| Column | Reason |
|---|---|
| `hu_start_date` | Target leakage — directly reveals when HU started |
| `patient_full_name` | PII |
| `patient_med_record_num` | PII |
| `history.pain_history_notes` | 98.8% missing + free text |
| `Date Patient Registration` | 92.9% missing |
| `Column1`, `Column2` | No clinical meaning |

### 4.4 Drop Redundant Unit Columns

| Measurement | Keep | Drop |
|---|---|---|
| ANC | `anc` | `anc (SI Unit) 10^9/L`, `anc (Conv. Unit)` |
| ARC | `arc` | `arc (SI unit) 10^9/L`, `arc (Conv. unit)` |
| WBC | `wbc` | `wbc (SI Unit) 10^9/L` |
| HCT | `hct` | `hct (SI unit)`, `hct(%)` |

### 4.5 Standardise Gender Casing

```r
df$gender <- tolower(trimws(df$gender))
```

### 4.6 Engineer Lag Features

These are computed in R before export so the Python pipeline can verify their integrity.

```r
df <- df %>%
  group_by(Case_ID) %>%
  arrange(Lab_Visit_Date) %>%
  mutate(
    days_in_care = as.numeric(Lab_Visit_Date - min(Lab_Visit_Date)),
    hgb_prev     = lag(hgb),
    wbc_prev     = lag(wbc),
    anc_prev     = lag(anc)
  ) %>%
  ungroup()
```

---

## 5. Step 2 — Target Construction

This step is performed in Python at the start of `ml_pipeline.py`. It runs after loading `hu_clean_data.csv`.

```python
# Filter to HU visits only
hu = raw[raw["Hu_Start"] == 1].copy()
hu["Lab_Visit_Date"] = pd.to_datetime(hu["Lab_Visit_Date"])
hu = hu.sort_values(["Case_ID", "Lab_Visit_Date"]).reset_index(drop=True)

# Per-patient HU baseline HGB (first HU visit)
baseline = (
    hu.groupby("Case_ID")
    .first()[["Lab_Visit_Date", "hgb"]]
    .rename(columns={"Lab_Visit_Date": "hu_start_date", "hgb": "baseline_hgb"})
)
hu = hu.merge(baseline, on="Case_ID")

# Days since HU initiation
hu["days_on_hu"] = (hu["Lab_Visit_Date"] - hu["hu_start_date"]).dt.days

# HGB improvement from HU baseline and response at THIS visit
hu["hgb_improvement"] = hu["hgb"] - hu["baseline_hgb"]

# Visits before 90 days are not assessable
df = hu[hu["days_on_hu"] >= 90].copy().reset_index(drop=True)
df["Responder"] = (df["hgb_improvement"] >= 1.0).astype(int)
```

Result: 5,869 rows, 1,087 patients, 29% Responders.

---

## 6. Step 3 — Feature Engineering

### 6.1 Encode Hb Genotype

```python
genotype_map = {"ss": 0, "sc": 1, "sbo": 2, "dont_know": 3}
df["hb_genotype_encoded"] = df["hb_genotype"].map(genotype_map)
```

### 6.2 Encode Gender

```python
gender_map = {"male": 0, "female": 1}
df["gender_encoded"] = df["gender"].map(gender_map).fillna(-1).astype(int)
```

### 6.3 Final Feature Set

```python
# Build a next-visit prediction dataset:
# use previous-visit information to predict response at the next eligible visit.
df = df.sort_values(["Case_ID", "Lab_Visit_Date"]).reset_index(drop=True)

prev_cols = ["hgb", "anc", "arc", "platelet_count", "wbc", "rbc", "mcv", "hct", "days_on_hu"]
for c in prev_cols:
    df[f"prev_{c}"] = df.groupby("Case_ID")[c].shift(1)

# The response label is defined at the CURRENT visit; we predict it using previous-visit features.
df["Responder_next_visit"] = df["Responder"].astype(int)
df = df[df["prev_days_on_hu"].notna()].copy()

FEATURES = [
    "age", "gender_encoded", "hb_genotype_encoded",
    "baseline_hgb",
    "prev_hgb", "prev_anc", "prev_arc", "prev_platelet_count",
    "prev_wbc", "prev_rbc", "prev_mcv", "prev_hct",
    "prev_days_on_hu",
]

TARGET = "Responder_next_visit"
```

> Current-visit `hgb` is not a feature. This prevents the model from trivially reconstructing `Responder` from \(`hgb - baseline_hgb`\). The model instead predicts the *next* visit's response status using the *previous* visit.

---

## 7. Step 4 — Train/Test Split & Imputation

> **Critical:** Split must be by patient (`Case_ID`), not by row. The same patient's visits must never appear in both training and test sets.

```python
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer

# Patient-level split
gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
train_idx, test_idx = next(gss.split(X, y, groups=df["Case_ID"]))

X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

# Imputation fit ONLY on training data, inside a Pipeline
preprocessing = Pipeline([
    ("imputer", SimpleImputer(strategy="median")),
])
```

A validation set (15% of the training set, also patient-level) is carved out for threshold tuning. Thresholds are never tuned on the test set.

---

## 8. Step 5 — Handling Class Imbalance

After applying the 90-day cutoff, the class balance is:

| Class | Count | Percentage |
|---|---|---|
| `Responder = 1` | 1,700 | 29.0% |
| `Responder = 0` | 4,169 | 71.0% |

`scale_pos_weight` for XGBoost = 4169 / 1700 ≈ **2.45**

All three models use built-in class weighting:
- XGBoost: `scale_pos_weight=2.45`
- LightGBM: `is_unbalance=True`
- Random Forest: `class_weight="balanced"`

> Accuracy is not reported. A model that predicts Non-Responder for every patient would score 71% accuracy while being clinically useless.

---

## 9. Step 6 — Six-Model Training

### Why These Six

| Model | Role | Imbalance Handling | Explainability | Search Iterations |
|---|---|---|---|---|
| **Logistic Regression** | Baseline — linear, interpretable | `class_weight='balanced'` | SHAP LinearExplainer | 20 |
| **Random Forest** | Ensemble — stable, low variance | `class_weight='balanced'` | SHAP TreeExplainer | 50 |
| **Gradient Boosting** | Ensemble — strong on tabular data | `class_weight='balanced'` | SHAP TreeExplainer | 50 |
| **XGBoost** | Primary — typically highest AUC | `scale_pos_weight` | SHAP TreeExplainer | 50 |
| **LightGBM** | Primary — fastest training | `is_unbalance=True` | SHAP TreeExplainer | 50 |
| **SVM** | Comparison — non-linear kernel | `class_weight='balanced'` | Permutation importance | 15 |

Logistic Regression serves as the clinical baseline. If XGBoost or LightGBM cannot outperform Logistic Regression by a meaningful margin (>0.03 AUC), the simpler model should be preferred for clinical deployment. SVM uses permutation importance rather than SHAP because SHAP KernelExplainer is computationally prohibitive at this dataset size.

### Shared Setup

```python
from sklearn.model_selection import GroupKFold, RandomizedSearchCV

gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
train_idx, test_idx = next(gss.split(X, y, groups=df["Case_ID"]))

neg, pos = (y_train == 0).sum(), (y_train == 1).sum()
scale_pos_weight = neg / pos  # ≈ 2.45

# Each model has its own search_iters — see models_config
search = RandomizedSearchCV(
    pipeline, params, n_iter=search_iters,
    scoring="roc_auc",
    cv=GroupKFold(n_splits=5),
    random_state=42, n_jobs=-1
)
search.fit(X_train, y_train, groups=patient_ids_train)
```

---

## 10. Step 7 — Evaluation

> Do not use Accuracy as the primary metric. Use **AUC-ROC** and **Sensitivity** as the primary measures.

### Metrics Reported

| Metric | Why It Matters |
|---|---|
| **AUC-ROC** | Overall discriminative ability across thresholds |
| **Sensitivity (Recall)** | Proportion of true Responders correctly identified |
| **Specificity** | Proportion of true Non-Responders correctly identified |
| **F1-Score** | Balance of precision and recall on the Responder class |
| ~~Accuracy~~ | Misleading — always inflated by majority class |

### Two Thresholds Per Model

```python
# Threshold 1: maximise F1 on the Responder class
f1_arr   = 2 * prec * rec / (prec + rec + 1e-9)
f1_thresh = thresh[f1_arr[:-1].argmax()]

# Threshold 2: ensure Sensitivity >= 90%
# (catch at least 90% of all Responders)
sens90_thresh = thresh[np.where(rec >= 0.90)[0][0]]
```

### How to Pick the Final Model

| Situation | Choose |
|---|---|
| XGBoost has highest AUC and Sensitivity | XGBoost |
| LightGBM matches XGBoost AUC | LightGBM (faster to deploy) |
| XGBoost/LightGBM overfit (CV AUC >> Test AUC) | Random Forest or Gradient Boosting |
| All gradient-boosted models within 0.01 AUC of each other | LightGBM |
| No tree model beats Logistic Regression by > 0.03 AUC | Logistic Regression (simpler, more defensible) |
| SVM matches tree models on AUC | Prefer tree model (SHAP available; better clinical explainability) |

### Paper Reporting Notes

- Report AUC-ROC, Sensitivity, Specificity, and F1 as primary metrics — do not report Accuracy
- Report results at both Best-F1 and Sens≥90% thresholds for all six models
- Report age-stratified results (paediatric < 18y vs adult) separately
- State in the methods section: minimum 90 days on HU, 1.0 g/dL response threshold
- State the 29/71 class imbalance and the class-weighting approach used per model
- State that VOC (pain episode) prediction was not feasible due to 100% missing pain data
- Logistic Regression results provide the baseline against which all other models are compared

---

## 11. Full Pre-Modelling Checklist

| # | Action | Status |
|---|---|---|
| 1 | Drop phantom rows (no Case ID) | ✅ |
| 2 | Filter to Lab visit rows only | ✅ |
| 3 | Drop `hu_start_date` (target leakage) | ✅ |
| 4 | Drop PII columns | ✅ |
| 5 | Standardise gender casing | ✅ |
| 6 | Drop redundant unit columns | ✅ |
| 7 | Engineer lag features in R, verify in Python | ✅ |
| 8 | Filter to `Hu_Start = 1` rows only | ✅ |
| 9 | Compute `baseline_hgb` per patient (first HU visit) | ✅ |
| 10 | Compute `days_on_hu` per visit | ✅ |
| 11 | Apply 90-day minimum cutoff | ✅ |
| 12 | Create per-visit `Responder` target (hgb_improvement ≥ 1.0) | ✅ |
| 13 | Create next-visit target `Responder_next_visit` and previous-visit features | ✅ |
| 14 | Encode `hb_genotype` and `gender` | ✅ |
| 15 | Patient-level train/test split BEFORE imputation | ✅ |
| 16 | Imputation fit only on training data via Pipeline | ✅ |
| 17 | Apply class weighting to all six models | ✅ |
| 18 | Evaluate using Sensitivity + AUC, not Accuracy | ✅ |
| 19 | Confirm `hgb_delta` and current-visit `hgb` are excluded from features | ✅ |
| 20 | Note VOC prediction not feasible — pain data 100% missing | ✅ |

---

## 12. Final Feature Set Reference

### Use These Features (13 total)

| Feature | Type | Source |
|---|---|---|
| `age` | Continuous | Patient record |
| `gender_encoded` | Categorical | Patient record (standardised) |
| `hb_genotype_encoded` | Categorical | Patient record |
| `baseline_hgb` | Continuous | First HU visit — from patient record |
| `prev_hgb` | Continuous | Previous HU visit |
| `prev_anc` | Continuous | Previous HU visit |
| `prev_arc` | Continuous | Previous HU visit |
| `prev_platelet_count` | Continuous | Previous HU visit |
| `prev_wbc` | Continuous | Previous HU visit |
| `prev_rbc` | Continuous | Previous HU visit |
| `prev_mcv` | Continuous | Previous HU visit |
| `prev_hct` | Continuous | Previous HU visit |
| `prev_days_on_hu` | Continuous | Previous HU visit (computed) |

### Never Use These Features

| Feature | Reason |
|---|---|
| `hgb_improvement` | This IS the target — never a feature |
| `hu_start_date` | Target leakage |
| `patient_full_name`, `patient_med_record_num` | PII |
| `history.pain_history_notes` | 98.8% missing, free text |
| `Date Patient Registration` | 92.9% missing |
| `Column1`, `Column2` | No clinical meaning |
| `days_in_care` | Not used for this task |
| `tox_low_anc`, `tox_low_hb`, `tox_low_platelet_count` | HU side-effect flags redundant with raw lab values |
| All duplicate unit columns | Redundant |
| Current-visit lab values (especially current `hgb`) | Would allow reconstruction of response at the same visit; we predict the next visit instead |

---

*Guide updated to reflect the corrected HU Response prediction framing.*
*Ahodwo Facility — Hydroxyurea Response Prediction Project*
