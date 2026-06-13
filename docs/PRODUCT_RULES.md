# Buykori Product Rules

## Plan Entitlements

Effective event quotas and feature access come from `app.services.plan_service`.
Client model fields such as `monthly_limit`, `enable_tiktok`, `enable_ga4`,
`deferred_purchase`, and `courier_auto_send` are persisted state, but request-time
snapshots must apply the plan rules before exposing those values to ingest,
workers, or portals.

Expired trials downgrade to the free entitlement set. Paid plan assignment is the
only supported path for enabling paid features after trial expiry.

## Account Ownership

Client signup uses globally unique owner email addresses. One email signs into one
owner account; multiple stores are managed through the portal store-switching flow
rather than by creating separate signup accounts with the same email.

## Fraud Review

Purchase events at or above `FRAUD_AUTO_HOLD_THRESHOLD` are held with
`portal_state="fraud_review"` for manual confirmation instead of being sent
immediately. The default threshold is `90`; set it to `0` only when auto-hold is
intentionally disabled. An explicit `force_send=true` request is the operator
override.

## Shared Monthly Quota

Stores sharing an owner billing identity reserve monthly usage against one
canonical `billing-monthly:*` counter. PostgreSQL initializes that counter from
the existing per-store monthly counters, then all stores increment the same row
atomically so parallel requests cannot exceed the shared limit through a
read-and-sum race.
