# 03 — Permission and Authorization Model

**Derived from:** Root Specification §6, §12, §15.2, §18, §21.7, §25 (permission tests), §26.
**Governing rule (§12):** *"All permissions must be enforced in the backend, not only by hiding
buttons."*

---

## 1. Two orthogonal questions

Every authorization decision answers two independent questions. Conflating them is the usual source of
holes, so they are separate code paths:

```text
   ┌──────────────────────────┐        ┌───────────────────────────────┐
   │  CAPABILITY              │        │  SCOPE                        │
   │  "may this role do this  │  AND   │  "is this specific object     │
   │   kind of thing at all?" │        │   inside the user's grants?"  │
   └──────────────────────────┘        └───────────────────────────────┘
        role → permission                user → Beam / Hub / Gateway
        static, seeded                   dynamic, admin-granted
```

A user must pass **both**. Capability alone is never sufficient — an Operator with
`satnets.add_satnet` still cannot create a Satnet under a Beam they hold no grant for (§6).

---

## 2. Roles and capabilities

Four roles (§12), implemented as Django `Group`s seeded by a data migration so they can be inspected
and audited like any other data. Roles are additive; a user may hold more than one.

| Role | Intent |
|---|---|
| `admin` | Owns inventory, Beam engineering, the Specification Dictionary, users, scopes and imports |
| `operator` | Creates and edits Satnets and pre-approval Satnet Paths within granted scope |
| `approver` | Moves Satnet Paths through the approval and operational lifecycle |
| `observer` | Read and export only |

### 2.1 Capability matrix

`✔` = permitted, `—` = denied, `S` = permitted **only within the user's scope**.

| Capability | admin | operator | approver | observer |
|---|:--:|:--:|:--:|:--:|
| **Inventory (independent)** — Satellite, Band, Gateway, Hub, Equipment Profile | | | | |
| view | ✔ | ✔ | ✔ | ✔ |
| add / change / deactivate | ✔ | — | — | — |
| **Inventory (dependent)** — Frequency Window, Payload Path | | | | |
| view | ✔ | ✔ | ✔ | ✔ |
| add / change / version | ✔ | — | — | — |
| **Specification Dictionary** | | | | |
| view | ✔ | ✔ | ✔ | ✔ |
| edit display metadata (name, description, unit, help, order, visibility) | ✔ | — | — | — |
| edit `code` of a system-registered entry | — | — | — | — |
| **Beam** | | | | |
| view | ✔ | S | S | S |
| build / edit engineering configuration | ✔ | — | — | — |
| run validation | ✔ | — | — | — |
| activate / deactivate | ✔ | — | — | — |
| **Satnet** | | | | |
| view | ✔ | S | S | S |
| create | ✔ | S | — | — |
| edit / deactivate | ✔ | S | — | — |
| **Satnet Path** | | | | |
| view | ✔ | S | S | S |
| create (`DRAFT`) | ✔ | S | — | — |
| edit while `DRAFT` / `PLANNED` | ✔ | S | — | — |
| edit while `PENDING_APPROVAL` / `ON_AIR` / `SUSPENDED` | — | — | — | — |
| use Auto-place / gap finder | ✔ | S | S | S |
| revise an `ON_AIR` path (creates a successor) | ✔ | S | — | — |
| **Lifecycle transitions** | | | | |
| `DRAFT → PLANNED` | ✔ | S | — | — |
| `PLANNED → PENDING_APPROVAL` | ✔ | S | — | — |
| `PENDING_APPROVAL → ON_AIR` | — | — | S | — |
| `PENDING_APPROVAL → PLANNED` (reject) | — | — | S | — |
| `ON_AIR → SUSPENDED`, `SUSPENDED → ON_AIR` | — | — | S | — |
| `ON_AIR`/`SUSPENDED → RETIRED` | — | — | S | — |
| `DRAFT`/`PLANNED → CANCELLED` | ✔ | S | S | — |
| **Spectrum** | | | | |
| view spectrum map, gaps, occupancy | ✔ | S | S | S |
| edit a Spectrum Reservation directly | — | — | — | — |
| **Import / Export** | | | | |
| run dry-run | ✔ | — | — | — |
| commit an import | ✔ | — | — | — |
| export (normalized / legacy) | ✔ | S | S | S |
| **Audit** | | | | |
| view audit | ✔ | own actions | own actions | — |
| edit or delete audit | — | — | — | — |
| **Administration** | | | | |
| manage users, roles, scopes | ✔ | — | — | — |
| manage system settings | ✔ | — | — | — |
| view health / operations pages | ✔ | — | — | — |

