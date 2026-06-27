import re

import pytest

from app.money import D
from app.trademe import bidder
from .factories import frend_html


# --------------------------------------------------------------------------- #
# Fakes for the shipping-radio selection helpers
# --------------------------------------------------------------------------- #
class FakeRadio:
    def __init__(self, rid, raise_on_check=False):
        self.rid = rid
        self.raise_on_check = raise_on_check
        self.checked = False

    async def get_attribute(self, name):
        return self.rid if name == "id" else None

    async def check(self, force=False):
        if self.raise_on_check:
            raise RuntimeError("element is not visible")
        self.checked = True


class FakeLabel:
    def __init__(self, text):
        self.text = text
        self.clicked = False

    async def inner_text(self):
        return self.text

    async def click(self):
        self.clicked = True


class FakeRadios:
    def __init__(self, radios):
        self._radios = radios

    def nth(self, i):
        return self._radios[i]


class FakeModal:
    def __init__(self, labels):
        self.labels = labels  # rid -> FakeLabel

    def locator(self, sel):
        m = re.search(r"label\[for='([^']+)'\]", sel)
        if m:
            return self.labels[m.group(1)]
        raise KeyError(sel)


def _ship_setup(raise_ids=(), with_pickup=False):
    radios = [FakeRadio("4", "4" in raise_ids),
              FakeRadio("5", "5" in raise_ids),
              FakeRadio("6", "6" in raise_ids)]
    labels = {
        "4": FakeLabel("Auckland, Standard — $9.00"),
        "5": FakeLabel("North Island, Standard — $17.00"),
        "6": FakeLabel("South Island, Economy — $22.00"),
    }
    if with_pickup:
        radios.insert(0, FakeRadio("p"))
        labels["p"] = FakeLabel("Pick-up")
    return FakeModal(labels), FakeRadios(radios), radios, labels


class TestSelectShipping:
    async def test_matches_by_method_label(self):
        modal, radios, rlist, _ = _ship_setup()
        await bidder._select_shipping(modal, radios, 3, 0, "North Island, Standard")
        assert rlist[1].checked is True
        assert rlist[0].checked is False and rlist[2].checked is False

    async def test_falls_back_to_index_when_method_absent(self):
        modal, radios, rlist, _ = _ship_setup()
        await bidder._select_shipping(modal, radios, 3, 2, "Nowhere City")
        assert rlist[2].checked is True

    async def test_clamps_out_of_range_index(self):
        modal, radios, rlist, _ = _ship_setup()
        await bidder._select_shipping(modal, radios, 3, 99, None)
        assert rlist[2].checked is True

    async def test_check_radio_clicks_label_when_check_fails(self):
        modal, radios, rlist, labels = _ship_setup(raise_ids=("4",))
        ok = await bidder._check_radio(modal, rlist[0])
        assert ok is True
        assert labels["4"].clicked is True

    async def test_label_only_pickup_selection(self):
        # index=None (pick-up): must match the "Pick-up" radio by label only.
        modal, radios, rlist, _ = _ship_setup(with_pickup=True)
        ok = await bidder._select_shipping(modal, radios, len(rlist), None, "Pick-up")
        assert ok is True
        assert rlist[0].checked is True            # the pick-up radio
        assert all(not r.checked for r in rlist[1:])

    async def test_label_only_does_not_fall_back_to_index(self):
        # index=None and no label match -> select nothing (never a paid option).
        modal, radios, rlist, _ = _ship_setup()
        ok = await bidder._select_shipping(modal, radios, len(rlist), None, "Pick-up")
        assert ok is False
        assert all(not r.checked for r in rlist)


# --------------------------------------------------------------------------- #
# Verification of a placed bid
# --------------------------------------------------------------------------- #
class TestVerify:
    async def test_leader_is_verified(self, monkeypatch):
        async def fetch(url, *a, **k):
            return frend_html(member_id=1, leader_id=1, current=5, bid_count=1)
        monkeypatch.setattr(bidder.browser, "fetch_html", fetch)
        ok, detail = await bidder._verify("u", "6006426545", D(5))
        assert ok is True and "leading" in detail

    async def test_outbid_by_autobid_is_verified(self, monkeypatch):
        async def fetch(url, *a, **k):
            return frend_html(member_id=1, leader_id=999, current=10, bid_count=2)
        monkeypatch.setattr(bidder.browser, "fetch_html", fetch)
        ok, detail = await bidder._verify("u", "6006426545", D(8))
        assert ok is True and "outbid" in detail

    async def test_still_behind_is_not_verified(self, monkeypatch):
        async def fetch(url, *a, **k):
            return frend_html(member_id=1, leader_id=999, current=3, bid_count=2)
        monkeypatch.setattr(bidder.browser, "fetch_html", fetch)
        ok, _ = await bidder._verify("u", "6006426545", D(8))
        assert ok is False

    async def test_fetch_failure(self, monkeypatch):
        async def fetch(url, *a, **k):
            return None
        monkeypatch.setattr(bidder.browser, "fetch_html", fetch)
        ok, detail = await bidder._verify("u", "1", D(1))
        assert ok is False and "re-fetch" in detail

    async def test_unparseable(self, monkeypatch):
        async def fetch(url, *a, **k):
            return "<html>nope</html>"
        monkeypatch.setattr(bidder.browser, "fetch_html", fetch)
        ok, detail = await bidder._verify("u", "1", D(1))
        assert ok is False and "parse" in detail


# --------------------------------------------------------------------------- #
# Inline error detection
# --------------------------------------------------------------------------- #
class FakeErrLoc:
    def __init__(self, count, text):
        self._count = count
        self._text = text
        self.first = self

    async def count(self):
        return self._count

    async def inner_text(self):
        return self._text


class FakeErrPage:
    def __init__(self, mapping):
        self.mapping = mapping

    def locator(self, sel):
        c, t = self.mapping.get(sel, (0, ""))
        return FakeErrLoc(c, t)


class TestReadError:
    async def test_detects_keyworded_error(self):
        page = FakeErrPage({".o-validation-summary": (1, "Your bid is too low")})
        assert await bidder._read_error(page) == "Your bid is too low"

    async def test_ignores_non_error_text(self):
        page = FakeErrPage({".o-validation-summary": (1, "Reserve met")})
        assert await bidder._read_error(page) is None

    async def test_no_elements(self):
        assert await bidder._read_error(FakeErrPage({})) is None
