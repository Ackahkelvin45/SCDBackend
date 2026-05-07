# Resolve working directory to the project root (SCDBackend/) regardless
# of where Rscript is invoked from
args        <- commandArgs(trailingOnly = FALSE)
script_path <- sub("--file=", "", args[grep("--file=", args)])
script_dir  <- dirname(normalizePath(script_path))
setwd(file.path(script_dir, "../.."))

required_packages <- c("dplyr", "readxl", "tableone")

missing_packages <- required_packages[!(required_packages %in% installed.packages()[, "Package"])]

if (length(missing_packages) > 0) {
  install.packages(missing_packages, repos = "https://cloud.r-project.org")
}

library(dplyr)
library(readxl)
library(tableone)

# ===============================================================
# 1. LOAD DATA
# ===============================================================

df <- read_excel("ML/data/data.xlsx", sheet = "Append1")

# ===============================================================
# 2. DROP PHANTOM ROWS
# ===============================================================

df <- df %>%
  filter(!is.na(`Case ID`) & `Case ID` != "")

print(df)

# ===============================================================
# 3. STANDARDIZE COLUMN NAMES
# ===============================================================

df <- df %>%
  rename(
    Case_ID              = `Case ID`,
    Hu_Start             = `Hu Start(0/1)`,
    Facility_name        = `Name of Facility`,
    Registration_Date    = `Date Patient Registration`,
    Clinic_Visit_Date    = `Clinic Visit Date`,
    Lab_Visit_Date       = `Lab Visit Date`,
    gender               = patient_gender,
    age                  = patient_age_in_years,
    dob                  = patient_date_of_birth,
    hb_genotype          = `Hb Genotype`,
    hgb                  = `hgb(g/dl)`,
    rbc                  = `rbc *10^12`,
    mcv                  = `mcv (fl)`,
    toxicity             = form.toxicity,
    experienced_pain     = history.experienced_pain,
    num_pain_events      = history.num_pain_events
  )

# ===============================================================
# 4. DROP COLUMNS THAT SHOULD NEVER BE USED
# ===============================================================

drop_cols <- c(
  # Target leakage
  "hu_start_date",
  # PII
  "patient_full_name",
  "patient_med_record_num",
  # Too empty to use
  "history.pain_history_notes",
  "Registration_Date",
  # Unnamed / meaningless
  "Index",
  "Column1",
  "Column2",
  # Uninformative form field
  "form.blood_test_results.select_labs",
  # Duplicate unit columns — keep anc, arc, wbc, hct; drop all SI/conventional variants
  "anc (SI Unit) 10^9/L",
  "anc (Conv. Unit)",
  "arc (SI unit) 10^9/L",
  "arc (Conv. unit)",
  "wbc (SI Unit) 10^9/L",
  "hct (SI unit)",
  "hct(%)"
)

df <- df %>%
  select(-any_of(drop_cols))

# ===============================================================
# 5. FILTER TO LABORATORY VISIT ROWS ONLY
# ===============================================================

df <- df %>%
  filter(`Visit type` == "Lab")

# ===============================================================
# 6. REMOVE DUPLICATES
# ===============================================================

df <- df[!duplicated(df), ]

# ===============================================================
# 7. CONVERT AND STANDARDISE DATA TYPES
# ===============================================================

# --- Dates ---
# readxl already parses date-formatted cells as POSIXct (<dttm>).
# as.Date() converts POSIXct → Date cleanly. The old pattern of
# as.Date(as.numeric(...), origin="1899-12-30") was treating POSIX
# seconds as days, producing dates millions of years in the future.
df$Lab_Visit_Date    <- as.Date(df$Lab_Visit_Date)
df$Clinic_Visit_Date <- as.Date(df$Clinic_Visit_Date)
df$dob               <- as.Date(df$dob)

# Drop rows where Lab_Visit_Date is NA — these cannot be placed correctly
# in the visit timeline, so their lag features (hgb_prev, hgb_delta, etc.)
# would be wrong regardless of sort order.
n_before <- nrow(df)
df <- df %>% filter(!is.na(Lab_Visit_Date))
cat(sprintf("Dropped %d rows with missing Lab_Visit_Date\n", n_before - nrow(df)))

# --- Lab values ---
lab_cols <- c("hgb", "anc", "arc", "platelet_count", "rbc", "mcv", "hct", "wbc")

df[lab_cols] <- lapply(df[lab_cols], function(x) suppressWarnings(as.numeric(as.character(x))))

# --- Gender ---
df$gender <- tolower(trimws(df$gender))
df$gender[is.na(df$gender) | df$gender == ""] <- "unknown"
df$gender <- as.factor(df$gender)

# --- Hb genotype ---
df$hb_genotype <- tolower(trimws(df$hb_genotype))
df$hb_genotype <- factor(df$hb_genotype, levels = c("ss", "sc", "sbo", "dont_know"))

# --- Toxicity ---
df$toxicity <- factor(
  df$toxicity,
  levels = c("none", "low_anc", "low_hb", "low_platelet_count", "other")
)

# --- Age ---
df$age <- as.integer(df$age)

# ===============================================================
# 8. HANDLE MISSING LAB VALUES
# ===============================================================
# IMPORTANT:
# Do not impute here. Any imputation performed on the full dataset can
# leak information into the holdout set once we do patient-level splits
# in Python. Leave missing values as NA and impute inside the sklearn
# Pipeline fit on training data only.

# ===============================================================
# 9. FEATURE ENGINEERING
# ===============================================================

# --- Age group ---
df$age_group <- ifelse(df$age < 18, "Paediatric", "Adult")
df$age_group <- as.factor(df$age_group)

# --- Days in care ---
df <- df %>%
  group_by(Case_ID) %>%
  arrange(Lab_Visit_Date) %>%
  mutate(days_in_care = as.numeric(Lab_Visit_Date - min(Lab_Visit_Date))) %>%
  ungroup()

# --- Longitudinal lab features ---
df <- df %>%
  group_by(Case_ID) %>%
  arrange(Lab_Visit_Date) %>%
  mutate(
    hgb_delta = hgb - first(hgb),
    hgb_prev  = lag(hgb),
    wbc_prev  = lag(wbc),
    anc_prev  = lag(anc)
  ) %>%
  ungroup()

# ===============================================================
# 10. DESCRIPTIVE TABLE (TABLE 1)
# ===============================================================

# Collapse to one row per patient using the earliest lab visit
patient_df <- df %>%
  group_by(Case_ID) %>%
  arrange(Lab_Visit_Date) %>%
  slice(1) %>%
  ungroup()

cont_vars <- c("age", "hgb", "wbc", "anc", "platelet_count")
cat_vars  <- c("gender", "hb_genotype", "Facility_name", "Hu_Start")

table1 <- CreateTableOne(
  vars       = c(cont_vars, cat_vars),
  strata     = "Hu_Start",
  data       = patient_df,
  factorVars = cat_vars,
  addOverall = TRUE
)

print(table1, showAllLevels = TRUE, smd = TRUE)

# ===============================================================
# 11. EXPORT CLEAN CSV
# ===============================================================

# Sort globally before export so Python's lag integrity checks pass.
# group_by() + arrange() in dplyr sorts within groups for mutate() but
# does not guarantee the exported row order after ungroup().
df <- df %>%
  arrange(Case_ID, Lab_Visit_Date)

write.csv(df, "ML/data/hu_clean_data.csv", row.names = FALSE)
