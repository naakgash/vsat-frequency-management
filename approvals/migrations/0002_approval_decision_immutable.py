"""Make approval_decision append-only at the database level.

`docs/design/02` §8 records the table as append-only, and §20 forbids hard-deleting an
approval. The same reasoning as ``audit_event`` (**A-15**) applies with the same force: "there
is no edit screen" is not a guarantee while ``queryset.update()``, a maintenance script and a
psql session all exist, and a decision that can be quietly rewritten is worth nothing as
evidence of who put a transmission on air.

A trigger rather than revoked privileges, for the reason ADR-0013's migration gives: the
on-premises deployment runs as a single database role that also needs DDL for migrations.
"""

from django.db import migrations

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION approval_decision_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'approval_decision is append-only; % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation',
              HINT = 'An approval decision is evidence, not state (specification section 18).';
END;
$$ LANGUAGE plpgsql;
"""

DROP_FUNCTION = "DROP FUNCTION IF EXISTS approval_decision_immutable();"

CREATE_TRIGGER = """
CREATE TRIGGER trg_approval_decision_immutable
    BEFORE UPDATE OR DELETE ON approval_decision
    FOR EACH ROW EXECUTE FUNCTION approval_decision_immutable();
"""

DROP_TRIGGER = "DROP TRIGGER IF EXISTS trg_approval_decision_immutable ON approval_decision;"


class Migration(migrations.Migration):
    dependencies = [
        ("approvals", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_FUNCTION, reverse_sql=DROP_FUNCTION),
        migrations.RunSQL(sql=CREATE_TRIGGER, reverse_sql=DROP_TRIGGER),
    ]
