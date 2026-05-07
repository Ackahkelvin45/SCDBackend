# ===============================================================
# 0. LOAD LIBRARIES
# ===============================================================

library(dplyr)
library(ggplot2)
library(lubridate)
library(caret)
library(randomForest)
library(pROC)
library(e1071)
library(tableone)

# ===============================================================
# 1. LOAD DATA
# ===============================================================

df <- read.csv("hu_raw_data.csv", stringsAsFactors = FALSE)

# ===============================================================
# 2. STANDARDIZE COLUMN NAMES
# ===============================================================

df <- df %>%
  rename(
    Case_ID = Case.ID,
    Hu_Start = Hu.Start.0.1.,
    Facility_name = Name.of.Facility,
    Registration_Date = Date.Patient.Registration,
    Clinic_Visit_Date = Clinic.Visit.Date,
    Lab_Visit_Date = Lab.Visit.Date,
    gender = patient_gender,
    age = patient_age_in_years,
    dob = patient_date_of_birth,
    hgb = hgb.g.dl.,
    rbc = rbc..10.12,
    mcv = mcv..fl.
  )

# ===============================================================
# 3. REMOVE DUPLICATES
# ===============================================================

df <- df[!duplicated(df), ]

# ===============================================================
# 4. CONVERT DATES
# ===============================================================

df$Lab_Visit_Date <- as.Date(df$Lab_Visit_Date, origin="1899-12-30")
df$Registration_Date <- as.Date(df$Registration_Date, origin="1899-12-30")
df$Clinic_Visit_Date <- as.Date(df$Clinic_Visit_Date, origin="1899-12-30")
df$dob <- as.Date(df$dob, origin="1899-12-30")

# ===============================================================
# 5. CONVERT LAB VARIABLES TO NUMERIC
# ===============================================================

numeric_cols <- c("hgb","anc","arc","platelet_count","rbc","mcv","hct")

df[numeric_cols] <- lapply(df[numeric_cols], function(x){
  suppressWarnings(as.numeric(as.character(x)))
})

# ===============================================================
# 6. FEATURE ENGINEERING
# ===============================================================

df$age_in_years <- as.integer(df$age)

df$days_since_registration <- as.numeric(
  df$Lab_Visit_Date - df$Registration_Date
)

df$gender <- tolower(df$gender)
df$gender[is.na(df$gender)] <- "unknown"
df$gender <- as.factor(df$gender)

# ===============================================================
# 7. HANDLE MISSING LAB VALUES
# ===============================================================

lab_cols <- c("hgb","anc","arc","platelet_count","wbc","rbc","mcv","hct")

for(col in lab_cols){
  df[[col]][is.na(df[[col]])] <- median(df[[col]], na.rm=TRUE)
}

# ===============================================================
# 8. CREATE LONGITUDINAL FEATURES
# ===============================================================

df <- df %>%
  group_by(Case_ID) %>%
  arrange(Lab_Visit_Date) %>%
  mutate(
    hgb_delta = hgb - first(hgb),
    hgb_prev = lag(hgb),
    wbc_prev = lag(wbc),
    anc_prev = lag(anc)
  ) %>%
  ungroup()

lag_cols <- c("hgb_prev","wbc_prev","anc_prev")

for(col in lag_cols){
  df[[col]][is.na(df[[col]])] <- median(df[[col]], na.rm=TRUE)
}

# ===============================================================
# 9. DESCRIPTIVE TABLE
# ===============================================================

patient_df <- df %>%
  group_by(id_hu_study_number) %>%
  summarise(
    age_in_years = first(age_in_years),
    gender = first(gender),
    Facility_name = first(Facility_name),
    hgb = first(hgb),
    wbc = first(wbc),
    anc = first(anc),
    platelet_count = first(platelet_count),
    .groups="drop"
  )

cont_vars <- c("age_in_years","hgb","wbc","anc","platelet_count")
cat_vars <- c("gender","Facility_name")

table1 <- CreateTableOne(
  vars=c(cont_vars,cat_vars),
  data=patient_df,
  factorVars=cat_vars
)

print(table1, showAllLevels=TRUE)

# ===============================================================
# 10. PREPARE DATA FOR MODELING
# ===============================================================

features <- c(
  "Hu_Start",
  "age_in_years","gender",
  "hgb","wbc","anc","rbc","platelet_count",
  "mcv","hct","arc",
  "hgb_delta","hgb_prev","wbc_prev","anc_prev"
)

model_df <- df[,features]

model_df <- na.omit(model_df)

model_df$Hu_Start <- as.factor(model_df$Hu_Start)

print(table(model_df$Hu_Start))

# ===============================================================
# 11. TRAIN TEST SPLIT
# ===============================================================

set.seed(123)

train_index <- createDataPartition(
  model_df$Hu_Start,
  p=0.7,
  list=FALSE
)

train_df <- model_df[train_index,]
test_df <- model_df[-train_index,]

train_df$Hu_Start <- as.factor(train_df$Hu_Start)
test_df$Hu_Start <- as.factor(test_df$Hu_Start)

# ===============================================================
# 12. MODEL 1 — LOGISTIC REGRESSION
# ===============================================================

log_model <- glm(
  Hu_Start ~ age_in_years + gender + hgb + wbc + anc + platelet_count,
  data=train_df,
  family=binomial
)

