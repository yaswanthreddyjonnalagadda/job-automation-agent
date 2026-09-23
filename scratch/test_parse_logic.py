import sys, os, re
sys.path.insert(0, os.getcwd())
import page_agent
from page_agent import Control, _LINE, _unquote, _same_question, ANSWER_ROLES, PRESS_ROLES, OPTION_ROLES

def custom_parse_snapshot(snapshot: str) -> list[Control]:
    controls: list[Control] = []
    stack: list[tuple[int, str, object]] = []
    last_text = ""
    last_text_indent = -1
    radio_context = ""
    unclickable = None
    last_control_indent = -1

    for raw in (snapshot or "").splitlines():
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
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent_group = next((label for _i, label, _ctl in reversed(stack) if label), "")
        owner = next((ctl for _i, _l, ctl in reversed(stack) if ctl is not None), None)

        if role in ("text", "paragraph", "heading", "strong", "emphasis", "generic") and (value or name):
            candidate_text = (value or name).strip()[:200]
            if not re.fullmatch(r"\(?\s*required\s*\)?|\*", candidate_text, re.IGNORECASE):
                last_text = candidate_text
                last_text_indent = indent
            if controls and not controls[-1].name and controls[-1].role in ("radio", "checkbox", "switch") \
                    and not controls[-1].value and role == "text":
                controls[-1].name = candidate_text
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
            owner.options.append(name or value)
            if "[checked]" in attrs:
                owner.selected_option = name or value
        elif role in ("radio", "checkbox", "switch") and not ref_m and (name or value):
            unclickable = (role, name or value, "[checked]" in attrs, 6)
        if role in OPTION_ROLES and owner is not None and owner.role in ("combobox", "listbox"):
            owner.options.append(name or value)
            if "[selected]" in attrs:
                owner.selected_option = name or value

        if ref_m and (role in ANSWER_ROLES or role in PRESS_ROLES or role in OPTION_ROLES
                      or role in ("radiogroup", "group", "list", "region")):
            if role == "radio" and not (controls and controls[-1].role == "radio"):
                radio_context = last_text
            shown = value if value and not value.endswith(":") else ""
            if not shown and role in ("combobox", "listbox") and last_text and last_text_indent == indent \
                    and not _same_question(last_text, name):
                shown = last_text
            # If previous combobox tentatively adopted an upcoming field label as value, revert it
            if controls and controls[-1].role in ("combobox", "listbox") and controls[-1].value \
                    and name and _same_question(controls[-1].value, name):
                controls[-1].value = ""

            control = Control(
                ref=ref_m.group(1), role=role, name=name, value=shown,
                checked="[checked]" in attrs or "[checked=true]" in attrs, selected="[selected]" in attrs,
                disabled="[disabled]" in attrs, group=(parent_group or radio_context) if role == "radio" else "",
                container=parent_group,
                context=last_text if (not name or Control.GENERIC_NAMES.match(name.strip())
                                      or re.fullmatch(r"(?:yes|no|select one) required", name.strip(), re.IGNORECASE))
                else "")
            controls.append(control)
            last_control_indent = indent

        stack.append((indent, name if role in ("group", "radiogroup", "region") else "", control))
    return controls

snapshot = open("data/_ask_Inframark_OT_Infrastructure_Engineer_Southeast.txt", encoding="utf-8").read()

controls = custom_parse_snapshot(snapshot)
for c in controls:
    if any(k in c.question for k in ("Country", "State", "Contact", "hear")):
        print(f"Custom: {c.ref}: {c.question!r} -> answer: {c.answer!r}".encode('ascii', 'backslashreplace').decode('ascii'))
