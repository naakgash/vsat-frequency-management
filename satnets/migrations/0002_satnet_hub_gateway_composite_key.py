"""Composite foreign key pinning a Satnet's Gateway to its Hub's.

docs/design/02 §4.1. ``Satnet.gateway`` is denormalised from ``hub.gateway`` so that scope
filtering and Gateway-based listing are one join rather than two.

A denormalised column is only worth having if it cannot lie. Without this key a Satnet could
name a Gateway its Hub is not at, and every scope check and every listing that trusted the
copy would then be answering about the wrong site — silently, and in the direction that grants
access rather than withholds it.

A composite foreign key against ``UNIQUE (id, gateway_id)`` on ``hub`` closes it. That unique
constraint was created in S4 specifically so this migration would not have to alter a
populated table.

Django does not model composite foreign keys, so this ships as raw SQL — the same device as
``inventory.0003`` and ``beams.0003``.
"""

from django.db import migrations

ADD_COMPOSITE_KEY = """
ALTER TABLE satnet
    ADD CONSTRAINT fk_satnet_hub_gateway
    FOREIGN KEY (hub_id, gateway_id)
    REFERENCES hub (id, gateway_id)
    DEFERRABLE INITIALLY IMMEDIATE;
"""

DROP_COMPOSITE_KEY = """
ALTER TABLE satnet DROP CONSTRAINT IF EXISTS fk_satnet_hub_gateway;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("satnets", "0001_initial"),
        ("inventory", "0004_spectrumresource_frequencywindow_uq_window_id_edges_and_more"),
    ]

    operations = [
        migrations.RunSQL(sql=ADD_COMPOSITE_KEY, reverse_sql=DROP_COMPOSITE_KEY),
    ]
