# Slice S4 — Independent Inventory

**Phase:** 2 (Specification Dictionary and Inventory)
**Report format:** Root Specification §27

---

## Goal

The five independent master-data entities are fully manageable, Inventory is visibly split
into Independent and Dependent groups, detail screens carry dependency summaries, and an
object in use cannot be deactivated. This slice also lands the first object-level scope
grants and resolvers, which is what settles **OQ-30**.

## Files created or changed

**inventory** — `models.py`, `constants.py`, `dependencies.py`, `scope.py`, `services.py`,
`forms.py`, `views.py`, `urls.py`, `apps.py`, `migrations/0001_initial.py`

**accounts** — `UserGatewayScope` and `UserHubScope` models, `mixins.py` (new),
`policy.record_denial`, two capability additions, migrations `0004` and `0005`

**Interface** — `templates/inventory/` (index, five list screens, five detail screens, a
shared edit form, and the `_dependency_summary`, `_actions`, `_list_header`,
`_detail_header` and `_status_badge` partials), navigation entry

**Tests** — `tests/inventory/{factories,test_scope,test_constraints,test_dependencies,test_permissions}.py`

**Documentation** — `docs/adr/0003-integer-hz-and-decimal-rolloff.md`, this report

## Database impact

| Table | Notes |
|---|---|
| `satellite` | Orbit type, orbital position, effective period |
| `band` | Informative RF bounds, `tuning_raster_hz` (nullable — **OQ-31**) |
| `band_polarization` | Child table; **no polarization is preselected** (**OQ-14**) |
| `gateway` | Site, coordinates, time zone |
| `hub` | `gateway_id` NOT NULL; code unique per Gateway |
| `equipment_profile` | RF/IF/LO limits, conversion algebra, version columns (**A-16**) |
| `user_gateway_scope`, `user_hub_scope` | Object-level grants |

Eleven CHECK constraints, all tested by attempting the violation through the ORM. Two are
worth naming:

- **`ck_equipment_conversion_sideband`** pins the conversion method to the sideband.
  `IF = |RF − LO|` cannot be inverted without knowing which side the local oscillator sits
  on, so a profile claiming low-side injection while subtracting the IF from the LO would
  silently produce a wrong IF. The pairing is enforced rather than trusted.
- **`uq_hub_id_gateway`** is not needed by anything yet. It is the target for the composite
  foreign key that will pin `Satnet.gateway_id` to its Hub's Gateway in S10
  (docs/design/04 §3.2), created now so that migration does not have to alter a populated
  table.

`PROTECT` on every foreign key into inventory: deleting a Gateway that has Hubs, or a Band
that has Equipment Profiles, is refused by the database (§20). There is no delete route in
the application at all — deactivation is the only retirement mechanism.

## Security and permission impact

- **Read for every role, write for Admin only.** An Operator selecting a Beam needs to see
  the Satellite and Band behind it; only an administrator changes master data (§12).
- **Scope tables landed with their targets**, as the S2 report said they would. The foreign
  keys are declared as **string references** to `inventory.Gateway` and `inventory.Hub`, so
  `accounts` gains full referential integrity without importing a domain module — the
  `accounts does not import domain modules` contract still holds.
- **Gateway and Hub lists are scope-filtered** through `for_user`, and an out-of-scope
  detail returns **404, not 403**: the existence of a site outside a user's scope is itself
  information.
- Deactivation is refused while dependants exist, and every create, update, deactivation
  and reactivation is audited with before/after values.
- Optimistic locking on every edit (§15.5).

### A gap this slice found in S2 and S3

`test_a_denied_write_is_audited` failed, and the reason turned out to affect every view
written so far. Django's `PermissionRequiredMixin` refuses the request inside `dispatch()`,
**before** any service runs — so a view protected only by the stock mixin denied silently,
with nothing reaching the audit trail. §18 requires denials to be recorded.

The S2 test passed only because it posted to `assign_roles`, a function view that delegates
to a service and therefore went through the choke point. Every class-based view added since
— the user list, the user detail, all three dictionary screens — was denying without a
record.

Fixed with `accounts.mixins.AuditedPermissionRequiredMixin`, now used everywhere the stock
mixin was, plus `policy.record_denial` as the public entry point for denials raised outside
`policy.require`. Anonymous requests are deliberately not audited: a redirect to the
sign-in page is ordinary behaviour, and recording every one would bury the real denials.

### Two other corrections

**`is_active` was declared five times.** mypy could not type the deactivation service
against it, which was the symptom rather than the problem. Extracted to a
`DeactivatableModel` abstract base and an `InventoryRecord` base combining it with the
timestamp/version mixin. `makemigrations --check` confirms no schema change — the columns
stay exactly where they were.

