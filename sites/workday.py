"""
Workday (*.myworkdayjobs.com).

Workday's markup is addressed almost entirely through data-automation-id
attributes. The selectors live here so the reusable form logic doesn't carry
them; the multi-step wizard helpers in browser_automation.py still use them
directly and are the next thing to move.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page

from .base import SiteAdapter

logger = logging.getLogger("browser_automation")

# data-automation-id values used by the wizard helpers.
SIGN_IN_SUBMIT = "button[data-automation-id='signInSubmitButton']"
DELETE_FILE = "button[data-automation-id='delete-file']"
PROMPT_OPTION = "[data-automation-id='promptOption']"
ACTIVE_STEP = "[data-automation-id='progressBarActiveStep']"
EMAIL_INPUT = "input[data-automation-id='email']"
# Repeated Work Experience / Education entries ("workExperience-3--jobTitle").
ENTRY_ID_MARKERS = ("workexperience-", "education-", "languages-", "certification-")


def _degree_candidates(degree: str) -> list[str]:
    """Maps a degree description onto the option labels these dropdowns
    actually offer, most-specific first. Workday's list has no
    'Bachelor of Technology'/'Engineering' entry, so a BTech maps to
    Bachelor of Science (the standard equivalent) rather than the much
    looser 'Technical Degree/Diploma'. Ordering matters: a bare 'Master'
    substring otherwise matches 'Masters of Arts' first, which would put
    the wrong degree on a real application.

    The short bare forms ('Masters', 'Bachelors') come last as a fallback:
    some tenants list only those, with no field-of-study variants at all.
    """
    d = degree.lower()
    if "master" in d or d.startswith("ms") or "m.s" in d:
        return ["Masters of Science", "Master of Science", "Masters of Arts", degree, "Masters", "Master"]
    if "bachelor" in d or "btech" in d or d.startswith("bs") or "b.tech" in d:
        return ["Bachelor of Science", "Bachelor of Arts", degree, "Bachelors", "Bachelor"]
    if "doctor" in d or "phd" in d:
        return ["Doctor of Philosophy", degree, "Doctorate"]
    if "associate" in d:
        return ["Associate of Science", "Associate of Arts", degree, "Associates"]
    return [degree]


def _split_month_year(date_str: str | None) -> tuple[str, str]:
    """'02/2025' -> ('2', '2025'). Empty/malformed input -> ('', '').

    The month is deliberately NOT zero-padded: a two-character month makes
    these spinbutton widgets auto-advance to the next date part mid-write,
    and the year keystrokes then land in the wrong sub-field (observed
    producing 'MM/2812' across every date). Unpadded worked."""
    if not date_str or "/" not in date_str:
        return "", ""
    month, _, year = date_str.partition("/")
    month = month.strip()
    return (str(int(month)) if month.isdigit() else ""), (year or "").strip()

# Maps our known profile fields to likely form field identifiers (name/id/
# placeholder/label substrings, lowercased). Extend as you encounter new ATS
# platforms (Workday, Greenhouse, Lever, iCIMS, etc.).
# Order matters: _match_field returns the FIRST match, so more specific
# hints (first/middle/last name, exact "phone number") must come before
# broader ones (bare "name", bare "phone") or they'll shadow each other --
# e.g. a generic "name" hint would wrongly match "First Name" too.
_FIELD_HINTS: dict[str, list[str]] = {
    "prefix": ["prefix", "title (mr", "salutation"],
    "first_name": ["first name", "firstname", "fname"],
    "middle_name": ["middle name", "middlename"],
    # NOTE: no bare "lname" here -- it collides as a substring with
    # "schoolName" (schoo-LNAME), which put the candidate's surname into
    # Education's School field.
    "last_name": ["last name", "lastname", "surname"],
    "email": ["email"],
    # Bare "phone" is needed as well as the longer forms: Greenhouse labels the
    # field simply "Phone", and requiring "phone number" left it blank.
    "phone": ["phone number", "mobile number", "telephone number", "phone", "mobile"],
    "address_line1": ["address line 1", "street address", "address 1"],
    "city": ["city"],
    # "county" is safe alongside "country" -- neither contains the other.
    "county": ["county", "regionsubdivision"],
    "state": ["state", "province"],
    "postal_code": ["postal code", "zip code", "zipcode", "zip"],
    "location": ["location", "current location"],
    "linkedin_url": ["linkedin"],
    "portfolio_url": ["portfolio", "website", "personal site"],
    # Deliberately NO bare "name" hint here: internal field identifiers like
    # "companyName" or "schoolName" contain "name" as a substring, and a
    # bare hint wrongly matches those and overwrites them with the
    # candidate's own name instead of leaving them alone.
    # "_systemfield_name" is Ashby's fixed id for its plain "Name" field --
    # specific enough to be safe where a bare "name" is not.
    "full_name": ["full name", "your name", "applicant name", "_systemfield_name"],
}

# Word-boundary matching, not raw substring: a bare "state" hint matched
# inside "united states" ("are you legally authorized to work in the united
# states?") and nearly filled a work-authorization question with "Virginia".
# \bstate\b requires 'state' as a whole word, which "states" is not -- and
# this still correctly misses camelCase ids like "companyName" (lowercased to
# "companyname"), since there's no word boundary between "company" and "name"
# with no separator between them. One general fix for the whole bug class
# instead of removing hints one collision at a time.
_FIELD_HINT_PATTERNS: dict[str, list[re.Pattern]] = {
    key: [re.compile(rf"\b{re.escape(hint)}\b") for hint in hints]
    for key, hints in _FIELD_HINTS.items()
}


class WorkdayAdapter(SiteAdapter):
    name = "workday"
    hosts = ("myworkdayjobs.com", "myworkday.com", "wd1.myworkdayjobs", "wd103.myworkdayjobs")
    confirmation_phrases = ("your application has been submitted", "application submitted", "already applied")
    portal_list_patterns = ("/candidate_home", "/applications")

    _REGISTRATION_ERROR = re.compile(
        r"passwords? (?:do not|don't) match|please check (?:the )?box|field is required|"
        r"(?:email|password).{0,30}(?:invalid|required)",
        re.IGNORECASE,
    )
    _VERIFICATION_CODE = re.compile(
        r"(?:verification|security|one[- ]time|access)\s*code|enter\s+(?:the\s+)?code",
        re.IGNORECASE,
    )

    def candidate_account_state(self, page) -> str:
        """Classifies Workday Candidate Home screens from visible structure."""
        try:
            body = page.locator("body").inner_text(timeout=3_000) or ""
            password_count = page.locator("input[type=password]:visible").count()
        except Exception:
            return ""
        if self._VERIFICATION_CODE.search(body):
            return "verification"
        registration = bool(re.search(r"\bcreate account\b", body, re.IGNORECASE)) and (
            password_count >= 2 or bool(re.search(r"\bverify new password\b", body, re.IGNORECASE))
        )
        if registration:
            return "registration_error" if self._REGISTRATION_ERROR.search(body) else "registration"
        sign_in = password_count == 1 and (
            bool(re.search(r"\bsign in\b", body, re.IGNORECASE))
            or page.locator("form[data-automation-id^='signInForm']:visible").count() > 0
        )
        if sign_in:
            return "sign_in"
        if page.locator("[data-automation-id='progressBarActiveStep']:visible").count() > 0:
            return "application"
        if re.search(r"\b(?:candidate home|my applications)\b", body, re.IGNORECASE):
            return "candidate_home"
        return ""

    def attachment_is_empty(self, page, kind: str):
        """Workday lists an attached file with a delete button beside it."""
        if kind != "resume":
            return None
        try:
            return page.locator(DELETE_FILE).count() == 0
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Workday's repeated-entry wizard (My Experience) and its pickers.
    # Moved here from browser_automation.py: every selector below is
    # Workday's own markup, and nothing outside Workday uses these.
    # Each takes the assistant so it can use the shared browser helpers.
    # ------------------------------------------------------------------
    def fill_experience_section(self, assistant, page: Page, experiences: list[dict]) -> None:
        """Deletes any existing Work Experience entries first (so a prior
        partial/buggy fill attempt can't leave stale or mismatched data
        behind), fills fresh entries from structured resume data, then
        waits and does a final correction pass -- some sites asynchronously
        overwrite Role Description a few seconds after entry creation,
        well past any fill-time verification."""
        self.delete_all_entries(assistant, page, "Work Experience", "Education")
        to_index = 0
        for i, job in enumerate(experiences):
            self._ensure_entry_slot(assistant, page, "Work Experience", "input[id$='--jobTitle']", i)
            # Address each entry by id+index, the way education already does.
            # fill_first_matching always wrote to the FIRST matching box, so
            # with several entries on screen the values landed on the wrong
            # rows -- one role ended up tagged with another's location.
            self.fill_by_id_suffix(assistant, page, "--jobTitle", i, job.get("title", ""))
            self.fill_by_id_suffix(assistant, page, "--companyName", i, job.get("company", ""))
            self.fill_by_id_suffix(assistant, page, "--location", i, job.get("location", ""))
            # The entry's own location stays: the owner's address sweep is
            # never run on a work entry (it wrote the owner's home into each).
            start_month, start_year = _split_month_year(job.get("start", ""))
            self.fill_date_spinner(assistant, page, "workExperience", "startDate", i, start_month, start_year)
            # A repeated checkbox belongs to this job, even when the site
            # starts every entry checked. Clear former jobs before locating To.
            current = page.locator('input[id$="--currentlyWorkHere"]').nth(i)
            current.set_checked(bool(job.get("current")), timeout=5_000)
            if not job.get("current"):
                end_month, end_year = _split_month_year(job.get("end", ""))
                # 'To' only renders for non-current entries, so index among
                # those, not the overall job index.
                self.fill_date_spinner(assistant, page, "workExperience", "endDate", to_index, end_month, end_year)
                to_index += 1
            self.fill_by_id_suffix(assistant, page, "--roleDescription", i, job.get("description", ""))
        logger.info("Filled %d work experience entries", len(experiences))

        for wait_ms in (3_000, 4_000, 5_000):
            page.wait_for_timeout(wait_ms)
            for i, job in enumerate(experiences):
                self.fill_by_id_suffix(assistant, page, "--jobTitle", i, job.get("title", ""))
                self.fill_by_id_suffix(assistant, page, "--companyName", i, job.get("company", ""))
                self.fill_by_id_suffix(assistant, page, "--location", i, job.get("location", ""))
                self.fill_by_id_suffix(assistant, page, "--roleDescription", i, job.get("description", ""))
                start_month, start_year = _split_month_year(job.get("start", ""))
                if start_year and not self._date_year_is(assistant, page, "workExperience", "startDate", i, start_year):
                    self.fill_date_spinner(assistant, page, "workExperience", "startDate", i, start_month, start_year)
        logger.info("Final correction pass done for work experience")

    # What every entry of a section has on this site, to count them: a job's title box; a degree's school, drawn as a
    # text box on some tenants and as a search-and-pick list on others.
    ENTRY_PROBES = {"Work Experience": "input[id$='--jobTitle']",
                    "Education": "input[id$='--schoolName'], [data-automation-id='formField-school']"}

    def entry_count(self, page: Page, section_heading: str):
        """How many entries a jobs or degrees section shows, or None when this site has no way to tell."""
        probe = self.ENTRY_PROBES.get(section_heading)
        if not probe:
            return None
        try:
            return page.locator(probe).count()
        except Exception:
            return None

    @staticmethod
    def _school_names(assistant, school: str) -> list[str]:
        """Every name the owner has for this school, record first: the profile's own spelling of it ("JNTU" in the
        work history is "JNTU Hyderabad" in the profile) and the substitutes the owner listed (answer_alternatives).
        A list is picked only by a row matching one of them."""
        import repeated_entries
        profile = getattr(assistant, "_profile", None)
        names = [school]
        for row in (getattr(profile, "education", None) or ()):
            other = str(row[2]) if len(row) > 2 else ""
            if other and repeated_entries._identifies(other, school):
                names.append(other)
        alternatives = getattr(profile, "answer_alternatives", None) or {}
        for name in list(names):
            names += [str(a) for a in (alternatives.get(name) or [])]
        return [n for n in dict.fromkeys(n.strip() for n in names) if n]

    @staticmethod
    def _field_names(assistant, field: str) -> list[str]:
        """The recorded subject, then only the owner's approved substitutes."""
        import option_match
        alternatives = getattr(getattr(assistant, "_profile", None), "answer_alternatives", None) or {}
        candidates = [field]
        for name, values in alternatives.items():
            if option_match.plain(str(name)) == option_match.plain(field) and isinstance(values, (list, tuple)):
                candidates.extend(str(value) for value in values if str(value).strip())
        return list(dict.fromkeys(candidates))

    def fill_education_section(self, assistant, page: Page, education: list[dict]) -> None:
        self.delete_all_entries(assistant, page, "Education", "Certifications")
        # Some Workday tenants collect only school, degree and field of study.
        # Avoid a 30-second locator wait per row when this optional date control
        # is not rendered at all (as on Verizon's form).
        has_completion_year = page.locator(
            "input[data-automation-id='dateSectionYear-input'][id*='education']"
        ).count() > 0
        # The School box is a text box on some tenants (--schoolName) and a search-and-pick list on others (--school,
        # Ciena, 30 September). Counted only by the text box, every entry looked missing and "Add Another" was
        # pressed again and again (an extra, empty entry whose Degree was then "required"), and typed into, the
        # list never took the school. Entries are counted by whichever the tenant draws; a list is picked from.
        school_probe = "input[id$='--schoolName'], [data-automation-id='formField-school']"
        for i, edu in enumerate(education):
            self._ensure_entry_slot(assistant, page, "Education", school_probe, i)
            school = edu.get("school", "")
            if page.locator("input[id$='--schoolName']").count() > i:
                self.fill_by_id_suffix(assistant, page, "--schoolName", i, school)
            elif school and not self.select_from_searchable_input(assistant, page, "--school",
                                                                  self._school_names(assistant, school), i,
                                                                  keyboard=False):
                logger.info("School %r is not in this employer's list as written -- left for you", school)
            degree = edu.get("degree", "")
            if degree:
                self.select_from_button_dropdown(assistant, page, "--degree", i, _degree_candidates(degree))
            # Field of Study is a Workday searchable multiselect, not a
            # text field.  Typing into it can look correct while Workday
            # still treats it as blank; type only to filter, then commit an
            # option from the tenant's list.
            field = edu.get("field", "")
            if field:
                self.select_from_searchable_input(assistant, page, "--fieldOfStudy",
                                                  self._field_names(assistant, field), i, keyboard=False)
            if has_completion_year:
                _, end_year = _split_month_year(edu.get("end", ""))
                # Education's date part is 'lastYearAttended', not 'endDate'.
                self.fill_date_spinner(assistant, page, "education", "lastYearAttended", i, "", end_year)
        logger.info("Filled %d education entries", len(education))

        # Dates belong in the correction pass too: a year that verified as
        # correct at fill time was later found showing 2922 instead of 2022,
        # the same delayed overwrite that hits titles and company names.
        for wait_ms in (3_000, 4_000, 5_000):
            page.wait_for_timeout(wait_ms)
            for i, edu in enumerate(education):
                if page.locator("input[id$='--schoolName']").count() > i:
                    self.fill_by_id_suffix(assistant, page, "--schoolName", i, edu.get("school", ""))
                field = edu.get("field", "")
                if field and not self._multiselect_selection(page, "--fieldOfStudy", index=i):
                    self.select_from_searchable_input(assistant, page, "--fieldOfStudy",
                                                  self._field_names(assistant, field), i, keyboard=False)
                if has_completion_year:
                    _, end_year = _split_month_year(edu.get("end", ""))
                    if end_year and not self._date_year_is(assistant, page, "education", "lastYearAttended", i, end_year):
                        self.fill_date_spinner(assistant, page, "education", "lastYearAttended", i, "", end_year)
        logger.info("Final correction pass done for education")

    def _date_year_is(self, assistant, page: Page, section_key: str, date_key: str, index: int, year: str) -> bool:
        """True when a date widget's year part already reads `year` -- so the
        correction pass only re-types dates that actually drifted."""
        try:
            got = page.locator(
                f"input[data-automation-id='dateSectionYear-input']"
                f"[id*='{section_key}'][id*='{date_key}']"
            ).nth(index).input_value()
        except Exception:
            return False
        return (got or "").strip() == year

    def fill_by_id_suffix(self, assistant, page: Page, id_suffix: str, index: int, value: str, retries: int = 2) -> bool:
        """Fills the Nth field whose id ends with the given suffix (e.g.
        '--schoolName'). These custom widgets don't associate their visible
        label in a way label-text matching can find, so targeting the
        stable id is the only reliable option -- and unlike label matching
        it can't collide with an unrelated field."""
        if not value:
            return False
        locator = page.locator(f"input[id$='{id_suffix}'], textarea[id$='{id_suffix}']").nth(index)
        try:
            if locator.count() == 0:
                logger.warning("No field found with id suffix %r at index %d", id_suffix, index)
                return False
        except Exception as exc:
            logger.warning("Lookup failed for id suffix %r: %s", id_suffix, exc)
            return False
        for attempt in range(retries + 1):
            try:
                locator.fill(value, timeout=5_000)
            except Exception as exc:
                logger.warning("Could not fill %r[%d] (attempt %d): %s", id_suffix, index, attempt + 1, exc)
                continue
            page.wait_for_timeout(400)
            try:
                if locator.input_value().strip() == value.strip():
                    return True
            except Exception:
                return False
        return False

    @staticmethod
    def fill_date_segment(page, field, value: str):
        """Update one segment while preserving and verifying its composite date."""
        marker = field.get_attribute("data-automation-id") or ""
        parts = ("Month", "Day", "Year")
        chosen = next((part for part in parts if marker == f"dateSection{part}-input"), None)
        if chosen is None:
            return None
        if not str(value).isdigit():
            return False
        group = field.locator(
            'xpath=ancestor::*[count(.//input[starts-with(@data-automation-id,"dateSection")]) > 1][1]')
        if not group.count():
            return None
        segments, expected = [], []
        for part in parts:
            segment = group.locator(f'input[data-automation-id="dateSection{part}-input"]')
            if not segment.count():
                continue
            wanted = str(value) if part == chosen else segment.first.input_value().strip()
            if wanted and not wanted.isdigit():
                return False
            segments.append(segment.first)
            expected.append(wanted.zfill(4 if part == "Year" else 2) if wanted else "")
        # Three-part variants can advance focus differently from MM/YYYY.
        # Their bounded spin controls change one segment without typing into
        # the neighboring segment. Verify every part after the adjustment.
        current = field.input_value().strip()
        target = int(value)
        if not current:
            field.press("ArrowUp")
            page.wait_for_timeout(100)
            current = field.input_value().strip()
        if current.isdigit() and abs(int(current) - target) <= 60:
            for _ in range(abs(int(current) - target)):
                field.press("ArrowUp" if int(current) < target else "ArrowDown")
                current = field.input_value().strip()
                if not current.isdigit():
                    break
            actual = [segment.input_value().strip() for segment in segments]
            if all(got.lstrip("0") == want.lstrip("0") for got, want in zip(actual, expected)):
                return True
        occupied = [i for i, v in enumerate(expected) if v]
        if not occupied or any(not expected[i] for i in range(occupied[0], occupied[-1] + 1)):
            return False  # An internal gap cannot be represented by a digit stream.
        stream = "".join(expected[occupied[0]:occupied[-1] + 1])
        for _ in range(3):
            segments[occupied[0]].focus(timeout=3_000)
            page.keyboard.press("Control+a")
            page.keyboard.type(stream, delay=120)
            page.wait_for_timeout(400)
            actual = [segment.input_value().strip() for segment in segments]
            if all(got.lstrip("0") == want.lstrip("0") for got, want in zip(actual, expected)):
                return True
        return False

    def fill_date_spinner(self, assistant, page: Page, section_key: str, date_key: str, index: int, month: str = "", year: str = ""
    ) -> bool:
        """Fills a Workday-style split-spinbutton date widget -- NOT a
        single text input for 'MM/YYYY'. Each date part is its own ARIA
        spinbutton input, and they carry stable ids of the form
        '<section>-<n>--<dateKey>-dateSection<Part>-input' (e.g.
        'workExperience-299--startDate-dateSectionMonth-input').

        Selecting on those ids is far more reliable than walking from the
        visible label text: a text match like 'From' hits several elements
        per entry, so .nth(i) drifts across entries and dates land in the
        wrong rows. Pass month="" to fill only the year (Education's date
        fields have no month part).

        section_key: 'workExperience' or 'education'
        date_key:    'startDate' or 'endDate'
        """
        try:
            def part(part_name: str):
                return page.locator(
                    f"input[data-automation-id='dateSection{part_name}-input']"
                    f"[id*='{section_key}'][id*='{date_key}']"
                ).nth(index)

            year_input = part("Year")
            if year_input.count() == 0:
                logger.warning(
                    "Date spinner %s/%s[%d]: year sub-input not found", section_key, date_key, index
                )
                return False
            month_input = part("Month") if month else None

            # These segments are NOT independent inputs -- they're parts of
            # one composite date control that consumes a digit STREAM and
            # auto-advances between parts. Setting a segment's value
            # directly scrambles the others (filling the year with '2019'
            # was observed leaving month='12', year='5'). So do what a
            # person does: focus the first segment and type the whole date
            # as digits, letting the widget advance on its own.
            stream = (month.zfill(2) + year) if month else year
            first_segment = month_input if (month and month_input is not None and month_input.count() > 0) else year_input
            for attempt in range(3):
                try:
                    first_segment.focus(timeout=5_000)
                    page.keyboard.press("Control+a")
                    page.keyboard.type(stream, delay=120)
                except Exception as exc:
                    logger.warning("Date %s/%s[%d] typing failed: %s", section_key, date_key, index, exc)
                    return False
                page.wait_for_timeout(400)
                try:
                    got_year = (year_input.input_value() or "").strip()
                    got_month = (month_input.input_value() or "").strip() if month_input is not None else ""
                except Exception:
                    return False
                # The widget may render the month unpadded ('5') or padded ('05').
                month_ok = (not month) or got_month.lstrip("0") == month.lstrip("0")
                if got_year == year and month_ok:
                    return True
                logger.warning(
                    "Date %s/%s[%d] attempt %d landed as %r/%r (wanted %r/%r) -- retrying",
                    section_key, date_key, index, attempt + 1, got_month, got_year, month, year,
                )
            return False
        except Exception as exc:
            logger.warning("Could not fill date spinner %s/%s[%d]: %s", section_key, date_key, index, exc)
            return False


    def delete_all_entries(self, assistant, page: Page, section_heading: str, next_heading: str, max_deletes: int = 10) -> int:
        """Repeatedly clicks the first Delete button within a section
        (bounded so it can't wander into a later section once this one is
        empty -- checked via vertical position against next_heading,
        RECOMPUTED every iteration since deleting an entry shifts the
        layout and a position captured once before the loop goes stale)
        until none remain. Used to reset Work Experience/Education to a
        clean slate before refilling."""
        deleted = 0
        for _ in range(max_deletes):
            try:
                next_box = page.get_by_text(next_heading, exact=True).first.bounding_box()
            except Exception:
                next_box = None
            try:
                heading = page.get_by_text(section_heading, exact=True).first
                del_btn = heading.locator("xpath=following::button[contains(., 'Delete')][1]")
                if del_btn.count() == 0:
                    break
                btn_box = del_btn.bounding_box()
                if next_box and btn_box and btn_box["y"] >= next_box["y"]:
                    break  # that Delete button belongs to a later section
                del_btn.click(timeout=5_000)
                page.wait_for_timeout(1_000)
                deleted += 1
            except Exception as exc:
                logger.warning("Stopped deleting entries in %r after %d: %s", section_heading, deleted, exc)
                break
        logger.info("Deleted %d existing entries from %r", deleted, section_heading)
        return deleted

    def _ensure_entry_slot(self, assistant, page: Page, section_heading: str, probe_selector: str, index: int) -> None:
        """Makes sure entry number `index` exists, adding one only if it
        doesn't.

        Clicking Add once per entry overshoots: these forms often already
        show one blank entry (and re-add one after the last is deleted), so
        N clicks for N entries leaves an N+1th, half-filled row whose
        required fields block submission."""
        try:
            existing = page.locator(probe_selector).count()
        except Exception:
            existing = 0
        if existing > index:
            return
        for _ in range(index + 1 - existing):
            if not self.click_add_button(assistant, page, section_heading):
                break

    def click_add_button(self, assistant, page: Page, section_heading: str) -> bool:
        """Clicks the 'Add' or 'Add Another' button belonging to a specific
        section, found by walking forward from the section's heading text
        (e.g. 'Work Experience', 'Education') to the next button whose text
        contains 'Add'. Filtering on 'Add' (not just 'the next button') is
        required once entries exist: each entry has its own Delete button
        positioned closer to the heading than the actual Add/Add Another
        button, and 'next button in document order' alone would grab that
        Delete button instead -- which is exactly what happened before."""
        try:
            heading = page.get_by_text(section_heading, exact=True).first
            btn = heading.locator("xpath=following::button[contains(., 'Add')][1]")
            if btn.count() == 0 or not btn.is_visible():
                return False
            btn.click()
            page.wait_for_timeout(1_500)
            return True
        except Exception as exc:
            logger.warning("Could not click add button for section %r: %s", section_heading, exc)
            return False
        return False


    def fill_nth_matching(self, assistant, page: Page, hints: list[str], index: int, value: str, settle_ms: int = 500, retries: int = 2,
    ) -> bool:
        """Fills the Nth (0-indexed, by visual/DOM position) field matching
        hints, REGARDLESS of its current content -- for fields that get
        pre-populated with wrong or duplicated content by something other
        than our own fill calls (so 'only fill if empty' doesn't help:
        entry 2 might already be non-empty with entry 1's wrong text before
        we ever get to it). Verifies the value stuck, retrying if not."""
        if not value:
            return False
        matches = [
            el for el in page.query_selector_all("textarea, input[type='text'], input:not([type])")
            if el.is_visible() and any(h in assistant._label_for(page, el).lower() for h in hints)
        ]
        if index >= len(matches):
            return False
        el = matches[index]
        for attempt in range(retries + 1):
            try:
                el.fill(value)
            except Exception as exc:
                logger.warning("Could not fill entry %d matching %s (attempt %d): %s", index, hints, attempt + 1, exc)
                continue
            page.wait_for_timeout(settle_ms)
            try:
                current = el.input_value()
            except Exception:
                current = None
            if current is not None and current.strip() == value.strip():
                return True
        return False

    def has_experience_section(self, assistant, page: Page) -> bool:
        """True on the step offering Work Experience / Education entries."""
        try:
            return (
                page.locator("input[id$='--jobTitle'], input[id$='--schoolName']").count() > 0
                or page.get_by_text("Work Experience", exact=True).count() > 0
            )
        except Exception:
            return False

    @staticmethod
    def is_searchable_input(field) -> bool:
        """A Workday prompt can expose a textbox role while requiring a selected row."""
        return bool(field.evaluate("""e => e.tagName === 'INPUT' && (
            e.getAttribute('data-uxi-widget-type') === 'selectinput' ||
            (e.getAttribute('data-automation-id') === 'searchBox' &&
             e.closest('[data-automation-id="monikerSearchBox"]')))
        """))

    def select_skills(self, assistant, page, field, values: list[str]) -> tuple[list[str], list[str]]:
        """Search each supported skill separately and verify its committed tag."""
        import option_match
        identifier = field.get_attribute("id") or ""
        widget = field.get_attribute("data-uxi-multiselect-id") or ""
        field = page.locator(f"input[id={json.dumps(identifier)}]")
        container = field.locator('xpath=ancestor::*[@data-automation-id="multiSelectContainer"][1]')
        selected = lambda: container.locator('[data-automation-id="selectedItem"]').all_text_contents()
        missing = []
        for value in dict.fromkeys(v.strip() for v in values if v.strip()):
            if option_match.best_option(selected(), value) is not None:
                continue
            self._open_prompt(assistant, page, field, widget)
            field.fill("")
            field.type(value, delay=10)
            page.wait_for_timeout(700)
            popup = page.locator(f'[data-automation-id="responsiveMonikerPrompt"][data-associated-widget={json.dumps(widget)}]')
            rows = popup.locator('[data-automation-id="promptOption"]')
            labels = rows.all_text_contents()
            index = option_match.best_option(labels, value)
            if index is not None:
                rows.nth(index).click(timeout=3_000)
                page.wait_for_timeout(300)
            if option_match.best_option(selected(), value) is None:
                missing.append(value)
            field.fill("")
            page.keyboard.press("Escape")
        return selected(), missing

    def select_from_searchable_input(
        self, assistant, page: Page, id_suffix: str, candidates: list[str], index: int = 0,
        keyboard: bool = True,
    ) -> bool:
        """Picks a value from a type-ahead combobox (an <input> that filters
        a list as you type, e.g. 'How Did You Hear About Us?'). Types the
        candidate, then clicks the matching option -- typing alone doesn't
        register a selection on these widgets."""
        try:
            field = page.locator(f"input[id$='{id_suffix}']").nth(index)
            if field.count() == 0:
                logger.warning("Searchable input %r not found", id_suffix)
                return False

            multiselect_id = field.get_attribute("data-uxi-multiselect-id") or ""
            # Opening a prompt relocates the input and changes nth() order.
            # Keep the identity selected before opening it, not its old index.
            identifier = field.get_attribute("id") or ""
            if identifier:
                field = page.locator(f"input[id={json.dumps(identifier)}]")
            elif multiselect_id:
                field = page.locator(f"input[data-uxi-multiselect-id={json.dumps(multiselect_id)}]")

            # Never re-drive a widget that already holds a value: these
            # retries toggle selections, so a second pass over a correctly
            # filled field is how a good answer gets cleared.
            existing = self._multiselect_selection(page, id_suffix, multiselect_id, index)
            if existing:
                import option_match
                supported = any(option_match.plain(existing) == option_match.plain(candidate) for candidate in candidates)
                logger.info("%s already holds %r; supported=%s, leaving it alone", id_suffix, existing, supported)
                return supported

            for cand in candidates:
                # Keyboard first: typing already opens the list with the best
                # match highlighted, so ArrowDown+Enter commits it. Clicking
                # the option needs the list to still be open, and reopening it
                # first closed the very list that had just been populated.
                # keyboard=False (a school): only the row the matcher says IS this name is clicked, by its exact
                # label. ArrowDown+Enter takes whichever row comes first, and a suggestion that merely starts with the
                # typed words ("JNTU" -> "JNTU Kakinada") would be another university.
                strategies = ("suggestion_text", "keyboard", "prompt_option") if keyboard else ("matched_row",)
                for strategy in strategies:
                    if strategy == "prompt_option":
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(200)
                    self._open_prompt(assistant, page, field, multiselect_id)
                    field.fill("")
                    # .type() sends real keystrokes; .fill() sets the value in
                    # one shot and these widgets never run their filter, so the
                    # option list stays stale or empty.
                    field.type(cand, delay=40)
                    page.wait_for_timeout(1_200)

                    if strategy == "matched_row":
                        import option_match
                        prompt = page.locator(
                            f'[data-automation-id="responsiveMonikerPrompt"][data-associated-widget={json.dumps(multiselect_id)}]')
                        root = prompt if prompt.count() else page
                        rows = ([row.get_attribute("data-automation-label") or row.inner_text()
                                 for row in root.locator('[data-automation-id="promptOption"]').all()]
                                if prompt.count() else assistant._visible_option_texts(page))
                        k = option_match.best_option(rows, cand)
                        if k is None or page.locator(
                                f'[data-automation-id="responsiveMonikerPrompt"][data-associated-widget={json.dumps(multiselect_id)}] '
                                '[data-uxi-multiselectlistitem-type="2"]').count():
                            chosen = self._choose_scrolled_prompt(assistant, page, multiselect_id, cand)
                            if not chosen:
                                chosen = self._choose_nested_prompt(assistant, page, multiselect_id, cand)
                            if chosen and self._searchable_value_committed(
                                    assistant, page, id_suffix, chosen, multiselect_id, index, exact=True):
                                logger.info("Selected %r in %s (nested prompt)", chosen, id_suffix)
                                return True
                            continue
                        row = root.locator(f"[data-automation-id='promptOption']"
                                           f"[data-automation-label={json.dumps(rows[k])}]")
                        if not row.count() or not assistant._click_resiliently(row.first):
                            continue
                        page.wait_for_timeout(800)
                        if self._searchable_value_committed(assistant, page, id_suffix, rows[k], multiselect_id, index, exact=True):
                            logger.info("Selected %r in %s (the row that is %r)", rows[k], id_suffix, cand)
                            return True
                        continue
                    if strategy == "suggestion_text":
                        if not assistant._click_visible_suggestion(page, field, cand):
                            continue
                    elif strategy == "keyboard":
                        field.press("ArrowDown")
                        field.press("Enter")
                    else:
                        clicked = self._click_matching_option(assistant, page, field, cand, id_suffix)
                        if not clicked:
                            continue

                    page.wait_for_timeout(800)
                    if self._searchable_value_committed(assistant, page, id_suffix, cand, multiselect_id, index):
                        logger.info("Selected %r in %s (via %s)", cand, id_suffix, strategy)
                        return True
                    logger.info(
                        "Option %r appeared to select in %s but did not commit; trying next strategy",
                        cand, id_suffix,
                    )

            if not keyboard:
                page.keyboard.press("Escape")       # none of the owner's names is in the list: left for the owner
                return False
            # None of our wordings matched this tenant's list. Read what it
            # actually offers and let Claude pick the equivalent, so a new
            # employer's vocabulary doesn't need a code change.
            self._open_prompt(assistant, page, field, multiselect_id)
            field.fill("")
            page.wait_for_timeout(1_000)
            available = assistant._visible_option_texts(page)
            pick = assistant._match_option_semantically(
                candidates, available, assistant._question_text_for(page, field)
            )
            if pick:
                self._open_prompt(assistant, page, field, multiselect_id)
                field.fill("")
                field.type(pick, delay=40)
                page.wait_for_timeout(1_200)
                if self._click_matching_option(assistant, page, field, pick, id_suffix):
                    page.wait_for_timeout(800)
                    if self._searchable_value_committed(assistant, page, id_suffix, pick, multiselect_id, index):
                        logger.info("Selected %r in %s (semantic)", pick, id_suffix)
                        return True
            return False
        except Exception as exc:
            logger.warning("Searchable selection failed for %s: %s", id_suffix, exc)
            return False

    @staticmethod
    def _choose_scrolled_prompt(assistant, page, multiselect_id: str, wanted: str):
        """Read virtualized rows by scrolling this input's own popup."""
        import option_match
        prompt = page.locator(
            f'[data-automation-id="responsiveMonikerPrompt"][data-associated-widget={json.dumps(multiselect_id)}]')
        listing = prompt.locator('[data-automation-id="activeListContainer"]')
        if not listing.count():
            return None
        listing = listing.first
        listing.evaluate('e => { e.scrollTop = 0; }')
        page.wait_for_timeout(200)
        for _ in range(80):
            rows = prompt.locator('[data-automation-id="promptOption"]')
            for i in range(rows.count()):
                row = rows.nth(i)
                label = row.get_attribute('data-automation-label') or row.inner_text()
                if option_match.plain(label) != option_match.plain(wanted):
                    continue
                category = row.evaluate("e => e.closest('[data-uxi-multiselectlistitem-type]')?.getAttribute('data-uxi-multiselectlistitem-type') === '2'")
                if not category and assistant._click_resiliently(row):
                    page.wait_for_timeout(800)
                    return label
            moved = listing.evaluate("""e => { const before = e.scrollTop; e.scrollTop += Math.max(1, e.clientHeight * .8); return e.scrollTop > before; }""")
            if not moved:
                break
            page.wait_for_timeout(200)
        listing.evaluate('e => { e.scrollTop = 0; }')
        page.wait_for_timeout(200)
        return None

    @staticmethod
    def _choose_nested_prompt(assistant, page, multiselect_id: str, wanted: str):
        """Explore marked category nodes; commit only a matching offered leaf."""
        import option_match
        prompt = page.locator(
            f'[data-automation-id="responsiveMonikerPrompt"][data-associated-widget={json.dumps(multiselect_id)}]')
        if not prompt.count():
            return None
        budget = [20]

        def visit(depth):
            if depth > 4 or budget[0] <= 0:
                return None
            rows = prompt.locator('[data-automation-id="promptOption"]')
            leaves, branches = [], []
            for i in range(min(rows.count(), 80)):
                row = rows.nth(i)
                if not row.is_visible():
                    continue
                label = row.get_attribute('data-automation-label') or row.inner_text()
                kind = row.evaluate("e => e.closest('[data-uxi-multiselectlistitem-type]')?.getAttribute('data-uxi-multiselectlistitem-type')")
                (branches if kind == '2' else leaves).append(label)
            hit = option_match.best_option(leaves, wanted)
            if hit is not None:
                label = leaves[hit]
                row = prompt.locator(f'[data-automation-id="promptOption"][data-automation-label={json.dumps(label)}]')
                if row.count() and assistant._click_resiliently(row.first):
                    page.wait_for_timeout(800)
                    return label
            for label in branches:
                if budget[0] <= 0:
                    break
                budget[0] -= 1
                row = prompt.locator(f'[data-automation-id="promptOption"][data-automation-label={json.dumps(label)}]')
                if not row.count() or not assistant._click_resiliently(row.first):
                    continue
                page.wait_for_timeout(500)
                chosen = visit(depth + 1)
                if chosen:
                    return chosen
                back = prompt.locator('[data-automation-id="backButton"]')
                if not back.count() or not assistant._click_resiliently(back.first):
                    return None
                page.wait_for_timeout(300)
            return None

        return visit(0)

    def _open_prompt(self, assistant, page: Page, field, multiselect_id: str) -> None:
        """Opens a Workday multiselect's option list. Clicking the input
        alone doesn't always mount the list; the prompt icon beside it is
        the element the widget actually listens to."""
        if multiselect_id:
            icon = page.locator(
                f"[data-uxi-multiselect-id='{multiselect_id}'][data-uxi-selectinputicon-type='promptIcon']"
            ).first
            if icon.count() and assistant._click_resiliently(icon, timeout_ms=4_000):
                page.wait_for_timeout(500)
                return
        try:
            field.click(timeout=5_000)
        except Exception as exc:
            logger.warning("Could not focus multiselect input: %s", exc)

    def _click_matching_option(self, assistant, page: Page, field, cand: str, id_suffix: str) -> bool:
        """Clicks the dropdown option matching `cand`. Workday renders each
        choice as [data-automation-id='promptOption'] carrying the exact
        label in data-automation-label -- far safer than a page-wide
        [role='option'] sweep, which also picks up whichever OTHER combobox
        happens to be open (the phone-country list kept polluting this one)."""
        exact = page.locator(
            f"[data-automation-id='promptOption'][data-automation-label={json.dumps(cand)}]"
        )
        if exact.count() and assistant._click_resiliently(exact.first):
            return True

        # Scope to the listbox THIS input owns. A page-wide [role='option']
        # sweep picks up whichever other combobox happens to be open -- on one
        # form every lookup returned the phone country-dialling-code list, so a
        # yes/no question was offered 'Afghanistan +93'.
        scope = None
        for attr in ("aria-controls", "aria-owns"):
            try:
                target_id = field.get_attribute(attr)
            except Exception:
                target_id = None
            if target_id:
                candidate_scope = page.locator(f"[id={json.dumps(target_id)}]")
                if candidate_scope.count():
                    scope = candidate_scope
                    break
        if scope is None:
            nearest = field.locator("xpath=following::*[@role='listbox'][1]")
            if nearest.count():
                scope = nearest

        root = scope if scope is not None else page
        options = root.locator("[data-automation-id='promptOption']")
        if options.count() == 0:
            options = root.locator("[role='option']")

        texts = []
        for i in range(min(options.count(), 40)):
            try:
                texts.append((i, (options.nth(i).inner_text() or "").strip()))
            except Exception:
                continue

        # Exact match before substring: 'Job Board' must not win a lookup for
        # a candidate that merely contains it.
        for want_exact in (True, False):
            for i, text in texts:
                hit = text.lower() == cand.lower() if want_exact else cand.lower() in text.lower()
                if hit and assistant._click_resiliently(options.nth(i)):
                    return True

        logger.info("SEARCHABLE_OPTIONS[%s] for %r: %s", id_suffix, cand, [t for _, t in texts][:20])
        return False

    @staticmethod
    def _multiselect_selection(page: Page, id_suffix: str, multiselect_id: str = "", index: int = 0) -> str:
        """The value a Workday multiselect currently holds, as the text of
        its selected-item pill ('' when empty).

        Anchors on the container's own id rather than walking up from the
        input: while the prompt is open the input is relocated into the
        popup, so its ancestors are the popup's, not the form field's. Reads
        the pill rather than promptAriaInstruction, whose text is transient
        ('Expanded' while the menu is opening)."""
        try:
            if multiselect_id:
                box = page.locator(f"[data-uxi-element-id='{multiselect_id}']").first
                if box.count() == 0:
                    box = page.locator(f"[data-uxi-multiselect-id='{multiselect_id}']").first
            else:
                box = page.locator(f"input[id$='{id_suffix}']").nth(index)
            if box.count() == 0:
                return ""
            return (
                box.evaluate(
                    """el => {
                        const box = el.closest('[data-automation-id="multiSelectContainer"]') || el;
                        const pills = [...box.querySelectorAll('[data-automation-id="selectedItem"]')];
                        return pills.map(p => (p.innerText || '').trim()).filter(Boolean).join(', ');
                    }"""
                )
                or ""
            ).strip()
        except Exception:
            return ""

    def _searchable_value_committed(self, assistant, page: Page, id_suffix: str, cand: str, multiselect_id: str = "", index: int = 0, exact: bool = False
    ) -> bool:
        """True when the widget actually holds the value. Clicking an option
        can look successful -- no exception, no error -- while committing
        nothing, which is how a required field reached Save still empty."""
        # Escape only where it's needed (Workday, whose pill renders once the
        # popup closes). On a React combobox Escape REVERTS the input, so
        # pressing it here wiped the value that had just been selected -- the
        # verification was destroying the thing it was verifying.
        if multiselect_id:
            page.keyboard.press("Escape")
            page.wait_for_timeout(600)
        selection = self._multiselect_selection(page, id_suffix, multiselect_id, index)
        if exact:
            import option_match
            logger.info("COMMIT_CHECK[%s] selection=%r wanted=%r", id_suffix, selection, cand)
            return bool(selection) and option_match.plain(selection) == option_match.plain(cand)
        if not selection:
            # Not every combobox uses a pill; simpler ones leave the chosen
            # text in the input. But raw text is NOT proof of a committed
            # selection -- an autocomplete happily holds typed text while
            # showing 'Please enter your location', and reporting that as
            # filled is worse than reporting nothing, because it hides a
            # required field that will block submission. So only count it when
            # the widget isn't showing a validation error.
            # Accept the input's own value when it reflects the chosen option.
            # An earlier version also failed the check whenever nearby text
            # said 'required' -- but that label lingers until the field
            # revalidates, so a GOOD selection was judged a failure and the
            # retry typed over it. Matching the value against what was asked
            # for is the reliable signal; the human reviews before submitting.
            try:
                typed = (page.locator(f"input[id$='{id_suffix}']").nth(index).input_value() or "").strip()
            except Exception:
                typed = ""
            term = cand.split(",")[0].strip().lower()
            selection = typed if (typed and term and term in typed.lower()) else ""
        logger.info("COMMIT_CHECK[%s] selection=%r wanted=%r", id_suffix, selection, cand)
        # Any committed selection counts: Workday often stores a canonical
        # label ('Company Career Site') that differs from the search term.
        return bool(selection)

    @staticmethod
    def _displayed_value(page: Page, id_suffix: str) -> str:
        """What the control currently SHOWS.

        react-select style widgets clear their search input once an option is
        chosen and render the value in a sibling element, so reading the input
        reports an empty field for a correctly answered question -- which then
        triggers a retry that types over the good answer."""
        try:
            return (page.locator(f"input[id$='{id_suffix}'], button[id$='{id_suffix}']").first.evaluate(
                """el => {
                    const box = el.closest('[class*="control"], [class*="select"], div');
                    if (!box) return '';
                    const value = box.querySelector(
                        '[class*="singleValue"], [class*="multiValue"], [class*="selected"]'
                    );
                    if (value) return (value.innerText || '').trim();
                    const own = (el.value || '').trim();
                    if (own) return own;
                    const text = (box.innerText || '').trim();
                    return /^(select\\.\\.\\.|select one|choose)$/i.test(text) ? '' : text;
                }"""
            ) or "").strip()
        except Exception:
            return ""

    def select_one_option(self, assistant, page: Page, id_suffix: str, candidates: list[str]) -> bool:
        """Picks exactly ONE of the options a dropdown offers.

        The rule this enforces: a dropdown is answered by CHOOSING from its
        list, never by typing a value into it. Treating one as a text box is
        what broke the earlier attempts -- typed text sits in the search input
        looking plausible while the widget holds no value at all.

        Typing here is only ever used to filter the list; the answer is always
        a click on an offered option. Verification reads what the control
        DISPLAYS, because these widgets keep the committed value in a rendered
        element while their input clears itself after selection."""
        field = page.locator(f"input[id$='{id_suffix}'], button[id$='{id_suffix}']").first
        if field.count() == 0:
            logger.warning("Dropdown %r not found", id_suffix)
            return False

        # Only leave it alone when what's showing is actually one of the
        # answers wanted. Skipping on ANY displayed text let stale leftovers
        # ('Springfield' typed into the search box) pass as a committed answer, so
        # a required field was never filled at all.
        # Only leave it alone when what's showing IS one of the wanted answers,
        # matched exactly. Skipping on any displayed text let a stale leftover
        # ('Springfield' still sitting in the search box) pass as a committed
        # answer, so a required field went unfilled while looking done.
        already = assistant._displayed_value(page, id_suffix)
        if already and any(already.lower() == c.strip().lower() for c in candidates if c):
            logger.info("%s already shows %r; leaving it alone", id_suffix, already)
            return True

        for cand in candidates:
            try:
                assistant._click_resiliently(field, timeout_ms=4_000)
                page.wait_for_timeout(600)
                # Filter only -- this narrows the list, it is not the answer.
                if field.get_attribute("type") not in (None, "button"):
                    try:
                        field.fill("")
                        field.type(cand.split(",")[0].strip(), delay=40)
                        page.wait_for_timeout(1_000)
                    except Exception:
                        pass

                options = assistant._open_option_texts(page)
                if not options:
                    continue
                choice = self._choose_from(assistant, options, cand, id_suffix)
                if not choice:
                    continue
                if not self._click_option_by_text(assistant, page, choice):
                    continue

                page.wait_for_timeout(700)
                shown = assistant._displayed_value(page, id_suffix)
                if shown:
                    logger.info("Chose %r for %s", shown[:60], id_suffix)
                    return True
                logger.info("Clicked %r for %s but the control shows nothing", choice[:40], id_suffix)
            except Exception as exc:
                logger.warning("Could not choose an option for %s: %s", id_suffix, exc)
        return False

    def _choose_from(self, assistant, options: list[str], cand: str, id_suffix: str) -> str:
        """Exact match, then prefix, then a semantic match -- never a guess."""
        for opt in options:
            if opt.lower() == cand.lower():
                return opt
        term = cand.split(",")[0].strip().lower()
        for opt in options:
            if term and opt.lower().startswith(term):
                return opt
        return assistant._match_option_semantically([cand], options, id_suffix)

    def _click_option_by_text(self, assistant, page: Page, text: str) -> bool:
        for selector in ("[role='option']", "[role='listbox'] li", "[class*='option']"):
            loc = page.locator(selector)
            for i in range(min(loc.count(), 40)):
                el = loc.nth(i)
                try:
                    if el.is_visible() and (el.inner_text() or "").strip() == text:
                        return assistant._click_resiliently(el, timeout_ms=4_000)
                except Exception:
                    continue
        return False

    @staticmethod
    def _open_option_texts(page: Page, limit: int = 40) -> list[str]:
        """Visible option labels of whichever list is currently open."""
        texts: list[str] = []
        for selector in ("[role='option']", "[role='listbox'] li", "[class*='option']"):
            loc = page.locator(selector)
            for i in range(min(loc.count(), limit)):
                try:
                    el = loc.nth(i)
                    if not el.is_visible():
                        continue
                    text = (el.inner_text() or "").strip()
                    if text and len(text) < 120 and text not in texts:
                        texts.append(text)
                except Exception:
                    continue
            if texts:
                break
        return texts

    def select_from_button_dropdown(self, assistant, page: Page, id_suffix: str, index: int, candidates: list[str], locator=None
    ) -> bool:
        """Picks an option from a button-triggered dropdown (a <button>
        that opens a listbox, not a <select> -- so select_option() doesn't
        apply). Clicks the Nth button whose id ends with id_suffix, then
        clicks the first option whose text matches any of `candidates`
        (case-insensitive substring). Logs the available options when
        nothing matches, so the candidate list can be corrected."""
        try:
            btn = locator if locator is not None else page.locator(f"button[id$='{id_suffix}']").nth(index)
            if btn.count() == 0:
                logger.warning("No dropdown button with id suffix %r at index %d", id_suffix, index)
                return False
            question_text = assistant._question_text_for(page, btn)
            btn.click(timeout=5_000)
            page.wait_for_timeout(700)

            options = page.locator("[role='option']")
            texts = []
            for i in range(min(options.count(), 60)):
                try:
                    texts.append((i, (options.nth(i).inner_text() or "").strip()))
                except Exception:
                    continue
            logger.info("DROPDOWN_OPTIONS[%s][%d]: %s", id_suffix, index, [t for _, t in texts])
            # Exact match first, then substring -- a loose substring match
            # picked 'Masters of Arts' for 'Master', which would put a
            # factually wrong degree on a real application.
            for cand in candidates:
                for i, text in texts:
                    if cand.strip().lower() == text.strip().lower():
                        options.nth(i).click(timeout=5_000)
                        page.wait_for_timeout(500)
                        logger.info("Selected %r (exact) for %s[%d]", text, id_suffix, index)
                        return True
            for cand in candidates:
                for i, text in texts:
                    if cand.lower() in text.lower():
                        options.nth(i).click(timeout=5_000)
                        page.wait_for_timeout(500)
                        logger.info("Selected %r (substring) for %s[%d]", text, id_suffix, index)
                        return True
            # Literal matching failed, so this tenant words its options
            # differently ('Masters' where we hold 'Masters of Science').
            # Ask Claude which of the options on THIS page means the same
            # thing, rather than requiring a code change per employer.
            available = [t for _, t in texts]
            pick = assistant._match_option_semantically(candidates, available, question_text)
            if pick:
                for i, text in texts:
                    if text == pick:
                        options.nth(i).click(timeout=5_000)
                        page.wait_for_timeout(500)
                        logger.info("Selected %r (semantic) for %s[%d]", text, id_suffix, index)
                        return True

            logger.warning(
                "No option matched %s for %s[%d]. Available: %s",
                candidates, id_suffix, index, available[:25],
            )
            page.keyboard.press("Escape")
            return False
        except Exception as exc:
            logger.warning("Dropdown selection failed for %s[%d]: %s", id_suffix, index, exc)
            return False

