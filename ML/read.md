# DATA REVIEW SUMMARY

## Hydroxyurea Response Prediction Study

**Ahodwo Facility — Patient Records Assessment**

*A plain-language review of the patient data and what needs to be addressed before analysis can proceed*

---

## Purpose of This Document

This document summarises the findings from a careful review of the patient dataset collected at Ahodwo facility. The goal of the review was to understand the quality of the data, identify any gaps or problems, and confirm that the right patient information is in place before a prediction model is built.

The model we are building aims to predict whether a sickle cell patient already on Hydroxyurea is **responding** to the drug — meaning their haemoglobin has risen by at least 1 g/dL above the level it was at when they first started treatment. For this to work reliably, the data feeding into it needs to be clean, complete, and clinically meaningful.

---

## 1. About the Dataset

The dataset contains records from patients enrolled at the Ahodwo facility. Here is a high-level summary of what was found:

| Item | Detail |
| --- | --- |
| Total number of records (rows) in the file | 22,052 |
| Total number of unique patients | 1,574 |
| Average number of visits per patient | Approximately 14 visits |
| Number of data fields (columns) per record | 39 fields |
| Type of data | Longitudinal — each patient has records across multiple clinic visits |

Having multiple records per patient is a strength — it means the model can look at how a patient's blood picture changes over time on Hydroxyurea, not just a single snapshot. However, it also requires careful handling to ensure the same patient's visits do not appear in both training and test sets.

---

## 2. A Problem With the File — Records That Should Not Be There

> ⚠ 5,204 records at the bottom of the file are incomplete and need to be removed before any analysis begins.

When reviewing the file, we found that the last 5,204 rows contain almost no patient information. The only thing recorded in these rows is a Hydroxyurea flag — but there is no name, no age, no lab results, no visit date, nothing else.

Records without any of this supporting information cannot contribute anything useful to the analysis. These rows are most likely a formatting issue that occurred when the data was exported.

| Group of Records | Number of Records | Status |
| --- | --- | --- |
| Complete records (with Patient ID) | 16,848 | Use for analysis |
| Incomplete records (no Patient ID) | 5,204 | Remove — not usable |
| Total in file | 22,052 | — |

> ✅ Once these 5,204 records are removed, the working dataset is 16,848 records from 1,574 unique patients.

---

## 3. Balance of Patients — Responders vs Non-Responders

The model needs to learn the difference between patients whose haemoglobin has meaningfully improved on Hydroxyurea (Responders) and those whose haemoglobin has not improved sufficiently (Non-Responders). Looking at the visits eligible for response assessment (patients on HU for at least 90 days), here is how the two groups compare:

| Patient Group | Number of Visits | Share of Total |
| --- | --- | --- |
| Responders (haemoglobin improved ≥ 1 g/dL from baseline) | ~1,700 | 29% |
| Non-Responders (haemoglobin improvement < 1 g/dL) | ~4,169 | 71% |

The two groups are not equal in size — Non-Responders outnumber Responders by roughly 2.5 to 1. This is an important consideration for the analysis team. If left unaddressed, a model trained on unbalanced data like this tends to favour the larger group and will miss many Responders. The technical team handles this through class weighting in all models.

> ⚠ Accuracy is not a useful measure for this dataset. A model that labels every patient as a Non-Responder would score 71% accuracy while being completely useless clinically. The primary metrics used are AUC-ROC and Sensitivity.

---

## 4. The 90-Day Minimum — Why Early Visits Are Excluded

Hydroxyurea typically takes 2 to 6 months to show a measurable effect on haemoglobin. A patient who has only been on the drug for 3 weeks cannot yet be meaningfully classified as a Responder or Non-Responder — the drug simply has not had enough time to work.

For this reason, only visits where the patient has been on Hydroxyurea for at least **90 days** are included in the modelling dataset. This is a deliberate clinical choice, not a data quality issue.

| Visits included | Visits excluded |
| --- | --- |
| ≥ 90 days on Hydroxyurea: ~5,869 visits | < 90 days on HU: excluded — too early to assess |

---

## 5. An Important Clinical Variable That Is Included

> ✅ Haemoglobin Genotype (HB Genotype) is recorded in the dataset and is included in the prediction model.

Haemoglobin genotype — whether a patient has HbSS, HbSC, HbSβ° or another variant — is one of the most clinically meaningful pieces of information in sickle cell care. Patients with HbSS typically have the most severe disease. Patients with HbSC generally have a milder course and may respond to Hydroxyurea differently.

The genotype data is already in the records. Here is how it breaks down:

| Genotype | Number of Records | Clinical Significance |
| --- | --- | --- |
| HbSS | 16,334 | Most severe form — primary candidates for Hydroxyurea |
| HbSC | 408 | Generally milder — different response trajectory |
| HbSβ° | 54 | Intermediate severity |
| Genotype unknown | 54 | Handled as a separate category in the model |

