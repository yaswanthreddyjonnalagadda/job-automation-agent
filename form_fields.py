"""Every field on an application page, found by what it is -- not by what it is called.

Built from the portal research in reference/ats_fields/ (29 September 2026). The agent's usual reading is the
page's accessibility snapshot, which does not show a hidden file input, a hidden <select> behind a styled button,
or which question an unlabelled box belongs to; a required field it did not see was silently skipped (Secunetics
on BambooHR: State and the resume, with the page reported complete).

inventory() runs one script in every frame of the page. The script walks the page and every open shadow root
(SmartRecruiters builds its whole form in Shadow DOM), and for each field records what kind it is, its question
(its label, else the nearest question text above it -- Rippling's custom questions are boxes named only
'Select'), whether it is required, whether a person can see it, what it holds now, and whether it is a trap (a
honeypot: hidden, or labelled 'leave this field blank' / 'honeypot'). Each field is stamped with
data-jaa-field so it can be found again.

The kinds (reference/ats_fields/README.md): text, textarea, select (native), combobox (react-select, Ant,
Workday search, div role=combobox), button_list (a button that opens a list: BambooHR, Workday 'Select One'),
checkbox, radio, yes_no (Ashby's two aria-pressed buttons), date, file.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import option_match

logger = logging.getLogger(__name__)

STAMP = "data-jaa-field"

TAGS_BESIDE_JS = r"""
(e, label) => {
  // A tag box: what it holds is drawn beside it as chips, each with its own "Remove <tag>" button, and the text
  // box stays empty. UKG's Skills held six tags and read as a required blank, so the run stopped and waited for
  // the owner (30 September). Looked for only as far out as this is the one box in the block.
  const text = n => (n ? (n.innerText || n.textContent || '').replace(/\s+/g, ' ').trim() : '');
  for (let n = e.parentElement, d = 0; n && d < 4; n = n.parentElement, d++) {
    if (n.querySelectorAll('input:not([type=hidden]), select, textarea').length > 1) break;
    const tags = [...n.querySelectorAll('button, [role=button]')]
        .map(b => (b.getAttribute('aria-label') || b.getAttribute('title') || text(b) || '')
            .match(/^\s*(?:remove|delete|clear)\s+(.+?)\s*$/i))
        .filter(Boolean).map(m => m[1])
        // "Remove Website" beside a single link box removes the box itself, not a tag in it
        .filter(t => !/^(this|item|entry|row|field|answer|file|attachment)$/i.test(t)
            && t.toLowerCase() !== String(label || '').replace(/[\s*✱:]+$/, '').toLowerCase());
    if (tags.length) return tags;
  }
  return [];
}
"""

INVENTORY_JS = r"""
(stampPrefix) => {
  const STAMP = 'data-jaa-field';
  const tagsBeside = __TAGS_BESIDE__;
  const text = e => (e ? (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim() : '');
  const visible = e => {
    if (!e || !e.getClientRects().length) return false;
    const s = getComputedStyle(e);
    if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0) return false;
    const r = e.getBoundingClientRect();
    return r.width > 1 && r.height > 1 && r.right > 0 && r.bottom > 0;
  };
  const byId = (root, id) => (root.getElementById ? root.getElementById(id) : null) || document.getElementById(id);
  const all = [];
  const walk = root => {
    root.querySelectorAll('input, select, textarea, button, [role=combobox], [role=radio], [role=checkbox], [role=switch]')
        .forEach(e => all.push(e));
    root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) walk(e.shadowRoot); });
  };
  walk(document);

  const GENERIC = /^(select|search|select\.\.\.|choose|choose file\*?|attach|upload|browse|drop or select.*|type here.*)$/i;
  const labelText = e => {
    const root = e.getRootNode();
    const ids = (e.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    if (ids.length) { const t = ids.map(i => text(byId(root, i))).join(' ').trim(); if (t) return t; }
    if (e.id && root.querySelector) {
      const l = root.querySelector(`label[for="${CSS.escape(e.id)}"]`);
      if (l && text(l)) return text(l);
    }
    const aria = e.getAttribute('aria-label');
    if (aria && !GENERIC.test(aria.trim())) return aria;
    const wrap = e.closest('label');
    if (wrap && text(wrap)) return text(wrap);
    return '';
  };
  // The question a box sits under when nothing links them: the nearest text above it, walking up the page.
  const QUESTIONY = 'label, legend, p, h1, h2, h3, h4, h5, span, div';
  const nearestText = e => {
    let node = e;
    for (let depth = 0; node && depth < 9; depth++) {
      let prev = node.previousElementSibling;
      while (prev) {
        if (!prev.querySelector('input, select, textarea, [role=combobox]') || prev.matches('label, legend, p, h1, h2, h3, h4, h5')) {
          const t = text(prev);
          if (t && t.length > 1 && t.length < 400 && !GENERIC.test(t)) return t;
        }
        prev = prev.previousElementSibling;
      }
      node = node.parentElement || (node.getRootNode && node.getRootNode().host);
    }
    return '';
  };
  const section = e => {
    let node = e;
    for (let depth = 0; node && depth < 12; depth++) {
      let prev = node.previousElementSibling;
      while (prev) {
        const h = prev.matches('h1,h2,h3,h4,legend') ? prev : prev.querySelector && prev.querySelector('h1,h2,h3,h4,legend');
        if (h && text(h)) return text(h).slice(0, 80);
        prev = prev.previousElementSibling;
      }
      node = node.parentElement || (node.getRootNode && node.getRootNode().host);
    }
    return '';
  };
  const around = e => {                         // the text of the field's own block, for files and traps
    let node = e;
    for (let depth = 0; node && depth < 4; depth++) node = node.parentElement || node;
    return text(node).slice(0, 300);
  };
  const shownValue = (e, label) => {
    // react-select, Ant, Workday: the picked value is drawn beside the input, not in it
    for (let n = e, d = 0; n && d < 6; n = n.parentElement, d++) {
      const v = n.querySelector && n.querySelector('.select__single-value, [class*="singleValue"], .ant-select-selection-item, [data-automation-id=selectedItem]');
      if (v && text(v)) return text(v);
      const chips = n.querySelector && n.querySelector('[role=listbox][aria-label="items selected"] [role=option], .select__multi-value__label');
      if (chips && text(chips)) return text(chips).replace(/, press delete to clear value\.?$/i, '');
    }
    if (e.value) return e.value;
    // A tag box: what it holds is drawn beside it as chips (TAGS_BESIDE_JS).
    const tags = tagsBeside(e, label);
    if (tags.length) return tags.join(', ');
    return '';
  };
  // Required: the box says so, its label ends in a star, or the label element carries a 'required' class
  // (Ashby marks the question's label, not the box).
  const labelEl = e => {
    const root = e.getRootNode();
    if (e.id && root.querySelector) { const l = root.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) return l; }
    const ids = (e.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    return ids.length ? byId(root, ids[0]) : null;
  };
  // "(required)" counts with or without a star (Paylocity writes "Do you currently reside ...? (required)").
  const required = (e, label) => e.required || e.getAttribute('aria-required') === 'true'
      || /[*✱]\s*$|\*\s*\(?required\)?|\(\s*required\s*\)/i.test(label)
      || /\brequired\b/i.test(e.getAttribute('aria-label') || '')
      || !!(e.closest && e.closest('[class*="_required"], .required, [data-required=true]'))
      || /(^|[\s_-])required/i.test((labelEl(e) || {}).className || '');
  const TRAP = /leave (this|it) (field )?blank|honeypot|for robots only|^hp[_-]|if you are (a )?human/i;

  const out = [];
  const seenGroups = new Set();
  let n = 0;
  for (const e of all) {
    const tag = e.tagName.toLowerCase();
    const type = (e.getAttribute('type') || '').toLowerCase();
    const role = e.getAttribute('role') || '';
    if (tag === 'input' && ['hidden', 'submit', 'image', 'reset', 'button'].includes(type)) continue;
    let kind = '';
    if (tag === 'input' && type === 'file') kind = 'file';
    else if (tag === 'select') kind = 'select';
    else if (tag === 'textarea') kind = 'textarea';
    else if (tag === 'input' && (type === 'checkbox' || role === 'checkbox' || role === 'switch')) kind = 'checkbox';
    else if (tag === 'input' && (type === 'radio' || role === 'radio')) kind = 'radio';
    else if (tag === 'input' && type === 'date') kind = 'date';
    else if (role === 'combobox' || (tag === 'input' && e.getAttribute('aria-autocomplete'))) kind = 'combobox';
    else if (tag === 'button' && /^(listbox|true|menu)$/.test(e.getAttribute('aria-haspopup') || '')) kind = 'button_list';
    else if (tag === 'button' && e.hasAttribute('aria-pressed') && /^(yes|no)$/i.test(text(e))) kind = 'yes_no';
    else if (tag === 'input') kind = /mm\s*\/\s*(dd|yyyy)/i.test(e.getAttribute('placeholder') || '') ? 'date' : 'text';
    else continue;

    // A hidden native select that only mirrors a styled button (BambooHR): one option, aria-hidden -- the button
    // is the field.
    if (kind === 'select' && e.options.length <= 1 && (e.getAttribute('aria-hidden') === 'true' || !visible(e))) continue;
    // A react-select's hidden value input, or a search box inside a list: not a field of its own.
    if (kind === 'text' && !visible(e) && e.closest('[class*=select__], .ant-select, [role=listbox], [role=menu]')) continue;

    let label = labelText(e);
    let question = label;
    if (kind === 'yes_no') {
      const box = e.parentElement;
      if (seenGroups.has(box)) continue;
      seenGroups.add(box);
      question = labelText(box) || nearestText(box);
    } else if (kind === 'radio' || kind === 'checkbox') {
      const fs = e.closest('fieldset, [role=radiogroup], [role=group]');
      const legend = fs ? (text(fs.querySelector('legend')) || fs.getAttribute('aria-label') || labelText(fs)) : '';
      question = legend || nearestText(fs || e.parentElement);
    } else if (kind === 'button_list') {
      // BambooHR names the button '<label> <value>' ('State –Select–'); Workday '<question> Select One Required'.
      question = label || nearestText(e);
    } else if (!question || GENERIC.test(question)) {
      question = nearestText(e) || e.getAttribute('placeholder') || e.getAttribute('name') || '';
    }

    const id = `${stampPrefix}${n++}`;
    e.setAttribute(STAMP, id);
    const isVisible = kind === 'file' ? true : visible(e);
    let value = '';
    if (kind === 'select') value = e.selectedIndex >= 0 ? text(e.options[e.selectedIndex]) : '';
    else if (kind === 'checkbox' || kind === 'radio') value = e.checked ? 'checked' : '';
    else if (kind === 'file') value = (e.files && e.files.length) ? [...e.files].map(f => f.name).join(', ') : '';
    else if (kind === 'button_list') value = text(e);
    else if (kind === 'yes_no') { const on = e.parentElement.querySelector('[aria-pressed=true]'); value = on ? text(on) : ''; }
    else if (kind === 'combobox' && tag !== 'input') value = text(e);
    else value = shownValue(e, label);

    out.push({
      id, kind, tag, type, role,
      label: (label || '').slice(0, 300),
      question: (question || '').slice(0, 300),
      // A choice's own label is "Yes" or "3": whether its question must be answered is said by the question.
      required: kind === 'yes_no' ? required(e.parentElement, question)
          : (kind === 'radio' || kind === 'checkbox') ? required(e, label) || required(e, question)
          : required(e, label || question),
      visible: isVisible,
      disabled: !!(e.disabled || e.getAttribute('aria-disabled') === 'true'),
      value: (value || '').slice(0, 200),
      options: kind === 'select' ? [...e.options].map(o => text(o)).slice(0, 400) : [],
      multiple: !!(e.multiple || e.getAttribute('aria-multiselectable') === 'true'),
      accept: e.getAttribute('accept') || '',
      name: (e.getAttribute('name') || e.getAttribute('data-testid') || e.getAttribute('data-automation-id')
             || e.getAttribute('data-qa') || e.id || '').slice(0, 120),
      section: section(e),
      around: kind === 'file' ? around(e) : '',
      trap: TRAP.test(label) || TRAP.test(e.getAttribute('name') || '') || TRAP.test(e.getAttribute('placeholder') || '')
            || (kind !== 'file' && !isVisible && (kind === 'textarea' || kind === 'text')),
      shadow: e.getRootNode() !== document,
    });
  }
  return out;
}
"""
INVENTORY_JS = INVENTORY_JS.replace("__TAGS_BESIDE__", TAGS_BESIDE_JS.strip())


@dataclass
class Field:
    id: str
    kind: str
    tag: str = ""
    type: str = ""
    role: str = ""
    label: str = ""
    question: str = ""
    required: bool = False
    visible: bool = True
    disabled: bool = False
    value: str = ""
    options: list = field(default_factory=list)
    multiple: bool = False
    accept: str = ""
    name: str = ""
    section: str = ""
    around: str = ""
    trap: bool = False
    shadow: bool = False
    frame: Any = None               # the Playwright frame the field is in

    @property
    def empty(self) -> bool:
        if self.kind == "button_list":
            return is_placeholder(value_of_button(self))
        if self.kind in ("select", "combobox"):
            return is_placeholder(self.value)
        return not (self.value or "").strip()

    def locator(self):
        return self.frame.locator(f"[{STAMP}='{self.id}']")


def is_placeholder(value: str) -> bool:
    return option_match.is_placeholder(value)


def value_of_button(f: Field) -> str:
    """What a list button shows, without its label: 'State –Select–' -> '–Select–'; 'Country United States' ->
    'United States'; Workday's '<question> Select One Required' -> 'Select One'."""
    shown = (f.value or "").strip()
    label = re.sub(r"[\s*✱:]+$", "", (f.label or "").strip())
    if label and shown.lower().startswith(label.lower()) and len(shown) > len(label):
        shown = shown[len(label):].strip()
    shown = re.sub(r"\s*\bRequired\s*$", "", shown, flags=re.IGNORECASE)
    if f.question and shown.lower().startswith(f.question.lower()):
        shown = shown[len(f.question):].strip()
    return shown


