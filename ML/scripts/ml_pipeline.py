"""
Hydroxyurea Response Prediction Model
Ahodwo Facility — ML Pipeline

Goal: Predict whether a patient on Hydroxyurea is a Responder
      (haemoglobin improvement >= 1 g/dL from HU initiation baseline)
      at the NEXT eligible visit, using information available at the
      PREVIOUS visit (i.e., a true prediction problem).

Fixed parameters (stated in write-up):
  - Minimum time on HU before a visit is assessable : 90 days
  - Response threshold                              : haemoglobin improvement >= 1.0 g/dL
"""

import os
import json
import copy
import time
import datetime
import warnings
import joblib
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="lightgbm")

_pipeline_start = time.time()

def section(title):
    elapsed = time.time() - _pipeline_start
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"  {ts}  |  {elapsed/60:.1f} min elapsed")
    print(f"{'='*60}")

def done(msg=""):
    elapsed = time.time() - _pipeline_start
    tag = f"  ✓  {msg}" if msg else "  ✓  done"
    print(f"{tag}  [{elapsed/60:.1f} min total]")

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.inspection import permutation_importance
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import GroupShuffleSplit, GroupKFold, RandomizedSearchCV, learning_curve
from sklearn.impute import SimpleImputer
from sklearn.calibration import calibration_curve
from sklearn.metrics import (
    roc_auc_score, classification_report, confusion_matrix,
    ConfusionMatrixDisplay, RocCurveDisplay, recall_score,
    f1_score, precision_recall_curve,
    average_precision_score, PrecisionRecallDisplay
)
from sklearn.pipeline import Pipeline

import xgboost as xgb
import lightgbm as lgb
import shap

# ================================================================
# PATHS
# ================================================================

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "../.."))
DATA_PATH    = os.path.join(PROJECT_ROOT, "ML/data/hu_clean_data.csv")
OUTPUT_DIR   = os.path.join(PROJECT_ROOT, "ML/outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Fixed study parameters
MIN_DAYS_ON_HU   = 90    # visits before this are excluded (HU hasn't had time to work)
RESPONSE_THRESHOLD = 1.0  # g/dL improvement required to be labelled a Responder

COLORS = {
    "XGBoost":           "#2196F3",
    "LightGBM":          "#4CAF50",
    "Random Forest":     "#FF9800",
    "Gradient Boosting": "#9C27B0",
    "Logistic Reg":      "#F44336",
    "SVM":               "#795548",
}

# ================================================================
# 1. LOAD DATA
# ================================================================

section("1 / 15  —  Loading data")
raw = pd.read_csv(DATA_PATH)
print(f"  Raw rows: {len(raw):,}  |  Columns: {raw.shape[1]}")
done("Data loaded")

# ================================================================
# 2. CONSTRUCT HU RESPONSE TARGET (per visit)
#
# Only patients already on Hydroxyurea are relevant.
# For each such patient we identify their HU initiation baseline HGB
# (the haemoglobin at their first Hu_Start=1 visit), then compute
# how much their haemoglobin has changed at every subsequent visit.
# Visits within the first 90 days are excluded — the drug has not
# had enough time to show an effect.
# ================================================================

section("2 / 15  —  Constructing HU Response target")

# Keep only HU visits
hu = raw[raw["Hu_Start"] == 1].copy()
hu["Lab_Visit_Date"] = pd.to_datetime(hu["Lab_Visit_Date"])
hu = hu.sort_values(["Case_ID", "Lab_Visit_Date"]).reset_index(drop=True)

print(f"  HU visits  : {len(hu):,}")
print(f"  HU patients: {hu['Case_ID'].nunique():,}")

# Per-patient HU baseline (first HU visit)
baseline = (
    hu.groupby("Case_ID")
    .first()[["Lab_Visit_Date", "hgb"]]
    .rename(columns={"Lab_Visit_Date": "hu_start_date", "hgb": "baseline_hgb"})
)
hu = hu.merge(baseline, on="Case_ID")

# Days since HU initiation
hu["days_on_hu"] = (hu["Lab_Visit_Date"] - hu["hu_start_date"]).dt.days

# HGB improvement from HU baseline
hu["hgb_improvement"] = hu["hgb"] - hu["baseline_hgb"]

# Exclude visits too early to assess response
df = hu[hu["days_on_hu"] >= MIN_DAYS_ON_HU].copy().reset_index(drop=True)

# Binary response label
df["Responder"] = (df["hgb_improvement"] >= RESPONSE_THRESHOLD).astype(int)

n_resp    = df["Responder"].sum()
n_nonresp = len(df) - n_resp
print(f"\n  Assessment visits (days_on_hu >= {MIN_DAYS_ON_HU}): {len(df):,}")
print(f"  Unique patients                                 : {df['Case_ID'].nunique():,}")
print(f"  Responders   (hgb improvement >= {RESPONSE_THRESHOLD} g/dL) : {n_resp:,} ({n_resp/len(df)*100:.1f}%)")
print(f"  Non-Responders                                  : {n_nonresp:,} ({n_nonresp/len(df)*100:.1f}%)")
print(f"\n  HGB improvement distribution:")
print(df["hgb_improvement"].describe().round(3).to_string())
done("Target constructed")

# ================================================================
# 3. FEATURE ENGINEERING — BUILD A NEXT-VISIT PREDICTION DATASET
#
# We want the model to answer:
#   "Given what we knew at the previous visit, will this patient be a
#    Responder at their next eligible visit (>= 90 days on HU)?"
#
# This avoids the methodological flaw of including the current visit HGB,
# which would make the label (hgb - baseline_hgb >= 1) almost directly
# reconstructable from features.
# ================================================================

section("3 / 15  —  Building next-visit prediction dataset")

# Sort for safe shifting
df = df.sort_values(["Case_ID", "Lab_Visit_Date"]).reset_index(drop=True)

# Previous visit features (within the HU timeline)
prev_cols = ["hgb", "anc", "arc", "platelet_count", "wbc", "rbc", "mcv", "hct", "days_on_hu"]
for c in prev_cols:
    df[f"prev_{c}"] = df.groupby("Case_ID")[c].shift(1)

# Target: response status at the CURRENT visit (we predict this using previous visit info)
df["Responder_next_visit"] = df["Responder"].astype(int)

# Keep only rows that have a previous HU visit to predict from
df = df[df["prev_days_on_hu"].notna()].copy().reset_index(drop=True)

gender_map   = {"male": 0, "female": 1}
genotype_map = {"ss": 0, "sc": 1, "sbo": 2, "dont_know": 3}

df["gender_encoded"]      = df["gender"].map(gender_map).fillna(-1).astype(int)
df["hb_genotype_encoded"] = df["hb_genotype"].map(genotype_map).fillna(3).astype(int)

print(f"  Rows with a previous HU visit available: {len(df):,}")
print(f"  Unique patients                         : {df['Case_ID'].nunique():,}")
done("Next-visit dataset ready")

# ================================================================
# 4. FEATURE MATRIX
#
# Features available at the previous visit (what the doctor knows now):
#   - Patient demographics and genotype
#   - Baseline HGB at HU initiation (from patient record)
#   - Previous visit blood test results (the most recent labs)
#   - Previous visit time-on-HU
#
# NOT included:
#   - hgb_improvement  (this IS the target — never a feature)
#   - Hu_Start         (all rows here are already Hu_Start=1)
#   - days_in_care     (replaced by days_on_hu for this task)
#   - Toxicity flags   (HU side-effect markers derived from the same
#                       lab values already in the feature set)
#   - Current-visit labs (especially current HGB) to keep this predictive
# ================================================================

FEATURES = [
    # Demographics
    "age", "gender_encoded", "hb_genotype_encoded",
    # HU initiation baseline — doctor has this from patient record
    "baseline_hgb",
    # Previous visit blood test
    "prev_hgb", "prev_anc", "prev_arc", "prev_platelet_count",
    "prev_wbc", "prev_rbc", "prev_mcv", "prev_hct",
    # Time context at previous visit
    "prev_days_on_hu",
]

TARGET = "Responder_next_visit"

X = df[FEATURES].copy()
y = df[TARGET].copy()

section("4 / 15  —  Feature matrix")
print(f"  Shape      : {X.shape}")
print(f"  Responder  : {y.mean()*100:.1f}%  |  Non-Responder: {(1-y.mean())*100:.1f}%")
print(f"\n  Missing values per feature:")
miss = X.isnull().mean() * 100
print(miss[miss > 0].round(1).to_string() if miss[miss > 0].any() else "  None")
done()

# ================================================================
# GRAPH A — Target class distribution
# ================================================================

fig, axes = plt.subplots(1, 2, figsize=(12, 5))

class_counts = y.value_counts().sort_index()
labels_bar   = ["Non-Responder (0)", "Responder (1)"]
axes[0].bar(
    labels_bar, class_counts.values,
    color=["#90CAF9", "#EF9A9A"], edgecolor="black", width=0.5,
)
for i, v in enumerate(class_counts.values):
    axes[0].text(i, v + max(class_counts)*0.01,
                 f"{v:,}\n({v/len(y)*100:.1f}%)", ha="center", fontweight="bold")
axes[0].set_title("Response Class Distribution", fontweight="bold")
axes[0].set_ylabel("Count")

axes[1].pie(
    class_counts.values, labels=labels_bar,
    autopct="%1.1f%%", colors=["#90CAF9", "#EF9A9A"],
    startangle=90, wedgeprops=dict(edgecolor="white", linewidth=2),
)
axes[1].set_title("Class Balance (Pie)", fontweight="bold")

plt.suptitle(
    f"HU Response Class Distribution  "
    f"(threshold: ≥{RESPONSE_THRESHOLD} g/dL at ≥{MIN_DAYS_ON_HU} days on HU)",
    fontsize=12, fontweight="bold"
)
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "class_distribution.png"), dpi=150)
plt.close()
print("Saved: class_distribution.png")

# ================================================================
# GRAPH B — Feature missingness
# ================================================================

fig, ax = plt.subplots(figsize=(10, 6))
miss_sorted = miss.sort_values(ascending=False)
ax.barh(
    miss_sorted.index, miss_sorted.values,
    color=["#EF5350" if v > 20 else "#FFA726" if v > 5 else "#66BB6A"
           for v in miss_sorted.values]
)
ax.set_xlabel("Missing (%)")
ax.set_title("Feature Missingness", fontweight="bold")
ax.axvline(5,  color="orange", linestyle="--", linewidth=1, label="5%")
ax.axvline(20, color="red",    linestyle="--", linewidth=1, label="20%")
ax.legend()
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "feature_missingness.png"), dpi=150)
plt.close()
print("Saved: feature_missingness.png")

# ================================================================
# GRAPH C — Feature correlation heatmap
# ================================================================

fig, ax = plt.subplots(figsize=(14, 11))
corr = X.corr()
mask = np.triu(np.ones_like(corr, dtype=bool))
sns.heatmap(
    corr, mask=mask, annot=True, fmt=".2f", cmap="RdBu_r",
    center=0, linewidths=0.5, ax=ax, annot_kws={"size": 7},
    vmin=-1, vmax=1,
)
ax.set_title("Feature Correlation Matrix", fontweight="bold", pad=14)
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "feature_correlation.png"), dpi=150)
plt.close()
print("Saved: feature_correlation.png")

# ================================================================
# GRAPH D — Feature distributions by response class
# ================================================================

numeric_features = [f for f in FEATURES
                    if f not in ["gender_encoded", "hb_genotype_encoded"]]
n_cols = 4
n_rows = int(np.ceil(len(numeric_features) / n_cols))
fig, axes = plt.subplots(n_rows, n_cols, figsize=(20, n_rows * 4))
axes = axes.flatten()

for i, feat in enumerate(numeric_features):
    ax = axes[i]
    for cls, color, label in [(0, "#90CAF9", "Non-Responder"), (1, "#EF9A9A", "Responder")]:
        vals = X.loc[y == cls, feat].dropna()
        ax.hist(vals, bins=30, alpha=0.6, color=color, label=label, density=True)
    ax.set_title(feat, fontweight="bold", fontsize=9)
    ax.legend(fontsize=7)
    ax.tick_params(labelsize=7)

for j in range(len(numeric_features), len(axes)):
    axes[j].set_visible(False)

plt.suptitle("Feature Distributions by HU Response Class", fontsize=14, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "feature_distributions.png"), dpi=150)
plt.close()
print("Saved: feature_distributions.png")

# ================================================================
# 5. PATIENT-LEVEL TRAIN / VAL / TEST SPLIT
#
# GroupShuffleSplit by Case_ID ensures the same patient never
# appears in both training and test — preventing data leakage
# from repeated visits of the same individual.
# ================================================================

