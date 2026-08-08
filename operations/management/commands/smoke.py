"""The release smoke check. §22.3.

Run after a deployment, before the change is called done. It answers one question — *is this
deployment serving the product* — and it answers it by fetching pages rather than by asking the
process how it feels.

**Read-only and anonymous.** Every check here is a GET as a signed-out visitor, so it can be
run against production without creating a session, a row or an audit event. What it can see
that way is exactly the point: the health endpoints, and a protected page correctly refusing to
show itself. A smoke check that needed a credential would be one nobody runs.

It is deliberately **not** the restore drill. That one proves data survived a restore and needs
a scratch database; this one proves a release is up and needs nothing.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.test import Client
from django.urls import reverse

from operations import health


class Command(BaseCommand):
    help = "Verify a deployment is serving (specification section 22.3)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--json", action="store_true", help="Emit the result for a monitor to read."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        client = Client()
        results: list[tuple[str, bool, str]] = []

        for result in health.run_readiness_checks():
            results.append((f"readiness: {result.name}", result.ok, result.detail or "ok"))

        live = client.get("/health/live")
        results.append(("liveness endpoint", live.status_code == 200, str(live.status_code)))

        sign_in = client.get(reverse("accounts:login"))
        results.append(("sign-in page", sign_in.status_code == 200, str(sign_in.status_code)))

        # A protected page must *refuse* an anonymous visitor. This is the check that catches a
        # deployment served with the wrong settings module, where a page that should redirect
        # renders instead — which no health endpoint would notice.
        protected = client.get(reverse("reporting:satnet-paths"))
        results.append(
            (
                "authorization is enforced",
                protected.status_code in (302, 403),
                f"{protected.status_code} for an anonymous visitor",
            )
        )

        static = client.get("/static/css/app.css")
        results.append(
            (
                "static files",
                static.status_code in (200, 304, 404),
                f"{static.status_code}"
                + (" — nginx serves these in production" if static.status_code == 404 else ""),
            )
        )

        ok = all(passed for _, passed, _ in results)
        if options["json"]:
            self.stdout.write(
                json.dumps(
                    {
                        "ok": ok,
                        "checks": [
                            {"name": name, "ok": passed, "detail": detail}
                            for name, passed, detail in results
                        ],
                    },
                    indent=2,
                )
            )
        else:
            for name, passed, detail in results:
                style = self.style.SUCCESS if passed else self.style.ERROR
                self.stdout.write(style(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}"))

        if not ok:
            raise CommandError("The smoke check failed. Do not mark this release done.")
        self.stdout.write(self.style.SUCCESS("Smoke check passed."))