def inventory(page) -> list[Field]:
    """Every field in every frame of the page."""
    fields: list[Field] = []
    for number, frame in enumerate(getattr(page, "frames", [page])):
        rows = None
        for attempt in range(2):
            try:
                rows = frame.evaluate(INVENTORY_JS, f"f{number}-")
                break
            except Exception as exc:
                # A page that re-renders itself while being read ("Execution context was destroyed") is read again
                # once; a frame that cannot be read (another site's captcha) is skipped.
                logger.debug("No field inventory in frame %s (try %d): %s", number, attempt + 1,
                             str(exc).splitlines()[0][:100])
                try:
                    frame.wait_for_timeout(800)
                except Exception:
                    break
        if rows is None:
            continue
        for row in rows or []:
            fields.append(Field(frame=frame, **row))
    return fields


def blank_required(fields: list[Field]) -> list[Field]:
    """Fields a person can see, that the page marks required, that hold nothing yet -- never a trap. Radio and
    checkbox groups count once, as answered if any box in the group is ticked."""
    groups: dict[str, list[Field]] = {}
    out = []
    for f in fields:
        if f.trap or not f.visible or f.disabled or not f.required:
            continue
        if f.kind in ("radio", "checkbox"):
            groups.setdefault(f.question or f.name, []).append(f)
            continue
        if f.kind == "file":
            continue                        # the resume has one authority: the submit gate
        if f.empty:
            out.append(f)
    for question, members in groups.items():
        if not any(m.value for m in members):
            out.append(members[0])
    return out


