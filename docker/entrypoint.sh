#!/bin/sh
# Container entrypoint.
#
# Migrations are deliberately NOT run here. Specification section 22.3 puts "review
# schema/data migrations" and "apply migrations" as separate, reviewed steps of the
# release flow. Auto-migrating on container start would apply a schema change to
# production the moment a container restarted, with no review and no backup gate.
set -eu

echo "Collecting static files..."
python manage.py collectstatic --noinput --clear

echo "Verifying configuration..."
python manage.py check --deploy --fail-level WARNING

exec "$@"
