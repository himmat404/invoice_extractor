<!--
Document: InvoiceFlow Complete SaaS Product Specification
Purpose: Consolidated product, functional, technical, operational, and governance requirements.
-->

# InvoiceFlow — Complete SaaS Product Specification

**AI-Powered Invoice Extraction, Validation, and Accounting Export Platform**

> **Product purpose:** InvoiceFlow enables individuals and businesses to upload invoices, automatically extract structured data using AI, review and correct the results, maintain invoice history, and export data in formats compatible with accounting and business software.

---

## 1. Product Vision

InvoiceFlow is a multi-user SaaS platform that reduces manual invoice data entry. Customers use a straightforward web application to upload invoices, review extracted information, and download their data. The platform's internal team controls AI providers, models, API credentials, system configuration, customer accounts, plans, usage, and billing through a separate, private admin console.

**Core customer workflow:**

Upload invoice → Extract automatically → Validate → Review and edit → Save → Export

**Core operating principle:** Customers do not need to know which AI model, provider, API, prompt, or backend process powers the service. All model selection and provider configuration are managed securely by the platform's administrators.

## 2. Product Goals

- Reduce repetitive manual invoice data entry.
- Extract invoice headers, supplier and customer details, taxes, totals, and line items.
- Support invoices with different layouts and document structures.
- Provide a human-review workflow for uncertain or inconsistent fields.
- Maintain searchable invoice history for each customer or workspace.
- Offer CSV, Excel, JSON, and PDF exports, along with supported accounting-software formats.
- Support individual and bulk invoice processing.
- Offer subscriptions and credit purchases.
- Give customers visibility into their own plan, billing cycle, payments, and usage.
- Give administrators complete control over users, plans, billing, AI models, API keys, usage, costs, and system configuration.
- Track AI usage and estimated API costs to help the business manage operating margins.
- Keep internal technical and financial details private from customers.

## 3. Users and Access Boundaries

### 3.1 Customer users

Target customers include small and medium businesses, accountants, bookkeepers, finance departments, freelancers, e-commerce businesses, procurement teams, operations teams, and organizations processing supplier invoices.

Customers can:
- Register for an account and sign in.
- Choose a subscription plan and/or purchase credits.
- Upload and process invoices.
- Review and edit extracted data.
- View and search their own invoice history.
- Export their own invoice data.
- View their own usage, subscription, billing cycle, and payment history.
- Manage their profile and workspace settings.

Customers **cannot**:
- Access the admin console.
- View or manage AI models or provider API keys.
- Configure prompts, model parameters, or backend processing.
- View other customers' data.
- View internal API costs, provider credentials, system logs, or business margins.
- Change global system settings.

### 3.2 Platform administrators

The admin console is for the platform owner and authorized internal team members only. It is a separate, role-protected application.

Administrators can manage:
- AI providers, models, API credentials, primary/fallback model configuration.
- Customer accounts and workspaces.
- Subscription plans, pricing, billing cycles, and credit packages.
- Customer subscriptions, payments, usage, and account status.
- Processing jobs, failures, retries, and system health.
- Export formats and system-wide configuration.
- AI usage, estimated provider costs, analytics, and audit logs.

Administrative actions involving pricing, credits, subscriptions, credentials, or account status must be permission-controlled and auditable.

## 4. Customer Application

### 4.1 Authentication and account

- Open customer registration.
- Sign in and sign out.
- Email verification.
- Password reset.
- Secure session management.
- User profile and account settings.
- Company/workspace information, where applicable.
- Timezone, locale, and currency preferences.
- Data export and account deletion controls.
- Privacy policy and terms pages.

### 4.2 Customer dashboard

Display information relevant to the signed-in customer:
- Total invoices.
- Invoices processed in the current billing period.
- Remaining invoice credits.
- Total invoice value and tax totals, where available.
- Recent invoices.
- Invoices requiring review.
- Failed processing jobs.
- Recent exports.
- Current subscription plan and status.
- Billing-cycle dates and next billing date, where applicable.

Quick actions:
- Upload Invoice.
- Bulk Upload.
- View Invoice History.
- Export Data.
- View Usage.
- Manage Subscription and Billing.

### 4.3 Invoice upload

- Drag-and-drop upload and file picker.
- PDF, JPG/JPEG, and PNG support.
- HEIC support where supported by the processing pipeline.
- Multiple-file and bulk upload.
- ZIP batch upload where supported.
- File type and size validation based on configured limits.
- Upload and processing progress.
- Success, warning, and failure states.
- Automatic duplicate detection.
- Cancellation of queued jobs where supported.
- Clear messages for unsupported, oversized, empty, or unreadable files.

### 4.4 AI extraction

The system extracts available invoice information, including:

**Invoice details**
- Invoice number, invoice date, due date.
- Purchase order number and reference number.
- Currency and payment terms.
- Notes and terms.

**Supplier and customer**
- Supplier/vendor name, address, and tax ID/GSTIN.
- Customer/buyer name, address, and tax ID/GSTIN.

**Payment information**
- Bank or payment information when present.
- Amount paid and balance due.

**Line items**
- Description, SKU/product code, and HSN/SAC where present.
- Quantity and unit of measure.
- Unit price, discount, tax rate, tax amount, and line total.

**Totals and taxes**
- Subtotal, shipping/freight, and other charges.
- Total discount.
- CGST, SGST, IGST, and other taxes.
- Grand total.

Fields that are absent from a source document should be represented as missing or null rather than fabricated.

### 4.5 Review and correction

- Side-by-side original invoice preview and extracted data.
- Field-level confidence information where available.
- Highlight low-confidence fields.
- Warnings for missing, ambiguous, or inconsistent values.
- Editable header fields and line items.
- Add or remove line items.
- Accept or reject suggested values.
- Re-run extraction when supported.
- Manual correction before export.
- Mark invoice as reviewed or approved.
- Review queue for invoices needing attention.

### 4.6 Validation

Application-side validation must not rely exclusively on the AI response.