section("5 / 15  —  Patient-level train / val / test split")

gss_outer = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
train_idx, test_idx = next(gss_outer.split(X, y, groups=df["Case_ID"]))

X_trainval      = X.iloc[train_idx].reset_index(drop=True)
y_trainval      = y.iloc[train_idx].reset_index(drop=True)
X_test          = X.iloc[test_idx].reset_index(drop=True)
y_test          = y.iloc[test_idx].reset_index(drop=True)
groups_trainval = df.iloc[train_idx]["Case_ID"].values

gss_inner = GroupShuffleSplit(n_splits=1, test_size=0.15, random_state=42)
tr_idx, val_idx = next(gss_inner.split(X_trainval, y_trainval, groups=groups_trainval))

X_tr  = X_trainval.iloc[tr_idx].reset_index(drop=True)
y_tr  = y_trainval.iloc[tr_idx].reset_index(drop=True)
X_val = X_trainval.iloc[val_idx].reset_index(drop=True)
y_val = y_trainval.iloc[val_idx].reset_index(drop=True)
groups_tr = groups_trainval[tr_idx]

print(f"\n  Train  : {len(X_tr):,} rows  ({len(X_tr)/len(X)*100:.0f}%)")
print(f"  Val    : {len(X_val):,} rows  ({len(X_val)/len(X)*100:.0f}%)")
print(f"  Test   : {len(X_test):,} rows  ({len(X_test)/len(X)*100:.0f}%)")

neg = (y_tr == 0).sum()
pos = (y_tr == 1).sum()
scale_pos_weight = neg / pos
print(f"\n  Class balance (train) — Non-Resp: {neg:,}  Resp: {pos:,}  ratio: {scale_pos_weight:.2f}x")
done("Splits ready")

# ================================================================
# GRAPH E — Split sizes
# ================================================================

fig, ax = plt.subplots(figsize=(8, 5))
split_labels = ["Train", "Validation", "Test"]
split_sizes  = [len(X_tr), len(X_val), len(X_test)]
bars = ax.bar(split_labels, split_sizes,
              color=["#42A5F5", "#FFA726", "#EF5350"], width=0.5, edgecolor="black")
for bar, v in zip(bars, split_sizes):
    ax.text(bar.get_x() + bar.get_width()/2, v + max(split_sizes)*0.01,
            f"{v:,}\n({v/len(X)*100:.0f}%)", ha="center", fontweight="bold")
ax.set_ylabel("Number of rows")
ax.set_title("Train / Validation / Test Split Sizes", fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "split_sizes.png"), dpi=150)
plt.close()
print("Saved: split_sizes.png")

# ================================================================
# 6. PIPELINES
# ================================================================

xgb_pipeline = Pipeline([
    ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
    ("clf",     xgb.XGBClassifier(
        scale_pos_weight=scale_pos_weight,
        eval_metric="auc", random_state=42, n_jobs=1, verbosity=0
    )),
])

lgb_pipeline = Pipeline([
    ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
    ("clf",     lgb.LGBMClassifier(
        is_unbalance=True,
        random_state=42, n_jobs=1, verbose=-1
    )),
])

rf_pipeline = Pipeline([
    ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
    ("clf",     RandomForestClassifier(
        class_weight="balanced",
        random_state=42, n_jobs=1
    )),
])

gb_pipeline = Pipeline([
    ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
    ("clf",     GradientBoostingClassifier(random_state=42)),
])

lr_pipeline = Pipeline([
    ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
    ("scaler",  StandardScaler()),
    ("clf",     LogisticRegression(
        class_weight="balanced",
        max_iter=5000, random_state=42, n_jobs=-1
    )),
])

svm_pipeline = Pipeline([
    ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
    ("scaler",  StandardScaler()),
    ("clf",     SVC(
        class_weight="balanced",
        probability=True, random_state=42
    )),
])

# ================================================================
# 7. HYPERPARAMETER GRIDS
# ================================================================

xgb_params = {
    "clf__n_estimators":     [200, 400, 600],
    "clf__max_depth":        [3, 4, 5, 6],
    "clf__learning_rate":    [0.01, 0.05, 0.1],
    "clf__subsample":        [0.7, 0.8, 1.0],
    "clf__colsample_bytree": [0.7, 0.8, 1.0],
    "clf__min_child_weight": [1, 3, 5],
    "clf__gamma":            [0, 0.1, 0.2],
}

lgb_params = {
    "clf__n_estimators":      [200, 400, 600],
    "clf__max_depth":         [3, 4, 5, 6, -1],
    "clf__learning_rate":     [0.01, 0.05, 0.1],
    "clf__num_leaves":        [20, 31, 50, 70],
    "clf__subsample":         [0.7, 0.8, 1.0],
    "clf__colsample_bytree":  [0.7, 0.8, 1.0],
    "clf__min_child_samples": [10, 20, 30],
}

rf_params = {
    "clf__n_estimators":      [200, 400, 600],
    "clf__max_depth":         [None, 10, 20, 30],
    "clf__max_features":      ["sqrt", "log2", 0.5],
    "clf__min_samples_split": [2, 5, 10],
    "clf__min_samples_leaf":  [1, 2, 4],
}

gb_params = {
    "clf__n_estimators":      [100, 200, 300, 400],
    "clf__max_depth":         [3, 4, 5],
    "clf__learning_rate":     [0.01, 0.05, 0.1, 0.15],
    "clf__subsample":         [0.7, 0.8, 1.0],
    "clf__min_samples_split": [2, 5, 10],
    "clf__min_samples_leaf":  [1, 2, 4],
}

lr_params = {
    "clf__C":       [0.001, 0.01, 0.1, 1, 10, 100],
    "clf__solver":  ["lbfgs", "saga"],
    "clf__penalty": ["l2"],
}

svm_params = {
    "clf__C":      [0.1, 1, 10, 100],
    "clf__kernel": ["rbf", "linear"],
    "clf__gamma":  ["scale", "auto", 0.001, 0.01],
}

models_config = [
    ("XGBoost",           xgb_pipeline, xgb_params, 50),
    ("LightGBM",          lgb_pipeline, lgb_params, 50),
    ("Random Forest",     rf_pipeline,  rf_params,  50),
    ("Gradient Boosting", gb_pipeline,  gb_params,  50),
    ("Logistic Reg",      lr_pipeline,  lr_params,  20),
    ("SVM",               svm_pipeline, svm_params, 15),
]