# --- the resume -----------------------------------------------------------------------------------------------

_RESUME = re.compile(r"\b(resume|résumé|cv|curriculum vitae)\b", re.IGNORECASE)
_NOT_RESUME = re.compile(r"cover\s*letter|photo|picture|avatar|profile image|transcript|portfolio|additional|"
                         r"other documents?|writing sample|autofill|auto-fill|import|parse", re.IGNORECASE)


def resume_input(fields: list[Field]) -> Optional[Field]:
    """The file input the resume goes in, told apart by the words around it, never by its own name alone.

    Most portals name the upload 'Attach', 'Choose File*', 'Drop or select' or 'From Device' (in the agent's
    recordings only 4 of 30 upload buttons had 'resume' near them); the section or block around it says what it
    is for. A second upload that re-fills the form from the resume ('Autofill from resume', 'Import Resume') is not
    the resume field."""
    files = [f for f in fields if f.kind == "file" and not f.disabled]
    if not files:
        return None

    def words(f: Field) -> str:
        return " ".join((f.label, f.question, f.section, f.name, f.around))

    named = [f for f in files if _RESUME.search(words(f)) and not _NOT_RESUME.search(f.label + " " + f.question)]
    for f in named:
        if not _NOT_RESUME.search(words(f)):
            return f
    if named:
        # 'Resume' beside 'Autofill from resume': the one whose own label or name says resume wins.
        own = [f for f in named if _RESUME.search(f.label + " " + f.name + " " + f.question)]
        return (own or named)[0]
    required = [f for f in files if f.required and not _NOT_RESUME.search(words(f))]
    if len(required) == 1:
        return required[0]
    plain = [f for f in files if not _NOT_RESUME.search(words(f))]
    return plain[0] if len(plain) == 1 else None


