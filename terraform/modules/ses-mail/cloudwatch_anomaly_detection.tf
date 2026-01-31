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

# Canary Sender Lambda Anomaly Detector
# Monitors canary test email sending and DNS validation patterns
resource "aws_cloudwatch_log_anomaly_detector" "canary_sender" {
  count = var.anomaly_detection_enabled ? 1 : 0

  detector_name = "ses-mail-canary-sender-${var.environment}"
  log_group_arn_list = [
    aws_cloudwatch_log_group.lambda_canary_sender_logs.arn
  ]

  evaluation_frequency = local.evaluation_frequency
  enabled              = true

  tags = {
    Name        = "canary-sender-anomaly-detector-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
  }
}

# ===========================
# CloudWatch Alarms for Anomalies
# ===========================

# High Severity Anomaly Alarm (aggregated across ses-mail Lambda functions only)
# Uses metric math to sum anomalies from each detector, filtering out anomalies from other log groups
resource "aws_cloudwatch_metric_alarm" "anomaly_high" {
  count = var.anomaly_detection_enabled ? 1 : 0

  alarm_name          = "ses-mail-anomaly-high-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  alarm_description   = "HIGH severity anomalies in ses-mail Lambda logs (${var.environment})"
  treat_missing_data  = "notBreaching"

  # Router Enrichment Lambda
  metric_query {
    id          = "router"
    return_data = false
    metric {
      metric_name = "AnomalyCount"
      namespace   = "AWS/Logs"
      period      = 900
      stat        = "Sum"
      dimensions = {
        LogAnomalyDetector = "ses-mail-router-enrichment-${var.environment}"
        LogAnomalyPriority = "HIGH"
      }
    }
  }

  # Gmail Forwarder Lambda
  metric_query {
    id          = "gmail"
    return_data = false
    metric {
      metric_name = "AnomalyCount"
      namespace   = "AWS/Logs"
      period      = 900
      stat        = "Sum"
      dimensions = {
        LogAnomalyDetector = "ses-mail-gmail-forwarder-${var.environment}"
        LogAnomalyPriority = "HIGH"
      }
    }
  }

  # Bouncer Lambda
  metric_query {
    id          = "bouncer"
    return_data = false
    metric {
      metric_name = "AnomalyCount"
      namespace   = "AWS/Logs"
      period      = 900
      stat        = "Sum"
      dimensions = {
        LogAnomalyDetector = "ses-mail-bouncer-${var.environment}"
        LogAnomalyPriority = "HIGH"
      }
    }
  }

  # SMTP Credential Manager Lambda
  metric_query {
    id          = "smtp"
    return_data = false
    metric {
      metric_name = "AnomalyCount"
      namespace   = "AWS/Logs"
      period      = 900
      stat        = "Sum"
      dimensions = {
        LogAnomalyDetector = "ses-mail-smtp-credential-manager-${var.environment}"
        LogAnomalyPriority = "HIGH"
      }
    }
  }

  # Outbound Metrics Publisher Lambda
  metric_query {
    id          = "metrics"
    return_data = false
    metric {
      metric_name = "AnomalyCount"
      namespace   = "AWS/Logs"
      period      = 900
      stat        = "Sum"
      dimensions = {
        LogAnomalyDetector = "ses-mail-outbound-metrics-publisher-${var.environment}"
        LogAnomalyPriority = "HIGH"
      }
    }
  }

  # Canary Sender Lambda
  metric_query {
    id          = "canary"
    return_data = false
    metric {
      metric_name = "AnomalyCount"
      namespace   = "AWS/Logs"
      period      = 900
      stat        = "Sum"
      dimensions = {
        LogAnomalyDetector = "ses-mail-canary-sender-${var.environment}"
        LogAnomalyPriority = "HIGH"
      }
    }
  }

  # Sum all ses-mail detectors
  metric_query {
    id          = "total"
    expression  = "router + gmail + bouncer + smtp + metrics + canary"
    label       = "Total HIGH Anomalies"
    return_data = true
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "anomaly-high-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
    Severity    = "high"
  }
}

# Medium Severity Anomaly Alarm (aggregated across ses-mail Lambda functions only)
# Uses metric math to sum anomalies from each detector, filtering out anomalies from other log groups
resource "aws_cloudwatch_metric_alarm" "anomaly_medium" {
  count = var.anomaly_detection_enabled ? 1 : 0

  alarm_name          = "ses-mail-anomaly-medium-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  alarm_description   = "MEDIUM severity anomalies in ses-mail Lambda logs (${var.environment})"
  treat_missing_data  = "notBreaching"

  # Router Enrichment Lambda
  metric_query {
    id          = "router"
    return_data = false
    metric {
      metric_name = "AnomalyCount"
      namespace   = "AWS/Logs"
      period      = 900
      stat        = "Sum"
      dimensions = {
        LogAnomalyDetector = "ses-mail-router-enrichment-${var.environment}"
        LogAnomalyPriority = "MEDIUM"
      }
    }
  }

  # Gmail Forwarder Lambda
  metric_query {
    id          = "gmail"
    return_data = false
    metric {
      metric_name = "AnomalyCount"
      namespace   = "AWS/Logs"
      period      = 900
      stat        = "Sum"
      dimensions = {
        LogAnomalyDetector = "ses-mail-gmail-forwarder-${var.environment}"
        LogAnomalyPriority = "MEDIUM"
      }
    }
  }

  # Bouncer Lambda
  metric_query {
    id          = "bouncer"
    return_data = false
    metric {
      metric_name = "AnomalyCount"
      namespace   = "AWS/Logs"
      period      = 900
      stat        = "Sum"
      dimensions = {
        LogAnomalyDetector = "ses-mail-bouncer-${var.environment}"
        LogAnomalyPriority = "MEDIUM"
      }
    }
  }

  # SMTP Credential Manager Lambda
  metric_query {
    id          = "smtp"
    return_data = false
    metric {
      metric_name = "AnomalyCount"
      namespace   = "AWS/Logs"
      period      = 900
      stat        = "Sum"
      dimensions = {
        LogAnomalyDetector = "ses-mail-smtp-credential-manager-${var.environment}"
        LogAnomalyPriority = "MEDIUM"
      }
    }
  }

  # Outbound Metrics Publisher Lambda
  metric_query {
    id          = "metrics"
    return_data = false
    metric {
      metric_name = "AnomalyCount"
      namespace   = "AWS/Logs"
      period      = 900
      stat        = "Sum"
      dimensions = {
        LogAnomalyDetector = "ses-mail-outbound-metrics-publisher-${var.environment}"
        LogAnomalyPriority = "MEDIUM"
      }
    }
  }

  # Canary Sender Lambda
  metric_query {
    id          = "canary"
    return_data = false
    metric {
      metric_name = "AnomalyCount"
      namespace   = "AWS/Logs"
      period      = 900
      stat        = "Sum"
      dimensions = {
        LogAnomalyDetector = "ses-mail-canary-sender-${var.environment}"
        LogAnomalyPriority = "MEDIUM"
      }
    }
  }

  # Sum all ses-mail detectors
  metric_query {
    id          = "total"
    expression  = "router + gmail + bouncer + smtp + metrics + canary"
    label       = "Total MEDIUM Anomalies"
    return_data = true
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "anomaly-medium-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Component   = "monitoring"
    Severity    = "medium"
  }
}
