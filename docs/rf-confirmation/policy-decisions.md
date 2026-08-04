# Policy decision sheet

**For:** whoever owns operational policy — not necessarily RF engineering.
**Covers:** OQ-05, OQ-08, OQ-11, OQ-13, OQ-23.

These five are different in kind from the intake sheets. They are not measurements; there is
no correct answer to be looked up. They are choices about how the platform should behave,
and each one is **already implemented in both directions** with a default chosen so work
could continue. Changing any of them is a setting, not a migration.

That is why they are on a decision sheet rather than in a workbook: what is needed is a
signature, not data. Where a default is already what you want, saying so is still an answer
— it turns a provisional position into a confirmed one, which is the difference between
"nobody has objected" and "somebody decided".

---

## OQ-05 — Which input should the operator see first?

An operator sizes a transmission either by **occupied bandwidth** or by **symbol rate**.
§9.2 requires both, and both are built; each derives the other, so nothing is lost either
way. The only question is which one is pre-selected when the form opens.

- [ ] Occupied bandwidth
- [ ] Symbol rate

**Currently:** a system setting, with no preference expressed.
**If you do not answer:** the form opens on occupied bandwidth and operators change it as
often as it is wrong, which is a small tax paid many times.

---

## OQ-08 — Does a suspended allocation keep its spectrum?

An allocation can be suspended. While it is suspended, does its spectrum stay reserved —
so nobody else can take it and it can be resumed — or is it released back to free capacity?

- [ ] Suspended allocations **keep** their spectrum
- [ ] Suspended allocations **release** their spectrum

**Currently:** keep. §15.3 recommends it, and it is the safer default: releasing means a
suspension can silently become unresumable when somebody else takes the gap.
**Why it is on this sheet:** it is the one status whose behaviour the database cannot pin.
Every other status has its answer written into a CHECK constraint; this one is a runtime
setting precisely because it was left open (**A-12**).
**If you choose release:** the setting is removed and the CHECK tightens, which is a small
migration rather than a configuration change. Worth answering before there are suspended
allocations to reinterpret.

---

## OQ-11 — Must a second person approve?

Should the person who creates an allocation be prevented from approving it?

- [ ] Yes — approval requires a different person
- [ ] No — a user with both capabilities may approve their own work

**Currently:** `REQUIRE_SEPARATE_APPROVER` is on.
**Note:** this is separate from who *holds* the approval capability. Turning it off does not
grant anybody anything; it only stops the platform refusing when creator and approver are
the same person.

---

## OQ-13 — Where must a code be unique?

Every entity carries a short human code. The platform has to know how widely each one must
be unique, and it currently uses assumption **A-18**:

| Entity | Unique within | Confirm |
|---|---|---|
| Satellite | everywhere | [ ] |
| Band | everywhere | [ ] |
| Gateway | everywhere | [ ] |
| Equipment Profile | everywhere | [ ] |
| Hub | its Gateway | [ ] |
| Frequency Window | its Satellite | [ ] |
| Payload Path | its Satellite | [ ] |
| Beam | its Satellite | [ ] |
| Satnet | its Beam | [ ] |
| Satnet Path | its Satnet | [ ] |

Codes are compared **case-insensitively** throughout, so `GW-IST` and `gw-ist` are the same
code.

**Widening a scope later is free. Narrowing one is not** — if two Hubs at different
Gateways already share a code and Hub codes have to become globally unique, one of them has
to be renamed, and it may be referenced in documents outside this platform. That asymmetry
is the reason this is worth ten minutes now.

---

## OQ-23 — What time zone should times be displayed in?

All times are **stored** in UTC and that is not in question. What a user sees is.

- [ ] UTC everywhere
- [ ] One fixed operational time zone: ________________
- [ ] The viewer's own time zone
- [ ] The relevant Gateway's local time zone

**Currently:** a system setting, deliberately unset — so nothing has been decided by
accident.
**Worth knowing before you choose:** Gateways already carry their own IANA time zone, so the
last option is available. It is also the one most likely to confuse a schedule that spans
sites, since two rows in the same table would then be in different time zones.

---

## Returning this

Mark the boxes and send it back — a scan, an email, or a comment on the pull request that
carries this file. Each answer moves one line of `docs/design/00-assumptions-and-open-questions.md`
from the provisional register to the settled one, and that document is what the acceptance
checklist in Phase 9 is scored against.
