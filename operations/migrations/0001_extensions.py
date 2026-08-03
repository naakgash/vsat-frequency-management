"""Install the PostgreSQL extensions the schema depends on.

This runs first, before any domain module's migrations, because later slices declare
columns and constraints that cannot be created without it:

* ``btree_gist`` — lets a single GiST index mix uuid/text equality with range overlap,
  which is what makes the spectrum overlap exclusion constraint possible at all
  (specification section 8.3, docs/design/04 section 3).
* ``citext``     — case-insensitive human-readable codes (docs/design/04 section 5).
* ``pgcrypto``   — ``gen_random_uuid()``.

All three are *trusted* extensions in PostgreSQL 13+, so the database owner can install
them without superuser rights.
"""

from django.contrib.postgres.operations import (
    BtreeGistExtension,
    CITextExtension,
    CryptoExtension,
)
from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        BtreeGistExtension(),
        CITextExtension(),
        CryptoExtension(),
    ]
