# Hydroxyurea Response Prediction
## Sickle Cell Disease — Ahodwo Facility

---

## What This Is

This folder contains the complete machine learning pipeline for a thesis project on Sickle Cell Disease (SCD) management at the Ahodwo facility.

The model is a **binary classifier** that predicts whether a patient on Hydroxyurea is responding to the drug — defined as a haemoglobin improvement of at least 1 g/dL compared to when they first started treatment.

The output of this model feeds a clinical decision-support app. At a routine visit, the doctor enters the patient's most recent blood test results (the *previous* visit) plus baseline information from when HU was started. The app returns a probability score for whether the patient will be a **Responder** at their **next** eligible visit (≥ 90 days on HU).

---

## Clinical Background

### Sickle Cell Disease and Hydroxyurea

Sickle Cell Disease is a genetic blood disorder in which red blood cells take on an abnormal sickle shape. This causes blockages in blood vessels, leading to pain crises, organ damage, and anaemia. The severity varies significantly between patients depending on their haemoglobin genotype (HbSS being the most severe), age, and other clinical factors.

Hydroxyurea (HU) is the primary disease-modifying drug for SCD. It works by stimulating the production of foetal haemoglobin (HbF), which prevents the sickling of red blood cells. When it works, it raises the patient's overall haemoglobin level, reduces the frequency of pain crises, and lowers hospitalisation rates.

### The Problem This Model Solves

Not every patient responds to Hydroxyurea equally. Some patients show a meaningful rise in haemoglobin within a few months. Others remain on the drug for extended periods with little measurable improvement. Currently, clinicians have no reliable way to identify non-responders early — they typically wait and observe over many months before adjusting treatment.

This model is designed to give the doctor a predictive signal at routine clinic visits: is this patient's blood picture consistent with a responding patient or not?

---

## What the Model Predicts

| Property | Value |
|---|---|
| Task | Binary Classification |
| Population | Patients already on Hydroxyurea |
| Target | `Responder_next_visit` — whether the patient is a Responder at their next eligible visit |
| Minimum time on HU | 90 days (visits before this are excluded) |
| Response threshold | 1.0 g/dL improvement |
| Primary metric | AUC-ROC (Sensitivity and Specificity as secondary) |

### Fixed Study Parameters

These parameters are fixed for this thesis and must be stated clearly in the methods section of the write-up:

- **Minimum time on HU before assessment: 90 days.** HU typically takes 2–6 months to show a measurable effect. Including visits too early would label many genuinely responsive patients as non-responders simply because the drug has not had enough time to work. 90 days was chosen as a conservative lower bound that still preserves a large enough dataset for reliable modelling.

- **Response threshold: ≥ 1.0 g/dL haemoglobin improvement.** A 1 g/dL rise in haemoglobin is the clinically established minimum for a meaningful response to HU. It is a widely used threshold in the SCD literature and represents a change that is both statistically detectable and clinically significant in terms of symptom reduction.

---

## Why This Is Not a Hydroxyurea Initiation Model

An earlier version of this pipeline attempted to predict whether a patient would be *started* on Hydroxyurea. That framing was abandoned for the following reasons:

**The target variable was not clinically meaningful.** In the dataset, `Hu_Start` is a running label — once a patient starts HU, every subsequent visit is labelled `Hu_Start = 1`. This means the model was not predicting who would start HU; it was detecting whether a given visit occurred after HU had already been initiated.

**The model was learning visit timing, not clinical risk.** Every `Hu_Start = 0` row in the clean dataset had `days_in_care = 0` — without exception. Every negative example was simply a patient's first ever visit. The model learned one rule: if this is a first visit, the answer is 0; otherwise, the answer is 1. This produced AUC scores of 1.0 on all three models, which appeared excellent but was entirely meaningless.

**99.3% of patients in the cleaned dataset started HU.** Once the analysis was restricted to laboratory visits (as required for modelling), only 11 patients out of 1,556 never started HU. A classifier cannot be trained on 11 negative examples.