def attach_resume(page, path: Path, fields: Optional[list[Field]] = None) -> Optional[Field]:
    """Puts the resume in its file input. Returns the field, or None when there is no such input or it would
    not take the file."""
    fields = fields if fields is not None else inventory(page)
    target = resume_input(fields)
    if target is None:
        return None
    try:
        target.locator().set_input_files(str(path), timeout=10_000)
    except Exception as exc:
        logger.info("Could not put the resume in %r: %s", target.question or target.label or target.name,
                    str(exc).splitlines()[0][:120])
        return None
    return target


# --- choosing from a list: one reader of options, one matcher (option_match.py) ------------------------------------

# Every way a list's rows are drawn in the portals studied: ARIA options and menu items (react-select, Workday,
# BambooHR, SmartRecruiters, Rippling), Ant rows, Workday prompt rows, select2 rows.
_OPTION_SELECTOR = ("[role=option], [role=menuitem], .ant-select-item-option, [data-automation-id=promptOption], "
                    ".select2-results__option")

_OPTIONS_JS = r"""
([fieldId, selector]) => {
  const deep = (root, sel, acc = []) => {
    root.querySelectorAll(sel).forEach(e => acc.push(e));
    root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) deep(e.shadowRoot, sel, acc); });
    return acc;
  };
  const visible = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  deep(document, '[data-jaa-opt]').forEach(e => e.removeAttribute('data-jaa-opt'));
  const field = deep(document, `[data-jaa-field="${fieldId}"]`)[0];
  let scope = null;
  if (field) {
    const id = field.getAttribute('aria-controls') || field.getAttribute('aria-owns');
    if (id) scope = (field.getRootNode().getElementById && field.getRootNode().getElementById(id)) || document.getElementById(id);
  }
  let rows = deep(scope || document, selector).filter(visible)
    // a picked value shown as a chip is drawn as an option too (Workday 'items selected'): not a row to choose
    .filter(o => !o.closest('[aria-label="items selected"], .select__multi-value'));
  return rows.slice(0, 600).map((o, i) => {
    o.setAttribute('data-jaa-opt', String(i));
    return (o.getAttribute('data-automation-label') || o.getAttribute('title') || o.innerText || o.textContent || '')
      .replace(/\s+/g, ' ').trim();
  });
}
"""