- Required-field checks.
- Date, currency, numeric, and decimal validation.
- Tax ID/GSTIN format checks where applicable.
- Line-item total calculations.
- Subtotal, tax, discount, and grand-total reconciliation.
- Duplicate invoice detection, including supplier/invoice-number checks.
- Warnings for inconsistent extracted totals.

Validation states:
- Passed
- Warning
- Needs Review
- Failed

The interface must clearly explain issues that require customer attention.

### 4.7 Invoice history

- Search by invoice number, supplier, customer, or text.
- Filter by date range, supplier, processing status, review status, export status, amount, and currency.
- Sort by date, amount, or upload time.
- Open invoice details.
- View original document, extracted data, and validation results.
- View export history.
- Reprocess an invoice.
- Download the original invoice.
- Delete an invoice, subject to retention and audit rules.

### 4.8 Bulk processing

- Upload many invoices at once.
- Queue-based processing.
- Per-file and batch progress.
- Successful, failed, and review-required counts.
- Retry failed documents.
- Review problematic invoices.
- Bulk export and batch download.
- Batch processing history.

### 4.9 Customer exports

Exports are generated from validated canonical invoice data.

Supported formats:
- CSV.
- Excel/XLSX.
- JSON.
- PDF summary/report where useful.
- Tally-compatible export.
- QuickBooks-compatible export.
- Zoho Books-compatible export.
- Xero-compatible export.
- Custom formats configured and made available by the platform.

The exact structure of software-specific formats must follow the current import documentation of each supported platform.

Customer export options:
- Export selected invoices.
- Export filtered results.
- Export all eligible invoices.
- Bulk export and ZIP packages.
- Select and reorder columns where supported.
- Configure date and decimal formats.
- Configure currency and tax-field mapping where permitted.
- Use available custom field mappings.

Export workflow:
1. Select invoices.
2. Choose an available export format or saved profile.
3. Run export validation.
4. Display warnings for missing required fields.
5. Generate the file.
6. Download the file.
7. Store export history.
8. Allow repeat downloads where appropriate.

### 4.10 Customer subscription, billing, and usage

Customers can view and manage their own:
- Current plan and plan price.
- Billing interval and billing-cycle dates.
- Usage for the current period.
- Remaining credits.
- Subscription status.
- Next billing date, where applicable.
- Payment method and billing history.
- Receipts or SaaS billing invoices.
- Available upgrade/downgrade options.
- Subscription cancellation.
- Available credit-purchase options.
- Coupon or promotion information, where applicable.

Customers see their own usage and billing information only. Internal provider costs and API credentials are never displayed.

---

## 5. Private Admin Console

### 5.1 Admin authentication and permissions

- Separate admin login and secure sessions.
- Role-based access control.
- Permissions for sensitive operations.
- Audit logs for administrative actions.
- Secure handling of credentials and secrets.

### 5.2 Admin dashboard

Show platform-wide operational and business metrics:
- Total and active users.
- Free and paid users.
- New registrations.
- Invoices processed today and this month.
- Processing success rate.
- Invoices requiring review.
- Subscription revenue.
- Estimated AI/API costs.
- Storage/infrastructure costs where available.
- Estimated gross margin.
- Plan distribution.
- Usage trends.
- Export usage.
- Failed jobs and system health.

### 5.3 AI provider and model management

Administrators control which AI models are used by the platform.

Capabilities:
- Add and configure AI providers and models.
- Store provider API keys securely on the server.
- Set a primary model.
- Configure one or more fallback models and their order.
- Activate or deactivate models.
- Configure model-specific settings and structured-output requirements.
- Manage extraction prompts and prompt versions.
- Test model configuration through controlled admin workflows.
- Monitor model usage, errors, latency, and estimated costs.
- Change model configuration without requiring customer-facing workflow changes.

**Fallback behavior:** If the primary model fails for a retryable or configured reason, the backend may attempt the next enabled fallback model according to administrator-defined rules. The system should record which model handled each job. Customers see a processing status or result, not internal model-selection details.

API keys must never be sent to the browser, embedded in frontend code, or exposed in customer-facing responses.

### 5.4 User and workspace management

- Search and view users.
- View profile and workspace details.
- View subscription and billing status.
- View usage and credit ledger.
- View invoices processed, failures, and exports.
- Change plans where authorized.
- Suspend or reactivate accounts.
- Grant or revoke credits where authorized.
- View account activity and audit trail.
- Apply account-level limits or exceptions where supported.

### 5.5 Plan and pricing management

Administrators create and manage the plans customers can choose from.

Plan configuration may include:
- Plan name and description.
- Monthly and annual prices.
- Included invoice credits per billing period.
- Credit-purchase availability and pricing.
- File size limits.
- Bulk upload limits.
- Enabled export formats.
- History retention.
- Team/member limits, if enabled.
- Overage behavior, if supported.
- Feature list.
- Active/inactive status.

Customers can select from active plans through the customer application. Plan changes must be reflected in subscription and usage records according to the configured billing rules.

### 5.6 Subscription, payment, and billing management

- View all subscriptions and their statuses.
- View billing cycles, renewals, cancellations, and payment history.
- Track successful, pending, and failed payments.
- Handle payment-provider webhooks.
- Configure grace-period behavior.
- Manage refunds or adjustments where supported and authorized.
- View customer receipts and SaaS billing invoices.
- Reconcile subscription and credit-purchase activity.

Customer-uploaded business invoices and InvoiceFlow's own SaaS billing invoices must be stored as separate data entities.

### 5.7 Credit and usage management

- Track allocated, consumed, and remaining credits.
- Track billing-period start and end.
- Record credit purchases, grants, adjustments, and consumption in a ledger.
- Consume one invoice credit after successful invoice processing, according to the platform's business rules.
- Do not consume a credit for a failed processing job where the rules define it as a failure.
- Prevent processing when limits are exceeded unless overage is explicitly enabled.
- Display usage to customers and provide detailed internal usage records to administrators.

### 5.8 AI cost monitoring

Track, where available:
- Provider and model used.
- Input and output token usage.
- Processing job ID.
- Estimated API cost.
- Processing duration.
- Success or failure.
- Cost by plan, user, model, and date.
- Cost per successfully processed invoice.
- Aggregate monthly AI cost.

### 5.9 Processing and system monitoring

- View queued, active, completed, and failed jobs.
- Inspect error details and retry history.
- Retry eligible failed jobs.
- Monitor queue health, API errors, rate limits, and timeouts.
- Configure safe retry and timeout behavior.
- Monitor application, storage, email, payment, and provider integrations.
- Maintain operational audit logs.

### 5.10 Export and system configuration

- Enable or disable supported export formats.
- Manage export templates and mapping definitions.
- Maintain software-specific export profiles.
- Configure system-wide limits and defaults.
- Manage email and notification templates.
- Configure data retention policies.
- Manage feature availability and controlled rollouts.

### 5.11 Coupons and promotions

- Create percentage or fixed-amount discounts where supported.
- Set expiration dates and maximum redemptions.
- Configure per-user redemption limits.
- Select applicable plans.
- Configure trial or promotional periods.
- Activate or deactivate coupons.
- View coupon usage reports.

---

## 6. Notifications

Customer notifications may include:
- Email verification and password reset.
- Invoice processing completed or failed.
- Review required.
- Usage threshold warning.
- Credits exhausted.
- Subscription renewal or change.
- Payment failure.
- Export ready.
- Important account and security notifications.

Administrators may receive operational alerts for:
- Provider/API failures.
- Elevated processing failure rates.
- Queue backlogs.
- Payment webhook failures.
- Unexpected usage or cost changes.
- System health issues.

---

## 7. Search and Reporting

Customer-facing reports:
- Invoice search.
- Supplier and customer reporting.
- Tax reporting.
- Monthly invoice totals.
- Spend analysis.
- Export history.
- Personal usage and billing reports.
- CSV/Excel report downloads.

Admin-only reports:
- User and subscription reporting.
- Platform-wide usage.
- Revenue and payment reporting.
- AI cost and margin reporting.
- Processing success/failure trends.
- Export usage.
- Plan distribution and growth metrics.

Reports must respect tenant boundaries and role permissions.

---

## 8. Security, Privacy, and Data Isolation

- Secure authentication and role-based authorization.
- Strict tenant/workspace isolation.
- Encrypted data in transit.
- Encryption of sensitive data at rest where supported.
- Private object storage for original invoices and generated exports.
- Signed or private document URLs.
- Server-side-only provider API keys and payment secrets.
- Rate limiting and upload validation.
- Malicious-file protection.
- Audit logging for sensitive actions.
- Account deletion and data deletion workflows.
- Configurable data retention.
- Privacy policy and terms pages.
- No exposure of one customer's data to another customer.
- No exposure of internal admin functionality or secrets to customer accounts.

---

## 9. Gemini and AI Integration

Gemini is one supported AI provider; the architecture should allow additional providers and models to be configured by administrators.

- Server-side AI integration.
- Structured output/schema enforcement.
- Provider and model configuration stored server-side.
- Prompt and version management.
- Primary/fallback model routing.
- Retry, timeout, and rate-limit handling.
- Asynchronous processing queue.
- Safe parsing and validation of model responses.
- Application-side validation after extraction.
- Record extraction version and actual model used for every invoice.
- Allow administrators to change model configuration without changing customer workflows.

---

## 10. Processing Status Model

Suggested statuses:
- Uploaded
- Queued
- Processing
- Extracted
- Validation Warning
- Needs Review
- Approved
- Exported
- Failed
- Archived/Deleted

Customer-facing status labels should be understandable and should not expose unnecessary internal implementation details.

---

## 11. Recommended Core Database Entities

- `users`
- `companies` / `workspaces`
- `memberships`
- `plans`
- `subscriptions`
- `payments`
- `billing_invoices` (InvoiceFlow SaaS billing documents)
- `coupons`
- `coupon_redemptions`
- `credit_packages` (if credit purchases are offered)
- `usage_records`
- `credit_ledger`
- `invoices` (customer-uploaded business invoices)
- `invoice_items`
- `invoice_tax_lines`
- `invoice_files`
- `extraction_jobs`
- `model_providers`
- `model_configurations`
- `provider_credentials` (securely stored or managed through a secrets service)
- `validation_results`
- `export_profiles`
- `exports`
- `notifications`
- `admin_users`
- `admin_roles`
- `audit_logs`
- `system_settings`

**Important:** Customer-uploaded business invoices and InvoiceFlow SaaS billing invoices are distinct entities and must not be mixed.

---

## 12. Application Pages

### 12.1 Customer application

- Landing page.
- Pricing page.
- Register and login.
- Forgot password.
- Email verification.
- Dashboard.
- Upload Invoice.
- Bulk Upload.
- Processing Queue.
- Invoice Review.
- Invoice Details.
- Invoice History.
- Exports.
- Export Profile/Preferences, where enabled.
- Billing and Subscription.
- Credit Purchase, where enabled.
- Usage.
- Account Settings.
- Company/Workspace Settings.
- Notifications.
- Help/Support.
- Privacy Policy and Terms.

### 12.2 Admin console

- Admin Login.
- Admin Dashboard.
- Users.
- User Details.
- Companies/Workspaces.
- Subscriptions.
- Plans and Pricing with Feature Entitlements.
- Payments and Billing.
- Payment Gateway Configuration.
- Autopay and Mandate Management.
- Credit Packages and Ledger.
- Coupons.
- AI Providers.
- AI Models and Fallback Configuration.
- API Credentials.
- Prompts and Extraction Settings.
- Usage.
- AI Costs.
- Processing Jobs.
- Exports and Templates.
- System Settings.
- Notifications Configuration.
- Audit Logs.
- Admin Roles and Permissions.

---

## 13. Suggested Navigation

**Customer navigation**
- Dashboard
- Invoices
- Upload
- Exports
- Usage
- Billing
- Settings

**Admin navigation**
- Dashboard
- Users
- Billing
- Plans and Feature Entitlements
- Payment Gateways and Autopay
- Credits and Usage
- AI Providers and Models
- AI Costs
- Jobs
- Exports
- Coupons
- Settings
- Audit Logs

