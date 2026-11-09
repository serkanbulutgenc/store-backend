from django.db import migrations

CREATE = """
CREATE FUNCTION staff_auditentry_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'staff_auditentry is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER staff_auditentry_append_only
BEFORE UPDATE OR DELETE ON staff_auditentry
FOR EACH ROW EXECUTE FUNCTION staff_auditentry_append_only();
"""

DROP = """
DROP TRIGGER staff_auditentry_append_only ON staff_auditentry;
DROP FUNCTION staff_auditentry_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("staff", "0001_initial")]

    operations = [migrations.RunSQL(CREATE, reverse_sql=DROP)]
