# Slice S9a — Spectrum Resources and Beam Spectrum Assignments

**Phase:** 4
**Report format:** Root Specification §27
**Answers:** **OQ-25**, **OQ-26**, **OQ-27** — the three questions that held the S9 gate

---

## Goal

Implement the reuse model RF engineering specified, so the reservation engine has something
correct to key on.

The gate is lifted. It was worth holding: the answer changed the exclusion-constraint key,
superseded the design's highest-risk assumption, and ruled out an implementation the platform
would otherwise have reached for.

## What the answers changed

**OQ-25 — reuse is not keyed on the Beam.** **A-01** is superseded.

> *"Frequency reuse shall not be determined solely by Beam identity… allocations compete
> whenever they occupy the same physical or logical spectrum resource… Hub, antenna and
> geographical-site identity do not independently create reusable spectrum."*

**The briefing proposed a reading and the reading was wrong.** It suggested keying the hub
uplink leg per Gateway, reasoning that a shared antenna is a shared signal. Gateway is not the
boundary: *"two redundant antennas at different sites shall remain in the same payload-input
spectrum domain when they feed the same satellite input."* The unit of competition is the
satellite payload input; geography has nothing to do with it. A Gateway-keyed constraint would
have accepted allocations that genuinely collide.

Being wrong in public is the point of having written it down. A question with no proposed
answer invites a meeting; a proposed answer invites a correction, and the correction is what
arrived.

**OQ-26 — a prohibition, not a permission.** The expected answer was "out of scope, skip it".
What came back also ruled out the obvious implementation:

> *"A single remote equipment profile shall not be stored on the Satnet Path because the
> remote fleet is heterogeneous… it shall use optional Remote Equipment Profiles with a
> many-to-many relationship."*

**A-05**'s stated blast radius — "a second profile FK and a second IF range on `SatnetPath`" —
was the design mistake, and it was the cheap-looking change nobody would have questioned. The
assumption now records a link table instead. No code was written for this: the value of the
answer is a wrong turn not taken in S11.

**OQ-27 — the Window is a ceiling.** A Beam may use one or more sub-ranges, as time-bounded
assignments tied to a payload-configuration version, and free capacity is computed **within
active assignments only**.

## Files created or changed

**inventory** — `SpectrumResource` model, `SpectrumResourceKind`, form, list and detail views,
two templates, dependency registration, `migrations/0004`

**beams** — `BeamSpectrumAssignment`, `BeamDirectionSpectrumResource`, two validation rules,
`_replace_resources` and `_ensure_default_assignments` services, `_spectrum_panel` partial,
`migrations/0002` and `0003`

**accounts** — `VIEW_SPECTRUM_RESOURCE`, `migrations/0008`

**Documentation** — ADR-0018, ADR-0019, the verbatim answer transcript, register updates
(A-01 superseded, A-05 confirmed, A-06 revised, A-21 to A-24 new), `docs/design/04` §3.1 and
§3.1.1, the slice plan

**S0 package** — a seventh sheet, `06-spectrum-resources.csv`. Resources are RF data somebody
must supply, and the answer created a new kind of it.

**Tests** — `tests/beams/test_spectrum_model.py` (24)

## Database impact

| Table | Notes |
|---|---|
| `spectrum_resource` | The reuse key. Satellite, kind, leg, optional polarization, effective period |
| `beam_spectrum_assignment` | A sub-range of one window for a period, pinned to a Payload Path version |
| `beam_direction_spectrum_resource` | Which resources a direction's legs occupy — many-to-many, per **A-23** |

The exclusion constraint that keys on all this lands in S9. What ships here is the thing it
will key on, which is the right order: designing a constraint and the record it points at in
one commit is how a key gets chosen to suit the code rather than the physics.

**Three constraints, and one that could not be written where it belonged — again.**

- **`ck_assignment_within_window`** — the CHECK that makes the Window a ceiling. Per-row only
  because the assignment carries a copy of its window's edges.
- **`fk_assignment_window_edges`** — the composite foreign key that makes that copy truthful.
  Without it, widening the copy satisfies the CHECK and lets an assignment reach outside the
  spectrum its window grants. Same device as the Payload Path's window sides, same reason.
- **`excl_assignment_overlap`** — two active assignments on one window may not overlap in RF
  *and* time. Two answers to "what may this Beam use" is not an answer, and the gap engine
  would count the shared spectrum twice.

## The rule whose failure mode is silence

