"""Divergence point and cause labels on hand-built traces."""
from analysis.diverge import classify, divergence_index


def step(i, action, label=None, url="/leads", error=None, **kw):
    s = {"i": i, "action": action, "target": {"tag": "a", "label": label} if label else None, "url": url,
         "error": error, "modal_at_act": False, "new_tab": False, "active_tab": 0, "tabs_after": 1,
         "loading": False, "t_act": 1.0 + i}
    s.update(kw)
    return s


def trace(steps, chaos=(), passed=False):
    return {"steps": steps, "chaos": list(chaos), "passed": passed, "hit_step_limit": False}


PASS = trace([step(0, {"action": "goto", "path": "/leads"}),
              step(1, {"action": "click", "id": 3}, "Next"),
              step(2, {"action": "click", "id": 9}, "Priya Raman", url="/leads?page=2"),
              step(3, {"action": "done"}, url="/leads/7")], passed=True)


def test_split_at_modal_error():
    f = trace([step(0, {"action": "goto", "path": "/leads"}),
               step(1, {"action": "click", "id": 3}, "Next", error="timeout: <div> intercepts pointer events", modal_at_act=True),
               step(2, {"action": "done"})],
              chaos=[{"kind": "modal", "t": 1.5, "page": "leads"}])
    k, ref = divergence_index(f, [PASS])
    assert (k, ref) == (1, True)
    cause, confirmed, _ = classify(f, k)
    assert cause == "modal" and confirmed


def test_different_valid_route_is_not_a_split():
    # goes straight to page 2 by URL instead of clicking Next, then errors later
    f = trace([step(0, {"action": "goto", "path": "/leads"}),
               step(1, {"action": "click", "id": 3}, "Next"),
               step(2, {"action": "click", "id": 9}, "Priya Raman", url="/leads?page=2",
                    error="stale element: [9] is no longer on the page")],
              chaos=[{"kind": "reorder", "t": 2.0, "page": "leads"}])
    k, _ = divergence_index(f, [PASS])
    assert k == 2
    assert classify(f, k)[0] == "stale_element"


def test_unconfirmed_label_is_flagged():
    f = trace([step(0, {"action": "click", "id": 1}, "Save", error="timeout: x intercepts pointer events")])
    cause, confirmed, _ = classify(f, 0)
    assert cause == "modal" and confirmed is False


def test_wrong_tab():
    f = trace([step(0, {"action": "click", "id": 4}, "Priya Raman", new_tab=True, tabs_after=2),
               step(1, {"action": "select", "id": 6, "option": "Qualified"}, "Stage", tabs_after=2)],
              chaos=[{"kind": "newtab", "t": 0.1, "page": "leads"}])
    assert classify(f, 1)[0] == "wrong_tab"