Two rows deserve emphasis because they are absolute, for **every** role including `admin`:

- **No one** may write a Spectrum Reservation directly (§13.11). There is no view, no form, no admin
  registration, no URL. Reservations exist only as a side effect of the Satnet Path service.
- **No one** may edit or delete an Audit Event (§18). Enforced by a database trigger, not just by the
  absence of a UI (**A-15**).

Admin is powerful but not omnipotent: `admin` cannot approve its own path when
`REQUIRE_SEPARATE_APPROVER` is on, cannot edit a `PENDING_APPROVAL`/`ON_AIR` record in place, and
above all **cannot bypass the overlap constraint** — §12 states this for Approver and §26.14 makes it
universal. There is no "force" flag anywhere in the codebase.

### 2.2 Permission codenames

Django model permissions where they fit, custom permissions where the action is not CRUD:

```text
beams.view_beam / add_beam / change_beam
beams.validate_beam            # run engineering validation
beams.activate_beam            # flip to active

satnets.view_satnet / add_satnet / change_satnet

satnet_paths.view_satnetpath / add_satnetpath / change_satnetpath
satnet_paths.submit_satnetpath      # PLANNED -> PENDING_APPROVAL
satnet_paths.approve_satnetpath     # PENDING_APPROVAL -> ON_AIR
satnet_paths.reject_satnetpath      # PENDING_APPROVAL -> PLANNED
satnet_paths.suspend_satnetpath     # ON_AIR <-> SUSPENDED
satnet_paths.retire_satnetpath
satnet_paths.cancel_satnetpath
satnet_paths.revise_satnetpath

spectrum.view_spectrum
specifications.change_specificationdefinition
imports_exports.run_import_dryrun / commit_import / export_data
audit.view_auditevent / view_all_auditevent
accounts.manage_users / manage_scopes
operations.manage_settings
```

---

## 3. Scope model

### 3.1 Grants

```text
UserBeamScope     (user, beam,    granted_by, granted_at, note)
UserHubScope      (user, hub,     granted_by, granted_at, note)
UserGatewayScope  (user, gateway, granted_by, granted_at, note)
```

Each is unique on `(user, object)`. Every grant and revocation writes an Audit Event (§18).

### 3.2 Resolution rules

- **Deny by default** (**A-17**). A non-admin with no grants sees an empty application, not the whole
  estate.
- **`admin` bypasses scope entirely.** Scope checks short-circuit on the admin role.
- **Gateway grants cascade to that Gateway's Hubs.** A Hub grant does **not** imply its Gateway.
  (**OQ-30**)
- **Scope on a Satnet is conjunctive:** `beam ∈ scope(user)` **and** `hub ∈ effective_hubs(user)`,
  per §6's "only within Beams, Hubs, and Gateways included in that user's authorization scope".
  (**OQ-30**)
- **Scope on a Satnet Path is inherited** from its Satnet. A Path is never independently granted.
- **Scope on a Beam** is the Beam grant alone — a user may view a Beam and its spectrum map without
  being able to act on any of its Satnets.

```python
def effective_hub_ids(user) -> set[UUID]:
    if user.is_admin:
        return ALL
    return (
        set(user.hub_scopes.values_list("hub_id", flat=True))
        | set(Hub.objects.filter(gateway__in=user.gateway_scopes.values("gateway_id"))
                         .values_list("id", flat=True))
    )
```

### 3.3 Where scope is applied

Scope is applied in **querysets**, not in view code, so that "invisible" and "unwritable" cannot drift
apart:

```python
class BeamQuerySet(models.QuerySet):
    def for_user(self, user):
        if user.is_admin:
            return self
        return self.filter(id__in=user.beam_scopes.values("beam_id"))

class SatnetQuerySet(models.QuerySet):
    def for_user(self, user):
        if user.is_admin:
            return self
        return self.filter(beam_id__in=user.beam_scopes.values("beam_id"),
                           hub_id__in=effective_hub_ids(user))
```

Every list view, detail view, form `ModelChoiceField` queryset, HTMX fragment, export and dashboard
card uses `for_user`. A detail view fetching an out-of-scope object returns **404**, not 403 — the
existence of a Beam outside a user's scope is itself information.