---

## 6. Pain Episode Data — Not Available

> ⚠ Pain crisis history cannot be used in the model because the fields are completely empty.

The dataset contains two fields for recording pain episodes: whether the patient experienced pain, and how many pain events occurred. Both of these fields are **100% empty** across all records — no data was ever entered into them during the study.

This means it is not possible to build a pain episode prediction model from this dataset. This is a data collection gap from the original study and should be clearly stated in the thesis.

| Field | Records completed | Usable? |
| --- | --- | --- |
| `experienced_pain` | 0 out of 10,594 | No |
| `num_pain_events` | 0 out of 10,594 | No |

---

## 7. Duplicate and Redundant Data Fields

Several lab measurements appear more than once in the dataset, recorded in different units. The following measurements each appear two or three times and have been consolidated to a single column per measurement:

| Measurement | Action taken |
| --- | --- |
| Absolute Neutrophil Count (ANC) | Kept primary column only |
| Absolute Reticulocyte Count (ARC) | Kept primary column only |
| White Blood Cell Count (WBC) | Kept primary column only |
| Haematocrit (HCT) | Kept primary column only |

---

## 8. Data Fields That Are Removed

Some fields in the dataset are not included in the prediction model:

| Data Field | Reason for Removal |
| --- | --- |
| Patient full name | Personal identifying information |
| Medical record number | Personal identifying information |
| Hydroxyurea start date | Directly reveals the response baseline — target leakage |
| `hgb_delta` (haemoglobin change from baseline) | This IS the target variable — using it as a feature would make results artificially perfect |
| Pain history notes (free text) | 98.8% blank and unstructured |
| Date of patient registration | Over 92% blank |
| Column1 and Column2 (unnamed) | No clinical meaning |
| Current-visit haemoglobin | Would allow the model to directly compute the response label at the same visit |

> ⚠ **`hgb_delta` and current-visit haemoglobin are the most critical items to exclude.** A model that sees these features will appear to predict perfectly but will have learned nothing clinically useful.

---

## 9. Summary — What the Model Is Based On

After removing unsuitable records and fields, the following patient information forms the basis of the prediction model:

### Patient Profile

- Age
- Gender
- Haemoglobin genotype (HbSS, HbSC, HbSβ°, unknown)
- Baseline haemoglobin at the time Hydroxyurea was started
- Number of days on Hydroxyurea at the previous visit

### Laboratory Results From the Previous Visit

Because the model predicts **next-visit** response from the **previous visit's** data, all lab values used are from the most recent visit on file — not the current visit.

- Haemoglobin (Hgb)
- Absolute Neutrophil Count (ANC)
- Absolute Reticulocyte Count (ARC)
- Platelet count
- White Blood Cell count (WBC)
- Red Blood Cell count (RBC)
- Mean Corpuscular Volume (MCV)
- Haematocrit (HCT)

---

## 10. How We Measure Whether the Model Is Actually Working

### 10.1 Why Accuracy Alone Is Not Enough

> 📋 A model that predicts "Non-Responder" for every patient would score 71% accuracy while being completely useless. This is why accuracy is not reported.

The three most important measures for this model are **Sensitivity**, **Specificity**, and **AUC-ROC**.

### 10.2 Sensitivity — Are We Catching Every Responder?

Sensitivity answers: of all the patients who truly are Responding to Hydroxyurea — how many did the model correctly identify?

A model with high sensitivity is important clinically. Missing a Responder means a patient who is doing well on the drug might be incorrectly flagged for treatment change.

### 10.3 Specificity — Are We Correctly Identifying Non-Responders?

Specificity answers: of all the patients who truly are NOT responding — how many did the model correctly identify?

High specificity ensures the model is not telling doctors a patient is responding when they are not, which could leave a failing treatment in place for too long.

### 10.4 AUC-ROC — The Overall Picture

AUC-ROC is the primary metric. It measures how well the model separates Responders from Non-Responders across all possible decision thresholds. A value of 0.5 is no better than chance; a value of 1.0 is perfect.

| AUC Value | What It Means |
| --- | --- |
| 0.5 | No better than a coin flip |
| 0.7 – 0.8 | Acceptable — useful as a support tool |
| 0.8 – 0.9 | Good — reliably distinguishes the two groups |
| 0.9 – 1.0 | Excellent — strong discriminative ability |

### 10.5 Two Decision Thresholds Are Reported

Each model is evaluated at two operating points:

- **Best F1 threshold** — the threshold that best balances catching Responders and avoiding false alarms
- **Sensitivity ≥ 90% threshold** — the most clinically conservative setting; catches at least 9 in 10 true Responders, accepting some false positives

---

*Prepared by the Data Analysis Team*

*Ahodwo Facility — Hydroxyurea Response Prediction Project*
