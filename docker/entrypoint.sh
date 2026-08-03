#!/bin/sh
# Container entrypoint.
#
# Migrations are deliberately NOT run here. Specification section 22.3 puts "review
# schema/data migrations" and "apply migrations" as separate, reviewed steps of the
# release flow. Auto-migrating on container start would apply a schema change to
# production the moment a container restarted, with no review and no backup gate.
set -eu

SETTINGS="${DJANGO_SETTINGS_MODULE:-config.settings.production}"

case "${SETTINGS}" in
  *production*)
    # The production posture, asserted before the process is allowed to serve: DEBUG
    # off, secure cookies, SSL redirect, HSTS. --fail-level WARNING is what makes it an
    # assertion rather than advice.
    echo "Collecting static files..."
    python manage.py collectstatic --noinput --clear

    echo "Verifying production configuration..."
    python manage.py check --deploy --fail-level WARNING
    ;;
  *)
    # Development settings deliberately violate every one of those checks — DEBUG is on
    # and the secure-cookie flags are off, because local development is plain HTTP and
    # secure cookies would make signing in impossible. Running --deploy here asserts a
    # posture the settings module exists to switch off, and with `set -e` it takes the
    # container down at boot.
    #
    # collectstatic is skipped for a second reason: compose bind-mounts the working tree
    # over /app, so it would write into the developer's checkout as a different uid, and
    # runserver serves static files from the finders anyway while DEBUG is on.
    echo "Verifying configuration (${SETTINGS})..."
    python manage.py check
    ;;
esac

exec "$@"
