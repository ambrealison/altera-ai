# Roles and permissions

Altera AI is a managed-service SaaS. Two kinds of organisation use
the platform, and each has its own role namespace. A user belongs to
exactly one organisation type — the namespaces are not mixed.

- **`gms_client`** — a retailer / supermarket chain (Carrefour, Lidl,
  Auchan, Casino, E.Leclerc, etc.).
- **`altera_internal`** — the Altera team operating the platform on
  behalf of clients.

A user may belong to multiple organisations, but every one of their
memberships uses the role namespace matching that organisation's
type. The frontend renders the **client UI** for users whose current
organisation is a `gms_client`, and the **internal-operator UI** for
users whose current organisation is `altera_internal`.

## GMS-client roles

| Role            | Description |
|-----------------|-------------|
| `client_owner`  | Manages the client organisation. Invites/removes other client users, configures billing, accepts the master agreement. There is at least one `client_owner` per client organisation. |
| `client_admin`  | Day-to-day operator on the client side: uploads catalogues, configures the project, downloads approved reports. Cannot transfer ownership. |
| `client_viewer` | Read-only access to approved reports. Cannot upload, configure, or change anything. Suitable for executives, auditors, NGO observers contracted by the client. |

Client users **never** see the manual review queue, draft reports
under Altera review, internal lifecycle states, or any organisation
other than their own.

## Altera-internal roles

| Role                       | Description |
|----------------------------|-------------|
| `altera_admin`             | Full system access. Manages client organisations (provisioning, suspension, deletion), Altera staff accounts, and platform configuration. |
| `altera_analyst`           | Operates the pipeline for client projects: triggers validation, classification, calculation; investigates data-quality issues; drafts reports. |
| `altera_reviewer`          | Owns the manual review queue. Resolves low-confidence and AI-failed classifications, handles methodology ambiguities. Every decision is logged with reviewer id, timestamp, before/after classification, and reason. |
| `altera_methodology_lead`  | Final approver for reports. Locks methodology versions per project, reviews drafts, and either approves the report for client delivery or returns it for rework. Acts as the methodology authority within Altera. |

Altera-internal users can see **all** client organisations they are
assigned to (typically: all of them for `altera_admin` and
`altera_methodology_lead`; a project-scoped subset for `altera_analyst`
and `altera_reviewer`).

## Permission matrix — GMS client side

| Action                                           | client_owner | client_admin | client_viewer |
|--------------------------------------------------|:------------:|:------------:|:-------------:|
| Invite / remove client users                     |      x       |              |               |
| Change client member roles                       |      x       |              |               |
| Transfer ownership                               |      x       |              |               |
| Configure billing                                |      x       |              |               |
| Create / configure project                       |      x       |      x       |               |
| Upload catalogue CSV                             |      x       |      x       |               |
| Upload WWF Step 2 companion JSON                 |      x       |      x       |               |
| See client-facing simplified status              |      x       |      x       |      x        |
| Download **approved** report (CSV / JSON / MD)   |      x       |      x       |      x        |
| Download draft report                            |              |              |               |
| See manual review queue                          |              |              |               |
| See internal project lifecycle states            |              |              |               |
| See other client organisations                   |              |              |               |

## Permission matrix — Altera-internal side

| Action                                           | altera_admin | altera_analyst | altera_reviewer | altera_methodology_lead |
|--------------------------------------------------|:------------:|:--------------:|:---------------:|:-----------------------:|
| Provision / suspend / delete client orgs         |      x       |                |                 |                         |
| Manage Altera staff accounts                     |      x       |                |                 |                         |
| Assign Altera staff to client projects           |      x       |                |                 |           x             |
| Trigger validation / classification / calc       |      x       |       x        |                 |           x             |
| Upload catalogue on behalf of a client           |      x       |       x        |                 |                         |
| Work the manual review queue                     |      x       |                |       x         |           x             |
| Override a deterministic-rule classification     |      x       |                |       x         |           x             |
| Change methodology version pin on a project      |      x       |                |                 |           x             |
| Generate a draft report                          |      x       |       x        |                 |           x             |
| **Approve a report** (`report_exports.approve`)  |              |                |                 |           x             |
| **Reject a report**                              |              |                |                 |           x             |
| Mark project `delivered_to_client`               |      x       |       x        |                 |           x             |
| Archive project                                  |      x       |                |                 |           x             |
| View audit logs (any client)                     |      x       |       x        |       x         |           x             |

### Key invariants

- **Only `altera_methodology_lead` (and not even `altera_admin`) can
  approve a report.** This separation of duties is intentional: admin
  rights cover provisioning and account management; methodology
  authority is a distinct, named role.
- **Clients cannot read draft or under-review reports.** The download
  endpoint checks `approval_status = 'approved'` before serving.
- **All manual review rows have `owner_type = 'altera_internal'`** in
  v1. The column exists so a future tier can opt clients into
  self-service review.

## Enforcement points

1. **Supabase RLS policies** are the source of truth for what a role
   can read and write at the database level. Policies join to
   `organisations.organisation_type` so that, e.g., the
   `manual_reviews` table is invisible to any session whose current
   organisation is `gms_client`.
2. **FastAPI route guards** check the role before calling domain
   logic and return `403` if the role is insufficient. Cross-tenant
   resource access returns `404`, not `403`, to avoid disclosing the
   existence of resources in other organisations.
3. **Frontend UI** routes to the client surface or the internal
   surface based on the current organisation's type, and hides
   actions the role cannot perform. UI gating is a usability feature,
   not a security boundary.

## Reviewer and approver accountability

Every manual review decision records:

- The reviewing user's id (always an `altera_*` user in v1).
- The previous and new classification.
- The reason text (free form, optional but encouraged).
- The timestamp.
- The methodology version active when the decision was made.

Every report approval records:

- The approving user's id (always an `altera_methodology_lead`).
- The methodology version pinned at approval.
- The approval status (`approved` / `rejected`) and timestamp.
- The reason text on rejection (required) or release note on
  approval (optional).

These records are immutable. A subsequent correction creates a new
event; it does not overwrite the prior one.
