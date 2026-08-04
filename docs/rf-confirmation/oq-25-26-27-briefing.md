# Briefing — OQ-25, OQ-26, OQ-27

**For:** RF engineering
**Why you are reading it:** these three answers change the database schema. Everything else
in the `OPEN QUESTION` register is a row in a table that can be filled in whenever it
arrives; these three decide what the tables *are*.

**What we need back:** one answer per question. Not a specification — a decision, and
enough of the reason that we can tell whether a later case falls inside it.

---

## The one-paragraph version

The platform guarantees that two allocations cannot occupy the same Hz at the same time.
That guarantee is a single database constraint, and a constraint has a **key** — the list of
things that have to match before two rows are considered to be competing for the same
spectrum. **OQ-25 decides that key.** OQ-27 decides what "inside the window" means.
OQ-26 decides what a Satnet Path has to record. All three are cheap now and expensive
later, because changing a constraint on a table that already holds live allocations means
taking the guarantee away while the change runs.

---

## OQ-25 — May two Beams reuse the same frequency?

**The question in operational terms.** Two Beams are configured on the same satellite. Both
are fed from the same teleport, through the same gateway antenna, and both use the same hub
uplink Frequency Window. An operator allocates 14 250–14 270 MHz in the first Beam. May
another operator allocate the same 14 250–14 270 MHz in the second Beam?

**Why we cannot answer it ourselves.** §4 of the specification removes the object that used
to express this — an "interference domain" that grouped Beams which must not overlap — and
does not replace it. That leaves the Beam itself as the reuse boundary (assumption
**A-01**), which is right for two spot beams pointing at different parts of the earth and
may be badly wrong for two Beams sharing one uplink antenna. Spatial separation is what
makes reuse safe, and two feeds into the same antenna are not spatially separated.

**What is built today.** The constraint treats a different Beam as a different pool:

```
EXCLUDE USING gist (
    beam_id             WITH =,     <-- this line is the question
    frequency_window_id WITH =,
    leg                 WITH =,
    polarization        WITH =,
    allocated_rf        WITH &&,
    active_period       WITH &&
) WHERE (reserves_spectrum)
```

So today the answer above is **yes, both allocations are accepted**. If that is wrong, the
platform will confidently permit an interfering allocation and report no conflict.

### The four shapes an answer can take

| If the answer is | The key becomes | Cost if decided now | Cost if decided after S9 |
|---|---|---|---|
| **Reuse is always fine between Beams** | Unchanged | None — already built | None |
| **Reuse is never permitted on a satellite** | Drop `beam_id` | One line | Constraint rebuild, and every existing allocation revalidated |
| **Reuse depends on the leg** — shared on the hub side, free on the remote side | Replace `beam_id` with a `reuse_key` column the service writes: the Gateway on hub-side legs, the Beam on remote-side legs | One column, written in one place | New column, backfill, constraint rebuild |
| **Some named groups of Beams must not overlap** | A `reuse_group` column, with Beams assigned to groups | One column, one admin screen | As above, plus deciding the groups retrospectively |

**Our reading, offered so you can disagree with something concrete.** The third row looks
like the physical situation: a hub uplink is a shared antenna and a shared amplifier, so two
Beams transmitting the same frequencies through it are one signal; a remote downlink is
spatially separated, which is exactly what reuse depends on. If that is right, the answer we
need is *"the hub uplink side is shared per Gateway; the remote side is free per Beam"* —
and possibly *"per Hub"* rather than *"per Gateway"*, which is a second, smaller question we
also cannot answer: a Gateway is a site and a Hub is a platform at it, and whether two Hubs
at one site share an antenna is a fact about the site.

**Interim behaviour.** The Beam-keyed constraint stands, and where two Beams share a Gateway
and a hub uplink Window with overlapping RF the platform raises a **warning**, not a block.
That way the situation is visible in the data before the answer arrives, rather than being
discovered afterwards.

---

## OQ-26 — Is remote-terminal equipment in scope?

**The question.** The platform models the hub's up- and down-converter: their RF limits,
their L-band IF limits and their local oscillator. It does **not** model the remote
terminal's. Should it?

**What it changes.** A Satnet Path currently records one Equipment Profile and one L-band IF
range — the hub's. Modelling the remote side means a second profile reference, a second IF
range, and a second candidate pool on each Beam direction. It is **additive**: nothing
already stored becomes wrong.

**What it costs if the answer is late.** Less than the other two. This does not touch the
overlap constraint at all — it lands on the Satnet Path table, which is built two slices
later. An answer any time before that slice costs nothing; after it, one migration on a
populated table.

**What the gap actually is.** With hub-side equipment only, the platform will accept a
placement whose RF is legal and whose *remote* IF is outside what the remote terminal can
convert — it has no way of knowing. Whether that matters depends on whether your remote
fleet is homogeneous enough that the hub-side check is effectively the binding one.

---

## OQ-27 — May a Beam use part of its Frequency Window?

**The question.** A Beam's direction points at a Payload Path, which has an uplink Window and
a downlink Window. Today the Beam must use **the whole of** those Windows. May a Beam
instead be given a portion of a Window — say 40 MHz of a shared 72 MHz transponder — with
another Beam given a different portion?

**What is built today.** Identity, not containment: the Beam's Windows must be exactly the
Payload Path's, and the Beam Builder does not even offer them as fields, because the surest
way to guarantee they match is never to accept them as input.

**What it changes if the answer is yes.** Three things at once, which is why it is on this
list rather than in the general register:

1. **Validation.** "Inside the Window" becomes "inside the Beam's slice of the Window", and
   every containment check moves to the narrower bound.
2. **Free capacity.** The gap engine reports free spectrum between the Window's edges. With
   sub-ranges it must report gaps between the *Beam's* edges, or it will report capacity in
   a neighbouring Beam's slice as available.
3. **It interacts with OQ-25.** If two Beams hold disjoint slices of one Window, they cannot
   overlap whatever the constraint key says — the slices do the separating. If they hold
   overlapping slices, the OQ-25 answer decides the outcome. The two questions are best
   answered together, and answering only one leaves the interesting case undefined.

**Interim behaviour.** Identity is enforced, and a Beam whose Windows differ from its Payload
Path's is refused with a message that names OQ-27 — so if this is how your transponders are
actually shared, it will show up as a Beam somebody cannot build, rather than as a silent
misconfiguration.

---

## What happens to the schedule

**S9 — the reservation table, the overlap constraint and the free-capacity engine — is
waiting on OQ-25 and OQ-27.** It is the slice where the platform stops describing spectrum
and starts guaranteeing it, and it is the wrong slice to build twice.

**OQ-26 is not blocking S9.** It lands on the Satnet Path table two slices later, so it has
more time than the other two. It is briefed here because it was raised alongside them and
because the three are often discussed together — but if only two answers are available,
OQ-25 and OQ-27 are the ones that unblock work.

Everything already built stands regardless of the answers. Nothing here is a request to
revisit a decision; it is a request for two facts we are not able to derive.
