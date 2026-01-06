# ===========================
# CloudWatch Log Anomaly Detection
# ===========================
# Automatically detect unusual patterns in Lambda function logs using machine learning.
# Anomalies are visible in CloudWatch console but do not trigger alarms initially.
# Requires 2-4 week learning period before accurate detection.
#
# Cost: FREE - anomaly detection is included with log ingestion, no additional charges.
# ===========================

# Map numeric frequency variable to AWS enum strings
locals {
  evaluation_frequency_map = {
    300  = "FIVE_MIN"
    900  = "FIFTEEN_MIN"
    1800 = "THIRTY_MIN"
    3600 = "ONE_HOUR"
  }
  evaluation_frequency = local.evaluation_frequency_map[var.anomaly_detection_evaluation_frequency]
}

# ===========================
# Anomaly Detectors for Lambda Functions
# ===========================

# Router Enrichment Lambda Anomaly Detector
# Monitors DynamoDB lookup patterns and routing decisions
resource "aws_cloudwatch_log_anomaly_detector" "router_enrichment" {
  count = var.anomaly_detection_enabled ? 1 : 0

  detector_name = "ses-mail-router-enrichment-${var.environment}"
  log_group_arn_list = [
    aws_cloudwatch_log_group.lambda_router_logs.arn
  ]

  evaluation_frequency = local.evaluation_frequency
  enabled              = true

  tags = {
    Name        = "router-enrichment-anomaly-detector-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# Gmail Forwarder Lambda Anomaly Detector
# Monitors Gmail API integration and email forwarding patterns
resource "aws_cloudwatch_log_anomaly_detector" "gmail_forwarder" {
  count = var.anomaly_detection_enabled ? 1 : 0

  detector_name = "ses-mail-gmail-forwarder-${var.environment}"
  log_group_arn_list = [
    aws_cloudwatch_log_group.lambda_gmail_forwarder_logs.arn
  ]

  evaluation_frequency = local.evaluation_frequency
  enabled              = true

  tags = {
    Name        = "gmail-forwarder-anomaly-detector-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# Bouncer Lambda Anomaly Detector
# Monitors email bounce handling patterns
resource "aws_cloudwatch_log_anomaly_detector" "bouncer" {
  count = var.anomaly_detection_enabled ? 1 : 0

  detector_name = "ses-mail-bouncer-${var.environment}"
  log_group_arn_list = [
    aws_cloudwatch_log_group.lambda_bouncer_logs.arn
  ]

  evaluation_frequency = local.evaluation_frequency
  enabled              = true

  tags = {
    Name        = "bouncer-anomaly-detector-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# SMTP Credential Manager Lambda Anomaly Detector
# Monitors security-sensitive IAM operations for SMTP user management
resource "aws_cloudwatch_log_anomaly_detector" "smtp_credential_manager" {
  count = var.anomaly_detection_enabled ? 1 : 0

  detector_name = "ses-mail-smtp-credential-manager-${var.environment}"
  log_group_arn_list = [
    aws_cloudwatch_log_group.lambda_smtp_credential_manager_logs.arn
  ]

  evaluation_frequency = local.evaluation_frequency
  enabled              = true

  tags = {
    Name        = "smtp-credential-manager-anomaly-detector-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# Outbound Metrics Publisher Lambda Anomaly Detector
# Monitors SES reputation metrics and outbound sending patterns
resource "aws_cloudwatch_log_anomaly_detector" "outbound_metrics_publisher" {
  count = var.anomaly_detection_enabled ? 1 : 0

  detector_name = "ses-mail-outbound-metrics-publisher-${var.environment}"
  log_group_arn_list = [
    aws_cloudwatch_log_group.lambda_outbound_metrics_logs.arn
  ]

  evaluation_frequency = local.evaluation_frequency
  enabled              = true

  tags = {
    Name        = "outbound-metrics-publisher-anomaly-detector-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# ===========================
# CloudWatch Alarms for High Severity Anomalies
# ===========================

# Router Enrichment High Severity Anomaly Alarm
resource "aws_cloudwatch_metric_alarm" "router_anomaly_high" {
  count = var.anomaly_detection_enabled ? 1 : 0

  alarm_name          = "ses-mail-router-anomaly-high-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "AnomalyCount"
  namespace           = "AWS/Logs"
  period              = 900 # 15 minutes
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when HIGH severity anomalies detected in router enrichment logs (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LogAnomalyDetector = aws_cloudwatch_log_anomaly_detector.router_enrichment[0].detector_name
    LogAnomalyPriority = "HIGH"
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "router-anomaly-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# Gmail Forwarder High Severity Anomaly Alarm
resource "aws_cloudwatch_metric_alarm" "gmail_forwarder_anomaly_high" {
  count = var.anomaly_detection_enabled ? 1 : 0

  alarm_name          = "ses-mail-gmail-forwarder-anomaly-high-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "AnomalyCount"
  namespace           = "AWS/Logs"
  period              = 900
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when HIGH severity anomalies detected in Gmail forwarder logs (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LogAnomalyDetector = aws_cloudwatch_log_anomaly_detector.gmail_forwarder[0].detector_name
    LogAnomalyPriority = "HIGH"
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "gmail-forwarder-anomaly-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# Bouncer High Severity Anomaly Alarm
resource "aws_cloudwatch_metric_alarm" "bouncer_anomaly_high" {
  count = var.anomaly_detection_enabled ? 1 : 0

  alarm_name          = "ses-mail-bouncer-anomaly-high-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "AnomalyCount"
  namespace           = "AWS/Logs"
  period              = 900
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when HIGH severity anomalies detected in bouncer logs (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LogAnomalyDetector = aws_cloudwatch_log_anomaly_detector.bouncer[0].detector_name
    LogAnomalyPriority = "HIGH"
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "bouncer-anomaly-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# SMTP Credential Manager High Severity Anomaly Alarm
resource "aws_cloudwatch_metric_alarm" "smtp_credential_manager_anomaly_high" {
  count = var.anomaly_detection_enabled ? 1 : 0

  alarm_name          = "ses-mail-smtp-credential-manager-anomaly-high-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "AnomalyCount"
  namespace           = "AWS/Logs"
  period              = 900
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when HIGH severity anomalies detected in SMTP credential manager logs (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LogAnomalyDetector = aws_cloudwatch_log_anomaly_detector.smtp_credential_manager[0].detector_name
    LogAnomalyPriority = "HIGH"
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "smtp-credential-manager-anomaly-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# Outbound Metrics Publisher High Severity Anomaly Alarm
resource "aws_cloudwatch_metric_alarm" "outbound_metrics_publisher_anomaly_high" {
  count = var.anomaly_detection_enabled ? 1 : 0

  alarm_name          = "ses-mail-outbound-metrics-publisher-anomaly-high-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "AnomalyCount"
  namespace           = "AWS/Logs"
  period              = 900
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when HIGH severity anomalies detected in outbound metrics publisher logs (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LogAnomalyDetector = aws_cloudwatch_log_anomaly_detector.outbound_metrics_publisher[0].detector_name
    LogAnomalyPriority = "HIGH"
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "outbound-metrics-publisher-anomaly-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# ===========================
# CloudWatch Alarms for Medium Severity Anomalies
# ===========================

# Router Enrichment Medium Severity Anomaly Alarm
resource "aws_cloudwatch_metric_alarm" "router_anomaly_medium" {
  count = var.anomaly_detection_enabled ? 1 : 0

  alarm_name          = "ses-mail-router-anomaly-medium-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "AnomalyCount"
  namespace           = "AWS/Logs"
  period              = 900 # 15 minutes
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when MEDIUM severity anomalies detected in router enrichment logs (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LogAnomalyDetector = aws_cloudwatch_log_anomaly_detector.router_enrichment[0].detector_name
    LogAnomalyPriority = "MEDIUM"
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "router-anomaly-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# Gmail Forwarder Medium Severity Anomaly Alarm
resource "aws_cloudwatch_metric_alarm" "gmail_forwarder_anomaly_medium" {
  count = var.anomaly_detection_enabled ? 1 : 0

  alarm_name          = "ses-mail-gmail-forwarder-anomaly-medium-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "AnomalyCount"
  namespace           = "AWS/Logs"
  period              = 900
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when MEDIUM severity anomalies detected in Gmail forwarder logs (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LogAnomalyDetector = aws_cloudwatch_log_anomaly_detector.gmail_forwarder[0].detector_name
    LogAnomalyPriority = "MEDIUM"
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "gmail-forwarder-anomaly-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# Bouncer Medium Severity Anomaly Alarm
resource "aws_cloudwatch_metric_alarm" "bouncer_anomaly_medium" {
  count = var.anomaly_detection_enabled ? 1 : 0

  alarm_name          = "ses-mail-bouncer-anomaly-medium-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "AnomalyCount"
  namespace           = "AWS/Logs"
  period              = 900
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when MEDIUM severity anomalies detected in bouncer logs (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LogAnomalyDetector = aws_cloudwatch_log_anomaly_detector.bouncer[0].detector_name
    LogAnomalyPriority = "MEDIUM"
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "bouncer-anomaly-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# SMTP Credential Manager Medium Severity Anomaly Alarm
resource "aws_cloudwatch_metric_alarm" "smtp_credential_manager_anomaly_medium" {
  count = var.anomaly_detection_enabled ? 1 : 0

  alarm_name          = "ses-mail-smtp-credential-manager-anomaly-medium-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "AnomalyCount"
  namespace           = "AWS/Logs"
  period              = 900
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when MEDIUM severity anomalies detected in SMTP credential manager logs (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LogAnomalyDetector = aws_cloudwatch_log_anomaly_detector.smtp_credential_manager[0].detector_name
    LogAnomalyPriority = "MEDIUM"
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "smtp-credential-manager-anomaly-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# Outbound Metrics Publisher Medium Severity Anomaly Alarm
resource "aws_cloudwatch_metric_alarm" "outbound_metrics_publisher_anomaly_medium" {
  count = var.anomaly_detection_enabled ? 1 : 0

  alarm_name          = "ses-mail-outbound-metrics-publisher-anomaly-medium-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "AnomalyCount"
  namespace           = "AWS/Logs"
  period              = 900
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when MEDIUM severity anomalies detected in outbound metrics publisher logs (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LogAnomalyDetector = aws_cloudwatch_log_anomaly_detector.outbound_metrics_publisher[0].detector_name
    LogAnomalyPriority = "MEDIUM"
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "outbound-metrics-publisher-anomaly-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}
