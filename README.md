# ICP Web Scraper

A GTM research tool that discovers companies through finance-related hiring signals and filters them based on ICP criteria.

## How it works

Naukri
→ finance job search
→ multiple result pages
→ company identification
→ employee-size filter
→ finance-team filter
→ company-type signals
→ scrape qualified jobs
→ final ICP company list

## ICP Criteria

- Total employees: 101–1,000
- Finance team: 11+ people

Preferred company types:

- AI
- AI agents
- SaaS
- B2B SaaS
- Enterprise software
- Startups
- Technology

## Finance Hiring Signals

The scraper searches for roles/signals including:

- Accounts Receivable
- Accounts Payable
- Reconciliation
- Billing
- Collections
- Order to Cash
- Revenue Accounting
- Finance Operations
- Record to Report

## Project Structure

```text
ICP_Webscrapper/
├── scraper/
│   └── main.py
├── data/
├── output/
├── config.yaml
├── requirements.txt
├── .gitignore
└── README.md