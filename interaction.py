"""
Interaction primitives for Playwright browser automation.

Provides core physical action dispatchers:
- fill_and_dispatch: fills inputs and dispatches bubbling synthetic events (input, change, blur)
- resolve_ant_dropdown: handles Ant Design rc-select elements via selector mousedown and detached portal matching
- commit_draft_cards: pre-navigation sweep for uncommitted resume experience/education draft cards
- click_resiliently: resilient multi-strategy click handler
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, Optional

from playwright.sync_api import Page

import safety
from perception import is_ant_single_select

logger = logging.getLogger(__name__)

# Patterns for internal card save buttons (case-insensitive)
CARD_COMMIT_TEXT_PATTERN = re.compile(
    r"^\s*(save entry|update|save|done|apply changes)\s*$",
    re.IGNORECASE,
)


def fill_and_dispatch(locator: Any, value: str, timeout: Optional[float] = None, **kwargs: Any) -> None:
    """Fills an input using Playwright's native .fill() and immediately dispatches
    three bubbling synthetic events: 'input', 'change', and 'blur'.

    This ensures reactive/virtual DOM frameworks (React, Vue, Angular, Ant Design)
    register controlled component state and do not silently drop field values on form submission.
    """
    fill_kwargs = dict(kwargs)
    if timeout is not None:
        fill_kwargs["timeout"] = timeout

    locator.fill(value, **fill_kwargs)
    try:
        locator.evaluate("""el => {
            el.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
            el.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true, cancelable: true }));
        }""")
    except Exception as exc:
        logger.debug("Synthetic event dispatch on locator failed: %s", exc)


def click_resiliently(locator: Any, timeout_ms: int = 4_000) -> bool:
    """Clicks an element resiliently, trying normal click, forced click, and JS dispatch."""
    try:
        locator.click(timeout=timeout_ms)
        return True
    except Exception:
        try:
            locator.click(force=True, timeout=min(timeout_ms, 2_000))
            return True
        except Exception:
            try:
                locator.evaluate("el => el.click()")
                return True
            except Exception as exc:
                logger.debug("click_resiliently failed: %s", exc)
                return False


# Ant Design's select (rc-select) draws its options in a list mounted at the
# end of document.body. A long list is *virtual*: only the rows in view exist,
# so a country list holds Afghanistan to Argentina until it is scrolled. Each
# row is `.ant-select-item-option` (its label in `title`, or in the row's
# `-content` part); the row also holds an empty `-state` marker, and the list
# keeps a hidden accessibility copy of a few rows whose text is the option's
# *value* ("1000"). Only the rows themselves are options.
_ANT_LIST_JS = r"""(el, token) => {
    // The list this select opened: the input names it (aria-controls /
    // aria-owns); failing that, the one list that is open.
    const input = el.matches('input') ? el : (el.querySelector('input') || el);
    const id = input.getAttribute('aria-controls') || input.getAttribute('aria-owns');
    let dd = id && document.getElementById(id) ? document.getElementById(id).closest('.ant-select-dropdown') : null;
    if (!dd) {
        const open = Array.from(document.querySelectorAll('.ant-select-dropdown'))
            .filter((d) => !d.classList.contains('ant-select-dropdown-hidden') && d.offsetParent !== null);
        dd = open.length ? open[open.length - 1] : null;
    }
    if (!dd || dd.classList.contains('ant-select-dropdown-hidden') || dd.offsetParent === null) return false;
    dd.setAttribute('data-jaa-list', token);
    return true;
}"""

_ANT_READ_JS = r"""async (dd) => {
    // Every option of the list, in order, as [position, label]: a virtual list
    // is scrolled through from the top, since only the rows in view exist.
    const label = (row) => {
        const part = row.querySelector('.ant-select-item-option-content');
        return ((row.getAttribute('title') || (part ? part.innerText : row.innerText)) || '').replace(/\s+/g, ' ').trim();
    };
    const holder = dd.querySelector('.rc-virtual-list-holder');
    const inner = dd.querySelector('.rc-virtual-list-holder-inner');
    if (!holder || !inner) {
        return Array.from(dd.querySelectorAll('.ant-select-item'))
            .map((row, i) => [i, row.classList.contains('ant-select-item-option') ? label(row) : null])
            .filter(([, text]) => text !== null);
    }
    const pause = () => new Promise((done) => requestAnimationFrame(() => setTimeout(done, 40)));
    const shift = () => { const m = /translateY\((-?[\d.]+)px\)/.exec(inner.style.transform || ''); return m ? parseFloat(m[1]) : 0; };
    const found = new Map();
    holder.scrollTop = 0;
    await pause();
    for (let step = 0; step < 500; step++) {
        const rows = Array.from(inner.children);
        const height = rows.length ? rows[0].offsetHeight || 1 : 1;
        const first = Math.round(shift() / height);
        rows.forEach((row, i) => {
            if (row.classList.contains('ant-select-item-option')) found.set(first + i, label(row));
        });
        if (holder.scrollTop + holder.clientHeight >= holder.scrollHeight - 1) break;
        const before = holder.scrollTop;
        holder.scrollTop = before + Math.max(height, holder.clientHeight - height);
        await pause();
        if (holder.scrollTop === before) break;
    }
    return Array.from(found.entries()).sort((a, b) => a[0] - b[0]);
}"""

_ANT_SHOW_JS = r"""async (dd, [position, token]) => {
    // Bring the row at this position into view and mark it for the click.
    const holder = dd.querySelector('.rc-virtual-list-holder');
    const inner = dd.querySelector('.rc-virtual-list-holder-inner');
    let row = null;
    if (!holder || !inner) {
        row = dd.querySelectorAll('.ant-select-item')[position] || null;
        if (row) row.scrollIntoView({block: 'nearest'});
    } else {
        const pause = () => new Promise((done) => requestAnimationFrame(() => setTimeout(done, 40)));
        const shift = () => { const m = /translateY\((-?[\d.]+)px\)/.exec(inner.style.transform || ''); return m ? parseFloat(m[1]) : 0; };
        const height = inner.children.length ? inner.children[0].offsetHeight || 1 : 1;
        holder.scrollTop = Math.max(0, position * height - height);
        await pause();
        const first = Math.round(shift() / height);
        row = inner.children[position - first] || null;
    }
    if (!row || !row.classList.contains('ant-select-item-option')) return false;
    row.setAttribute('data-jaa-row', token);
    return true;
}"""

_ANT_SHOWN_JS = r"""(el) => {
    // What the select now shows as chosen.
    const root = el.closest('.ant-select');
    const item = root && root.querySelector('.ant-select-selection-item');
    return item ? ((item.getAttribute('title') || item.innerText || '').replace(/\s+/g, ' ').trim()) : '';
}"""


def _strict_pick(labels: list[str], wanted: list[str]) -> Optional[int]:
    """The option that says what is wanted: the same words, or the same
    country or US state in another spelling ("United States of America",
    "VA"). A place is that place or nothing -- "United States" is never
    "United States Minor Outlying Islands". Anything else may also be the
    one option whose words contain it ("LinkedIn" -> "LinkedIn Job Board").
    An empty label is never a match."""
    import geo_reference

    norm = [geo_reference.normalize(label) for label in labels]
    for want in wanted:
        w = geo_reference.normalize(want)
        if not w:
            continue
        for i, label in enumerate(norm):
            if label and label == w:
                return i
        if geo_reference.country_code(want) or geo_reference.us_state_code(want):
            for i, label in enumerate(labels):
                if norm[i] and geo_reference.same_place(label, want):
                    return i
            continue
        pattern = re.compile(rf"(^|\s){re.escape(w)}(\s|$)")
        hits = [i for i, label in enumerate(norm) if label and pattern.search(label)]
        if len(hits) == 1:
            return hits[0]
    return None


def resolve_ant_dropdown(
    page_or_tab: Any,
    locator: Any,
    target_value: str,
    candidates: Optional[list[str]] = None,
    timeout_ms: int = 5_000,
    pick: Optional[Callable[[list[str]], Optional[int]]] = None,
) -> bool:
    """Chooses an option in an Ant Design (rc-select) list and confirms the
    select kept it. True only when the select then shows that option.

    1. Opens the list the way a person does: mousedown on the selector (the
       input itself is covered and ignores clicks).
    2. Reads the WHOLE list -- scrolling a virtual list from top to bottom --
       rather than only the rows in view.
    3. Chooses by label: `pick(labels)` when the caller has its own rules
       (the page agent's dial-code and profile rules), otherwise exactly, or
       as the same place in another spelling. Never an empty label: matching
       "" was how "United States" became "Afghanistan" and "Virginia" became
       "Badakhshan", the first rows of those lists, on 23 September.
    4. Scrolls that row into view and clicks it.
    5. Reads back what the select shows. If it is not the option chosen, the
       choice did not take, and this says so instead of reporting success.
    """
    page = getattr(page_or_tab, "page", page_or_tab)

    # 1. Dispatch mousedown event on parent .ant-select-selector wrapper
    opened = False
    try:
        opened = bool(locator.evaluate("""el => {
            const wrapper = el.closest('.ant-select-selector, [class*=select-selector], .ant-select, [role=combobox]') || el.parentElement || el;
            wrapper.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
            wrapper.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
            wrapper.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
            const inp = wrapper.querySelector('input') || el;
            if (inp && inp.focus) inp.focus();
            return true;
        }"""))
    except Exception as exc:
        logger.debug("Could not dispatch mousedown on ant-select-selector wrapper: %s", exc)
        opened = False

    if not opened:
        try:
            wrapper = locator.locator(
                "xpath=ancestor-or-self::*[contains(@class, 'ant-select-selector') or contains(@class, 'ant-select')][1]"
            )
            if wrapper.count():
                wrapper.first.click(timeout=2_000)
            else:
                locator.click(timeout=2_000)
        except Exception:
            pass

    def close() -> None:
        try:
            locator.press("Escape", timeout=1_000)
        except Exception:
            pass

    # 2. The list this select opened, read whole.
    token = f"jaa{time.time_ns() % 10**12}"
    deadline = time.monotonic() + timeout_ms / 1000
    listed = False
    pressed_down = False
    while not listed:
        try:
            listed = bool(locator.evaluate(_ANT_LIST_JS, token))
        except Exception:
            listed = False
        if listed:
            break
        if time.monotonic() > deadline:
            if pressed_down:
                logger.debug("Ant dropdown: the list did not open")
                return False
            pressed_down = True              # some lists open from the keyboard only
            deadline = time.monotonic() + 2.0
            try:
                locator.press("ArrowDown", timeout=1_000)
            except Exception:
                pass
        page.wait_for_timeout(100)
    dropdown = page.locator(f'[data-jaa-list="{token}"]')
    try:
        found = dropdown.evaluate(_ANT_READ_JS)
    except Exception as exc:
        logger.debug("Ant dropdown: could not read the list: %s", exc)
        close()
        return False
    positions = [int(position) for position, _label in found]
    labels = [str(label or "") for _position, label in found]

    # 3. Choose by label, never an empty one.
    wanted = [str(v).strip() for v in [target_value, *(candidates or [])] if str(v or "").strip()]
    index = pick(labels) if pick is not None else _strict_pick(labels, wanted)
    if index is None or not (0 <= index < len(labels)) or not labels[index]:
        logger.info("Ant dropdown: %r is not among the %d options of this list -- left as it is",
                    target_value, len(labels))
        close()
        return False
    chosen = labels[index]

    # 4. Into view, and click it as a person would.
    row_token = f"{token}r"
    try:
        shown_row = bool(dropdown.evaluate(_ANT_SHOW_JS, [positions[index], row_token]))
    except Exception:
        shown_row = False
    if not shown_row or not click_resiliently(page.locator(f'[data-jaa-row="{row_token}"]').first, timeout_ms=3_000):
        logger.info("Ant dropdown: could not click %r in its list", chosen)
        close()
        return False

    # 5. The select must now show that option.
    import geo_reference

    shown = ""
    for _ in range(20):
        try:
            shown = str(locator.evaluate(_ANT_SHOWN_JS) or "")
        except Exception:
            shown = ""
        if shown and (geo_reference.normalize(shown) == geo_reference.normalize(chosen)
                      or geo_reference.same_place(shown, chosen)):
            return True
        page.wait_for_timeout(100)
    logger.info("Ant dropdown: chose %r but the select shows %r -- the choice did not take", chosen, shown)
    return False


def wipe_and_enforce_location_sweep(
    page: Page,
    scope: Optional[Any] = None,
    profile: Optional[Any] = None,
    country: Optional[str] = None,
    state: Optional[str] = None,
    city: Optional[str] = None,
    record_callback: Optional[Callable[[str, str], None]] = None,
) -> bool:
    """Puts the owner's own country, state and city right, in that order.

    A form's country list decides which states its state list offers, so the
    country goes first, the page is allowed to settle, and only then the
    state and the city. Three rules keep it honest:

    * The places come from the profile (or the arguments). A level the
      profile leaves empty is left alone -- nothing is assumed. (It used to
      fall back to one owner's country, state and city for everyone.)
    * Only the owner's own address: never a field inside a work or
      education entry, whose place belongs to that entry. (It used to be run
      on every such entry and wrote the owner's home into each of them.)
    * Only a value nobody chose: an empty field, or one the site put there
      that contradicts the profile -- provenance.py must have seen that no
      person touched it, and SITE_PREFILL_POLICY must be "correct". A value
      the owner entered is never changed, and on a page without the
      observer a present value is left alone. (It used to find the fields
      by two place names hard-coded as "wrong" and overwrite whatever it
      found.)

    Returns True when it found a location field to look at.
    """
    import geo_reference
    import provenance
    from config import site_prefill_policy

    if profile is None and not (country or state or city):
        try:
            import config
            profile = config.get_user_profile()
        except Exception:
            profile = None
    want_country = (country or str(getattr(profile, "country", "") or "")).strip()
    want_state = (state or str(getattr(profile, "state", "") or "")).strip()
    want_city = (city or str(getattr(profile, "city", "") or "")).strip()
    if not (want_country or want_state or want_city):
        return False

    finder_script = r"""(scopeEl) => {
        let root = document;
        if (scopeEl) {
            if (scopeEl.nodeType === Node.ELEMENT_NODE) root = scopeEl;
            else if (scopeEl.element && scopeEl.element.nodeType === Node.ELEMENT_NODE) root = scopeEl.element;
            else if (scopeEl.parentElement) root = scopeEl.parentElement;
        }
        const candidates = Array.from(root.querySelectorAll(
            'select, input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"]):not([type="file"]):not([type="submit"]):not([type="button"])'
        ));
        const getMeta = (el) => {
            let meta = (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('placeholder') || '') + ' ' +
                       (el.getAttribute('name') || '') + ' ' + (el.getAttribute('id') || '') + ' ' +
                       (el.getAttribute('data-automation-id') || '');
            if (el.id) {
                const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                if (lbl) meta += ' ' + (lbl.innerText || lbl.textContent || '');
            }
            const parentLabel = el.closest('label');
            if (parentLabel) meta += ' ' + (parentLabel.innerText || parentLabel.textContent || '');
            const container = el.closest(
                '.form-group, .form-item, tr, fieldset, div[class*="field"], div[class*="input"], ' +
                'div[class*="form"], div[class*="entry"], [data-automation-id*="form"], [data-automation-id*="country"], [data-automation-id*="state"]'
            );
            // A container's heading labels this control only when the
            // container holds nothing else: in a section with several fields
            // the first label belongs to the first field.
            if (container && container.querySelectorAll('input:not([type="hidden"]), select, textarea').length === 1) {
                const heading = container.querySelector('label, [class*="label"], legend, [class*="title"], [class*="prompt"]');
                if (heading) meta += ' ' + (heading.innerText || heading.textContent || '');
            }
            return meta.toLowerCase();
        };
        // A work or education entry: its places belong to the entry.
        const entryWords = /experience|employment|employer|work.?history|education|school|university|college|degree|reference/i;
        const inEntry = (el) => {
            for (let n = el, i = 0; n && n.getAttribute && i < 8; n = n.parentElement, i++) {
                const attrs = [n.getAttribute('data-automation-id'), n.id, n.getAttribute('aria-label')].join(' ');
                if (entryWords.test(attrs)) return true;
                if (n !== el && /^(FIELDSET|SECTION)$/.test(n.tagName)) {
                    const heading = n.querySelector(':scope > legend, :scope > h2, :scope > h3, :scope > h4');
                    if (heading && entryWords.test(heading.innerText || '')) return true;
                }
            }
            return false;
        };
        const getVal = (el) => {
            if (el.tagName.toLowerCase() === 'select') return (el.options[el.selectedIndex]?.text || el.value || '').trim();
            // An Ant Design select keeps its choice beside the input, which
            // only ever holds search text.
            const ant = el.closest('.ant-select');
            const item = ant && ant.querySelector('.ant-select-selection-item');
            if (ant) return item ? (item.getAttribute('title') || item.innerText || '').trim() : '';
            return (el.value || el.getAttribute('value') || '').trim();
        };

        const countryNegative = /citizenship|nationality|phone|dial|country\s*code/;
        const stateNegative = /statement|united states|country/;
        const cityNegative = /employer|company|school/;
        let cEl = null, sEl = null, cityEl = null;
        for (const el of candidates) {
            if (inEntry(el)) continue;
            const meta = getMeta(el);
            if (!cEl && /\b(country|country\s*\/\s*region|country\s+or\s+region|residence\s*country|domicile|nation)\b/.test(meta) && !countryNegative.test(meta)) {
                cEl = el;
            } else if (!sEl && /\b(state|province|state\s*\/\s*province|state\s+or\s+province|province\s*\/\s*territory|region|state\s*\/\s*region)\b/.test(meta) && !stateNegative.test(meta)) {
                sEl = el;
            } else if (!cityEl && /\b(city|town|city\s*\/\s*town|city\s+of\s+residence)\b/.test(meta) && !cityNegative.test(meta)) {
                cityEl = el;
            }
        }
        const mark = (el, prefix) => {
            if (!el) return null;
            if (!el.id) el.id = prefix + '_' + Math.random().toString(36).substr(2, 9);
            return { id: el.id, tagName: el.tagName.toLowerCase(), value: getVal(el) };
        };
        return { country: mark(cEl, '__sweep_country'), state: mark(sEl, '__sweep_state'), city: mark(cityEl, '__sweep_city') };
    }"""

    try:
        loc_info = page.evaluate(finder_script, scope)
    except Exception as exc:
        logger.debug("Location sweep element inspection failed: %s", exc)
        return False
    if not loc_info or not any(loc_info.values()):
        return False

    policy = site_prefill_policy()

    def locate(field: dict):
        return page.locator(f"[id={json.dumps(field['id'])}]").first

    def needs_changing(field: dict, wanted: str, same) -> bool:
        current = (field.get("value") or "").strip()
        if not current or geo_reference.is_placeholder(current):
            return True                      # empty, or a "- Select -" prompt: fill it
        if same(current, wanted):
            return False                     # already right
        # Who put it there decides (safety.may_overrule): never the owner's,
        # the site's only when the owner's policy says "correct".
        return safety.may_overrule(provenance.origin(locate(field), value=current), policy)

    def same_country(a: str, b: str) -> bool:
        return geo_reference.same_country(a, b) or geo_reference.normalize(a) == geo_reference.normalize(b)

    def same_state(a: str, b: str) -> bool:
        return geo_reference.same_us_state(a, b) or geo_reference.normalize(a) == geo_reference.normalize(b)

    def same_city(a: str, b: str) -> bool:
        a, b = geo_reference.normalize(a), geo_reference.normalize(b)
        return a == b or a.startswith(b + " ") or b.startswith(a + " ")

    def choose(field: dict, wanted: str, same) -> None:
        loc = locate(field)
        # Dependent controls may remain disabled until a country is committed.
        # Never attempt to clear or force-enable them.
        from playwright.sync_api import expect
        try:
            expect(loc).to_be_enabled(timeout=3_000)
        except Exception:
            logger.info("LOCATION_SWEEP: %s is disabled -- deferred", field['id'])
            return
        if field["tagName"] == "select":
            texts = []
            try:
                texts = [x.strip() for x in loc.locator("option").all_inner_texts()]
            except Exception:
                pass
            label = next((x for x in texts if x and same(x, wanted)), None)
            if label is None:
                logger.info("LOCATION_SWEEP: %r is not among this list's options -- left for you", wanted)
                return
            loc.select_option(label=label)
            loc.evaluate("""el => {
                el.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
                el.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true, cancelable: true }));
            }""")
        elif 'rcmpaginatedselectinput' in (loc.get_attribute('class') or ''):
            from sites.successfactors import SuccessFactorsAdapter
            if not SuccessFactorsAdapter().choose_location(page, loc, wanted, same):
                return
        elif is_ant_single_select(loc):
            # Read whole, chosen by label, confirmed -- never typed and
            # Entered, which takes the list's first row.
            if not resolve_ant_dropdown(page, loc, wanted, pick=lambda labels: next(
                    (i for i, text in enumerate(labels) if text.strip() and same(text, wanted)), None)):
                logger.info("LOCATION_SWEEP: %r is not among this list's options -- left for you", wanted)
                return
        else:
            fill_and_dispatch(loc, "")
            fill_and_dispatch(loc, wanted)
            try:
                offered = page.locator("[role='option']:visible, [class*='option']:visible, li:visible")
                for i in range(min(offered.count(), 40)):
                    text = (offered.nth(i).inner_text(timeout=500) or "").strip()
                    if text and same(text, wanted):
                        click_resiliently(offered.nth(i))
                        break
            except Exception:
                pass
        if record_callback:
            record_callback(f"[id={json.dumps(field['id'])}]", wanted)

    # 1. Country first: the state list depends on it.
    country_field = loc_info.get("country")
    if country_field and want_country and needs_changing(country_field, want_country, same_country):
        logger.info("LOCATION_SWEEP: Country %r -> %r (from your profile)", country_field.get("value", ""), want_country)
        choose(country_field, want_country, same_country)
        # 2. Let the page load the country's states before touching them.
        try:
            page.wait_for_load_state("networkidle", timeout=7_000)
        except Exception:
            pass
        page.wait_for_timeout(1_000)
        try:
            loc_info = page.evaluate(finder_script, scope) or loc_info
        except Exception:
            pass

    # 3. Then the state, then the city.
    state_field = loc_info.get("state")
    if state_field and want_state and needs_changing(state_field, want_state, same_state):
        logger.info("LOCATION_SWEEP: State %r -> %r (from your profile)", state_field.get("value", ""), want_state)
        choose(state_field, want_state, same_state)
    city_field = loc_info.get("city")
    if city_field and want_city and needs_changing(city_field, want_city, same_city):
        logger.info("LOCATION_SWEEP: City %r -> %r (from your profile)", city_field.get("value", ""), want_city)
        choose(city_field, want_city, same_city)
    return True


def commit_draft_cards(page: Page, timeout_ms: int = 4_000) -> int:
    """Pre-navigation sweep: scans DOM for inline resume-parsed experience or education
    cards stuck in active 'Edit' or 'Draft' modes, clicks their own 'Update',
    'Save Entry', 'Done' etc. buttons, and waits for network and DOM settlement
    before evaluating page progression.

    A card's location is left exactly as it is: it belongs to that job or that
    school, not to the owner, so the owner's address sweep never runs here.

    Returns the count of cards committed.
    """
    committed = 0

    target_selectors = [
        "button:has-text('Save Entry')",
        "button:has-text('Update')",
        "button:has-text('Done')",
        "button:has-text('Save')",
        "button:has-text('Apply Changes')",
    ]

    for selector in target_selectors:
        buttons = page.locator(selector)
        count = min(buttons.count(), 10)
        for i in range(count):
            btn = buttons.nth(i)
            try:
                if not btn.is_visible() or not btn.is_enabled():
                    continue
                label = (btn.inner_text(timeout=1_000) or "").strip()
                if not CARD_COMMIT_TEXT_PATTERN.match(label):
                    continue
                # CRITICAL safety check: never click anything that reads as a final submit
                if safety.is_submit_label(label):
                    continue

                # Deduplication: never click the exact same card commit button twice in one sweep
                is_already_clicked = btn.evaluate("""el => {
                    if (el.getAttribute('data-draft-committed') === 'true') return true;
                    el.setAttribute('data-draft-committed', 'true');
                    return false;
                }""")
                if is_already_clicked:
                    continue

                # Ensure button is internal to a card or form section with inputs
                scope_handle = btn.evaluate_handle("""el => {
                    const card = el.closest(
                        '[data-automation-id*="experience" i], [data-automation-id*="education" i], ' +
                        '[class*="card" i], [class*="entry" i], [class*="item" i], ' +
                        '[class*="form-section" i], fieldset, form, div[role="group"], ' +
                        '.ant-modal-content, [role="dialog"]'
                    );
                    const scope = card || el.parentElement;
                    const hasInputs = scope && scope.querySelectorAll('input, select, textarea').length > 0;
                    return hasInputs ? scope : null;
                }""")

                if not scope_handle or not scope_handle.as_element():
                    continue

                logger.info("Committing active draft card with button %r", label)
                click_resiliently(btn, timeout_ms=timeout_ms)
                committed += 1

                # Wait for network settlement
                try:
                    page.wait_for_load_state("networkidle", timeout=3_000)
                except Exception:
                    pass
                page.wait_for_timeout(800)
            except Exception as exc:
                logger.debug("Failed while inspecting/clicking card button: %s", exc)

    return committed


def sweep_modals_and_policies(page_or_tab: Any, profile: Any = None) -> bool:
    """Universal Modal & Policy Interceptor:
    Checks for active modal dialogs (role="dialog", .ant-modal, aria-modal="true", dialog[open])
    on initial page load and immediately following navigation clicks.

    When an active modal dialog (like Inframark 'Recruiting Communications' popup) appears:
    1. Programmatically scrolls internal text container to the bottom (scrollTop = scrollHeight).
    2. Asserts any mandatory compliance checkboxes inside the modal.
    3. Clicks the primary confirmation button ('Save', 'Agree', 'Accept', 'Confirm', 'Done', etc.).
    4. Verifies that both the modal card and background backdrop mask detach completely or hide.
    """
    page = getattr(page_or_tab, "page", page_or_tab)

    modal_selectors = (
        "[role='dialog']:visible",
        ".ant-modal:visible",
        "[aria-modal='true']:visible",
        "dialog[open]:visible",
        ".modal.show:visible",
        ".modal:visible",
    )

    dismissed_any = False

    for selector in modal_selectors:
        try:
            modals = page.locator(selector)
            count = modals.count()
            if count == 0:
                continue

            for idx in range(count):
                modal = modals.nth(idx)
                if not modal.is_visible():
                    continue

                # Safety: Skip authentication dialogs (password inputs present) or file inputs
                has_password = modal.evaluate("""el => {
                    return Boolean(el.querySelector('input[type="password"]'));
                }""")
                if has_password:
                    continue

                # Safety: Skip modals asking for attestations/certifications unless allowed
                try:
                    modal_text = modal.inner_text(timeout=1_000) or ""
                    if safety.is_attestation(modal_text) and not getattr(profile, "sign_attestations", False):
                        logger.info("Modal contains legal attestation/certification -- leaving for user")
                        continue
                except Exception:
                    pass

                # 1. Programmatically scroll internal text container to the bottom
                try:
                    modal.evaluate("""el => {
                        const candidates = [
                            el.querySelector('.ant-modal-body'),
                            el.querySelector('[class*="modal-body"]'),
                            el.querySelector('[class*="dialog-body"]'),
                            el.querySelector('[class*="scroll"]'),
                            el.querySelector('[class*="content"]'),
                            el.querySelector('article'),
                            el.querySelector('section'),
                            el
                        ];
                        for (const c of candidates) {
                            if (c && c.scrollHeight > c.clientHeight) {
                                c.scrollTop = c.scrollHeight;
                            }
                        }
                        const allChildren = el.querySelectorAll('*');
                        for (const child of allChildren) {
                            if (child.scrollHeight > child.clientHeight && child.clientHeight > 40) {
                                child.scrollTop = child.scrollHeight;
                            }
                        }
                    }""")
                    page.wait_for_timeout(300)
                except Exception as exc:
                    logger.debug("Failed scrolling modal content: %s", exc)

                # 2. Assert any mandatory compliance checkboxes
                try:
                    cbs = modal.locator("input[type='checkbox'], [role='checkbox']")
                    for cb_idx in range(cbs.count()):
                        cb = cbs.nth(cb_idx)
                        if cb.is_visible() and not cb.is_checked():
                            try:
                                cb.check(timeout=1_500)
                            except Exception:
                                cb.evaluate("""el => {
                                    if (!el.checked) {
                                        el.checked = true;
                                        el.dispatchEvent(new Event('input', {bubbles: true}));
                                        el.dispatchEvent(new Event('change', {bubbles: true}));
                                    }
                                }""")
                            page.wait_for_timeout(200)
                except Exception as exc:
                    logger.debug("Failed asserting compliance checkbox in modal: %s", exc)

                # 3. Locate and click primary confirmation button ('Save', 'Agree', 'Accept', etc.)
                confirm_clicked = False
                confirm_selectors = (
                    "button:has-text('I Accept')",
                    "button:has-text('Accept All')",
                    "button:has-text('Accept')",
                    "button:has-text('I Agree')",
                    "button:has-text('Agree')",
                    "button:has-text('Save')",
                    "button:has-text('Continue')",
                    "button:has-text('Confirm')",
                    "button:has-text('Done')",
                    "button:has-text('OK')",
                    "button:has-text('Close')",
                    "[role='button']:has-text('Agree')",
                    "[role='button']:has-text('Accept')",
                )

                for c_sel in confirm_selectors:
                    c_btn = modal.locator(c_sel).first
                    try:
                        if c_btn.count() and c_btn.is_visible():
                            label = (c_btn.inner_text(timeout=500) or "").strip()
                            if safety.is_submit_label(label) and "agree" not in label.lower() and "accept" not in label.lower():
                                continue
                            if click_resiliently(c_btn, timeout_ms=3_000):
                                confirm_clicked = True
                                logger.info("Modal & Policy Interceptor: confirmed modal with %r", label)
                                break
                    except Exception:
                        continue

                # 4. Verify that both the modal card and background backdrop mask detach completely from the DOM
                if confirm_clicked:
                    page.wait_for_timeout(500)
                    try:
                        modal.wait_for(state="hidden", timeout=4_000)
                    except Exception:
                        pass

                    backdrop_selectors = (
                        ".ant-modal-mask",
                        ".ant-modal-wrap",
                        ".modal-backdrop",
                        "[class*='backdrop']",
                        "[class*='mask']",
                    )
                    for b_sel in backdrop_selectors:
                        try:
                            mask = page.locator(b_sel)
                            if mask.count() and mask.first.is_visible():
                                mask.first.wait_for(state="hidden", timeout=2_000)
                        except Exception:
                            pass

                    dismissed_any = True
        except Exception as exc:
            logger.debug("sweep_modals_and_policies encountered: %s", exc)

    return dismissed_any