_VALUE_JS = r"""
(fieldId) => {
  const deep = (root, sel, acc = []) => {
    root.querySelectorAll(sel).forEach(e => acc.push(e));
    root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) deep(e.shadowRoot, sel, acc); });
    return acc;
  };
  const e = deep(document, `[data-jaa-field="${fieldId}"]`)[0];
  if (!e) return '';
  const text = n => (n ? (n.innerText || n.textContent || '').replace(/\s+/g, ' ').trim() : '');
  if (e.tagName === 'SELECT') return e.selectedIndex >= 0 ? text(e.options[e.selectedIndex]) : '';
  if (e.tagName === 'BUTTON' || (e.getAttribute('role') === 'combobox' && e.tagName !== 'INPUT')) return text(e);
  for (let n = e, d = 0; n && d < 6; n = n.parentElement, d++) {
    const v = n.querySelector && n.querySelector('.select__single-value, [class*="singleValue"], .ant-select-selection-item, [data-automation-id=selectedItem]');
    if (v && text(v)) return text(v);
    const chip = n.querySelector && n.querySelector('[role=listbox][aria-label="items selected"] [role=option], .select__multi-value__label');
    if (chip && text(chip)) return text(chip).replace(/, press delete to clear value\.?$/i, '');
  }
  return e.value || '';
}
"""


def list_options(f: Field) -> list[str]:
    """The rows of the list that belongs to this field, as they are drawn now; each row is stamped data-jaa-opt."""
    try:
        return f.frame.evaluate(_OPTIONS_JS, [f.id, _OPTION_SELECTOR]) or []
    except Exception as exc:
        logger.debug("Could not read the options of %r: %s", f.question, exc)
        return []


def shown_value(f: Field) -> str:
    """What the field shows now (a list shows its pick beside the input, not in it)."""
    try:
        return f.frame.evaluate(_VALUE_JS, f.id) or ""
    except Exception:
        return ""


