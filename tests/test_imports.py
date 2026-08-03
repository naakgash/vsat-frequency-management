"""Every application module imports cleanly.

This exists because of a specific, repeated trap. django-stubs declares Django base
classes such as ``ListView`` and ``ModelForm`` as generic, so mypy asks for a type
parameter — but the real classes have no ``__class_getitem__``, and the subscript raises
``TypeError`` at import time. The result is a change that type-checks perfectly and
breaks the entire application.

Without this test the symptom is dozens of unrelated failures across the suite, because
every view that touches the URL configuration collapses at once. With it, the failure
names the module and the reason.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest
from django.apps import apps

# Modules that are deliberately not importable in isolation.
SKIP = {"migrations"}


def _application_modules() -> list[str]:
    names = []
    for app_config in apps.get_app_configs():
        if app_config.name.split(".")[0] not in {
            "accounts",
            "audit",
            "operations",
            "specifications",
        }:
            continue
        package = importlib.import_module(app_config.name)
        for module in pkgutil.walk_packages(package.__path__, prefix=f"{app_config.name}."):
            if any(part in SKIP for part in module.name.split(".")):
                continue
            names.append(module.name)
    return sorted(names)


@pytest.mark.parametrize("module_name", _application_modules())
def test_module_imports(module_name):
    importlib.import_module(module_name)
