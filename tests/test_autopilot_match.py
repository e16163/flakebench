"""Element matching: the safety-critical part of replay."""
from autopilot.match import href_pattern, next_link, resolve


def el(i, tag, label, **kw):
    extra = kw.pop("extra", {})
    return {"id": i, "tag": tag, "label": label, **kw, "extra": extra}


SAVE = {"tag": "button", "label": "Save", "type": "submit"}


def test_exact_match():
    els = [el(1, "a", "Leads", href="/leads"), el(2, "button", "Save", type="submit")]
    e, how, _ = resolve(SAVE, els)
    assert (e["id"], how) == (2, "exact")


def test_renamed_only_submit_button_is_tolerant_match():
    els = [el(1, "a", "Leads", href="/leads"), el(2, "button", "Save changes", type="submit")]
    e, how, why = resolve(SAVE, els)
    assert (e["id"], how) == (2, "tolerant")
    assert any("only submit" in w for w in why)


def test_two_equally_good_candidates_refuses_to_guess():
    els = [el(1, "button", "Save draft", type="submit"), el(2, "button", "Save and send", type="submit")]
    e, how, why = resolve(SAVE, els)
    assert e is None and "not guessing" in why[0]


def test_relabeled_field_found_by_name():
    t = {"tag": "textarea", "label": "Notes", "name": "notes", "dom_id": "notes"}
    els = [el(1, "textarea", "Internal notes", extra={"name": "notes", "dom_id": "notes"}),
           el(2, "input", "Search", type="text", extra={"name": "q"})]
    e, how, _ = resolve(t, els)
    assert (e["id"], how) == (1, "tolerant")


def test_select_must_offer_the_option():
    t = {"tag": "select", "label": "Stage", "name": "stage", "options": ["New", "Qualified", "Lost"]}
    owner = el(1, "select", "Owner", options=["Ana", "Marco", "Riley"], extra={"name": "owner"})
    e, how, _ = resolve(t, [owner], option="Qualified")
    assert e is None


def test_link_found_by_label_when_link_moved():
    t = {"tag": "a", "label": "Priya Raman", "href": "/leads/7"}
    els = [el(1, "a", "Priya Raman", href="/leads/7")]
    assert resolve(t, els)[1] == "exact"
    els = [el(1, "a", "Priya Raman", href="/people/7")]
    e, how, _ = resolve(t, els)
    assert (e["id"], how) == (1, "tolerant")


def test_same_label_different_links_picks_by_link():
    t = {"tag": "a", "label": "Edit", "href": "/calendar/2"}
    els = [el(i, "a", "Edit", href=f"/calendar/{i}") for i in (1, 2, 3)]
    e, how, _ = resolve(t, els)
    assert e["id"] == 2


def test_renamed_next_link():
    t = {"tag": "a", "label": "Next", "href": "/leads?page=2&stage="}
    els = [el(1, "a", "Previous page", href="/leads?page=1&stage="), el(2, "a", "1", href="/leads?page=1&stage="),
           el(3, "a", "3", href="/leads?page=3&stage="), el(4, "a", "Next page", href="/leads?page=3&stage=")]
    e, how, _ = resolve(t, els)
    assert e["id"] == 4
    assert next_link(els)["id"] == 4


def test_href_pattern():
    assert href_pattern("/leads/12") == "/leads/N"
    assert href_pattern("/leads?page=3&stage=") == "/leads?page=N&stage="
