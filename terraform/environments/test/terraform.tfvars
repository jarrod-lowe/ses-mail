# AWS Configuration
aws_region  = "ap-southeast-2"
environment = "test"

# Email Domain Configuration
domain            = ["testmail.rrod.net"]
mta_sts_mode      = "enforce"  # testing, enforce, or none

# Email Retention
email_retention_days = 90

# Alarm Configuration
alarm_sns_topic_arn            = "arn:aws:sns:ap-southeast-2:453430506965:Pushover"
alarm_email_count_threshold    = 100  # emails per 5 minutes
alarm_rejection_rate_threshold = 50   # percentage
