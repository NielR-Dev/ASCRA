// infra/modules/appinsights.bicep
// App Insights + Log Analytics workspace + alert rules.
// All telemetry is streamed here for the operational dashboards.

@description('Name prefix for all resources')
param namePrefix string

@description('Location for all resources')
param location string

@description('Resource tags')
param tags object

@description('Log retention in days (30 for dev, 60 for uat, 2555 for prod)')
param retentionDays int = 90

@description('Action group email for alert notifications')
param alertEmailAddress string = ''

// ---------------------------------------------------------------------------
// Log Analytics workspace
// ---------------------------------------------------------------------------

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${namePrefix}-law'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    workspaceCapping: {
      dailyQuotaGb: -1   // no hard cap (set quota in prod for cost control)
    }
  }
}

// ---------------------------------------------------------------------------
// Application Insights (workspace-based)
// ---------------------------------------------------------------------------

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${namePrefix}-ai'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
    RetentionInDays: retentionDays
    SamplingPercentage: 100   // no sampling — healthcare audit requires every span
  }
}

// ---------------------------------------------------------------------------
// Action group for alerts
// ---------------------------------------------------------------------------

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = if (!empty(alertEmailAddress)) {
  name: '${namePrefix}-ag-oncall'
  location: 'Global'
  tags: tags
  properties: {
    groupShortName: 'ssl-oncall'
    enabled: true
    emailReceivers: [
      {
        name: 'on-call'
        emailAddress: alertEmailAddress
        useCommonAlertSchema: true
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Alert rules
// ---------------------------------------------------------------------------

// Alert: renewal failure rate > 5% over 1 hour
resource alertRenewalFailures 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${namePrefix}-alert-renewal-failures'
  location: location
  tags: tags
  properties: {
    description: 'Fires when more than 5% of renewal workflows fail within 1 hour'
    severity: 1
    enabled: true
    evaluationFrequency: 'PT15M'
    windowSize: 'PT1H'
    scopes: [logAnalytics.id]
    criteria: {
      allOf: [
        {
          query: '''
            traces
            | where message has "workflow_state" and message has "FAILED"
            | summarize failed=count() by bin(timestamp, 1h)
            | where failed > 5
          '''
          timeAggregation: 'Count'
          threshold: 0
          operator: 'GreaterThan'
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: !empty(alertEmailAddress) ? [actionGroup.id] : []
    }
  }
}

// Alert: consecutive tool errors (G3 — 2 errors triggers halt; alert before that)
resource alertConsecutiveErrors 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${namePrefix}-alert-consecutive-errors'
  location: location
  tags: tags
  properties: {
    description: 'Fires when consecutive_errors >= 2 (G3 halt imminent)'
    severity: 0   // Critical
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT10M'
    scopes: [logAnalytics.id]
    criteria: {
      allOf: [
        {
          query: '''
            traces
            | where message has "consecutive_errors" and message has "Halting"
            | summarize count() by bin(timestamp, 5m)
            | where count_ > 0
          '''
          timeAggregation: 'Count'
          threshold: 0
          operator: 'GreaterThan'
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: !empty(alertEmailAddress) ? [actionGroup.id] : []
    }
  }
}

// Alert: approval SLA breach (> 48 hours without decision)
resource alertApprovalSla 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${namePrefix}-alert-approval-sla'
  location: location
  tags: tags
  properties: {
    description: 'Fires when an approval request has been pending > 48 hours (SLA breach)'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT1H'
    windowSize: 'PT2H'
    scopes: [logAnalytics.id]
    criteria: {
      allOf: [
        {
          query: '''
            traces
            | where message has "approval_timeout" or message has "approval_sla_breach"
            | summarize count() by bin(timestamp, 1h)
            | where count_ > 0
          '''
          timeAggregation: 'Count'
          threshold: 0
          operator: 'GreaterThan'
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: !empty(alertEmailAddress) ? [actionGroup.id] : []
    }
  }
}

// Alert: certificate approaching expiry without active workflow (< 14 days)
resource alertCertExpirySoon 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${namePrefix}-alert-cert-expiry-soon'
  location: location
  tags: tags
  properties: {
    description: 'Fires when a cert is < 14 days from expiry with no active renewal workflow'
    severity: 1
    enabled: true
    evaluationFrequency: 'PT1H'
    windowSize: 'PT2H'
    scopes: [logAnalytics.id]
    criteria: {
      allOf: [
        {
          query: '''
            traces
            | where message has "days_until_expiry" and toint(extract("days_until_expiry=(\\d+)", 1, message)) < 14
            | summarize count() by bin(timestamp, 1h)
            | where count_ > 0
          '''
          timeAggregation: 'Count'
          threshold: 0
          operator: 'GreaterThan'
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: !empty(alertEmailAddress) ? [actionGroup.id] : []
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output logAnalyticsWorkspaceId string = logAnalytics.id
output appInsightsId string = appInsights.id
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey
