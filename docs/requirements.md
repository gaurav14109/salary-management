# Employee Salary Management — Requirements

**User:** HR Manager, ACME Org
**Scale:** 10,000 employees, multiple countries

> This is the markdown mirror of `Employee Salary Management.docx`, committed so the
> requirement is reviewable and diffable in the repo. The `.docx` is the artifact that
> was authored first; this file is kept in sync with it.

## Goal

Replace the existing Excel-based employee salary management process with a centralized,
secure, and user-friendly web application that enables HR managers to efficiently manage,
analyze, and access employee salary information.

## Scope

The MVP will include the following capabilities:

- Centralized employee salary data management
- Bulk salary data import through Excel/CSV
- Secure access using Role-Based Access Control (RBAC)
- Interactive salary analytics dashboard
- Employee salary search, filtering, sorting, and pagination
- AI-powered chatbot for querying salary data
- Salary data validation and audit tracking for modifications

## Features

| Feature | Description | Priority |
|---|---|---|
| Salary Management | View and edit employee salary records. | P0 |
| Excel/CSV Import | Bulk upload with validation and error reporting. | P0 |
| Multi-Currency | Salary stored in the employee local currency; dashboards and totals are reported in USD, converted using an admin-maintained FX rate table. | P0 |
| RBAC | Admin and HR Manager access controls. | P0 |
| Salary Dashboard | Total payroll, average salary, highest/lowest salary. | P0 |
| Search & Filter | Search by employee, country, department. | P0 |
| Audit Trails | Track salary changes and modification details. | P0 |
| AI Chatbot | Answer salary queries from authorized data. | P1 |
| Export | Export filtered records to CSV. | P1 |

**Sequencing:** P0 is the Week 1 deliverable, built end to end before anything else
starts. P1 items are stretch scope.

## Out of Scope

- **Payroll processing, tax calculation and disbursement** — each country needs its own
  statutory engine; that is a separate product, not a data-management tool.
- **Approval workflows for salary changes** — the MVP has a single persona; an approval
  chain needs a second role and an org hierarchy we do not yet have.
- **Employee self-service portal** — the user persona is the HR Manager only; exposing
  salary data to 10,000 users is a different security and scale problem.
- **Compensation planning and merit cycles** — an annual planning process with its own
  data model (budgets, guidelines, proposed vs. effective), not a system of record.
- **HRIS / payroll system integration** — no target system is named; Excel/CSV is the
  integration boundary for the MVP.