# ================================================================
# 8. HYPERPARAMETER TUNING  (fitted on X_tr only)
# ================================================================

section("8 / 15  —  Hyperparameter tuning  (5-fold CV × 6 models — variable search iterations)")

best_models = {}
cv_aucs     = {}
cv_results  = {}

n_models = len(models_config)
for i, (name, pipeline, params, search_iters) in enumerate(models_config, 1):
    print(f"\n  [{i}/{n_models}]  {name} — starting tuning  ({search_iters} iterations) ...")
    t0 = time.time()

    search = RandomizedSearchCV(
        pipeline, params,
        n_iter=search_iters, scoring="roc_auc",
        cv=GroupKFold(n_splits=5), random_state=42, n_jobs=-1, verbose=1,
    )
    search.fit(X_tr, y_tr, groups=groups_tr)
    elapsed = time.time() - t0

    best_models[name] = search.best_estimator_
    cv_aucs[name]     = round(search.best_score_, 4)
    cv_results[name]  = pd.DataFrame(search.cv_results_)

    print(f"  [{i}/{n_models}]  {name}  ✓")
    print(f"         CV AUC : {search.best_score_:.4f}")
    print(f"         Time   : {elapsed/60:.1f} min")
    print(f"         Params : {search.best_params_}")

# ================================================================
# GRAPH F — CV AUC distribution (box plots over search iterations)
# ================================================================

fig, ax = plt.subplots(figsize=(14, 6))
bp = ax.boxplot(
    [cv_results[n]["mean_test_score"].dropna().values for n in cv_results],
    patch_artist=True, notch=True,
    medianprops=dict(color="black", linewidth=2)
)
for patch, color in zip(bp["boxes"], COLORS.values()):
    patch.set_facecolor(color)
    patch.set_alpha(0.7)
ax.set_xticklabels(list(cv_results.keys()), rotation=15, ha="right")
ax.set_ylabel("Mean CV AUC-ROC")
ax.set_title("CV AUC Distribution Across Hyperparameter Iterations", fontweight="bold")
ax.yaxis.grid(True, linestyle="--", alpha=0.7)
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "cv_auc_distribution.png"), dpi=150)
plt.close()
print("Saved: cv_auc_distribution.png")

# ================================================================
# GRAPH G — Top 20 search iterations
# ================================================================

fig, axes = plt.subplots(2, 3, figsize=(18, 10))
axes = axes.flatten()
for ax, (name, cv_df) in zip(axes, cv_results.items()):
    top20 = cv_df.sort_values("mean_test_score", ascending=False).head(20)
    ax.scatter(range(len(top20)), top20["mean_test_score"],
               c=COLORS[name], s=60, edgecolors="black", linewidth=0.5)
    ax.fill_between(
        range(len(top20)),
        top20["mean_test_score"] - top20["std_test_score"],
        top20["mean_test_score"] + top20["std_test_score"],
        alpha=0.2, color=COLORS[name]
    )
    ax.set_title(f"{name} — Top Configs", fontweight="bold")
    ax.set_xlabel("Rank")
    ax.set_ylabel("Mean CV AUC")
    ax.yaxis.grid(True, linestyle="--", alpha=0.5)
for ax in axes[len(cv_results):]:
    ax.set_visible(False)
plt.suptitle("Top Hyperparameter Search Iterations (±1 std)", fontsize=13, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "hyperparam_search_top20.png"), dpi=150)
plt.close()
print("Saved: hyperparam_search_top20.png")

# ================================================================
# GRAPH H — Learning curves
# ================================================================

section("9 / 15  —  Learning curves")

fig, axes = plt.subplots(2, 3, figsize=(18, 10))
axes = axes.flatten()
for ax, (name, model) in zip(axes, best_models.items()):
    if name == "SVM":
        ax.text(0.5, 0.5, "Learning curve skipped\nfor SVM\n(computational cost)",
                ha="center", va="center", transform=ax.transAxes, fontsize=11,
                color="gray")
        ax.set_title(f"{name} — Learning Curve", fontweight="bold")
        continue
    train_sizes, train_scores, val_scores = learning_curve(
        model, X_tr, y_tr,
        train_sizes=np.linspace(0.1, 1.0, 8),
        cv=GroupKFold(n_splits=5),
        groups=groups_tr,
        scoring="roc_auc",
        n_jobs=-1,
    )
    t_mean, t_std = train_scores.mean(axis=1), train_scores.std(axis=1)
    v_mean, v_std = val_scores.mean(axis=1),   val_scores.std(axis=1)

    ax.plot(train_sizes, t_mean, "o-", color=COLORS[name], label="Train AUC")
    ax.fill_between(train_sizes, t_mean - t_std, t_mean + t_std, alpha=0.2, color=COLORS[name])
    ax.plot(train_sizes, v_mean, "s--", color="gray", label="CV AUC")
    ax.fill_between(train_sizes, v_mean - v_std, v_mean + v_std, alpha=0.15, color="gray")
    ax.set_title(f"{name} — Learning Curve", fontweight="bold")
    ax.set_xlabel("Training Examples")
    ax.set_ylabel("AUC-ROC")
    ax.legend()
    ax.yaxis.grid(True, linestyle="--", alpha=0.5)
    ax.set_ylim(0.4, 1.05)

for ax in axes[len(best_models):]:
    ax.set_visible(False)
plt.suptitle("Learning Curves (Train vs CV AUC)", fontsize=13, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "learning_curves.png"), dpi=150)
plt.close()
print("Saved: learning_curves.png")

# ================================================================
# 10. THRESHOLD TUNING  (on X_val — never X_test)
#
# Two thresholds are tuned per model:
#   Best F1    — maximises F1 for the Responder class
#   Sens >=90% — minimum threshold that achieves 90% sensitivity
#                (clinically: miss no more than 10% of Responders)
# ================================================================

section("10 / 15  —  Threshold tuning on validation set")

best_thresholds = {}

for name, model in best_models.items():
    y_prob_val = model.predict_proba(X_val)[:, 1]
    prec_arr, rec_arr, thresh_arr = precision_recall_curve(y_val, y_prob_val)

    f1_arr   = 2 * prec_arr * rec_arr / (prec_arr + rec_arr + 1e-9)
    f1_cands = np.where(f1_arr[:-1] == f1_arr[:-1].max())[0]
    f1_thresh = float(thresh_arr[f1_cands[0]]) if len(f1_cands) > 0 else 0.5

    s90_cands = np.where(rec_arr >= 0.90)[0]
    if len(s90_cands) == 0:
        s90_thresh = 0.5
        print(f"  WARNING: {name} cannot reach 90% sensitivity on val set — using 0.5")
    else:
        s90_thresh = float(thresh_arr[s90_cands[0]])

    best_thresholds[name] = {"Best F1": f1_thresh, "Sens ≥90%": s90_thresh}
    print(f"  {name:15s}  Best-F1: {f1_thresh:.3f}  |  Sens≥90%: {s90_thresh:.3f}")

