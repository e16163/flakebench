"""Find a recorded element again on today's page, without a model.

A recorded target is a fingerprint: tag, visible label, link, input type, form
field name, DOM id, role, the group it sits in, and a select's options. Matching
runs in two passes:

  exact     same tag, label, link and field name, and exactly one such element
  tolerant  score every element of a compatible kind on how much of the
            fingerprint still agrees, and accept the best one only if it clears a
            floor AND clearly beats the runner-up

The margin rule is the safety rule. When two elements look equally right (two
"Edit" links, two "Save" buttons) we refuse to guess and hand the step to the
healer instead, because clicking the wrong thing is worse than asking.
"""
import re

ACCEPT = 3.0     # minimum score for a tolerant match
MARGIN = 1.0     # how far ahead of the runner-up the winner must be

NEXT_RE = re.compile(r"^(next|next page|more|older|›|»|>|→)\b", re.I)
SUBMIT_TAGS = {("button", "submit"), ("input", "submit")}


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _tokens(s):
    return set(re.findall(r"[a-z0-9]+", _norm(s)))


def _href(h):
    return (h or "").replace(" (opens new tab)", "")


def href_pattern(h):
    """/leads/7 -> /leads/N, /leads?page=3&stage= -> /leads?page=N&stage="""
    return re.sub(r"\d+", "N", _href(h))


def _extra(e):
    return e.get("extra") or {}


def fp_of(e):
    """Flatten an observed element into the same shape as a recorded target."""
    t = {k: e[k] for k in ("tag", "label", "href", "type", "options") if e.get(k) is not None}
    t.update(_extra(e))
    return t


def _kind(t):
    tag, typ = t.get("tag"), t.get("type")
    if (tag, typ) in SUBMIT_TAGS:
        return "submit"
    if tag == "input" and typ in ("checkbox", "radio"):
        return typ
    if tag in ("input", "textarea"):
        return "text"
    return tag


def compatible(target, e):
    a, b = _kind(target), _kind(fp_of(e))
    if a == b:
        return True
    # a button that became a link styled as a button, and the reverse
    return {a, b} <= {"a", "button", "submit"}


def is_exact(target, e):
    f = fp_of(e)
    if f.get("tag") != target.get("tag") or _norm(f.get("label")) != _norm(target.get("label")):
        return False
    if target.get("href") is not None and _href(f.get("href")) != _href(target.get("href")):
        return False
    if target.get("name") and f.get("name") != target.get("name"):
        return False
    return True


def score(target, e, page_elements):
    """How well element e matches the recorded target, with the reasons."""
    f = fp_of(e)
    s, why = 0.0, []
    tl, fl = _norm(target.get("label")), _norm(f.get("label"))
    if tl and tl == fl:
        s += 4; why.append("same label")
    elif tl and fl and (tl in fl or fl in tl):
        s += 2.5; why.append(f"label now '{f.get('label')}'")
    else:
        common = _tokens(tl) & _tokens(fl)
        if common:
            s += 1.0 + (1.0 if len(common) / max(1, len(_tokens(tl) | _tokens(fl))) >= 0.5 else 0)
            why.append(f"label shares '{' '.join(sorted(common))}'")
    for k, w in (("name", 3), ("dom_id", 3)):
        if target.get(k) and target.get(k) == f.get(k):
            s += w; why.append(f"same field {k.replace('dom_', '')}")
    if target.get("href"):
        if _href(target["href"]) == _href(f.get("href")):
            s += 3; why.append("same link")
        elif f.get("href") and href_pattern(target["href"]) == href_pattern(f["href"]):
            s += 1; why.append("same kind of link")
    if target.get("type") and target.get("type") == f.get("type"):
        s += 0.5
    if target.get("options") and f.get("options"):
        a, b = set(target["options"]), set(f["options"])
        if len(a & b) / max(1, len(a | b)) >= 0.6:
            s += 2; why.append("same choices")
    if _kind(target) == "submit" and _kind(f) == "submit":
        if sum(1 for x in page_elements if _kind(fp_of(x)) == "submit") == 1:
            s += 2.5; why.append("the page's only submit button")
    if target.get("group") and target.get("group") == f.get("group"):
        s += 0.5
    return s, why


def resolve(target, elements, option=None):
    """Return (element, how, reasons). how is 'exact', 'tolerant' or None.

    option: for a select step, the choice we are about to make. A candidate that
    doesn't offer it can't be the right element.
    """
    if not target:
        return None, None, ["no recorded target"]
    pool = [e for e in elements if compatible(target, e)]
    if option is not None:
        pool = [e for e in pool if not e.get("options") or option in e["options"]]
    exact = [e for e in pool if is_exact(target, e)]
    if len(exact) == 1:
        return exact[0], "exact", []
    if len(exact) > 1:
        # same label and link twice (e.g. a lead shown in two places): take the first
        # only if they are truly interchangeable, i.e. point to the same place
        if target.get("href") and len({_href(e.get("href")) for e in exact}) == 1:
            return exact[0], "exact", []
        pool = exact
    scored = sorted(((score(target, e, elements), e) for e in pool), key=lambda x: -x[0][0])
    if not scored:
        return None, None, ["nothing of that kind on the page"]
    (best, why), el = scored[0]
    runner = scored[1][0][0] if len(scored) > 1 else 0.0
    if best >= ACCEPT and best - runner >= MARGIN:
        return el, "tolerant", why
    if best >= ACCEPT:
        return None, None, [f"two elements look equally right (score {best:.1f} vs {runner:.1f}); not guessing"]
    return None, None, [f"no element close enough (best score {best:.1f})"]


def next_link(elements):
    """A pagination 'next' control, if the page has exactly one."""
    c = [e for e in elements if e.get("tag") in ("a", "button") and NEXT_RE.match(_norm(e.get("label")))]
    return c[0] if len(c) == 1 else None


def describe(step):
    t = step.get("target") or {}
    label = t.get("label") or t.get("name") or t.get("tag") or ""
    a = step["action"]
    if a == "goto":
        return f"Open {step['path']}"
    if a == "click":
        return f"Click '{label}'"
    if a == "type":
        return f"Type into '{label}'"
    if a == "select":
        return f"Set '{label}' to {step.get('option')}"
    if a == "check":
        return f"{'Tick' if step.get('checked', True) else 'Untick'} '{label}'"
    return a