---

## 14. Business Rules

- Anyone may register through the customer application, subject to configured abuse-prevention controls.
- Customers can select and subscribe to active plans.
- Administrators can configure multiple payment gateways through provider-specific adapters behind a common internal interface.
- Autopay is offered only where the selected provider, payment method, merchant account, and applicable rules support it; customer consent and mandate lifecycle events must be recorded.
- Plan features are enforced by backend entitlements, not only by frontend visibility.
- Customers may also purchase credits if credit purchases are enabled for their account or plan.
- One successfully processed invoice consumes one credit under the standard usage rule.
- Failed jobs do not consume credits when classified as failures by the platform's business rules.
- Processing beyond plan allowances is blocked unless overage is explicitly supported.
- Every processing job receives a unique ID.
- Every invoice belongs to a user/workspace.
- Every export is associated with the invoices included in it.
- Every subscription is associated with a plan.
- Every usage and credit event is recorded for billing and analytics.
- Exports use validated canonical data.
- Low-confidence fields and validation failures are surfaced before export when relevant.
- Administrative changes to plans, pricing, credits, credentials, and account status must be auditable.
- Provider API keys and payment secrets must never be exposed to client-side code.
- Customers must not be able to discover or control the AI model configuration.
- The backend records the model/provider used for each processing job for internal support, cost analysis, and audit purposes.

---

## 15. Error Handling

Handle and communicate:
- Unsupported file type.
- File too large.
- Unreadable or empty document.
- AI extraction failure.
- Malformed AI response.
- Validation failure.
- Duplicate invoice warning.
- API rate limit.
- Temporary provider outage.
- Primary model failure and fallback attempt.
- Failure of all configured models.
- Payment failure.
- Subscription expired or inactive.
- Insufficient credits.
- Export mapping error.
- Missing required export field.

Customer messages should explain the next action without revealing secrets, raw provider errors, or sensitive backend details. Detailed diagnostics belong in the admin console.

---

## 16. UX Principles

- Keep invoice processing simple and fast.
- Make upload prominent.
- Show processing progress clearly.
- Make uncertain fields easy to identify.
- Never hide validation errors.
- Keep the original invoice visible during review.
- Make exporting a short, predictable workflow.
- Remember commonly used export preferences.
- Show usage and billing status clearly.
- Use clear success, warning, and failure states.
- Design for desktop-first finance workflows while remaining responsive on mobile.
- Keep technical AI and provider details out of the customer experience.
- Make the admin console powerful but clearly separated from the customer application.

---

## 17. Product Positioning and Value Proposition

InvoiceFlow is an AI-powered bridge between invoices and business software. Customers upload invoices instead of manually typing information into spreadsheets or accounting systems. The platform handles document understanding, structured extraction, validation, human review, and conversion into import-ready formats.

Key customer value:
- Reduce repetitive data-entry work.
- Process invoices individually or in bulk.
- Review extracted data before using it elsewhere.
- Maintain searchable invoice history.
- Export to generic and supported software-specific formats.
- Manage subscription, credits, and billing in one account.

Key platform-owner value:
- Control AI providers, models, and API keys centrally.
- Switch primary and fallback models without changing the customer experience.
- Manage users, plans, billing, and credits.
- Monitor provider costs, processing performance, and business metrics.
- Extend supported models, export formats, and plans over time.

---

## 18. High-Level Technical Architecture

Recommended components:
- **Customer frontend:** Responsive web application.
- **Admin frontend:** Separate, role-protected management console.
- **Backend API:** Secure REST/JSON or equivalent application API.
- **Authentication and authorization:** Customer and admin access controls.
- **Relational database:** Users, workspaces, invoices, subscriptions, usage, configuration, and audit data.
- **Private object storage:** Original invoice files and generated exports.
- **Queue and workers:** Asynchronous invoice processing.
- **AI orchestration layer:** Provider/model abstraction, primary/fallback routing, and server-side credentials.
- **Validation engine:** Schema, field, and financial reconciliation checks.
- **Export engine:** Generic and software-specific mapping.
- **Payment orchestration layer:** Provider-agnostic gateway interface with separate adapters, secure credentials, transaction verification, and webhook synchronization.
- **Recurring billing and mandates:** Provider-specific autopay integrations with consent, renewal, retry, cancellation, and status tracking.
- **Transactional email:** Verification, billing, and processing notifications.
- **Monitoring:** Application, queue, AI provider, storage, and billing monitoring.
- **Admin console:** Internal management of models, credentials, users, plans, usage, costs, and system settings.

---

## 19. End-to-End Customer Workflow

1. Customer registers and verifies their email.
2. Customer selects a plan and/or purchases credits.
3. Customer signs in and uploads an invoice.
4. System validates the file and creates a processing job.
5. System checks account status, subscription, and remaining credits.
6. Worker sends the document to the configured primary AI model.
7. If configured failure conditions occur, the system attempts fallback models in admin-defined order.
8. The AI provider returns structured invoice data.
9. Backend validates the response schema and safely parses the result.
10. Application recalculates totals and checks consistency.
11. Invoice receives confidence and validation statuses.
12. Customer opens the review screen and corrects fields as needed.
13. Customer approves the invoice.
14. Invoice is stored in the customer's history.
15. Customer selects an available export format.
16. System maps canonical invoice data to the selected format.
17. System validates export-required fields.
18. File is generated and downloaded.
19. Export activity is recorded.
20. Usage is recorded against the customer's billing period or credit balance.
21. Administrators can inspect processing, usage, billing, and AI cost records in the private console.

---


## 21. Additional Product Features

This section defines the additional capabilities requested for InvoiceFlow. These features must follow the existing customer/admin access boundaries, tenant-isolation requirements, auditability rules, and server-side entitlement enforcement.

### 21.1 Duplicate Detection with Configurable Rules

The platform must support configurable duplicate detection at upload time and, where appropriate, during invoice review.

