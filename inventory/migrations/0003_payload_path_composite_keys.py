"""Composite foreign keys pinning a Payload Path's window sides to the real windows.

docs/design/04 section 3.2. ``PayloadPath`` carries ``uplink_window_side`` and
``downlink_window_side`` denormalised from the windows it points at, and a CHECK ties
those columns to the direction. That CHECK is only worth anything if the columns are
truthful — otherwise a row could claim ``HUB_UPLINK`` while pointing at a remote downlink
window, satisfy the CHECK, and be wrong.

A composite foreign key against ``UNIQUE (id, side)`` on ``frequency_window`` closes it:
the pair must exist as an actual window. Together the two constraints make "this FWD path
runs hub uplink to remote downlink" a fact the database enforces rather than a convention
the application maintains.

Django does not model composite foreign keys, so they ship as raw SQL with matching
reverse SQL.
"""

from django.db import migrations

ADD_COMPOSITE_KEYS = """
ALTER TABLE payload_path
    ADD CONSTRAINT fk_path_uplink_window_side
    FOREIGN KEY (uplink_window_id, uplink_window_side)
    REFERENCES frequency_window (id, side)
    DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE payload_path
    ADD CONSTRAINT fk_path_downlink_window_side
    FOREIGN KEY (downlink_window_id, downlink_window_side)
    REFERENCES frequency_window (id, side)
    DEFERRABLE INITIALLY IMMEDIATE;
"""

DROP_COMPOSITE_KEYS = """
ALTER TABLE payload_path DROP CONSTRAINT IF EXISTS fk_path_uplink_window_side;
ALTER TABLE payload_path DROP CONSTRAINT IF EXISTS fk_path_downlink_window_side;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0002_dependent_inventory"),
    ]

    operations = [
        migrations.RunSQL(sql=ADD_COMPOSITE_KEYS, reverse_sql=DROP_COMPOSITE_KEYS),
    ]