def _wait_for_options(f: Field, timeout_ms: int) -> list[str]:
    """Rows appear a moment after opening -- or after a server answers what was typed (location, school)."""
    waited, last = 0, []
    while waited <= timeout_ms:
        rows = list_options(f)
        if rows and rows == last:
            return rows                  # the list has settled
        last = rows
        f.frame.wait_for_timeout(250)
        waited += 250
    return last


def _click_row(f: Field, index: int, timeout_ms: int) -> bool:
    try:
        row = f.frame.locator(f"[data-jaa-opt='{index}']").first
        row.scroll_into_view_if_needed(timeout=timeout_ms)
        row.click(timeout=timeout_ms)
        return True
    except Exception as exc:
        logger.info("Could not click the row for %r: %s", f.question, str(exc).splitlines()[0][:100])
        return False


def _kept(f: Field, chosen: str) -> bool:
    shown = option_match.plain(shown_value(f))
    want = option_match.plain(chosen)
    return bool(shown) and not is_placeholder(shown) and (want in shown or shown in want)


def _in_ant_select(f: Field) -> bool:
    try:
        return bool(f.locator().evaluate("e => !!e.closest('.ant-select')"))
    except Exception:
        return False


def choose(page, f: Field, answer: str, timeout_ms: int = 5_000) -> bool:
    """Gives a list field its answer, whatever the list is built as; True only when the field then shows it.

    native <select>: select the option the matcher names. Ant select: interaction.resolve_ant_dropdown (opens with
    mousedown, reads the whole virtual list). Anything else that opens a list -- react-select, a button list,
    Workday search, SmartRecruiters, Rippling: a real click opens it (react-select ignores page-script key presses),
    the answer is typed where the field takes typing (the list filters, or a server answers), and the row the
    matcher names is clicked. Never Enter, which takes whichever row is first."""
    want = (answer or "").strip()
    if not want:
        return False
    if f.kind == "select":
        index = option_match.best_option(f.options, want)
        if index is None:
            logger.info("No option of %r is %r (offered: %s)", f.question, want, f.options[:8])
            return False
        try:
            f.locator().select_option(index=index, timeout=timeout_ms)
        except Exception as exc:
            logger.info("Could not select %r in %r: %s", f.options[index], f.question, str(exc).splitlines()[0][:100])
            return False
        return _kept(f, f.options[index])
    if f.kind == "combobox" and _in_ant_select(f):
        import interaction
        return interaction.resolve_ant_dropdown(f.frame, f.locator(), want, timeout_ms=timeout_ms,
                                                pick=lambda labels: option_match.best_option(labels, want))

    button = f.locator()
    try:
        button.scroll_into_view_if_needed(timeout=timeout_ms)
        button.click(timeout=timeout_ms)
    except Exception as exc:
        logger.info("Could not open the list %r: %s", f.question, str(exc).splitlines()[0][:100])
        return False
    frame = f.frame
    frame.wait_for_timeout(300)
    typeable = f.tag == "input" and f.type in ("", "text", "search")
    search = None
    if not typeable:
        # BambooHR's menu brings its own search box
        candidate = frame.locator("[role=menu] input:visible, [role=listbox] input:visible, "
                                  "input[placeholder^='Search']:visible").first
        search = candidate if candidate.count() else None
    queries = [want, want.split(",")[0].split("(")[0].strip()[:20]] if (typeable or search) else [None]
    chosen = None
    for query in dict.fromkeys(queries):
        if query:
            box = button if typeable else search
            try:
                box.fill("", timeout=timeout_ms)
                box.type(query[:40], delay=25, timeout=timeout_ms)
            except Exception as exc:
                logger.debug("Could not type into %r: %s", f.question, exc)
        rows = _wait_for_options(f, timeout_ms)
        index = option_match.best_option(rows, want)
        if index is not None:
            # A search that answers every keystroke redraws its list after it was read (the row read is gone):
            # read the list again and click the same answer in it.
            for _attempt in range(3):
                if _click_row(f, index, timeout_ms):
                    chosen = rows[index]
                    break
                f.frame.wait_for_timeout(400)
                rows = _wait_for_options(f, timeout_ms)
                index = option_match.best_option(rows, want)
                if index is None:
                    break
            break
        logger.info("No row of %r is %r (offered: %s)", f.question, want, rows[:8])
    if chosen is None:
        try:
            frame.keyboard.press("Escape")
        except Exception:
            pass
        return False
    frame.wait_for_timeout(300)
    return _kept(f, chosen)