# ================================================================
# 11. REFIT ON FULL TRAIN+VAL  (hyperparameters locked)
# ================================================================

section("11 / 15  —  Refit on full train+val")
deploy_models = {name: copy.deepcopy(model) for name, model in best_models.items()}
for name in deploy_models:
    t0 = time.time()
    deploy_models[name].fit(X_trainval, y_trainval)
    print(f"  {name}  ✓  ({time.time()-t0:.0f}s)")

joblib.dump(best_thresholds, os.path.join(OUTPUT_DIR, "best_thresholds.pkl"))
print("Saved: best_thresholds.pkl")

# ================================================================
# 12. EVALUATION ON HELD-OUT TEST SET
# ================================================================

section("12 / 15  —  Evaluation on held-out test set")

results = {}

for name, model in best_models.items():
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]
    report = classification_report(y_test, y_pred, output_dict=True)

    results[name] = {
        "AUC-ROC":       round(roc_auc_score(y_test, y_prob), 4),
        "Avg Precision": round(average_precision_score(y_test, y_prob), 4),
        "Sensitivity":   round(recall_score(y_test, y_pred), 4),
        "Specificity":   round(recall_score(y_test, y_pred, pos_label=0), 4),
        "F1 (Resp)":     round(report["1"]["f1-score"], 4),
        "Precision":     round(report["1"]["precision"], 4),
    }

results_df = pd.DataFrame(results).T
print(f"\n{results_df.to_string()}")
results_df.to_csv(os.path.join(OUTPUT_DIR, "model_comparison.csv"))

# Threshold-tuned results
print(f"\n{'='*55}")
print("  THRESHOLD-TUNED RESULTS ON TEST SET")
print(f"{'='*55}")

threshold_results = {}
for name, model in best_models.items():
    y_prob = model.predict_proba(X_test)[:, 1]
    for label, thresh in best_thresholds[name].items():
        y_pred_t = (y_prob >= thresh).astype(int)
        threshold_results[f"{name} ({label})"] = {
            "Threshold":   round(thresh, 3),
            "Sensitivity": round(recall_score(y_test, y_pred_t, zero_division=0), 4),
            "Specificity": round(recall_score(y_test, y_pred_t, pos_label=0, zero_division=0), 4),
            "F1 (Resp)":   round(f1_score(y_test, y_pred_t, zero_division=0), 4),
        }

thresh_df = pd.DataFrame(threshold_results).T
print(f"\n{thresh_df.to_string()}")
thresh_df.to_csv(os.path.join(OUTPUT_DIR, "threshold_tuned_results.csv"))

# ================================================================
# GRAPH I — ROC Curves + AUC bar chart
# ================================================================

fig, axes = plt.subplots(1, 2, figsize=(15, 6))

for name, model in best_models.items():
    y_prob = model.predict_proba(X_test)[:, 1]
    RocCurveDisplay.from_predictions(y_test, y_prob, ax=axes[0], name=name, color=COLORS[name])

axes[0].plot([0, 1], [0, 1], "k--", label="Random Chance")
axes[0].set_title("ROC Curves — All 3 Models", fontweight="bold")
axes[0].legend()

aucs = {n: results[n]["AUC-ROC"] for n in best_models}
bars = axes[1].bar(aucs.keys(), aucs.values(), color=list(COLORS.values()), width=0.4)
axes[1].set_ylim(0.5, 1.0)
axes[1].set_ylabel("AUC-ROC")
axes[1].set_title("AUC-ROC Comparison", fontweight="bold")
for bar, v in zip(bars, aucs.values()):
    axes[1].text(bar.get_x() + bar.get_width()/2, v + 0.005,
                 f"{v:.4f}", ha="center", fontweight="bold", fontsize=11)

plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "roc_curves.png"), dpi=150)
plt.close()
print("\nSaved: roc_curves.png")

# ================================================================
# GRAPH J — Precision-Recall Curves
# ================================================================

fig, axes = plt.subplots(1, 2, figsize=(15, 6))
for name, model in best_models.items():
    y_prob = model.predict_proba(X_test)[:, 1]
    PrecisionRecallDisplay.from_predictions(y_test, y_prob, ax=axes[0], name=name, color=COLORS[name])
axes[0].set_title("Precision-Recall Curves", fontweight="bold")
axes[0].legend()

avg_precs = {n: results[n]["Avg Precision"] for n in best_models}
bars = axes[1].bar(avg_precs.keys(), avg_precs.values(), color=list(COLORS.values()), width=0.4)
axes[1].set_ylim(0, 1.0)
axes[1].set_ylabel("Average Precision")
axes[1].set_title("Average Precision Comparison", fontweight="bold")
for bar, v in zip(bars, avg_precs.values()):
    axes[1].text(bar.get_x() + bar.get_width()/2, v + 0.01,
                 f"{v:.4f}", ha="center", fontweight="bold", fontsize=11)

plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "precision_recall_curves.png"), dpi=150)
plt.close()
print("Saved: precision_recall_curves.png")

# ================================================================
# GRAPH K — Confusion Matrices (Best F1 threshold)
# ================================================================

fig, axes = plt.subplots(2, 3, figsize=(18, 10))
axes = axes.flatten()
for ax, (name, model) in zip(axes, best_models.items()):
    y_prob   = model.predict_proba(X_test)[:, 1]
    thresh   = best_thresholds[name]["Best F1"]
    y_pred_t = (y_prob >= thresh).astype(int)
    ConfusionMatrixDisplay(
        confusion_matrix(y_test, y_pred_t),
        display_labels=["Non-Responder", "Responder"],
    ).plot(ax=ax, colorbar=False)
    ax.set_title(f"{name}\n(threshold = {thresh:.3f})", fontweight="bold")

for ax in axes[len(best_models):]:
    ax.set_visible(False)
plt.suptitle("Confusion Matrices — Best F1 Threshold", fontsize=14, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "confusion_matrices.png"), dpi=150)
plt.close()
print("Saved: confusion_matrices.png")