`_check_spectrum_resources` **blocks**, and that is the slice's main judgement call.

Every other rule in `beams.validation` guards against a configuration that would be *refused*
later. This one guards against a configuration that would be **accepted**. A leg mapped to no
resource competes with nothing: every allocation on it succeeds, no conflict is reported, and
there is no missing row for anyone to notice. The database cannot help — an exclusion
constraint compares rows that exist, and the failure here is that none do.

So it is caught before activation, and there is deliberately no fallback. Inferring one
resource per Beam reinstates the superseded A-01 under a new name; inferring one per satellite
forbids all reuse. Both are guesses about interference, which is the thing this record exists
to replace.

**The consequence, stated plainly: every Beam built before this slice is invalid until its
legs are mapped.** The test factories needed updating for exactly this reason, and that is the
change working rather than the change being awkward — the identity-based model was giving an
answer for free that was never ours to give.

## How this lands without breaking S8

Configuring a direction creates **one full-width, open-ended assignment per window** — the
degenerate case OQ-27 permits explicitly. Existing Beams behave identically; what was implicit
in "the Beam uses its whole window" is now a row that says so.

`_ensure_default_assignments` **fills a gap and never edits**, and that distinction is the one
way it could do real harm. A direction narrowed to half its window, then saved for an unrelated
reason, must not silently get the other half back.
`test_the_default_assignment_never_re_widens_a_narrowed_one` pins it.

## Security and permission impact

- `inventory.view_spectrumresource` for **all four roles** — an Operator choosing a Beam needs
  to see what it competes on.
- Writes go through the existing admin-only inventory choke point; no new write path.
- Resource mapping is Beam engineering, so administrator-only (§25), like the rest of it.

## Tests added

697 total, up from 666 at S0. 31 new: 24 for the model below, and 7 from the seventh intake
sheet, which the S0 harness parametrises over automatically.

| Covers | |
|---|---|
| The silent failure | An unmapped leg blocks; both legs reported; half a mapping still blocks; the finding names the leg and what to supply |
| Resource integrity | Wrong satellite refused; deactivated resource refused; polarization is a property, not a key column |
| The point of the change | One resource shared by two Beams — inexpressible under A-01 |
| Assignments | The full-window default; never re-widening a narrowed one; several disjoint sub-ranges; no active assignment blocks |
| The database | `excl_assignment_overlap`; RF overlap allowed when periods do not overlap; `ck_assignment_within_window`; `fk_assignment_window_edges` catching a lying edge copy; `ck_assignment_start_lt_end` |
| §26.20 | No resource and no assignment is seeded |

One existing test changed meaning rather than breaking:
`test_the_identity_finding_cites_the_open_question` asserted that pointing a direction at the
wrong window cites OQ-27 as an unbuilt feature. Sub-ranges are built now, so the message has to
send someone to the assignment rather than to a limitation that no longer exists. Renamed and
rewritten rather than deleted — the rename is the record that the rule's *meaning* moved.

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.6 — a Beam cannot be activated while an enabled direction is invalid | **Extended.** Two more ways to be invalid, both of which were previously silent. |
| §26.16 — calculated values are engine-owned | **Held.** No derived frequency is stored. |
| §26.20 — no invented RF value | **Held.** Both tables ship empty, and the sheet to fill one of them ships with them. |

## Verification performed

```
pytest                                   697 passed, 2 skipped (the OQ-22 gate)
ruff check . / ruff format --check .     clean
mypy (8 modules, calculations strict)    no issues in 100 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
```

## Remaining open questions

**OQ-25, OQ-26 and OQ-27 are closed.** The register records them as answered with the
transcript alongside.

**OQ-32 got more consequential.** Whether a Satnet Path's validity may extend beyond its
parents was a question about one containing period; there are now two, because an allocation
must also sit inside its assignment's period. Worth re-asking before S11 rather than after.

Untouched: OQ-01 to OQ-24 as before, OQ-28 to OQ-31, OQ-33 to OQ-35.

## Next slice

**S9 — Reservations, the exclusion constraint and the gap engine**, now unblocked and with a
key that reflects the physics. Two things it inherits from here and must not lose: the
constraint keys on `spectrum_resource_id` alone, and an allocation writes **N ≥ 2** occupancy
rows rather than a canonical/translated pair — so the §9.5 blocking message has to name *which
resource* conflicted, or an operator sees "this overlaps" with no way to tell which of three
shared chains is the problem.
