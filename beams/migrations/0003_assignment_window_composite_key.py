"""Composite foreign key pinning an assignment's copy of its window edges to the real window.

ADR-0019. ``BeamSpectrumAssignment`` carries ``window_rf_start_hz`` and ``window_rf_end_hz``
denormalised from the Frequency Window it carves, and ``ck_assignment_within_window`` uses
them to make ``assignment ⊆ window`` a per-row CHECK.

That CHECK is worth nothing unless the copy is truthful. Without this key a row could claim
window edges four times wider than the real ones, satisfy the CHECK, and hold an assignment
reaching well outside the spectrum its window actually grants — which is the one thing the
Window exists to prevent (§13.2).

A composite foreign key against ``UNIQUE (id, rf_start_hz, rf_end_hz)`` on
``frequency_window`` closes it: the triple must exist as an actual window.

Django does not model composite foreign keys, so this ships as raw SQL with matching reverse
SQL — the same device, and for the same reason, as the Payload Path's window sides in
``inventory.0003``.
"""

from django.db import migrations

ADD_COMPOSITE_KEY = """
ALTER TABLE beam_spectrum_assignment
    ADD CONSTRAINT fk_assignment_window_edges
    FOREIGN KEY (frequency_window_id, window_rf_start_hz, window_rf_end_hz)
    REFERENCES frequency_window (id, rf_start_hz, rf_end_hz)
    DEFERRABLE INITIALLY IMMEDIATE;
"""

DROP_COMPOSITE_KEY = """
ALTER TABLE beam_spectrum_assignment DROP CONSTRAINT IF EXISTS fk_assignment_window_edges;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("beams", "0002_beamdirectionspectrumresource_beamspectrumassignment"),
        ("inventory", "0004_spectrumresource_frequencywindow_uq_window_id_edges_and_more"),
    ]

    operations = [
        migrations.RunSQL(sql=ADD_COMPOSITE_KEY, reverse_sql=DROP_COMPOSITE_KEY),
    ]