# ================================================================
# GRAPH L — Confusion Matrices (Sens >=90% threshold)
# ================================================================

fig, axes = plt.subplots(2, 3, figsize=(18, 10))
axes = axes.flatten()
for ax, (name, model) in zip(axes, best_models.items()):
    y_prob   = model.predict_proba(X_test)[:, 1]
    thresh   = best_thresholds[name]["Sens ≥90%"]
    y_pred_t = (y_prob >= thresh).astype(int)
    ConfusionMatrixDisplay(
        confusion_matrix(y_test, y_pred_t),
        display_labels=["Non-Responder", "Responder"],
    ).plot(ax=ax, colorbar=False)
    ax.set_title(f"{name}\n(threshold = {thresh:.3f}, Sens ≥90%)", fontweight="bold")

for ax in axes[len(best_models):]:
    ax.set_visible(False)
plt.suptitle("Confusion Matrices — Sensitivity ≥90% Threshold", fontsize=14, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "confusion_matrices_sens90.png"), dpi=150)
plt.close()
print("Saved: confusion_matrices_sens90.png")

# ================================================================
# GRAPH M — Threshold sweep
# ================================================================

fig, axes = plt.subplots(2, 3, figsize=(18, 10))
axes = axes.flatten()
for ax, (name, model) in zip(axes, best_models.items()):
    y_prob  = model.predict_proba(X_test)[:, 1]
    threshs = np.linspace(0.01, 0.99, 100)
    sens_list, spec_list, f1_list = [], [], []
    for t in threshs:
        y_pred_t = (y_prob >= t).astype(int)
        sens_list.append(recall_score(y_test, y_pred_t, zero_division=0))
        spec_list.append(recall_score(y_test, y_pred_t, pos_label=0, zero_division=0))
        f1_list.append(f1_score(y_test, y_pred_t, zero_division=0))

    ax.plot(threshs, sens_list, label="Sensitivity",  color="#EF5350")
    ax.plot(threshs, spec_list, label="Specificity",  color="#42A5F5")
    ax.plot(threshs, f1_list,   label="F1 Score",     color="#66BB6A")
    ax.axvline(best_thresholds[name]["Best F1"],
               color="green", linestyle="--", linewidth=1.5,
               label=f"Best F1 ({best_thresholds[name]['Best F1']:.2f})")
    ax.axvline(best_thresholds[name]["Sens ≥90%"],
               color="red", linestyle=":", linewidth=1.5,
               label=f"Sens≥90% ({best_thresholds[name]['Sens ≥90%']:.2f})")
    ax.set_title(f"{name}", fontweight="bold")
    ax.set_xlabel("Threshold")
    ax.set_ylabel("Score")
    ax.legend(fontsize=7)
    ax.yaxis.grid(True, linestyle="--", alpha=0.5)

for ax in axes[len(best_models):]:
    ax.set_visible(False)
plt.suptitle("Sensitivity / Specificity / F1 vs Decision Threshold", fontsize=13, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "threshold_sweep.png"), dpi=150)
plt.close()
print("Saved: threshold_sweep.png")

# ================================================================
# GRAPH N — Multi-metric comparison bar chart
# ================================================================

metrics = ["AUC-ROC", "Sensitivity", "Specificity", "F1 (Resp)", "Precision"]
x       = np.arange(len(metrics))
n_m     = len(results)
width   = 0.8 / n_m
fig, ax = plt.subplots(figsize=(16, 6))

for i, (name, color) in enumerate(COLORS.items()):
    if name not in results:
        continue
    vals = [results[name][m] for m in metrics]
    offset = (i - n_m / 2 + 0.5) * width
    bars = ax.bar(x + offset, vals, width, label=name, color=color, alpha=0.85)
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width()/2, v + 0.004,
                f"{v:.2f}", ha="center", va="bottom", fontsize=6, fontweight="bold",
                rotation=90)

ax.set_xticks(x)
ax.set_xticklabels(metrics)
ax.set_ylim(0, 1.18)
ax.set_ylabel("Score")
ax.set_title("Model Performance Comparison — All Metrics", fontweight="bold")
ax.legend(loc="lower right", fontsize=8)
ax.yaxis.grid(True, linestyle="--", alpha=0.5)
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "metrics_comparison.png"), dpi=150)
plt.close()
print("Saved: metrics_comparison.png")

# ================================================================
# GRAPH O — Calibration curves
# ================================================================

fig, axes = plt.subplots(2, 3, figsize=(18, 10))
axes = axes.flatten()
for ax, (name, model) in zip(axes, best_models.items()):
    y_prob = model.predict_proba(X_test)[:, 1]
    prob_true, prob_pred = calibration_curve(y_test, y_prob, n_bins=10)
    ax.plot(prob_pred, prob_true, "o-", color=COLORS[name], label=name, linewidth=2)
    ax.plot([0, 1], [0, 1], "k--", label="Perfect calibration")
    ax.set_title(f"{name} — Calibration", fontweight="bold")
    ax.set_xlabel("Mean Predicted Probability")
    ax.set_ylabel("Fraction of Positives")
    ax.legend()
    ax.yaxis.grid(True, linestyle="--", alpha=0.5)

for ax in axes[len(best_models):]:
    ax.set_visible(False)
plt.suptitle("Calibration Curves (Reliability Diagrams)", fontsize=13, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "calibration_curves.png"), dpi=150)
plt.close()
print("Saved: calibration_curves.png")

# ================================================================
# GRAPH P — Predicted probability distributions
# ================================================================

fig, axes = plt.subplots(2, 3, figsize=(18, 10))
axes = axes.flatten()
for ax, (name, model) in zip(axes, best_models.items()):
    y_prob = model.predict_proba(X_test)[:, 1]
    ax.hist(y_prob[y_test == 0], bins=40, alpha=0.6, color="#90CAF9",
            label="Non-Responder", density=True)
    ax.hist(y_prob[y_test == 1], bins=40, alpha=0.6, color="#EF9A9A",
            label="Responder", density=True)
    ax.axvline(best_thresholds[name]["Best F1"], color="green", linestyle="--",
               label=f"Best F1 ({best_thresholds[name]['Best F1']:.2f})")
    ax.axvline(best_thresholds[name]["Sens ≥90%"], color="red", linestyle=":",
               label=f"Sens≥90% ({best_thresholds[name]['Sens ≥90%']:.2f})")
    ax.set_title(f"{name}", fontweight="bold")
    ax.set_xlabel("Predicted Probability of Response")
    ax.set_ylabel("Density")
    ax.legend(fontsize=7)