A test walks every registered view and asserts it either declares `scope_exempt = True` (with a
reason) or resolves its queryset through `for_user`. That prevents the classic omission where a new
list view is added without scoping.

---

## 4. Enforcement layers

Four layers, each independently sufficient to deny. The specification requires the outermost to be
non-decorative (§12) and the innermost to be final (§8.3).

| # | Layer | Mechanism | Failure mode it catches |
|---|---|---|---|
| 1 | **Template** | `{% if perms.x %}` and scope-aware context flags | Cosmetic only. Never the sole control. |
| 2 | **View** | `PermissionRequiredMixin` + `for_user()` querysets + object lookup returning 404 | Direct URL access, crafted POST |
| 3 | **Service** | `policy.require(user, action, obj)` at the top of every service function; raises `PermissionDenied` and writes a denial Audit Event (§18) | Any caller — view, importer, management command, future API |
| 4 | **Database** | Exclusion constraints, CHECKs, append-only audit trigger | Bugs in layers 1–3, concurrency, direct SQL |

The service layer is the authoritative one. The importer (§17) and any management command reach the
domain through the same service functions, so they inherit the same checks — this is the reason
authorization lives in `services.py` and not in `views.py`.

```python
# every service function starts this way
@transaction.atomic
def submit_for_approval(*, user, satnet_path, reason, expected_version):
    policy.require(user, "satnet_paths.submit_satnetpath", satnet_path)
    _assert_version(satnet_path, expected_version)      # §15.5 optimistic locking
    ...
```

`policy.require` is the single choke point: it checks capability, then scope, then any
state-dependent rule, and on denial records `AuditEvent(action="PERMISSION_DENIED", success=False)`
before raising. §18 requires permission denials to be audited, which is only possible if denial goes
through one function.

---

## 5. State-dependent rules

Capability and scope are not enough; several rules depend on the object's own state.

| Rule | Applies to | Statement |
|---|---|---|
| Editable statuses | Satnet Path | Field edits are accepted only in `DRAFT` and `PLANNED`. `PENDING_APPROVAL`, `ON_AIR` and `SUSPENDED` are edited only via `revise()`, which creates a successor (§15.4). |
| Derived fields | Satnet Path | `symbol_rate`, all RF/IF ranges, `allocated_bw` and both reservations are never bound from a form, for any role (§26.16). |
| Separate approver | Approval | When `REQUIRE_SEPARATE_APPROVER` is true, `decided_by != created_by` (**OQ-11**). |
| Inactive parents | Satnet, Satnet Path | An inactive Satnet cannot receive new Paths (§13.9); an inactive Frequency Window cannot receive new reservations (§13.6). |
| Beam activation | Beam | Activation is refused while any enabled direction fails validation (§5.4, §26.6). |
| Beam deactivation | Beam | Refused while spectrum-reserving Satnet Paths exist (**OQ-33**). |
| Validity containment | Satnet | A Satnet may not outlive its parent Beam's effective period (§13.9). |
| Deletion | everything operational | `ON_AIR` history, used inventory versions, reservations, approvals, imports and audit are never hard-deleted (§20). Deactivation replaces deletion. |

---

## 6. URL and navigation map with required permissions

Navigation follows §7's recommended order. `S` marks scope-filtered listings.

