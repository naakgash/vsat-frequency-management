"""Browser-driven accessibility of the specification information popover.

Specification section 2 and section 25: *"Spec info popover is keyboard accessible"*, and
the button *"must be accessible and usable without relying only on hover"*.

These run in a real browser because that is the only way to prove the claim. Asserting
that the HTML contains ``aria-expanded`` shows the attribute exists; it does not show that
pressing Enter opens anything, that Escape closes it, or that a keyboard user can reach
the button at all. Those are the requirements, so those are what is tested.

Marked ``browser`` and skipped automatically when Chromium is unavailable, so the suite
still runs on a machine without it.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from tests.factories import TEST_PASSWORD, make_admin

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

playwright = pytest.importorskip("playwright.sync_api", reason="Playwright not installed")


@pytest.fixture
def signed_in_page(page, live_server, seeded_roles, seeded_dictionary):
    """A browser session signed in as an administrator."""
    admin = make_admin("browser-admin")

    page.goto(f"{live_server.url}{reverse('accounts:login')}")
    page.fill("#id_username", admin.get_username())
    page.fill("#id_password", TEST_PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(f"{live_server.url}/")

    page.goto(f"{live_server.url}{reverse('specifications:list')}")
    return page


def _first_info_button(page):
    button = page.locator("[data-spec-info]").first
    button.wait_for(state="visible")
    return button


def test_the_information_button_is_reachable_by_keyboard(signed_in_page):
    """It must be in the tab order. A div with a click handler would not be."""
    button = _first_info_button(signed_in_page)

    button.focus()

    assert signed_in_page.evaluate("() => document.activeElement.hasAttribute('data-spec-info')"), (
        "the information button did not receive keyboard focus"
    )


def test_enter_opens_the_popover(signed_in_page):
    button = _first_info_button(signed_in_page)
    popover_id = button.get_attribute("aria-controls")

    button.focus()
    signed_in_page.keyboard.press("Enter")

    assert button.get_attribute("aria-expanded") == "true"
    assert signed_in_page.locator(f"#{popover_id}").is_visible()


def test_space_opens_the_popover(signed_in_page):
    """A real <button> responds to Space as well as Enter; a link would not."""
    button = _first_info_button(signed_in_page)
    popover_id = button.get_attribute("aria-controls")

    button.focus()
    signed_in_page.keyboard.press(" ")

    assert signed_in_page.locator(f"#{popover_id}").is_visible()


def test_escape_closes_the_popover_and_returns_focus(signed_in_page):
    button = _first_info_button(signed_in_page)
    popover_id = button.get_attribute("aria-controls")

    button.focus()
    signed_in_page.keyboard.press("Enter")
    assert signed_in_page.locator(f"#{popover_id}").is_visible()

    signed_in_page.keyboard.press("Escape")

    assert button.get_attribute("aria-expanded") == "false"
    assert signed_in_page.locator(f"#{popover_id}").is_hidden()
    # Focus must come back to the trigger, or a keyboard user is dropped at the top of
    # the document after dismissing.
    assert signed_in_page.evaluate("() => document.activeElement.hasAttribute('data-spec-info')"), (
        "focus was not returned to the trigger after Escape"
    )


def test_the_popover_shows_the_name_description_and_unit(signed_in_page):
    """Section 2: the popover shows the human-readable name, description, unit, and the
    calculation or source note when available."""
    button = signed_in_page.locator('[aria-label="About Symbol rate"]')
    button.click()

    popover = signed_in_page.locator(f"#{button.get_attribute('aria-controls')}")

    assert popover.is_visible()
    content = popover.inner_text()
    assert "Symbol rate" in content
    assert "SYMBOL_RATE" in content
    assert "symbols/second" in content
    assert "Occupied Bandwidth" in content  # the calculation note


def test_hover_alone_does_not_open_the_popover(signed_in_page):
    """Section 2 requires the control to work without relying on hover.

    A hover-only tooltip is unusable by keyboard and touch users, so hovering must not be
    the mechanism — the popover stays shut until it is activated.
    """
    button = _first_info_button(signed_in_page)
    popover_id = button.get_attribute("aria-controls")

    button.hover()
    signed_in_page.wait_for_timeout(250)

    assert signed_in_page.locator(f"#{popover_id}").is_hidden()


def test_only_one_popover_is_open_at_a_time(signed_in_page):
    """Opening a second popover closes the first.

    Driven from the keyboard rather than the mouse. An open popover overlays the table
    row beneath it — which is what a popover is supposed to do — so a mouse click aimed
    at the next button lands on the open popover instead. The keyboard path has no such
    ambiguity, and it is the path the accessibility requirement is actually about.
    """
    buttons = signed_in_page.locator("[data-spec-info]")
    assert buttons.count() >= 2, "expected several specification codes on the page"

    buttons.nth(0).focus()
    signed_in_page.keyboard.press("Enter")
    assert buttons.nth(0).get_attribute("aria-expanded") == "true"

    buttons.nth(1).focus()
    signed_in_page.keyboard.press("Enter")

    assert buttons.nth(0).get_attribute("aria-expanded") == "false"
    assert buttons.nth(1).get_attribute("aria-expanded") == "true"


def test_clicking_outside_dismisses_the_popover(signed_in_page):
    button = _first_info_button(signed_in_page)
    button.click()
    assert button.get_attribute("aria-expanded") == "true"

    signed_in_page.locator("h1").click()

    assert button.get_attribute("aria-expanded") == "false"
