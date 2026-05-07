required <- c("rmarkdown", "ggplot2", "dplyr", "tableone", "tidyr", "scales", "stringr")
missing  <- required[!(required %in% installed.packages()[, "Package"])]
if (length(missing) > 0) install.packages(missing, repos = "https://cloud.r-project.org")

rmarkdown::render(
  input       = "data_report.Rmd",
  output_file = "../../ML/data/data_report.html"
)