| URL | View | Capability | Scope |
|---|---|---|---|
| `/` | Dashboard | authenticated | S |
| `/spectrum/` | Spectrum map (Beam-first) | `spectrum.view_spectrum` | S |
| `/beams/` | Beam list | `beams.view_beam` | S |
| `/beams/<uuid>/` | Beam detail | `beams.view_beam` | S |
| `/beams/new/` … `/beams/<uuid>/builder/<step>/` | Beam Builder wizard | `beams.add_beam` / `change_beam` | admin only |
| `/beams/<uuid>/validate/` | Run validation | `beams.validate_beam` | admin only |
| `/beams/<uuid>/activate/` | Activate | `beams.activate_beam` | admin only |
| `/satnets/` | Satnet list | `satnets.view_satnet` | S |
| `/satnets/new/` | Create Satnet | `satnets.add_satnet` | S (Beam + Hub) |
| `/satnets/<uuid>/` | Satnet detail + capacity | `satnets.view_satnet` | S |
| `/satnet-paths/` | Satnet Path table | `satnet_paths.view_satnetpath` | S |
| `/satnet-paths/new/step/<n>/` | Guided wizard | `satnet_paths.add_satnetpath` | S |
| `/satnet-paths/<uuid>/` | Detail | `satnet_paths.view_satnetpath` | S |
| `/satnet-paths/<uuid>/submit/` | → `PENDING_APPROVAL` | `submit_satnetpath` | S |
| `/satnet-paths/<uuid>/approve/` | → `ON_AIR` | `approve_satnetpath` | S |
| `/satnet-paths/<uuid>/reject/` | → `PLANNED` | `reject_satnetpath` | S |
| `/satnet-paths/<uuid>/suspend/`, `/resume/`, `/retire/`, `/cancel/` | lifecycle | matching codename | S |
| `/satnet-paths/<uuid>/revise/` | New revision | `revise_satnetpath` | S |
| `/inventory/…` | Independent + dependent inventory | `view_*` to read, admin to write | global |
| `/specifications/` | Dictionary list | `view` all, `change` admin | global |
| `/imports/` , `/imports/<uuid>/dry-run/`, `/commit/` | Import | `run_import_dryrun`, `commit_import` | admin only |
| `/exports/…` | Export | `export_data` | S — exported rows are scope-filtered |
| `/audit/` | Audit search | `view_all_auditevent`, else own actions | — |
| `/administration/users/`, `/scopes/`, `/settings/` | Administration | `manage_users`, `manage_scopes`, `manage_settings` | admin only |
| `/health/live`, `/health/ready` | Health (§21) | unauthenticated, no data disclosure | — |

HTMX fragment endpoints (`/satnet-paths/fragments/gaps/`, `/preview/`, `/validate/`) carry **the same
decorators as their parent page**. Fragment endpoints are a common authorization gap; a test
enumerates every URL pattern and fails on any that lacks either a permission declaration or an
explicit `public = True`.

Exports are scope-filtered at the queryset, not at render time — an Observer exporting "all Satnet
Paths" receives only their scope, and the export file records the filter parameters used (§17.2).

---

## 7. Authentication and session security (§21)

| Requirement | Implementation |
|---|---|
| Custom user | `accounts.User` with UUID pk, behind a pluggable auth backend (**OQ-16**: local now, LDAP/AD later) |
| Password policy | Django validators, minimum length raised, common-password and numeric checks on |
| Login rate limiting + temporary lockout | `LoginAttempt` table + per-username and per-IP throttle; lockout duration a system setting (§21.4, §21.5) |
| MFA for admin | TOTP for accounts holding `admin`, enabled when operationally possible (§21.6) |
| Cookies | `Secure`, `HttpOnly`, `SameSite=Lax`; session cookie only, no persistent auth cookie |
| CSRF | Django CSRF on; HTMX configured to send the token on every non-GET |
| Audit | Login, logout, failed login, lockout, role change, scope change, permission denial all recorded (§18) |
| Errors | Safe 403/404/500 pages, no stack traces (§21.15) |

---

## 8. Test plan for authorization (§25)

One test per matrix cell is generated from a declarative table, so adding a capability without a test
is impossible. Explicit named tests cover the specification's five stated cases plus the ones that
protect the model's integrity:

```text
tests/permissions/
├── test_matrix.py                # parametrised over the §2.1 matrix, all roles x all actions
├── test_scope_beam.py            # operator creates Satnet only under a granted Beam       (§25)
├── test_scope_hub.py             # Beam grant without Hub grant is refused                 (A-17)
├── test_scope_leakage.py         # out-of-scope object -> 404; list views never leak counts
├── test_beam_engineering.py      # operator cannot edit Beam engineering data              (§25)
├── test_spec_dictionary.py       # admin can edit spec metadata; others cannot             (§25)
├── test_observer_readonly.py     # observer write attempts all rejected                    (§25)
├── test_approver_cannot_bypass.py# approver overlapping approval still hits the constraint (§25)
├── test_derived_fields.py        # POSTing derived fields is ignored for every role        (§26.16)
├── test_reservation_unwritable.py# no URL, form or admin route writes a reservation        (§13.11)
├── test_audit_immutable.py       # UPDATE/DELETE on audit_event raises at the DB           (§18)
├── test_denial_is_audited.py     # every PermissionDenied produces an AuditEvent           (§18)
└── test_all_urls_declare_perms.py# URL-conf sweep incl. HTMX fragments
```

Every write test is performed as a **direct HTTP POST**, never by clicking a rendered button, so a
hidden button can never be mistaken for an enforced rule.