**Detection signals may include:**
- Supplier/vendor identity or normalized supplier name.
- Invoice number, including normalized whitespace and punctuation.
- Invoice date.
- Currency and grand total.
- Tax ID/GSTIN.
- Document content fingerprint or file hash.
- Purchase order number, where available.

**Configuration:**
- Administrators can define global duplicate-detection rules and thresholds.
- Workspace-level overrides may be offered when enabled by the plan.
- Rules can be configured as exact-match, multi-field match, or similarity-based checks.
- The system must distinguish a confirmed duplicate from a potential match.
- Customers can review potential matches and choose an allowed action: keep both, mark as duplicate, or cancel the new upload.
- Duplicate checks must not expose another workspace's invoice data. Cross-tenant matching is prohibited.
- Reprocessing an existing invoice must not create a new invoice record or consume another credit unless explicitly configured.

**Outcomes:** `No Match`, `Potential Duplicate`, `Confirmed Duplicate`, and `Review Required`.

Every duplicate decision and any customer override must be recorded in the invoice activity history.

### 21.2 AI Confidence Score

InvoiceFlow should present confidence information to help customers prioritize review. Confidence must not be represented as a guarantee of correctness.

**Requirements:**
- Store confidence at the field level where the provider supplies suitable information or where a documented normalization method is available.
- Display an overall invoice confidence indicator only when its calculation is defined and versioned.
- Distinguish AI confidence from deterministic validation results.
- Highlight low-confidence or missing fields in the review interface.
- Allow administrators to configure review thresholds and maintain a version history of threshold changes.
- Record the provider, model, extraction version, and confidence method used for each job.
- Do not fabricate confidence values when a provider does not supply sufficient evidence. Display “Unavailable” instead.
- Customers must be able to review and correct data regardless of the confidence score.

Suggested display bands (configurable): `High`, `Medium`, and `Low`. These are review-priority labels, not accuracy guarantees.

### 21.3 Bulk Upload and Batch Processing

Bulk upload is a first-class workflow for customers with appropriate plan entitlements.

**Capabilities:**
- Upload multiple supported files in one batch.
- Support ZIP upload where enabled, with safe archive inspection and extraction limits.
- Show batch-level and per-file progress.
- Display counts for queued, processing, completed, review-required, duplicate, and failed files.
- Allow retry of eligible failures without duplicating successful results or credit deductions.
- Allow cancellation of queued jobs where supported.
- Permit customers to open and review individual invoices from a batch.
- Support batch export and downloadable result packages.
- Maintain batch history, including the initiating user, timestamps, files, outcomes, and errors.
- Enforce file-count, file-size, concurrency, and processing limits through backend plan entitlements.
- Apply fair-use and queue controls to prevent one workspace from monopolizing shared workers.

Each batch and each processing job must have a unique identifier and an idempotent lifecycle.

### 21.4 Audit Logs and Activity History

InvoiceFlow must maintain separate but related audit records for platform administration, workspace activity, and invoice-level history.

**Admin audit log events include:**
- Changes to plans, prices, entitlements, coupons, and credit balances.
- Provider/model configuration and prompt-version changes.
- Credential creation, rotation, or deletion (never log secret values).
- Account suspension, reactivation, and permission changes.
- Payment adjustments, refunds, and subscription overrides.
- System setting and feature-flag changes.
- Data access or support actions that expose customer information.

**Customer activity history may include:**
- Invoice upload, extraction, reprocessing, editing, approval, export, archive, and deletion.
- Duplicate-match decisions and overrides.
- Batch creation, retry, and cancellation.
- Template and saved-preference changes.
- API key creation, rotation, and revocation.
- Webhook endpoint changes and delivery outcomes.
- Scheduled export creation, modification, execution, and failure.

Each event should record, where applicable: actor, workspace, action, affected entity, timestamp (UTC), request/correlation ID, outcome, and a safe summary of changed fields. Sensitive values, credentials, full document contents, and payment secrets must not be logged.

Audit records must be access-controlled, tamper-resistant, searchable by authorized users, and retained according to a documented policy. Customer-visible activity history must never expose internal-only admin details.

### 21.5 Customer API Access

The platform may offer API access to customers according to plan entitlements.

**Initial API capabilities may include:**
- Create and retrieve invoice-processing jobs.
- Check job and batch status.
- Retrieve validated canonical invoice data.
- List and retrieve exports.
- Manage customer API credentials.
- Read usage and applicable workspace metadata.

**API requirements:**
- Version the public API and publish OpenAPI documentation.
- Use scoped, revocable credentials; support rotation and last-used timestamps.
- Store only secure credential hashes where feasible and show the secret only at creation.
- Enforce workspace authorization, scopes, rate limits, quotas, and plan entitlements on every request.
- Provide consistent error formats and request IDs.
- Support pagination, filtering, and idempotency keys for write operations.
- Define API versioning and deprecation policies.
- Never expose admin APIs, provider credentials, internal cost data, or another customer's records.
- Record API activity without logging authorization secrets.

### 21.6 Customer Webhooks

Customers may register HTTPS webhook endpoints to receive events from their own workspace.

**Suggested events:**
- `invoice.processing_started`
- `invoice.processing_completed`
- `invoice.review_required`
- `invoice.processing_failed`
- `batch.completed`
- `export.ready`
- `export.failed`
- `subscription.updated`
- `usage.threshold_reached`

**Delivery requirements:**
- Sign each delivery using a workspace-specific secret and include a timestamp to support replay protection.
- Provide event IDs and delivery IDs.
- Retry transient failures with bounded exponential backoff.
- Support delivery history, response status, manual retry, endpoint disablement, and secret rotation.
- Prevent duplicate business effects through event IDs and documented at-least-once delivery semantics.
- Require HTTPS and validate endpoint configuration to mitigate server-side request forgery and unsafe destinations.
- Do not include invoice files or unnecessary sensitive data in webhook payloads; provide authorized API retrieval instead.
- Enforce endpoint and event limits through plan entitlements.

### 21.7 Invoice Templates and Saved Preferences

Customers should be able to save reusable preferences to reduce repetitive work.

