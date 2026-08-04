"""Composite foreign key pinning a Path's Beam to its Satnet's.

``SatnetPath.beam`` is denormalised from ``satnet.beam`` so that reservations, capacity queries
and scope filtering are one join rather than two.

Without this key a Path could name a Beam its Satnet is not under, and the allocation would be
judged against **another Beam's spectrum resources** (ADR-0018) — the constraint would enforce
faithfully against the wrong pool, which is the worst kind of wrong: no error, and no overlap
reported where one exists.

``UNIQUE (id, beam_id)`` on ``satnet`` was created in S10 specifically so this migration would
not have to alter a populated table.
"""

from django.db import migrations

ADD = """
ALTER TABLE satnet_path
    ADD CONSTRAINT fk_path_satnet_beam
    FOREIGN KEY (satnet_id, beam_id)
    REFERENCES satnet (id, beam_id)
    DEFERRABLE INITIALLY IMMEDIATE;
"""

DROP = """
ALTER TABLE satnet_path DROP CONSTRAINT IF EXISTS fk_path_satnet_beam;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("satnet_paths", "0001_initial"),
        ("satnets", "0002_satnet_hub_gateway_composite_key"),
    ]

    operations = [migrations.RunSQL(sql=ADD, reverse_sql=DROP)]