def pick_open_row(scope, answer: str, timeout_ms: int = 3_000) -> Optional[str]:
    """After typing into a search box: clicks the row of the list now showing that IS the answer (option_match), and
    returns its text -- or None, closing the list, when no row is. Never ArrowDown + Enter: that takes whichever row
    is first ("United States" became "Afghanistan"), and in a box with no list open it sends the whole form -- Lucid,
    30 September: Greenhouse took it as Submit, showed its errors and mailed its security code."""
    rows = scope.locator(", ".join(f"{s}:visible" for s in _OPTION_SELECTOR.split(", ")) + ", [role=listbox] li:visible")
    texts = []
    try:
        for i in range(min(rows.count(), 80)):
            texts.append(" ".join((rows.nth(i).inner_text(timeout=1_000) or "").split()))
    except Exception:
        return None
    index = option_match.best_option(texts, answer) if texts else None
    if index is None:
        try:
            scope.keyboard.press("Escape")
        except Exception:
            pass
        return None
    try:
        rows.nth(index).click(timeout=timeout_ms)
    except Exception as exc:
        logger.debug("Could not click the row %r: %s", texts[index], str(exc).splitlines()[0][:100])
        return None
    return texts[index]


def read_choices(f: Field, timeout_ms: int = 4_000) -> list[str]:
    """What a list offers, read by opening it and closed again without choosing. A list that draws its rows only
    when opened (Greenhouse's type-to-search) shows no choices until then: asked without them, the AI took Lucid's
    "sexual orientation (mark all that apply)" for an essay question (29 September)."""
    if f.kind == "select":
        return [o for o in f.options if not is_placeholder(o)]
    try:
        f.locator().scroll_into_view_if_needed(timeout=timeout_ms)
        f.locator().click(timeout=timeout_ms)
        rows = _wait_for_options(f, timeout_ms)
    except Exception as exc:
        logger.debug("Could not open the list %r to read it: %s", f.question, str(exc).splitlines()[0][:100])
        rows = []
    try:
        f.frame.keyboard.press("Escape")
    except Exception:
        pass
    return [r for r in rows if r and not is_placeholder(r)]


def choose_from_button_list(page, f: Field, answer: str, timeout_ms: int = 4_000) -> bool:
    """A list drawn as a button (BambooHR, Workday 'Select One'): see choose()."""
    return choose(page, f, answer, timeout_ms)


# --- several answers ---------------------------------------------------------------------------------------------

def choose_several(page, boxes: list[Field], answers: list[str]) -> list[str]:
    """A 'tick all that apply' group (Lever languages): ticks the box whose label is each answer. Returns what was
    ticked. A box already ticked is left as it is -- someone ticked it."""
    labels = [b.label or b.question for b in boxes]
    ticked = []
    for answer in answers:
        index = option_match.best_option(labels, answer)
        if index is None:
            logger.info("No box of %r is %r", boxes[0].question if boxes else "", answer)
            continue
        box = boxes[index]
        try:
            if not box.locator().is_checked():
                box.locator().check(timeout=4_000)
            ticked.append(labels[index])
        except Exception as exc:
            logger.info("Could not tick %r: %s", labels[index], str(exc).splitlines()[0][:100])
    return ticked


# --- dates ---------------------------------------------------------------------------------------------------------

_MONTHS = {m.lower(): n for n, m in enumerate(("January February March April May June July August September "
                                               "October November December").split(), start=1)}


def parse_date(text: str) -> Optional[tuple[int, int, int]]:
    """(year, month, day) from the ways the profile and work history write a date: '02/2025', 'February 2025',
    'Feb 2025', '2025-02-01', '02/01/2025', '2025'. A missing day is 1; a missing month is 1."""
    t = (text or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", t)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        return int(m.group(3)), int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"(\d{1,2})[/-](\d{4})", t)
    if m:
        return int(m.group(2)), int(m.group(1)), 1
    m = re.fullmatch(r"([A-Za-z]+)\.?,?\s+(\d{4})", t)
    if m:
        month = next((n for name, n in _MONTHS.items() if name.startswith(m.group(1).lower()[:3])), None)
        if month:
            return int(m.group(2)), month, 1
    m = re.fullmatch(r"(\d{4})", t)
    if m:
        return int(m.group(1)), 1, 1
    return None


_RELATIVE_DATE = re.compile(
    r"^\s*(?:(immediately|now|asap|right away|as soon as possible)|(?:in\s+|within\s+)?(\d{1,3}|a|an|one|two|three|"
    r"four|six)\s*(day|week|month)s?\b)", re.IGNORECASE)
_COUNT_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "six": 6}
# A box that shows how it wants a date: "MM/DD/YYYY", "dd.mm.yyyy", "YYYY-MM-DD", "MM/YYYY".
DATE_PLACEHOLDER = re.compile(r"(?:mm|dd|yyyy|yy)\s*[/.\-]\s*(?:mm|dd|yyyy|yy)|^\s*yyyy\s*$", re.IGNORECASE)


