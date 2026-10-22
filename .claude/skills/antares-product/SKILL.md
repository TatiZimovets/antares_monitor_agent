---
name: antares-product
description: Antares product knowledge (BI migration tool, getantares.io) covering what it does, supported routes, pricing, positioning, and the migration domain knowledge it is built on. Load when working on the Antares Reddit monitor's keywords, scoring or drafting prompts, or when you need to judge whether a thread is relevant to Antares.
---

# Antares — product brief

Source: public site https://getantares.io/ (captured 2026-10-22) plus the
architecture overview at https://antares-arch-overview.spot.t1a.dev/.
Re-check the site before relying on routes or pricing; both change.

## What it is

Antares is a **BI migration tool**: "BI migration, from analysis to delivery,
automated." It analyzes a BI estate, scores how hard each dashboard is to move,
converts dashboards to the target platform, and validates the output against
the source. It is sold as a tool, not a consulting project: "A tool you can buy,
not a project you have to scope."

Vision: migrations fail because nobody maps the estate before converting.
Counting workbooks says nothing about effort. Teams should inventory at the
feature level, score complexity honestly, check usage (30–40% of Tableau estates
are "ghost dashboards" nobody opens), map dependencies, then convert in waves.
"You can't migrate what you haven't analyzed."

## Products

| Surface | Price | What it does |
|---|---|---|
| **Analyzer** | Free, self-serve | Read-only scan. Migration Readiness Score per workbook, complexity and risk detection, usage analytics (find unused content), dependency graph, effort and cost estimate, prioritized conversion order. |
| **Converter** | Flat **$200 / €200 per dashboard** | Automated conversion with validated, ready-to-use output ("90% less manual work than a rebuild"). Deterministic mapping rules first, then guardrailed and validated AI steps for edge cases; what the agent can't resolve is flagged for review. Includes a side-by-side validation report (measure values, chart types, filter behavior, row counts). LLM usage is billed separately by the customer's provider, typically under $20 per dashboard; private LLM endpoints are supported. |
| **End-to-end migration** | Custom | Delivery partners run the whole migration on Antares: strategy, testing, deployment, change management, training. |

There's no per-user licensing and no time limit. Antares is also listed on the
Microsoft and Databricks marketplaces.

A "dashboard" = one Tableau dashboard inside a workbook (a workbook with five
dashboards is five conversions). It becomes a Power BI report page or a
Databricks AI/BI dashboard.

### How it connects

- **Tableau Server (2019.1+) / Tableau Cloud:** read-only via REST API, or upload
  workbooks. It reads metadata only (structure, calculations, usage stats),
  never business data. No agent or on-prem install is needed.
- **Power BI:** upload a .pbix or a zipped PBIP project. No tenant access is needed.
- Large estates (1000+ workbooks) analyze in up to ~30 minutes.

## Migration routes

| Source → Target | Status |
|---|---|
| Tableau → Power BI | Available |
| Tableau → Databricks AI/BI | Available |
| Tableau → Sigma | Private beta |
| Power BI → Tableau | Private beta |
| Power BI → Databricks AI/BI | Private beta |
| Power BI → Sigma | Coming soon |
| Qlik, SAS VA → (Power BI / Tableau / Databricks AI/BI) | "Notify me" (planned sources) |

Converter output by target:
- **Power BI:** PBIP (TMDL semantic model + PBIR report), DAX measures and columns.
- **Databricks AI/BI:** SQL, metric views, AI/BI dashboard, deployment bundle;
  RLS → Unity Catalog row filters.
- **Sigma:** compiled model + workbook JSON, Parquet data.

Deploy adapters exist for Fabric, Databricks and Sigma environments.

## Domain knowledge: what makes migrations hard

The site's main argument is that effort depends on what a dashboard computes and
where it reads from, not how many charts it has.

**Tableau → Power BI mapping (from the Converter page):**

| Tableau | Power BI | Mode | Difficulty |
|---|---|---|---|
| Calculated fields | DAX measures & columns | Auto | Easy |
| LOD (FIXED/INCLUDE/EXCLUDE) | CALCULATE + ALLEXCEPT (not always enough) | Review | Hard |
| Table calculations | DAX window functions | Auto | Moderate |
| Parameters | What-if parameters & field parameters | Auto | Easy |
| Data blending | Model relationships (blends aren't joins) | Review | Moderate |
| Custom SQL | Power Query / native query | Review | Moderate |
| Dashboard actions | Bookmarks & drillthrough | Auto | Moderate |
| Row-level security | RLS roles | Review | Moderate |

**Tableau → Databricks AI/BI:** calculated fields → SQL and metric-view
measures; LOD → windowed aggregates; custom SQL → native Databricks SQL;
RLS → Unity Catalog row filters.

**Hard parts of a Power BI source:** calculation groups and field parameters,
custom visuals (don't run outside Power BI), DAX with complex filter context,
bookmarks and drillthrough, RLS roles, many-to-many or bidirectional
relationships, Power Query (M) transformations.

**Complexity rubric (Simple / Moderate / Complex)** over five dimensions:
calculation logic, data sources, interactivity, visualization types, and size
and layout.
- Score by the **hardest** dimension, not the average. One set action wired
  through six sheets outweighs forty plain calculated fields.
- Score the workbook together with the source under it. An unreachable live
  connection or an extract doing hidden data prep moves work to data engineering.
- Visual, sheet and calc counts correlate weakly with effort. What predicts
  effort is whether each feature has a direct target counterpart.
- Use the bands for wave order (simple first, to build the model, theme,
  validation routine and an hours-per-dashboard baseline), for budgeting hours
  per band, and for retirement decisions (complex + unused = retire, with
  owner sign-off).

**Why migrations fail ("it's not the tools"):**
- "We have 47 workbooks" estimates.
- One "simple" dashboard can really be five projects: model redesign, LOD → DAX,
  parameter rework, DirectQuery SQL tuning, interaction rebuild.
- Hidden dependencies: calc chains, shared data sources and extract schedules,
  user workflows that chain dashboards, fiscal calendar and timezone assumptions.
- Redesigning during migration ("while we're at it"): you can no longer
  validate accuracy and nobody knows what "done" means. Migrate first,
  improve later.
- Five questions to answer before converting: feature-level complexity?
  rebuild vs convert? how will numbers be verified? which workbooks are worth
  migrating? what breaks if migrated out of order?

## Audience (ICP)

Decision makers: Head of BI / BI Director, Director of Analytics, Head of Data
Platform, IT leadership, and systems integrators (SIs can partner). Practitioners
who feel the pain: BI and Tableau/Power BI developers, BI architects, analytics
engineers, and data engineers near the BI layer. They know LODs, DAX, semantic
models, and they distrust "fully automated migration" claims.

## Current go-to-market stance on Reddit (2026-10)

We are **building trust, not selling**. Replies in Reddit threads must be
genuinely useful practitioner answers. They do **not** mention or recommend
Antares, link getantares.io, or pitch anything. Use the domain knowledge above
to be helpful, not the product facts. If someone directly asks whether we work
on a tool, answer honestly.