**Examples:**
- Default export format and column order.
- Date, decimal, and currency display formats.
- Preferred tax-field mappings.
- Saved export profiles.
- Review display preferences.
- Frequently used supplier mappings.
- Default workspace or processing options.

Templates and preferences must be workspace-scoped, permission-controlled, versioned where changes affect exports, and validated before use. A template must not silently alter canonical invoice data. Customers should be able to preview the effect of a template before generating an export.

### 21.8 Supplier/Vendor Directory

Provide an optional workspace-level directory of suppliers/vendors.

**Fields may include:**
- Display name and normalized name.
- Tax ID/GSTIN and country.
- Address and contact details.
- Default currency.
- Common payment terms.
- Default tax or accounting mappings.
- Supplier reference/code.
- Active/inactive status.
- Linked invoice count and last activity.

The directory may suggest matches during extraction and review. Suggestions must be confirmable by the customer; the system must not silently merge distinct suppliers based only on name similarity. Supplier records and mappings must be isolated by workspace and included in applicable export and retention controls.

### 21.9 Multi-Currency Reporting

InvoiceFlow must preserve the original currency and amount for every invoice and support reporting across currencies without overwriting source values.

**Requirements:**
- Store currency as a standard currency code and monetary values using decimal-safe types.
- Allow customers to select a reporting currency.
- Show original amounts and converted reporting amounts distinctly.
- Record the exchange-rate source, rate, effective date, and conversion timestamp used for each report or conversion.
- Allow reporting by original currency and consolidated reporting currency.
- Define treatment of missing rates, rounding, refunds, and historical exchange-rate changes.
- Do not silently recalculate previously generated reports when rates change; preserve the report's conversion basis.
- Keep tax reporting based on source invoice values unless the customer explicitly selects a supported converted view.
- Clearly label estimates and converted totals.

### 21.10 Export Scheduling

Customers may schedule recurring exports where their plan permits.

**Capabilities:**
- Schedule exports by frequency (for example, daily, weekly, or monthly).
- Select saved filters, export profiles, timezone, and delivery method.
- Preview the next run and selected data scope.
- View run history, generated files, warnings, and failures.
- Pause, resume, edit, or delete schedules.
- Notify customers when an export is ready.
- Use private, expiring download links or an explicitly configured supported destination.
- Prevent overlapping runs and duplicate exports through idempotent scheduling.
- Enforce retention, data-access permissions, and plan limits.
- Record schedule changes and executions in activity history.

### 21.11 Customer Support Tools

Provide authorized internal support staff with tools to investigate customer issues without bypassing security controls.

**Capabilities may include:**
- Search customers, workspaces, invoices, jobs, batches, and exports.
- View processing status, safe error summaries, and relevant event history.
- View subscription and usage records according to role permissions.
- Add internal support notes and link them to relevant records.
- Initiate approved retries or resend notifications.
- Track support case status, owner, priority, and resolution.
- Use a time-limited, audited support-access workflow when customer data access is necessary.

Support staff must not see provider/payment secrets or access customer documents by default. Any elevated access must be purpose-limited, approved where required, logged, and revocable. Support actions must not silently modify customer data or billing records.

### 21.12 System Status Page

Provide a public or customer-accessible status page showing service availability and incidents.

**Components may include:**
- Customer application and API.
- Invoice upload and processing.
- AI provider integrations.
- Export generation.
- Billing and payment integrations.
- Notifications.
- Scheduled exports.

The status page should show current status, incident updates, historical incidents, and maintenance notices. Status must be based on monitored service signals and clearly distinguish platform incidents from third-party provider outages. Publishing or editing incidents must be permission-controlled and audited. Do not expose customer data, internal infrastructure details, or sensitive diagnostics.

### 21.13 Backup and Disaster Recovery

Define and test backup and recovery procedures for databases, object storage, configuration, and other critical platform data.

**Requirements:**
- Document backup frequency, retention, encryption, access controls, and storage locations.
- Maintain separate protection for database backups and customer document objects.
- Define recovery point objective (RPO) and recovery time objective (RTO) for each critical service.
- Use point-in-time database recovery where supported.
- Test restoration regularly in an isolated environment.
- Document disaster-recovery ownership, escalation, and communication procedures.
- Monitor backup completion and alert on failures.
- Ensure deletion and retention policies account for backup lifecycle and legally required exceptions.
- Maintain recovery procedures for compromised credentials, accidental deletion, provider outages, and regional infrastructure failures.
- Record and review recovery tests and corrective actions.

Specific RPO, RTO, and retention values must be selected before production launch based on business requirements and infrastructure capabilities.

### 21.14 Feature Flags

Provide controlled feature availability for gradual rollout, testing, and plan-specific access.

**Capabilities:**
- Enable or disable features globally, by environment, plan, workspace, or authorized user.
- Support percentage-based rollout where appropriate.
- Define safe defaults and behavior when the flag service is unavailable.
- Record flag owner, purpose, creation date, expiry/review date, and change history.
- Restrict flag administration by role and audit all changes.
- Separate feature flags from authorization and billing entitlements: a flag must never grant access that the user's permissions or plan does not allow.
- Provide kill switches for high-risk integrations or workflows.
- Remove obsolete flags after rollout to avoid permanent configuration complexity.


### 21.15 Item/Product Directory and Intelligent Item Matching

InvoiceFlow should provide a workspace-level Item Directory to map invoice descriptions or abbreviated item names to the customer's preferred, canonical product or service names.

**Problem addressed:** Suppliers may use short names, abbreviations, codes, alternate spellings, or descriptions that differ from the full product name used in the customer's accounting or inventory software. InvoiceFlow should retain the supplier's original description while suggesting the customer's known item.

**Item Directory page:**
- Allow customers to create, view, search, edit, archive, and restore item records.
- Support bulk import from CSV/XLSX using a column-mapping and preview workflow.
- Support export of the directory and a downloadable import template.
- Validate required fields, duplicate item codes, and conflicting mappings before import.
- Show import results, including created, updated, skipped, and rejected rows with downloadable error details.
- Allow customers to update mappings later without changing historical approved invoices or previously generated exports.
- Support workspace-level permissions for who can manage the directory and mappings.