def resolve_date(text: str, today=None) -> Optional[tuple[int, int, int]]:
    """A date as the profile or an answer says it (parse_date), or counted from today: "Immediately", "2 weeks",
    "30 days notice", "one month". None when it is not a date at all -- never today in its place."""
    parts = parse_date(text)
    if parts is not None:
        return parts
    m = _RELATIVE_DATE.match(text or "")
    if not m:
        return None
    import datetime as _dt
    today = today or _dt.date.today()
    if m.group(1):
        when = today
    else:
        count = int(m.group(2)) if m.group(2).isdigit() else _COUNT_WORDS[m.group(2).lower()]
        unit = m.group(3).lower()
        if unit == "day":
            when = today + _dt.timedelta(days=count)
        elif unit == "week":
            when = today + _dt.timedelta(weeks=count)
        else:
            month0 = today.month - 1 + count
            year, month = today.year + month0 // 12, month0 % 12 + 1
            when = _dt.date(year, month, min(today.day, 28))
    return when.year, when.month, when.day


def date_text(value: str, input_type: str = "", placeholder: str = "") -> Optional[str]:
    """The one way a date answer is written into a box, whatever the portal: a date picker takes yyyy-mm-dd, a month
    picker yyyy-mm, a box that shows its format ("MM/DD/YYYY", "dd.mm.yyyy", "YYYY-MM-DD", "MM/YYYY") takes that
    format, and a box that shows none takes mm/dd/yyyy. None when the answer is not a date."""
    parts = resolve_date(value)
    if parts is None:
        return None
    year, month, day = parts
    kind, shown = (input_type or "").lower(), (placeholder or "").lower()
    if kind == "date":
        return f"{year:04d}-{month:02d}-{day:02d}"
    if kind == "month":
        return f"{year:04d}-{month:02d}"
    tokens = re.findall(r"yyyy|yy|mm|dd", shown)
    if tokens and DATE_PLACEHOLDER.search(shown):
        between = re.search(r"(?:yyyy|yy|mm|dd)\s*([/.\-])\s*(?:yyyy|yy|mm|dd)", shown)
        written = {"yyyy": f"{year:04d}", "yy": f"{year % 100:02d}", "mm": f"{month:02d}", "dd": f"{day:02d}"}
        return (between.group(1) if between else "/").join(written[t] for t in tokens)
    return f"{month:02d}/{day:02d}/{year:04d}"


def is_date_box(input_type: str = "", placeholder: str = "") -> bool:
    return (input_type or "").lower() in ("date", "month") or bool(DATE_PLACEHOLDER.search(placeholder or ""))


def fill_date(page, f: Field, value: str) -> bool:
    """Types a date the way this box takes it (date_text). True when the box then holds it."""
    placeholder = ""
    try:
        placeholder = (f.locator().get_attribute("placeholder") or "").lower()
    except Exception:
        pass
    text = date_text(value, f.type, placeholder)
    if text is None:
        logger.info("Not a date the agent can read: %r for %r", value, f.question)
        return False
    box = f.locator()
    try:
        box.fill(text, timeout=5_000)
        box.dispatch_event("change")
        box.blur()
    except Exception as exc:
        logger.info("Could not type the date into %r: %s", f.question, str(exc).splitlines()[0][:100])
        return False
    try:
        return (box.input_value(timeout=2_000) or "") == text
    except Exception:
        return False


def tags_beside(locator) -> list[str]:
    """The tags a tag box holds (see TAGS_BESIDE_JS); [] when it holds none or cannot be read."""
    try:
        return list(locator.evaluate("(e) => (" + TAGS_BESIDE_JS.strip() + ")(e, '')", timeout=2_000) or [])
    except Exception:
        return []