for ax in axes[len(best_models):]:
    ax.set_visible(False)
plt.suptitle("Predicted Probability Distributions by Response Class", fontsize=13, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "probability_distributions.png"), dpi=150)
plt.close()
print("Saved: probability_distributions.png")

# ================================================================
# 13. AGE-STRATIFIED EVALUATION — Paediatric vs Adult
# ================================================================

section("13 / 15  —  Age-stratified evaluation (paediatric vs adult)")

PAED_CUTOFF = 18
paed_mask  = (X_test["age"] < PAED_CUTOFF).values
adult_mask = (X_test["age"] >= PAED_CUTOFF).values

print(f"  Paediatric (<{PAED_CUTOFF}y): {paed_mask.sum():,} rows")
print(f"  Adult     (≥{PAED_CUTOFF}y): {adult_mask.sum():,} rows")

strat_results = {}
for name, model in best_models.items():
    y_prob_all = model.predict_proba(X_test)[:, 1]
    y_pred_all = model.predict(X_test)

    for label, mask in [("Paediatric", paed_mask), ("Adult", adult_mask)]:
        y_t  = y_test.values[mask]
        y_pr = y_prob_all[mask]
        y_pd = y_pred_all[mask]

        if len(np.unique(y_t)) < 2:
            print(f"  WARNING: {name} {label} — only one class present, skipping.")
            continue

        report_s = classification_report(y_t, y_pd, output_dict=True)
        strat_results[f"{name} ({label})"] = {
            "n":           int(mask.sum()),
            "AUC-ROC":     round(roc_auc_score(y_t, y_pr), 4),
            "Sensitivity": round(recall_score(y_t, y_pd), 4),
            "Specificity": round(recall_score(y_t, y_pd, pos_label=0), 4),
            "F1 (Resp)":   round(report_s.get("1", {}).get("f1-score", 0.0), 4),
        }

strat_df = pd.DataFrame(strat_results).T
print(f"\n{strat_df.to_string()}")
strat_df.to_csv(os.path.join(OUTPUT_DIR, "age_stratified_results.csv"))
print("Saved: age_stratified_results.csv")

metric_cols = ["AUC-ROC", "Sensitivity", "Specificity", "F1 (Resp)"]
x     = np.arange(len(metric_cols))
width = 0.35

fig, axes = plt.subplots(2, 3, figsize=(18, 12))
axes = axes.flatten()
for ax, name in zip(axes, best_models.keys()):
    paed_key  = f"{name} (Paediatric)"
    adult_key = f"{name} (Adult)"
    paed_vals  = [strat_df.loc[paed_key,  m] if paed_key  in strat_df.index else 0.0 for m in metric_cols]
    adult_vals = [strat_df.loc[adult_key, m] if adult_key in strat_df.index else 0.0 for m in metric_cols]
    n_paed  = int(strat_df.loc[paed_key,  "n"]) if paed_key  in strat_df.index else 0
    n_adult = int(strat_df.loc[adult_key, "n"]) if adult_key in strat_df.index else 0

    b1 = ax.bar(x - width/2, paed_vals,  width, label=f"Paediatric (n={n_paed})",  color="#1976D2", alpha=0.85)
    b2 = ax.bar(x + width/2, adult_vals, width, label=f"Adult (n={n_adult})",       color="#FF7043", alpha=0.85)
    for bar, v in zip(b1, paed_vals):
        ax.text(bar.get_x() + bar.get_width()/2, v + 0.01, f"{v:.2f}", ha="center", fontsize=7, fontweight="bold")
    for bar, v in zip(b2, adult_vals):
        ax.text(bar.get_x() + bar.get_width()/2, v + 0.01, f"{v:.2f}", ha="center", fontsize=7, fontweight="bold")
    ax.set_title(f"{name}", fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(metric_cols, fontsize=8)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("Score")
    ax.legend(fontsize=8)
    ax.yaxis.grid(True, linestyle="--", alpha=0.5)

for ax in axes[len(best_models):]:
    ax.set_visible(False)
plt.suptitle(f"Age-Stratified Performance — Paediatric (<{PAED_CUTOFF}y) vs Adult",
             fontsize=13, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "age_stratified_comparison.png"), dpi=150)
plt.close()
print("Saved: age_stratified_comparison.png")

# ================================================================
# 14. SHAP FEATURE IMPORTANCE
# ================================================================

section("14 / 15  —  Feature importance (SHAP + permutation)")

shap_vals_dict = {}

for name, model in best_models.items():
    imputer    = model.named_steps["imputer"]
    clf        = model.named_steps["clf"]
    X_test_imp = pd.DataFrame(imputer.transform(X_test), columns=FEATURES)

    if isinstance(clf, (xgb.XGBClassifier, lgb.LGBMClassifier,
                        RandomForestClassifier, GradientBoostingClassifier)):
        # Tree-based: fast exact SHAP via TreeExplainer
        explainer   = shap.TreeExplainer(clf)
        shap_values = explainer(X_test_imp)
        shap_vals_dict[name] = shap_values

        plt.figure(figsize=(10, 7))
        shap.summary_plot(shap_values, X_test_imp, show=False)
        plt.title(f"SHAP Feature Importance — {name}", fontweight="bold", pad=14)
        plt.tight_layout()
        fname = f"shap_{name.lower().replace(' ', '_')}.png"
        plt.savefig(os.path.join(OUTPUT_DIR, fname), dpi=150, bbox_inches="tight")
        plt.close()
        print(f"Saved: {fname}")

    elif isinstance(clf, LogisticRegression):
        # Linear model: LinearExplainer — pass scaler-transformed data if present
        scaler      = model.named_steps.get("scaler")
        X_train_imp = pd.DataFrame(imputer.transform(X_tr), columns=FEATURES)
        if scaler is not None:
            X_train_proc = pd.DataFrame(scaler.transform(X_train_imp), columns=FEATURES)
            X_test_proc  = pd.DataFrame(scaler.transform(X_test_imp),  columns=FEATURES)
        else:
            X_train_proc = X_train_imp
            X_test_proc  = X_test_imp
        explainer   = shap.LinearExplainer(clf, X_train_proc)
        shap_values = explainer(X_test_proc)
        shap_vals_dict[name] = shap_values

        plt.figure(figsize=(10, 7))
        shap.summary_plot(shap_values, X_test_imp, show=False)
        plt.title(f"SHAP Feature Importance — {name}", fontweight="bold", pad=14)
        plt.tight_layout()
        fname = f"shap_{name.lower().replace(' ', '_')}.png"
        plt.savefig(os.path.join(OUTPUT_DIR, fname), dpi=150, bbox_inches="tight")
        plt.close()
        print(f"Saved: {fname}")

    else:
        # SVM: permutation importance (SHAP KernelExplainer too slow for thesis use)
        print(f"  {name}: computing permutation importance (SHAP skipped for SVM) ...")
        shap_vals_dict[name] = None
        perm = permutation_importance(
            model, X_test, y_test,
            n_repeats=10, random_state=42, scoring="roc_auc", n_jobs=-1,
        )
        perm_sorted = perm.importances_mean.argsort()
        fig, ax = plt.subplots(figsize=(10, 7))
        ax.barh(
            [FEATURES[i] for i in perm_sorted],
            perm.importances_mean[perm_sorted],
            xerr=perm.importances_std[perm_sorted],
            color=COLORS[name], alpha=0.85,
        )
        ax.set_title(f"Permutation Importance — {name}", fontweight="bold")
        ax.set_xlabel("Mean AUC decrease when feature shuffled")
        ax.xaxis.grid(True, linestyle="--", alpha=0.5)
        plt.tight_layout()
        fname = f"perm_importance_{name.lower().replace(' ', '_')}.png"
        plt.savefig(os.path.join(OUTPUT_DIR, fname), dpi=150, bbox_inches="tight")
        plt.close()
        print(f"Saved: {fname}")