**Annotated list querysets were paginating unordered.** An aggregate annotation adds a
`GROUP BY` that drops `Meta.ordering`, and paginating without an `ORDER BY` can repeat or
skip rows between pages. Django warns about this; the warning was real. Both annotated
querysets now order explicitly.

## Tests added

282 tests total, up from 200. 57 new:

| File | Covers |
|---|---|
| `test_scope.py` (11) | **OQ-30 settled in both directions**: a Gateway grant cascades to its Hubs including one commissioned *after* the grant; a Hub grant does **not** imply its Gateway; deny by default; Admin bypass; no duplicate rows when both grants exist; Observer scoped like any non-admin; out-of-scope detail returns 404 |
| `test_constraints.py` (16) | Every CHECK attempted through the ORM; hub code unique per Gateway but not globally; the conversion/sideband pairing rejected both ways and accepted for all three valid combinations; `PROTECT` refuses deletion; **no inventory is seeded** |
| `test_dependencies.py` (10) | Counts including zeros; deactivation refused when in use; reactivation never blocked; informational dependencies do not block; repeated `ready()` does not duplicate rows; audit of deactivation |
| `test_permissions.py` (20) | Read for all four roles; every write attempted as a direct POST for three non-admin roles; denials audited; the Independent/Dependent split rendered; MHz→Hz conversion; sub-Hz input refused; Gateway and Hub proven to be separate entities |

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.4 — Inventory visibly divided into Independent Data and Dependent Data | **Met.** The index renders both groups from one declaration; dependent entities are named with the slice that delivers them. |
| §26.20 — no invented RF values | **Enforced by test.** `test_no_inventory_is_seeded` fails if anyone ships a plausible-looking satellite or equipment profile. |
| §26.17 — data traceable | **Advanced**: every inventory write is audited with before/after. |
| §26.18 | **Partial**: ADR-0003 added; runbooks remain S17. |

## Verification performed

```
pytest                                   282 passed
ruff check . / ruff format --check .     clean
mypy (6 modules)                         no issues in 66 source files
lint-imports                             4 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
manage.py migrate                        clean on a fresh database
```

Role capabilities after the reseed migration: admin 12, operator 7, approver 7, observer 6.

## What was deliberately not invented

**The inventory ships empty.** No satellite, no band, no equipment profile. Every value
those tables would hold is an open question — Frequency Windows (**OQ-01**), translations
(**OQ-02**), polarization mappings (**OQ-03**), equipment RF/IF/LO limits (**OQ-04**),
polarization types in use (**OQ-14**), tuning raster (**OQ-31**). A plausible-looking
Ka-band satellite with a made-up LO would be indistinguishable from real data once loaded,
which is precisely the failure §26.20 exists to prevent. A test enforces this.

Two modelling choices deserve flagging rather than burying:

- **`orbital_position` is free text.** For a geostationary satellite it is a longitude like
  `42.0E`, but MEO and LEO constellations have no single such value, and imposing a numeric
  column would force a shape the specification does not state.
- **`PolarizationType` lists all four standard forms** (RHCP, LHCP, H, V) because they are
  standard technical labels, not invented values. Which are *in use* is **OQ-14**, and no
  Band ships with any selected.

## OQ-30 answered

The slice plan flagged **OQ-30** as becoming concrete here, and it has. Both parts are now
implemented and tested:

| Question | Answer | Rationale |
|---|---|---|
| Does a Gateway grant cascade to its Hubs? | **Yes** | Granting a teleport site should not require enumerating every hub at it, and a hub commissioned later must be covered without anyone remembering a second grant. |
| Does a Hub grant imply its Gateway? | **No** | A hub-level grant is narrower than a site-level one; widening it would hand out access to every other hub at the site. |

The third part — whether scope is conjunctive for an object with both a Beam and a Hub —
does not arise yet: Gateway and Hub each have a single axis. It becomes real with Satnet in
**S10**, and the design (**A-17**) says conjunctive.

## Remaining open questions

Touched, not resolved: **OQ-04**, **OQ-13** (code uniqueness scopes — implemented per
**A-18**, still to be confirmed), **OQ-14**, **OQ-31**.

Unchanged and still required before **S9**: **OQ-25**, **OQ-26**, **OQ-27**.

## Next slice

**S5 — Dependent Inventory and Master-Data Versioning.** Frequency Windows and Payload
Paths as versioned master data, the version-overlap exclusion constraints, and the
composite foreign keys that pin a Payload Path's window sides to its direction. Equipment
Profile's version columns ship already; S5 adds the versioning service all three share.
