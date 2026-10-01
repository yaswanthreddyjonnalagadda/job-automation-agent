"""Reading a page: the accessibility snapshot turned into the controls on it and plain facts about it.

Moved out of page_agent.py on 1 October (architecture review, item 2: one module per stage of
read page -> identify fields -> resolve answers -> check policy -> act -> verify -> save progress).
This is the "read page / identify fields" stage: no browser actions and no answering here, only what the
page says. page_agent imports every name back, so code that used page_agent.parse_snapshot still works;
new code imports from here.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from perception import is_honeypot
import safety

logger = logging.getLogger("page_agent")


# Roles that take an answer or move the application on.
ANSWER_ROLES = {"textbox", "searchbox", "combobox", "listbox", "radio", "checkbox", "switch", "spinbutton", "slider"}


PRESS_ROLES = {"button", "link", "menuitem", "tab"}


OPTION_ROLES = {"option", "menuitemradio", "menuitem", "treeitem", "listitem", "gridcell"}


# Never pressed, whatever the page or Claude says.
NEVER_PRESS = re.compile(
    r"finish later|save (and|&) (exit|finish later|close)|save for later|withdraw|log ?out|sign ?out|\bcancel\b|"
    r"\bback\b|previous|\bdelete\b|\bremove\b|linked ?in|\bindeed\b|facebook|twitter|\bprint\b",
    re.IGNORECASE)


PLACEHOLDER = re.compile(
    r"^[\s\-–—]*(no selection|select( one| an option)?|please select|choose( one| an option)?|make a selection|"
    r"none selected)?[\s\-–—.]*$", re.IGNORECASE)


def _blank_choice_on_page(controls) -> bool:
    """A dropdown still showing its placeholder ("Choose an option"), whatever the agent made of it.

    The profile-only shortcut trusted the questions the agent recognised; ADP's dropdown is a bare
    button the agent does not take for a question, so the page was called fully answered and Next
    was pressed with a required answer blank. Such a page goes to the plan."""
    for c in controls:
        shown = (c.value or c.name or "").strip()
        if c.role in ("button", "combobox", "listbox") and not c.disabled and shown and PLACEHOLDER.match(shown):
            return True
    return False


# ---------------------------------------------------------------------------
# Reading the page
# ---------------------------------------------------------------------------
@dataclass
class Control:
    ref: str
    role: str
    name: str = ""
    value: str = ""
    checked: bool = False
    selected: bool = False
    disabled: bool = False
    group: str = ""                       # the question a radio button belongs to
    container: str = ""                   # the section it sits in ("Cover Letter")
    context: str = ""                     # nearby text, when the control has no name of its own
    options: list[str] = field(default_factory=list)
    selected_option: str = ""
    toggle: bool = False                  # a choice drawn as a pressable button: a second click clears it

    GENERIC_NAMES = re.compile(
        r"^(attach( file)?|choose file|upload( file)?|browse|add file|select file|select one( required)?)$",
        re.IGNORECASE,
    )

    @property
    def holds_choices(self) -> bool:
        """A group whose choices have no reference of their own."""
        return self.role in ("radiogroup", "group", "list", "region") and bool(self.options)

    @property
    def question(self) -> str:
        if self.group:
            return self.group
        if re.fullmatch(r"(?:yes|no|select one) required", self.name.strip(), re.IGNORECASE) and self.context:
            return self.context
        if self.name and not self.GENERIC_NAMES.match(self.name.strip()):
            return self.name
        return self.container or self.context or self.name

    @property
    def answer(self) -> str:
        """What the control shows as answered, "" when nothing is."""
        if self.role in ("radio", "checkbox", "switch"):
            return "checked" if self.checked else ""
        shown = (self.selected_option or self.value or "").strip()
        # An icon drawn from a symbol font is not an answer: R+L's ZIP box
        # showed one and the agent would not touch the field.
        if shown and not re.search(r"[0-9A-Za-z]", shown):
            return ""
        # A box that shows its own label is showing a placeholder, not an
        # answer: R+L's date lists read as answered "Month", "Day", "Year".
        if shown and shown.lower() in (self.name.strip().lower(), self.group.strip().lower()):
            return ""
        if re.fullmatch(r"mm|dd|yy(yy)?|mm/dd/yyyy|month|day|year", shown, re.IGNORECASE):
            return ""
        # A phone box the site started with its country's dial code ("+1") is
        # still empty: the code is what the number is typed after.
        if self.role in ("textbox", "searchbox") and re.fullmatch(r"\+\s?\d{1,4}", shown):
            return ""
        return "" if PLACEHOLDER.match(shown) else shown


_LINE = re.compile(r'^(?P<indent>\s*)- (?P<role>[\w/-]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?(?P<attrs>(?: \[[^\]]*\])*)'
                   r'(?::\s?(?P<value>.*))?$')


# Text that introduces a question of choices: it asks ('?'), leads in (':'), or is marked required. Between two runs
# of radio buttons or tick boxes, such text starts the next question.
_INTRODUCES_CHOICES = re.compile(r"\?|:\s*$|\(\s*required\s*\)|\*\s*$|\b(?:select|check|choose|tick|pick)\b|"
                                 r"all that apply", re.IGNORECASE)


# A tick box whose label is a statement of the owner's, not a choice among others.
_STATEMENT = re.compile(r"\s*i\s+(?:agree|consent|acknowledge|understand|have\s+read|certify|authori[sz]e|accept|"
                        r"confirm|attest|would\s+like|wish\s+to\s+receive|opt\s+in|allow)\b", re.IGNORECASE)


# The note a site writes under a question left unanswered: it belongs to that question, it starts nothing.
_REQUIRED_NOTE = re.compile(r"\s*(?:(?:this\s+)?(?:question|field|answer|selection)\s+(?:is\s+)?)?required\.?\s*",
                            re.IGNORECASE)


def _unquote(text: str) -> str:
    text = (text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    return text.replace('\\"', '"').replace("\\\\", "\\")


def parse_snapshot(snapshot: str) -> list[Control]:
    """The answerable and pressable controls in an accessibility snapshot, in
    page order, each with its reference, current answer and choices."""
    controls: list[Control] = []
    stack: list[tuple[int, str, Optional[Control]]] = []   # (indent, role/name, control)
    last_text = ""
    last_text_indent = -1
    radio_context = ""   # the question above a run of radio buttons with no group of their own
    # Radio buttons and tick boxes with no group of their own belong to the question written above them. A run of
    # them used to end only where something other than a choice came next, so questions drawn one straight after
    # another became one: Paylocity's "Do you currently reside in the United States?" took the next question's
    # Yes/No as well, and a "background check" answer made five more questions look answered (29 September).
    # Question text that appears between two choices now starts the next question.
    text_since_choice = ""      # question-like text seen since the last radio/tick box
    last_choice_indent = -1
    checkbox_context, checkbox_run = "", 0
    checkbox_runs: dict[int, tuple[int, str]] = {}    # id(tick box) -> (its run, the text that introduced it)
    unclickable = None   # a tick box with no reference: (role, name, checked, lines left to find it)
    last_control_indent = -1
    entry_box: Optional[tuple[int, Control]] = None   # an empty-looking text box whose value may follow as a child line
    toggle_row: list[tuple[Control, bool]] = []       # buttons side by side that may be one question's choices
    toggle_indent, toggle_question = -1, ""
    inside_choice: list[int] = []   # indents of the list options (and open lists) the current line sits inside
    for raw in (snapshot or "").splitlines():
        # A line whose text holds a colon comes wrapped in quotes:
        #   - 'heading "Apply: Network Engineer" [level=1] [ref=e2]'
        #   - 'combobox "Country: where you live" [ref=e20]': United States
        quoted = re.match(r"^(\s*)- '((?:[^']|'')*)'(:.*)?$", raw)
        if quoted:
            raw = f"{quoted.group(1)}- {quoted.group(2).replace(chr(39) * 2, chr(39))}{quoted.group(3) or ''}"
        m = _LINE.match(raw)
        if not m:
            continue
        indent, role = len(m.group("indent")), m.group("role")
        name = _unquote(m.group("name") or "")
        attrs = m.group("attrs") or ""
        value = _unquote(m.group("value") or "")
        ref_m = re.search(r"\[ref=([\w-]+)\]", attrs)
        if toggle_row and indent <= toggle_indent and not (role == "button" and ref_m and indent == toggle_indent):
            _settle_toggle_row(toggle_row, toggle_question)
            toggle_row = []
        # Captured before the text-tracking block below can overwrite
        # last_text with THIS line's own text -- needed because a clickable
        # generic (unlike a real button) is one of the roles that block
        # updates, and would otherwise see its own label as its context.
        preceding_text = last_text
        while stack and stack[-1][0] >= indent:
            stack.pop()
        while inside_choice and inside_choice[-1] >= indent:
            inside_choice.pop()
        if role in ("radio", "checkbox") and inside_choice:
            # A tick or radio drawn inside a list's own option is that choice's picture, not a question: Workday's
            # Field of Study list, left open on the page, was read as a question per subject -- "Accounting",
            # "Advertising", "African Languages" ... -- and answered one by one for three minutes (Ciena, 30 Sept).
            stack.append((indent, "", None))
            continue
        parent_group = next((label for _i, label, _ctl in reversed(stack) if label), "")
        owner = next((ctl for _i, _l, ctl in reversed(stack) if ctl is not None), None)

        # A text box that carries a placeholder does not show what is typed in it
        # after its colon: the snapshot draws the value as a child line,
        #   - textbox "Email" [ref=e104]:
        #     - /placeholder: ""
        #     - text: jane@example.com
        # (ADP's Email and Mobile Number). Read as empty, the box was retyped on
        # every pass and reported "could not set". The first such child is its value.
        if entry_box is not None and indent <= entry_box[0]:
            entry_box = None
        if entry_box is not None and role == "text" and value and not entry_box[1].value:
            entry_box[1].value = value
            continue
        # A search-and-pick box shows what was picked as a tag inside it, named by the choice:
        #   - combobox "Institution" [ref=e179]:
        #     - textbox "Eastern Illinois University" [ref=e1006]:
        #       - button: ×
        # (Avature, Steelcase, 29 September). Read as empty, the school was typed again on every pass and the
        # tag was taken for a question of its own. The tag is the box's answer.
        if role in ("textbox", "searchbox") and name and owner is not None and owner.role == "combobox" \
                and not owner.value and not _same_question(name, owner.name) \
                and not re.fullmatch(r"(?:search|type|select|choose|start typing)\b.*", name, re.IGNORECASE):
            owner.value = name
            stack.append((indent, "", None))
            continue

        if role in ("text", "paragraph", "heading", "strong", "emphasis", "generic") and (value or name):
            candidate_text = (value or name).strip()[:200]
            # Workday's search-and-pick boxes show the choice as a tag beside an empty search box:
            # "1 item selected, United States of America (+1)". That is the box's answer, not a label.
            # Read as empty, Rackspace's Country Phone Code was "corrected" four times and the run stopped
            # with "could not set" (29 September).
            tagged = _SELECTED_TAG.fullmatch(candidate_text)
            if tagged:
                box = _last_entry_box(controls)
                if box is not None and not box.value and int(tagged.group(1)) > 0 and tagged.group(2):
                    box.value = tagged.group(2).strip()
                stack.append((indent, "", None))
                continue
            # Keep the field label when an accessibility tree emits its
            # required marker as a later child ("First Name" then "(required)").
            if not re.fullmatch(r"\(?\s*required\s*\)?|\*", candidate_text, re.IGNORECASE):
                last_text = candidate_text
                last_text_indent = indent
            # Schwab's veteran question draws its radio buttons with no label of
            # their own: each is followed by its text ("I AM NOT A PROTECTED
            # VETERAN"). That text is the button's name.
            if controls and not controls[-1].name and controls[-1].role in ("radio", "checkbox", "switch") \
                    and not controls[-1].value and (role == "text" or (role == "generic" and indent == last_control_indent)):
                # Meta draws each choice as a radio and then its word in a box beside it ("radio" / "generic: Male"):
                # that word, right after the radio at its own level, is the radio's name. Left nameless, "Male" was
                # matched to nothing and the Female button was the one clicked (30 September).
                controls[-1].name = candidate_text
            # After a choice: text that reads as a question starts the next one. Not the choice's own label -- the
            # same words as its name, or any text right after a choice that has no name of its own (Federal
            # Recovery Service draws "Facebook", "Indeed" ... beside unnamed radios) -- and not the site's
            # "Question Required" note.
            last_choice = controls[-1] if controls and controls[-1].role in ("radio", "checkbox") else None
            if last_choice is not None and last_choice.name and not _REQUIRED_NOTE.fullmatch(candidate_text) \
                    and not _same_question(candidate_text, last_choice.name) \
                    and _INTRODUCES_CHOICES.search(candidate_text):
                text_since_choice = candidate_text
            # Ant Design / React custom comboboxes render their selected value as a
            # trailing generic/text element right after the combobox input.
            if controls and controls[-1].role in ("combobox", "listbox") and not controls[-1].value \
                    and role in ("generic", "text", "paragraph", "status") and candidate_text \
                    and indent >= last_control_indent \
                    and not candidate_text.endswith(":") and not candidate_text.endswith("*") \
                    and not re.fullmatch(r"\(?\s*required\s*\)?|\*|select\s*one.*", candidate_text, re.IGNORECASE) \
                    and not _same_question(candidate_text, controls[-1].name):
                controls[-1].value = candidate_text
        control = None
        if unclickable is not None:
            # R+L draws "Current Job" as a box with no reference at all; the
            # only thing that can be clicked is the label drawn beside it.
            # Whatever carries the box's own words is what a person clicks:
            # a label, the status line beside it, or the generic Oracle draws.
            beside = ref_m and role in ("generic", "status", "text", "paragraph", "label", "listitem")
            if beside and _same_question((value or name).strip(), unclickable[1]):
                controls.append(Control(ref=ref_m.group(1), role=unclickable[0], name=unclickable[1],
                                        checked=unclickable[2], container=parent_group))
                unclickable = None
            elif unclickable[3] <= 0 or (ref_m and role in ANSWER_ROLES):
                unclickable = None
            else:
                unclickable = (*unclickable[:3], unclickable[3] - 1)
        if role in ("radio", "checkbox", "switch") and not ref_m and owner is not None \
                and owner.role in ("radiogroup", "group", "list", "listbox"):
            # R+L's yes/no radios carry no reference of their own -- only their
            # group does -- so nothing could be clicked and the question came
            # back to the owner. They are the group's choices.
            owner.options.append(name or value)
            if "[checked]" in attrs:
                owner.selected_option = name or value
        elif role in ("radio", "checkbox", "switch") and not ref_m and (name or value):
            unclickable = (role, name or value, "[checked]" in attrs, 6)
        chip = _TAG_CHOICE.match((name or value or "").strip()) if role in OPTION_ROLES else None
        if chip:
            box = _last_entry_box(controls)
            if box is not None and not box.value:
                box.value = chip.group(1).strip()
            stack.append((indent, "", None))
            continue
        if ref_m and role == "listbox" and _TAG_LIST.fullmatch((name or "").strip()):
            # The list of chosen tags ("items selected") belongs to the box before it; it is not a question.
            stack.append((indent, "", None))
            continue
        if role in OPTION_ROLES and owner is not None and owner.role in ("combobox", "listbox"):
            owner.options.append(name or value)
            if "[selected]" in attrs:
                owner.selected_option = name or value
        # Choices drawn by script have references of their own, and are clicked.
        # A styled <label>/<div> wired to a hidden file input (Greenhouse's
        # "Attach file", and the same pattern elsewhere) carries no ARIA
        # press role at all, so it never reached ANSWER_ROLES/PRESS_ROLES --
        # the resume upload silently had nothing to click (24 September:
        # Rubrik/Greenhouse, "Resume/CV *" -> "Attach file"). The snapshot
        # already flags it [cursor=pointer]; when its own text is one of the
        # generic attach/upload names, treat it as a button, using the same
        # nearby-label lookup (`context`) a real generic-named button
        # already relies on to say which of several identical "Attach file"
        # controls (resume vs. cover letter) it is.
        clickable_generic = bool(
            ref_m and role in ("generic", "text") and "[cursor=pointer]" in attrs
            and Control.GENERIC_NAMES.match((name or value or "").strip())
        )
        if ref_m and (role in ANSWER_ROLES or role in PRESS_ROLES or role in OPTION_ROLES
                      or role in ("radiogroup", "group", "list", "region") or clickable_generic):
            # The tick box before this one, list items aside: Meta puts each tick box in a list item of its own, and
            # counting the list item, every box began a run of one -- the question over them was lost (30 September).
            # (Named groups still end a run: they are where one question stops and the next begins.)
            before = next((c for c in reversed(controls) if c.role != "listitem"), None)
            if role == "radio" and (not (controls and controls[-1].role == "radio") or text_since_choice):
                radio_context = text_since_choice or last_text
            if role == "checkbox" and (not (before and before.role == "checkbox") or text_since_choice):
                checkbox_context, checkbox_run = text_since_choice or last_text, checkbox_run + 1
            shown = value if value and not value.endswith(":") else ""
            # Some widgets put the chosen value in a box beside the control
            # rather than in it ("Yes." next to "Would you relocate to London?",
            # "+1" next to "Country"). The text right beside it, at the same
            # level, is that choice -- unless it is the question's own label.
            if not shown and role in ("combobox", "listbox") and last_text and last_text_indent == indent \
                    and not _same_question(last_text, name):
                shown = last_text
            # If previous combobox tentatively adopted an upcoming field label as value, revert it
            if controls and controls[-1].role in ("combobox", "listbox") and controls[-1].value \
                    and name and _same_question(controls[-1].value, name):
                controls[-1].value = ""
            control_name = (name or value).strip() if clickable_generic else name
            control = Control(
                ref=ref_m.group(1), role=("button" if clickable_generic else role),
                name=control_name, value="" if clickable_generic else shown,
                checked="[checked]" in attrs or "[checked=true]" in attrs, selected="[selected]" in attrs,
                disabled="[disabled]" in attrs, group=(parent_group or radio_context) if role == "radio" else "",
                container=parent_group,
                context=(preceding_text if clickable_generic else last_text) if (
                    clickable_generic or not name or Control.GENERIC_NAMES.match(name.strip())
                    or re.fullmatch(r"(?:yes|no|select one) required", name.strip(), re.IGNORECASE))
                else "")
            if control.role in ("textbox", "searchbox") and is_honeypot(control.name):
                # A decoy for robots: not a question, never answered, never "still blank".
                stack.append((indent, "", None))
                continue
            controls.append(control)
            last_control_indent = indent
            text_since_choice = ""
            if control.role in ("radio", "checkbox"):
                last_choice_indent = indent
            if control.role == "checkbox":
                checkbox_runs[id(control)] = (checkbox_run, checkbox_context)
            if control.role == "button" and not clickable_generic and (not toggle_row or indent == toggle_indent):
                if not toggle_row:
                    toggle_indent, toggle_question = indent, preceding_text
                toggle_row.append((control, bool(re.search(r"\[pressed(?:=true)?\]", attrs))))
            entry_box = (indent, control) if control.role in ("textbox", "searchbox") and not control.value else None
        # A named group is both a question for what is inside it and, when it
        # has a reference, a control the agent can act on.
        stack.append((indent, name if role in ("group", "radiogroup", "region") else "", control))
        if role in ("option", "menuitem", "menuitemradio", "treeitem", "listbox"):
            inside_choice.append(indent)
    if toggle_row:
        _settle_toggle_row(toggle_row, toggle_question)
    _group_tick_boxes(controls, checkbox_runs)
    return [c for c in controls if not is_bot_trap(c)]


def _group_tick_boxes(controls: list[Control], runs: dict[int, tuple[int, str]]) -> None:
    """Two or more tick boxes under one question that asks for a choice ("Please select any certifications you may
    have.", "Check all that apply") are that question's choices, as radio buttons are: before, each was read as a
    question of its own named by its label, and a required group was never asked (Paylocity, 29 September).
    A single tick box, or a run holding a declaration or signature, is left as it was."""
    members: dict[int, list[Control]] = {}
    for control in controls:
        run = runs.get(id(control))
        if run is not None and not control.group:
            members.setdefault(run[0], []).append(control)
    asked: dict[str, int] = {}
    for run, boxes in members.items():
        question = runs[id(boxes[0])][1]
        # Boxes that each say something the owner agrees to ("I agree to ...", "I consent to ...") are separate
        # questions even under one heading: grouped, only the first would be ticked.
        # And a box named by the same words is labelled by them, so they are not a question over the others
        # (Chobani's "Notification:" opt-in).
        if len(boxes) < 2 or not question or not _INTRODUCES_CHOICES.search(question) \
                or any(safety.is_attestation(box.name) or safety.is_privacy_consent(box.name)
                       or _STATEMENT.match(box.name or "") or _same_question(box.name, question) for box in boxes):
            continue
        # The same question twice (Paylocity's form asked for certifications in two lists) is two questions:
        # one name for both made the second list look answered by the first.
        asked[question] = asked.get(question, 0) + 1
        for box in boxes:
            box.group = question if asked[question] == 1 else f"{question} [{asked[question]}]"


# A field a site hides from people to catch programs: "Enter website. This input is for robots only, do not
# enter if you're human." Filled, it marks the application as a bot's; asked about, it spent an AI request
# (Writer, 28 September). It is not the owner's question, so it is not read as a control at all.
_BOT_TRAP = re.compile(
    r"\b(?:for robots only|robots? only|do not (?:enter|fill|type)[^.]{0,40}\bif you(?:'| a)re (?:a )?human|"
    r"if you are (?:a )?human,? (?:leave|do not))", re.IGNORECASE)


def is_bot_trap(control: "Control") -> bool:
    return control.role in ANSWER_ROLES and bool(
        _BOT_TRAP.search(" ".join(filter(None, (control.name, control.question, control.context)))))


# What a button that does something is called -- never an answer to a question.
_ACTION_WORDS = re.compile(
    r"^(add|edit|save|upload|attach|browse|preview|view|open|close|clear|reset|done|ok|update|search|"
    r"sign in|log in|enter manually|apply|skip|show|hide|more|less)\b", re.IGNORECASE)


# What an answer is called when a row carries no other sign of being a question.
_CHOICE_WORDS = re.compile(
    r"^(yes|no|true|false|maybe|n/?a|not applicable|prefer not to (say|answer|disclose)|"
    r"decline to (answer|state|self.identify))\b", re.IGNORECASE)


def _settle_toggle_row(row: list[tuple[Control, bool]], question: str) -> None:
    """Buttons side by side under a question are that question's choices.

    Ashby draws Yes/No as two <button aria-pressed>. Read as plain buttons
    called "Yes" and "No" they belonged to no question, and four questions the
    profile answers were left blank on every run (Writer, 28 September). The
    row becomes the question's choices, pressed meaning chosen. A row of
    actions (Back / Next, Edit / Remove) stays a row of buttons.
    """
    question = " ".join((question or "").split())
    names = [" ".join(c.name.split()) for c, _pressed in row]
    if len(row) < 2 or not question or any(not n or len(n) > 40 for n in names):
        return
    if len({n.lower() for n in names}) != len(names) or any(_same_question(question, n) for n in names):
        return
    if any(FORWARD_LABEL.match(n) or NEVER_PRESS.search(n) or Control.GENERIC_NAMES.match(n)
           or _ACTION_WORDS.match(n) for n in names):
        return
    asks = re.search(r"\?\s*\*?\s*$|\*\s*$", question)
    if not (any(p for _c, p in row) or asks or all(_CHOICE_WORDS.match(n) for n in names)):
        return
    for control, is_pressed in row:
        control.role, control.group, control.checked, control.toggle = "radio", question, is_pressed, True
        control.context = ""


def _section_holds_a_file(snapshot: str, section: str) -> bool:
    """True when the section already shows an attached file."""
    if not section:
        return False
    lines = (snapshot or "").splitlines()
    for i, line in enumerate(lines):
        if not re.search(rf'- (group|region) "{re.escape(section)}[^"]*"', line):
            continue
        indent = len(line) - len(line.lstrip())
        for later in lines[i + 1:]:
            if later.strip() and len(later) - len(later.lstrip()) <= indent:
                break
            if re.search(r"\.(pdf|docx?|txt|rtf)\b|remove file", later, re.IGNORECASE):
                return True
    return False


_UPLOAD_VERB = re.compile(r"attach|upload|choose|add|browse|file|import", re.IGNORECASE)


def _upload_control(controls: list[Control], section: re.Pattern) -> Optional[Control]:
    """The button that opens the file chooser for the section `section` names."""
    return next((c for c in controls
                 if c.role in PRESS_ROLES | {"button"}
                 and section.search(f"{c.container} {c.context} {c.name}")
                 and _UPLOAD_VERB.search(c.name or "")), None)


def host_of(url: str) -> str:
    return urlparse(url or "").netloc


# What a button that moves an application on is called, and what is never
# pressed on the agent's own initiative.
FORWARD_LABEL = re.compile(r"^(apply|apply now|start application|begin application|apply manually|create account|next|continue|save and continue|save & continue|save and next|next step|"
                           r"submit|submit application|review|review and submit|proceed|"
                           r"save and proceed|begin)$", re.IGNORECASE)


def required_questions(snapshot: str) -> set[str]:
    """The questions a form marks with a star."""
    marked = set(re.findall(r'- generic "([^"]{2,120})" \[ref=[\w-]+\]: "\*"', snapshot or ""))
    marked |= {m.strip() for m in re.findall(r'- generic \[ref=[\w-]+\]: ([^\n]{2,120}?) \*$',
                                             snapshot or "", re.MULTILINE)}
    return {" ".join(q.split()) for q in marked if q.strip()}


def is_workday_choice_button(control: Control) -> bool:
    """Whether a Workday custom dropdown is represented as an answer button.

    A dropdown still showing its placeholder ("Select One") is an unanswered
    choice field. Any such field with a real question is answerable; the
    profile planner decides from the question whether it has an answer to give,
    so a field with no profile/library answer is simply left for the owner.
    """
    if control.role != "button" or not control.value or not PLACEHOLDER.match(control.value):
        return False
    question = (control.question or "").strip()
    return bool(question) and not control.GENERIC_NAMES.match(question)


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


_ERROR_TEXT = re.compile(
    r"is required and must have a value|\bis required\b|\bare required\b|must have a value|"
    r"^please (enter|select|provide|upload|choose|complete|fill)|^(error|errors found)\b|\binvalid\b|"
    r"must be (a|an|at|in|between|less|more)\b|not a valid", re.IGNORECASE)


_NOT_AN_ERROR = re.compile(r"indicates a required field|^\*|^required$|^error$|^errors found$", re.IGNORECASE)


def page_errors(snapshot: str) -> list[str]:
    """What the page says is wrong with the form, in its own words, once each.

    OCC's Workday My Experience said "The field Upload a file (5MB max) is required and must have a value."
    in an Errors Found panel, and the agent pressed Save and Continue some thirty times without reading it."""
    found: list[str] = []
    for line in (snapshot or "").splitlines():
        if "/url:" in line:
            continue
        for quoted, trailing in re.findall(r'"((?:[^"\\]|\\.)*)"|:\s+(.+)$', line.strip()):
            text = " ".join((quoted or trailing or "").split()).strip('"')
            if 8 <= len(text) <= 200 and _ERROR_TEXT.search(text) and not _NOT_AN_ERROR.search(text) \
                    and not text.lower().startswith("error ") and text not in found:
                found.append(text)
    return found[:8]


_SELECTED_TAG = re.compile(r"(\d+)\s+items?\s+selected(?:,\s*(.+))?", re.IGNORECASE)


_TAG_CHOICE = re.compile(r"(.+?),\s*press (?:delete|backspace) to (?:clear|remove)", re.IGNORECASE)


_TAG_LIST = re.compile(r"\d*\s*items?\s+selected", re.IGNORECASE)


def _last_entry_box(controls: list) -> Optional["Control"]:
    """The search-and-pick box a chosen-value tag belongs to: the last box read."""
    for control in reversed(controls[-3:]):
        if control.role in ("textbox", "searchbox", "combobox"):
            return control
    return None


_STEP = re.compile(r"\b(current\s+|completed\s+)?(?:step|page)\s*(\d+)\s*(?:of|/)\s*(\d+)\b", re.IGNORECASE)


def current_step(snapshot: str) -> Optional[tuple[int, int]]:
    """The step the page is ON, as (step, total) -- or None when the page does not say which.

    Workday's progress bar reads "completed step 1 of 5 ... completed step 4 of 5, current step 5 of 5". The
    first "step N of M" on the page was taken for the current one, so on Aristocrat's Review page (step 5 of
    5, 29 September) the agent read "1 of 5", took the last "Submit" for a mid-form one and pressed it: the
    application was sent without the owner's review. A counter marked current wins; one marked completed is
    never the current step; several unmarked counters that disagree say nothing -- and a page that does not
    say which step it is on has no steps to come, so its Submit is the last and the agent stops for the owner.
    """
    marked, plain = [], []
    for match in _STEP.finditer(snapshot or ""):
        kind, step, total = (match.group(1) or "").strip().lower(), int(match.group(2)), int(match.group(3))
        if not 0 < step <= total:
            continue
        if kind == "current":
            marked.append((step, total))
        elif not kind:
            plain.append((step, total))
    if marked:
        return marked[-1] if len(set(marked)) == 1 else None
    distinct = set(plain)
    return plain[0] if len(distinct) == 1 else None


def _same_question(a: str, b: str) -> bool:
    """The same question, whatever asterisks, punctuation or truncation differ."""
    a, b = _plain(a), _plain(b)
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 25 and (a.startswith(b[:60]) or b.startswith(a[:60])))


def still_loading(snapshot: str) -> bool:
    """True while the page is still drawing part of itself.

    R+L's "How did you hear about us?" was a spinner when the agent read it,
    so there was nothing to choose and the question came back to the owner.
    """
    return bool(re.search(r"^\s*- progressbar\b", snapshot or "", re.MULTILINE))


def frames_loading(snapshot: str) -> bool:
    """True when a frame on the page has nothing in it yet."""
    lines = [l for l in (snapshot or "").splitlines() if l.strip()]
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)- iframe\b[^\n]*:\s*$", line)
        if not m:
            continue
        indent = len(m.group(1))
        children = []
        for later in lines[i + 1:]:
            if len(later) - len(later.lstrip()) <= indent:
                break
            children.append(later.strip())
        if not [c for c in children if re.sub(r"^- (text|generic)(\s*\[[^\]]*\])*:?\s*$", "", c)]:
            return True
    return False
