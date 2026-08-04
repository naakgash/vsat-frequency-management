"""Composite foreign key pinning a reservation's copy of its assignment bounds.

ADR-0019, and the same device as ``beams.0003``. ``SpectrumReservation`` carries
``assignment_start_hz`` and ``assignment_end_hz`` denormalised from the Beam Spectrum
Assignment it sits inside, and ``ck_res_within_assignment`` uses them to make
``allocated ⊆ assignment`` a per-row CHECK.

Without this key the copy could claim bounds far wider than the assignment's, satisfy the
CHECK, and hold spectrum the Beam is not entitled to — on a resource it shares with another
Beam, which is exactly the interference the constraint exists to prevent.

**Time containment is deliberately not enforced here.** A reservation's active period must
also sit inside its assignment's effective period, and that cannot be a composite foreign
key: an open-ended assignment has ``effective_until IS NULL``, and a MATCH SIMPLE foreign key
with a NULL in any column is satisfied trivially. The guarantee would therefore be vacuous in
the common case — which is worse than not having it, because the constraint would be there to
read. ``spectrum.services.reserve`` checks it instead, and says so.
"""

from django.db import migrations

ADD_COMPOSITE_KEY = """
ALTER TABLE spectrum_reservation
    ADD CONSTRAINT fk_reservation_assignment_bounds
    FOREIGN KEY (beam_spectrum_assignment_id, assignment_start_hz, assignment_end_hz)
    REFERENCES beam_spectrum_assignment (id, rf_start_hz, rf_end_hz)
    DEFERRABLE INITIALLY IMMEDIATE;
"""

DROP_COMPOSITE_KEY = """
ALTER TABLE spectrum_reservation DROP CONSTRAINT IF EXISTS fk_reservation_assignment_bounds;
"""

ADD_ASSIGNMENT_TARGET = """
ALTER TABLE beam_spectrum_assignment
    ADD CONSTRAINT uq_assignment_id_edges UNIQUE (id, rf_start_hz, rf_end_hz);
"""

DROP_ASSIGNMENT_TARGET = """
ALTER TABLE beam_spectrum_assignment DROP CONSTRAINT IF EXISTS uq_assignment_id_edges;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("spectrum", "0001_initial"),
        ("beams", "0003_assignment_window_composite_key"),
    ]

    operations = [
        migrations.RunSQL(sql=ADD_ASSIGNMENT_TARGET, reverse_sql=DROP_ASSIGNMENT_TARGET),
        migrations.RunSQL(sql=ADD_COMPOSITE_KEY, reverse_sql=DROP_COMPOSITE_KEY),
    ]