**Suggested item fields:**
- Canonical item name (the customer's preferred full name).
- Internal item code/SKU.
- Description and category.
- Unit of measure.
- HSN/SAC or other applicable classification.
- Default tax code/rate, where appropriate.
- Active/inactive status.
- Optional accounting or inventory-system reference ID.
- Created/updated timestamps and actor.

**Aliases and matching records:**
- Store one or more supplier-specific or general aliases for each canonical item.
- An alias may include the source description, supplier, supplier item code, normalized text, and the canonical item it maps to.
- Support exact matches, normalized matches, and similarity-based suggestions.
- Prefer supplier-specific mappings when the same short description means different things for different suppliers.
- Do not automatically merge distinct canonical items based only on fuzzy similarity.
- Show the original invoice line description alongside the suggested canonical item so the customer can verify the match.
- Permit customers to accept, reject, or correct suggestions.
- Preserve the source description and extracted values for traceability.

**Learning from customer corrections:**
- When a customer confirms or corrects a mapping, save that decision as a workspace-scoped mapping/alias for future suggestions.
- On later invoices, check confirmed mappings before asking the AI to interpret the item again.
- If an exact confirmed mapping exists, suggest it with a clear indication that it comes from the customer's saved directory.
- If only a fuzzy or ambiguous match exists, present it for review rather than silently applying it.
- Record mapping provenance, confirmation status, confidence/match method, supplier scope, and timestamps.
- Allow customers to undo, edit, or disable learned mappings.
- Changes to the directory should affect future suggestions by default, not silently rewrite historical approved invoice data.
- Do not use one customer's directory or corrections to train or expose mappings to another customer.

**AI role:** AI may assist with candidate matching when deterministic matching is insufficient, but the directory and confirmed mapping records are the source of truth. “Learning” should initially mean storing and reusing customer-confirmed mappings—not automatically fine-tuning a shared AI model. This is more predictable, auditable, reversible, and tenant-safe. Any future model-training program would require a separate opt-in, privacy, governance, and data-retention design.

**Invoice review experience:**
- Display supplier's original item description, extracted quantity, unit, price, tax, and line total.
- Show the suggested directory item, match method, and confidence where available.
- Allow selection of an existing directory item or creation of a new item.
- On confirmation, offer to save the mapping for future invoices.
- Warn when a proposed match conflicts with unit, tax, SKU, or other configured business rules.
- Keep canonical item matching separate from financial validation; matching a name must not change quantity, unit price, tax, or totals without explicit customer action.

### 21.16 Item Mapping Governance and Data Integrity

- Item directories and learned aliases are workspace-scoped.
- Supplier-specific aliases take precedence over general aliases when applicable.
- Conflicting aliases must be flagged for resolution rather than overwritten silently.
- Every manual mapping change and confirmation must appear in activity history.
- Bulk imports must support a dry-run/preview and clear conflict-resolution options.
- Directory updates must be transactional so a failed import does not leave partially corrupted mappings.
- Historical invoices and exports must retain the item name/code values used at approval or export time, or reference immutable snapshots.
- Customers must be able to export their directory and mapping history.
- Item matching must never bypass invoice review requirements or alter financial values automatically.

---

## 22. Additional Database Entities

Add the following entities to the core database model in Section 11, adapting names to the selected database conventions:

- `duplicate_detection_rules`
- `duplicate_match_results`
- `invoice_confidence_scores` (or versioned confidence fields on extraction results)
- `batches`
- `batch_items`
- `activity_events`
- `api_credentials`
- `api_request_logs` (with secret-safe logging)
- `webhook_endpoints`
- `webhook_deliveries`
- `invoice_templates`
- `saved_preferences`
- `suppliers`
- `supplier_mappings`
- `exchange_rates` (or immutable rate references/snapshots)
- `scheduled_exports`
- `scheduled_export_runs`
- `support_cases`
- `support_notes`
- `status_incidents`
- `backup_records`
- `recovery_test_records`
- `feature_flags`
- `feature_flag_assignments`
- `feature_flag_audit_events`
- `items` (workspace-level canonical item/product/service directory)
- `item_aliases` (confirmed alternate names, codes, and supplier-specific mappings)
- `item_match_suggestions` (optional persisted suggestions and review outcomes)
- `item_directory_imports` (file, mapping configuration, preview, and import results)

These entities must follow workspace ownership, access-control, retention, and audit requirements. Where a feature is not enabled, its data model may remain dormant without exposing the feature to customers.

---

## 23. Additional Application Pages

### Customer application
- Duplicate Review / Potential Matches.
- Batch Upload and Batch Details.
- Activity History.
- API Keys and API Documentation.
- Webhook Endpoints and Delivery History.
- Invoice Templates and Saved Preferences.
- Supplier/Vendor Directory.
- Item/Product Directory and Alias Management.
- Item Directory Import History and Mapping Review.
- Multi-Currency Reports.
- Scheduled Exports and Run History.
- Support Requests / Help Center.
- System Status Page.

### Admin console
- Duplicate Detection Rules.
- Confidence Threshold Configuration.
- Batch Processing Monitor.
- Audit Logs and Activity Search.
- Customer API Usage and Credential Management.
- Webhook Delivery Monitor.
- Supplier/Mapping Configuration, where globally applicable.
- Item Directory and Matching Configuration (workspace-scoped customer data remains private).
- Scheduled Export Monitor.
- Support Cases and Controlled Support Access.
- Status and Incident Management.
- Backup and Recovery Monitoring.
- Feature Flags and Rollout Management.

---

## 24. Additional Business Rules

- Duplicate detection must be workspace-scoped and must not reveal cross-tenant invoice existence or details.
- Duplicate detection results are advisory unless a configured rule explicitly blocks processing.
- Confidence scores are indicators for review prioritization, not guarantees of correctness.
- Missing confidence data must remain unavailable rather than being fabricated.
- Batch retries must not duplicate successful invoice records or credit deductions.
- API access, webhook usage, batch limits, scheduled exports, and advanced reporting are governed by backend entitlements.
- Customer API credentials must be scoped, revocable, and stored securely.
- Webhook delivery is at-least-once; consumers must be able to identify duplicate events.
- Saved templates and preferences must not silently mutate canonical invoice data.
- Original invoice currency and amounts must always be preserved.
- Currency conversions must retain their rate source, effective date, and calculation basis.
- Scheduled exports must execute under the permissions and data scope of the owning workspace.
- Support access to customer data must be purpose-limited and audited.
- Status-page updates and feature-flag changes must be permission-controlled and auditable.
- Backup completion does not count as verified recoverability until restoration has been tested.
- Feature flags must not bypass authentication, authorization, or plan entitlements.
- Activity and audit logs must exclude credentials, secrets, and unnecessary sensitive document contents.

---

## 25. Additional Architecture Components

Extend the architecture in Section 18 with:
- **Duplicate detection service:** configurable matching rules, similarity checks, and customer review decisions.
- **Batch orchestration:** batch manifests, per-file jobs, concurrency limits, and retry coordination.
- **Confidence evaluation:** provider-aware confidence normalization and versioned review thresholds.
- **Public API gateway:** authentication, scopes, quotas, rate limiting, versioning, and request tracing.
- **Webhook delivery service:** event outbox, signed delivery, retry queue, and delivery history.
- **Template and preference service:** workspace-scoped saved configurations.
- **Supplier directory service:** normalized supplier records and customer-confirmed matching.
- **Item directory and matching service:** canonical item records, aliases, supplier-specific mappings, confirmed-match memory, and reviewable AI-assisted suggestions.
- **Currency reporting service:** immutable rate references and conversion snapshots.
- **Scheduler:** recurring export execution with idempotency and timezone awareness.
- **Support operations module:** case management and audited, limited support access.
- **Status monitoring and incident publishing:** health checks, incident history, and notifications.
- **Backup and recovery operations:** backup monitoring, restore testing, and documented recovery procedures.
- **Feature-flag service:** controlled rollout and auditable configuration.

All additional components must use the existing authentication, tenant-isolation, audit, and monitoring foundations.

---

## 26. Updated End-to-End Workflows

### 26.1 Bulk invoice workflow
1. Customer selects or creates a batch and uploads multiple files.
2. Backend validates file type, size, archive safety, workspace limits, and available credits.
3. System creates a batch record and one idempotent processing job per accepted file.
4. Duplicate detection checks each file against permitted records within the workspace.
5. Eligible jobs enter the processing queue under concurrency and plan limits.
6. Workers extract data and record model, extraction version, and available confidence information.
7. Validation and duplicate checks assign per-invoice outcomes.
8. Batch progress and counts update as jobs finish.
9. Customer reviews flagged invoices and retries eligible failures.
10. Customer exports selected or eligible batch results.
11. Usage, credit events, activity history, and batch outcomes are recorded.

### 26.2 Customer API and webhook workflow
1. Workspace administrator creates a scoped API credential.
2. Customer sends an authenticated, versioned API request with an idempotency key where applicable.
3. Backend validates credential, workspace membership, scopes, rate limits, and entitlements.
4. The requested job or resource operation is performed and recorded.
5. Relevant domain events are written to a durable event/outbox record.
6. Webhook workers deliver signed events to configured HTTPS endpoints.
7. Delivery attempts, response codes, and retries are recorded.
8. Customer can inspect delivery history and rotate or revoke credentials and endpoint secrets.

### 26.3 Scheduled export workflow
1. Customer creates a schedule using a saved filter and export profile.
2. Backend validates permissions, plan entitlements, timezone, and data scope.
3. Scheduler creates a unique run when the schedule is due.
4. Export engine generates and validates the output from canonical invoice data.
5. System stores the export privately and records the run outcome.
6. Customer receives a notification and accesses the file through an authorized, expiring link.
7. Failures are retried according to bounded rules and shown in run history.

### 26.4 Item directory and matching workflow
1. Customer imports or manually creates canonical items in the workspace Item Directory.
2. Import workflow validates columns, duplicate codes, conflicts, and required values before committing changes.
3. During invoice extraction, the system retains the supplier's original line description and checks confirmed workspace mappings.
4. Supplier-specific exact mappings are considered before general aliases; ambiguous matches are flagged for review.
5. Customer accepts, rejects, or corrects the suggested canonical item on the invoice review screen.
6. If the customer confirms a new mapping, the system saves it as a workspace-scoped alias with provenance and timestamps.
7. Future invoices can reuse that confirmed mapping, subject to current directory status and matching rules.
8. Historical approved invoices and exports retain their original snapshots; directory changes affect future suggestions unless the customer explicitly performs a controlled update.

### 26.5 Disaster recovery workflow
1. Monitoring detects a service or data-recovery incident.
2. On-call personnel follow the documented incident and escalation procedure.
3. The incident lead determines the recovery point and recovery strategy.
4. Authorized personnel restore required data and services in a controlled environment.
5. Integrity checks and application smoke tests are completed before traffic is restored.
6. Incident status and customer communications are updated.
7. Recovery outcomes, data loss (if any), and corrective actions are documented.

---

## 27. Final Product Definition

InvoiceFlow is a SaaS platform for converting invoices into structured, validated, and export-ready business data. Customers receive an application for uploading and batch-processing invoices, reviewing confidence and validation results, managing suppliers and a reusable item directory with customer-confirmed matching memory, managing saved preferences, accessing their data through APIs and webhooks, scheduling exports, managing subscriptions and credits, and viewing activity history. A separate private admin console gives the platform owner control over users, plans, billing, AI providers, API keys, primary and fallback models, duplicate-detection rules, feature flags, support operations, processing, system status, recovery operations, and internal costs.

The customer experience remains independent of backend model choices. This separation allows the platform to add providers, models, export formats, plans, integrations, and business workflows without exposing internal complexity or rebuilding the core invoice-processing experience. All features remain subject to strict workspace isolation, server-side entitlements, auditable administration, and documented reliability and recovery controls.

