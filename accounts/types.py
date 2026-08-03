"""Shared type aliases for the accounts module."""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser

from accounts.models import User

#: Whoever is attempting an action. Deliberately includes ``AnonymousUser``.
#:
#: Service functions accept an ``Actor`` rather than a ``User`` because they are the
#: layer that *decides* whether the caller is permitted, and an anonymous caller is a
#: perfectly ordinary input to that decision. Typing services as ``User`` would push the
#: authentication check up into the views, which is exactly where specification section
#: 12 says it must not live.
Actor = User | AnonymousUser