# SHAP bar comparison — tree models + LR only
shap_models = {n: sv for n, sv in shap_vals_dict.items() if sv is not None}
n_shap = len(shap_models)
if n_shap > 0:
    cols = min(n_shap, 3)
    rows = int(np.ceil(n_shap / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(7 * cols, 7 * rows))
    axes = np.array(axes).flatten()
    for ax, (name, shap_values) in zip(axes, shap_models.items()):
        vals = shap_values.values
        if vals.ndim == 3:
            vals = vals[:, :, 1]
        mean_abs = pd.Series(np.abs(vals).mean(axis=0), index=FEATURES).sort_values()
        mean_abs.plot(kind="barh", ax=ax, color=COLORS[name], alpha=0.85)
        ax.set_title(f"{name}", fontweight="bold")
        ax.set_xlabel("Mean |SHAP value|")
        ax.xaxis.grid(True, linestyle="--", alpha=0.5)
    for ax in axes[n_shap:]:
        ax.set_visible(False)
    plt.suptitle("SHAP Mean Absolute Feature Importance", fontsize=13, fontweight="bold")
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "shap_bar_comparison.png"), dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved: shap_bar_comparison.png")

# Gini vs SHAP for Random Forest
if "Random Forest" in shap_vals_dict and shap_vals_dict["Random Forest"] is not None:
    rf_clf      = best_models["Random Forest"].named_steps["clf"]
    rf_imp_gini = pd.Series(rf_clf.feature_importances_, index=FEATURES).sort_values()
    rf_shap     = shap_vals_dict["Random Forest"].values
    if rf_shap.ndim == 3:
        rf_shap = rf_shap[:, :, 1]
    rf_imp_shap = pd.Series(np.abs(rf_shap).mean(axis=0), index=FEATURES).sort_values()

    fig, axes = plt.subplots(1, 2, figsize=(16, 7))
    rf_imp_gini.plot(kind="barh", ax=axes[0], color="#FF9800", alpha=0.85)
    axes[0].set_title("Random Forest — Gini Importance", fontweight="bold")
    axes[0].set_xlabel("Mean Decrease in Impurity")
    rf_imp_shap.plot(kind="barh", ax=axes[1], color="#FF9800", alpha=0.6)
    axes[1].set_title("Random Forest — SHAP Importance", fontweight="bold")
    axes[1].set_xlabel("Mean |SHAP value|")
    plt.suptitle("Gini vs SHAP Feature Importance (Random Forest)", fontsize=13, fontweight="bold")
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "rf_gini_vs_shap.png"), dpi=150)
    plt.close()
    print("Saved: rf_gini_vs_shap.png")

# ================================================================
# 15. SAVE MODELS + RUN METADATA
# ================================================================

section("15 / 15  —  Saving models and run metadata")

for name, model in deploy_models.items():
    fname = name.lower().replace(" ", "_") + ".pkl"
    joblib.dump(model, os.path.join(OUTPUT_DIR, fname))
    print(f"Saved: {fname}")

best_name = results_df["AUC-ROC"].idxmax()
print(f"\n  Best model: {best_name}")
print(results_df.loc[best_name].to_string())

meta = {
    "trained_at":          datetime.datetime.now().isoformat(),
    "data_path":           DATA_PATH,
    "study_parameters": {
        "min_days_on_hu":      MIN_DAYS_ON_HU,
        "response_threshold_gdl": RESPONSE_THRESHOLD,
        "target_definition": (
            f"Responder_next_visit: response at visit t where days_on_hu(t) >= {MIN_DAYS_ON_HU}, "
            f"defined as hgb(t) - baseline_hgb >= {RESPONSE_THRESHOLD} g/dL; "
            f"predicted using features from visit t-1 only"
        ),
    },
    "dataset": {
        "n_total":    len(df),
        "n_patients": int(df["Case_ID"].nunique()),
        "n_responders":     int(df[TARGET].sum()),
        "n_non_responders": int((df[TARGET] == 0).sum()),
        "responder_pct":    round(df[TARGET].mean() * 100, 1),
    },
    "splits": {
        "n_train": len(X_tr),
        "n_val":   len(X_val),
        "n_test":  len(X_test),
    },
    "features":   FEATURES,
    "cv_aucs":    cv_aucs,
    "test_aucs":  {n: results[n]["AUC-ROC"] for n in results},
    "best_model": best_name,
    "thresholds": {
        name: {k: round(v, 4) for k, v in threshs.items()}
        for name, threshs in best_thresholds.items()
    },
}

with open(os.path.join(OUTPUT_DIR, "run_metadata.json"), "w") as f:
    json.dump(meta, f, indent=2)
print("Saved: run_metadata.json")

done("Pipeline complete.")
print(f"\n  All outputs saved to: {OUTPUT_DIR}")