log_prob <- predict(log_model,test_df,type="response")

log_pred <- ifelse(log_prob>0.5,1,0)

log_pred <- factor(log_pred, levels=c(0,1))
test_df$Hu_Start <- factor(test_df$Hu_Start, levels=c(0,1))

log_cm <- confusionMatrix(
  log_pred,
  test_df$Hu_Start,
  positive="1"
)

print(log_cm)

# ===============================================================
# 13. MODEL 2 — RANDOM FOREST (BALANCED)
# ===============================================================

class_counts <- table(train_df$Hu_Start)
min_class <- min(class_counts)

rf_model <- randomForest(
  Hu_Start~.,
  data=train_df,
  ntree=300,
  importance=TRUE,
  sampsize=rep(min_class,2)
)

rf_prob <- predict(rf_model,test_df,type="prob")[,2]

rf_pred <- ifelse(rf_prob>0.5,1,0)

rf_pred <- factor(rf_pred, levels=c(0,1))

rf_cm <- confusionMatrix(
  rf_pred,
  test_df$Hu_Start,
  positive="1"
)

print(rf_cm)

# ===============================================================
# 14. MODEL 3 — SUPPORT VECTOR MACHINE
# ===============================================================

svm_model <- svm(
  Hu_Start~.,
  data=train_df,
  kernel="radial",
  probability=TRUE
)

svm_pred <- predict(svm_model,test_df)

svm_cm <- confusionMatrix(
  svm_pred,
  test_df$Hu_Start,
  positive="1"
)

print(svm_cm)

# ===============================================================
# 15. MODEL COMPARISON
# ===============================================================

results <- data.frame(
  Model=c("Logistic Regression","Random Forest","SVM"),
  Accuracy=c(
    log_cm$overall["Accuracy"],
    rf_cm$overall["Accuracy"],
    svm_cm$overall["Accuracy"]
  ),
  Sensitivity=c(
    log_cm$byClass["Sensitivity"],
    rf_cm$byClass["Sensitivity"],
    svm_cm$byClass["Sensitivity"]
  ),
  Specificity=c(
    log_cm$byClass["Specificity"],
    rf_cm$byClass["Specificity"],
    svm_cm$byClass["Specificity"]
  )
)

print(results)

# ===============================================================
# 16. ROC CURVE
# ===============================================================

log_roc <- roc(as.numeric(test_df$Hu_Start),log_prob)
rf_roc <- roc(as.numeric(test_df$Hu_Start),rf_prob)

plot(log_roc,col="blue",lwd=2)
plot(rf_roc,col="red",lwd=2,add=TRUE)

legend(
  "bottomright",
  legend=c("Logistic Regression","Random Forest"),
  col=c("blue","red"),
  lwd=2
)

auc(log_roc)
auc(rf_roc)

# ===============================================================
# 17. BEST MODEL
# ===============================================================

best_model <- results[which.max(results$Accuracy),]

print("Best Performing Model")
print(best_model)




# ===============================================================
# 15. MODEL 4 — GRADIENT BOOSTING
# ===============================================================

library(gbm)

# Convert outcome to numeric for GBM
train_gbm <- train_df
test_gbm <- test_df

train_gbm$Hu_Start <- as.numeric(as.character(train_gbm$Hu_Start))
test_gbm$Hu_Start <- as.numeric(as.character(test_gbm$Hu_Start))

gbm_model <- gbm(
  Hu_Start ~ .,
  data=train_gbm,
  distribution="bernoulli",
  n.trees=300,
  interaction.depth=3,
  shrinkage=0.01,
  n.minobsinnode=10,
  verbose=FALSE
)

gbm_prob <- predict(
  gbm_model,
  test_gbm,
  n.trees=300,
  type="response"
)

gbm_pred <- ifelse(gbm_prob > 0.5,1,0)

gbm_pred <- factor(gbm_pred, levels=c(0,1))
test_gbm$Hu_Start <- factor(test_gbm$Hu_Start, levels=c(0,1))

gbm_cm <- confusionMatrix(
  gbm_pred,
  test_gbm$Hu_Start,
  positive="1"
)

print(gbm_cm)





# ===============================================================
# ===============================================================
# 16. MODEL 5 — XGBOOST
# ===============================================================

library(xgboost)

train_matrix <- model.matrix(Hu_Start ~ . - 1, train_df)
test_matrix  <- model.matrix(Hu_Start ~ . - 1, test_df)

train_label <- as.numeric(train_df$Hu_Start) - 1
test_label  <- as.numeric(test_df$Hu_Start) - 1

xgb_model <- xgboost(
  x = train_matrix,
  y = train_label,
  nrounds = 200,
  objective = "reg:logistic",
  eval_metric = "auc"
)

xgb_prob <- predict(xgb_model, test_matrix)

xgb_pred <- ifelse(xgb_prob > 0.5, 1, 0)

xgb_pred <- factor(xgb_pred, levels = c(0,1))
test_df$Hu_Start <- factor(test_df$Hu_Start, levels = c(0,1))

xgb_cm <- confusionMatrix(
  xgb_pred,
  test_df$Hu_Start,
  positive = "1"
)

print(xgb_cm)