**The correct clinical question is about response, not initiation.** The doctor's real problem is not identifying who to start on HU — clinical guidelines already inform that decision. The problem is knowing whether the drug is working once the patient is on it.

---

## Data

### Source

Raw data: `data/data.xlsx` (Excel workbook, sheet `Append1`)
Clean data: `data/hu_clean_data.csv` (produced by `scripts/data_cleaning.r`)

### Dataset After Cleaning

| Property | Value |
|---|---|
| Raw rows | 22,052 |
| After removing phantom rows (no Case ID) | 16,848 |
| After filtering to Lab visits only | ~10,632 |
| After deduplication and date cleaning | 10,594 |
| Unique patients | 1,556 |

### Modelling Dataset (Response Prediction)

From the clean data, only HU visits at 90+ days are assessable for response. The final modelling dataset is constructed as a **next-visit** prediction problem, so the first eligible HU visit for each patient is dropped (there is no “previous visit” to predict from).

| Property | Value |
|---|---|
| Eligible HU visits (days_on_hu ≥ 90) | 5,869 |
| Patients with eligible visits | 1,087 |
| Model rows (next-visit dataset) | (computed in `ml_pipeline.py`) |

### Key Data Cleaning Decisions

- **Phantom rows removed first.** 5,204 rows at the bottom of the Excel file had no Case ID — they are an export artefact and carry no usable information.
- **Registration-only visits excluded.** ~23% of rows represent administrative registration entries with no lab data. Only rows where `Visit type = "Lab"` are used.
- **Target leakage columns dropped.** `hu_start_date` was removed from all analyses — including it would trivially reveal the outcome.
- **PII removed.** `patient_full_name` and `patient_med_record_num` are not used at any stage.
- **Duplicate unit columns dropped.** ANC, ARC, WBC, and HCT each appeared in multiple unit formats. Only the primary column for each is retained.

---

## Features

The model uses features available at the **previous** visit, so the task is genuinely predictive (it does not use current-visit haemoglobin to reconstruct the label).

| Feature | Source | Notes |
|---|---|---|
| `age` | Patient record | Age in years |
| `gender_encoded` | Patient record | 0 = male, 1 = female |
| `hb_genotype_encoded` | Patient record | 0 = SS, 1 = SC, 2 = SBO, 3 = unknown |
| `baseline_hgb` | Patient record | Haemoglobin at HU initiation — doctor has this on file |
| `prev_hgb` | Previous visit | Haemoglobin at last visit |
| `prev_anc` | Previous visit | Absolute Neutrophil Count at last visit |
| `prev_arc` | Previous visit | Absolute Reticulocyte Count at last visit |
| `prev_platelet_count` | Previous visit | Platelet count at last visit |
| `prev_wbc` | Previous visit | White Blood Cell count at last visit |
| `prev_rbc` | Previous visit | Red Blood Cell count at last visit |
| `prev_mcv` | Previous visit | Mean Corpuscular Volume at last visit |
| `prev_hct` | Previous visit | Haematocrit at last visit |
| `prev_days_on_hu` | Computed | Days since HU was initiated at the previous visit |

### Features Deliberately Excluded

| Feature | Reason |
|---|---|
| `hgb_improvement` | This is the target variable — never a feature |
| `days_in_care` | Replaced by `days_on_hu`; time since first ever visit is not clinically relevant here |
| `tox_low_anc`, `tox_low_hb`, `tox_low_platelet_count` | HU side-effect flags derived from the same lab values already in the feature set — redundant |
| `history.num_pain_events` | 100% missing in the clean dataset |
| `hu_start_date` | Target leakage |
| Current-visit lab values (especially current `hgb`) | Would allow the model to reconstruct the response label at the same visit; we predict the next visit instead |

---

## Models

Six models are trained and compared — matching the thesis specification:

| Model | Role | Imbalance Handling | Explainability |
|---|---|---|---|
| Logistic Regression | Baseline | `class_weight='balanced'` | SHAP LinearExplainer |
| Random Forest | Ensemble | `class_weight='balanced'` | SHAP TreeExplainer |
| Gradient Boosting | Ensemble | `class_weight='balanced'` (sklearn default) | SHAP TreeExplainer |
| XGBoost | Primary | `scale_pos_weight` | SHAP TreeExplainer |
| LightGBM | Primary | `is_unbalance=True` | SHAP TreeExplainer |
| SVM | Comparison | `class_weight='balanced'` | Permutation importance |

Tree-based models and Logistic Regression use SHAP for feature-level explainability. SVM uses permutation importance (SHAP KernelExplainer is computationally prohibitive at this dataset size).

---

## How to Run

### 1. Data Cleaning (R)

```bash
Rscript ML/scripts/data_cleaning.r
```

Produces `ML/data/hu_clean_data.csv`.

### 2. ML Pipeline (Python)

```bash
python3 ML/scripts/ml_pipeline.py
```

Expected runtime: **45–75 minutes** (SVM and Gradient Boosting are the bottlenecks; XGBoost and LightGBM are fastest).

### Dependencies

```
pandas, numpy, scikit-learn, xgboost, lightgbm, shap,
matplotlib, seaborn, joblib, imbalanced-learn
```

---

## Outputs

All outputs are saved to `outputs/`:

| File | Description |
|---|---|
| `xgboost.pkl`, `lightgbm.pkl`, `random_forest.pkl` | Trained deploy models (refit on train+val) |
| `gradient_boosting.pkl`, `logistic_reg.pkl`, `svm.pkl` | Trained deploy models (refit on train+val) |
| `best_thresholds.pkl` | Decision thresholds tuned on validation set for all six models |
| `model_comparison.csv` | AUC, Sensitivity, Specificity, F1, Precision for all six models |
| `threshold_tuned_results.csv` | Metrics at Best-F1 and Sens≥90% thresholds |
| `age_stratified_results.csv` | Paediatric vs adult breakdown for all six models |
| `run_metadata.json` | Full record of study parameters, split sizes, and results |
| `roc_curves.png` | ROC curves for all six models |
| `shap_*.png` | SHAP feature importance plots (tree models + Logistic Regression) |
| `perm_importance_svm.png` | Permutation importance for SVM |
| `confusion_matrices.png` | Confusion matrices at Best-F1 threshold (2×3 grid) |
| `confusion_matrices_sens90.png` | Confusion matrices at Sens≥90% threshold (2×3 grid) |
| `calibration_curves.png` | Reliability diagrams for all six models |
| `learning_curves.png` | Train vs CV AUC (SVM skipped — computational cost) |

---

## Evaluation Metrics

| Metric | Why It Matters |
|---|---|
| **AUC-ROC** | Primary — overall discriminative ability across all thresholds |
| **Sensitivity (Recall)** | Proportion of true Responders correctly identified |
| **Specificity** | Proportion of true Non-Responders correctly identified |
| **F1-Score** | Balance of precision and recall on the Responder class |
| ~~Accuracy~~ | Not reported — misleading with 29/71 class imbalance |

Two decision thresholds are reported for each model:
- **Best F1** — maximises F1 for the Responder class
- **Sens ≥90%** — catches at least 90% of all Responders (clinically conservative)

---

## Write-Up Notes

- The cohort is predominantly paediatric (mean age ~14.6 years). Age-stratified results (paediatric vs adult) are reported separately. Conclusions should explicitly state this when discussing generalisability.
- The class imbalance (29% Responders) is handled through class weighting in all six models. Accuracy is therefore not a meaningful metric and should not appear in the thesis results table.
- All six models use patient-level train/test splits (`GroupShuffleSplit` by `Case_ID`) to ensure the same patient never appears in both training and test sets.
- Imputation is performed inside a `sklearn Pipeline` fitted only on training data. No test-set information leaks into imputed values.
- VOC (vaso-occlusive crisis) prediction was not feasible: `experienced_pain` and `num_pain_events` are 100% missing across all 10,594 rows. This is a data collection gap from the original study and must be stated in the thesis limitations section.

---

*Ahodwo Facility — Hydroxyurea Response Prediction Project*
