"""Profile-first answers: the families of questions every form asks in its own words, answered from what the owner's
profile and resume already say, with no model call.

An application form asks perhaps thirty kinds of question ("are you fluent in X", "do you have N years of Y", "are you
authorized to work in Z", "have you worked here", "how did you hear about us", ...), each worded differently by each
employer. One resolver per kind reads the question's meaning (its parameters: the language, the country, the number of
years, the skill) and answers from evidence. If the evidence is not there, the resolver returns nothing and the question
goes on to the next layer (saved answers, the writer, the owner) -- it never guesses, and it never claims what the
resume and profile do not support.

`answer(ask)` returns (value, source), or ("", "") when no family applies or the evidence is missing.
`profile_gaps(asks)` names the profile fields that would unlock a family, so the owner is asked once, up front, instead
of being stopped mid-form.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import employment_history
import geo_reference
import option_match

_KNOWLEDGE = json.loads((Path(__file__).resolve().parent / "reference" / "knowledge.json").read_text(encoding="utf-8"))
LANGUAGES = frozenset(_KNOWLEDGE["human_languages"])
PROGRAMMING = tuple(_KNOWLEDGE["programming_languages"])
NUMBER_WORDS = _KNOWLEDGE["number_words"]
STOP = frozenset(_KNOWLEDGE["experience_stopwords"])


@dataclass
class Ask:
    question: str
    options: list[str] = field(default_factory=list)
    role: str = "textbox"                 # textbox, combobox, listbox, radio, checkbox
    profile: Any = None
    company: str = ""                     # the employer being applied to
    resume_text: str = ""
    employers: list[str] = field(default_factory=list)     # who the owner has worked for
    saved: Callable[[str], str] = lambda _q: ""            # the owner's saved answers
    container: str = ""                   # the section or text around the field: "Work Experience", "Education" ...


def clean(question: str) -> str:
    text = re.sub(r"[✱*∗⁎＊]+|\(required\)|\(optional\)|\[optional\]", " ", question or "", flags=re.IGNORECASE)
    return " ".join(text.split()).lower()


def _say(profile: Any, name: str) -> str:
    return str(getattr(profile, name, "") or "").strip()


def _yes(value: str) -> Optional[bool]:
    v = (value or "").strip().lower()
    if v in ("yes", "y", "true", "1"):
        return True
    if v in ("no", "n", "false", "0", "none"):
        return False
    return None


def pick(options: list[str], wanted: str) -> str:
    index = option_match.best_option(list(options), wanted) if options and wanted else None
    return options[index] if index is not None else ""


def yes_no(ask: Ask, truth: bool, source: str) -> Optional[tuple[str, str]]:
    """Yes or No as the form words it. A list that offers neither is not a yes/no question for this family."""
    word = "Yes" if truth else "No"
    if ask.role == "textbox" and not ask.options:
        return None                                   # a free-text box (an ID, a name, a URL) is never answered Yes or No
    if ask.options:
        label = pick(ask.options, word)
        return (label, source) if label else None
    return word, source


def _count(q: str) -> Optional[int]:
    found = re.search(r"(\d+)\s*\+?\s*(?:or more\s+)?years?", q)
    if found:
        return int(found.group(1))
    words = "|".join(NUMBER_WORDS)
    found = re.search(rf"\b({words})\b\s*\+?\s*(?:or more\s+)?years?", q)
    return NUMBER_WORDS[found.group(1)] if found else None


# ------------------------------------------------------------------------------------------------------------------
# The families. Each takes (ask, q) with q = the cleaned question, and returns (value, source) or None.
# ------------------------------------------------------------------------------------------------------------------
_EMPLOYER_NOW = re.compile(r"\b(?:current|most recent|previous|last|present)\b[^?]{0,30}\b(?:employer|company|organi[sz]ation)\b"
                           r"|\b(?:employer|company)\b[^?]{0,20}\b(?:current|most recent)\b")
_NOT_EMPLOYER_NAME = re.compile(r"\b(?:eid|id\b|phone|address|email|supervisor|manager|reason|why|salary|title|dates?|start|end|"
                                r"contact|may we|permission)\b")


def employer_name(ask: Ask, q: str):
    if ask.options or ask.role != "textbox" or not _EMPLOYER_NOW.search(q) or _NOT_EMPLOYER_NAME.search(q):
        return None
    value = _say(ask.profile, "current_employer")
    return (value, "profile.current_employer") if value else None


def names(ask: Ask, q: str):
    if ask.options or ask.role != "textbox":
        return None
    if re.search(r"\blegal\s+first\s+name\b", q):
        value = _say(ask.profile, "first_name")
        return (value, "profile.first_name") if value else None
    if re.search(r"\blegal\s+last\s+name\b", q):
        value = _say(ask.profile, "last_name")
        return (value, "profile.last_name") if value else None
    if re.search(r"\b(?:prefer\w*|go by|like us to (?:call|use)|referred to as|called)\b[^?]{0,40}\bname\b|\bname\b[^?]{0,30}\b(?:prefer|go by)\b", q) \
            and "pronounc" not in q:
        value = _say(ask.profile, "preferred_name") or _say(ask.profile, "first_name")
        return (value, "profile.preferred_name") if value else None
    return None


def current_location(ask: Ask, q: str):
    # A bare "Location" is not answered: inside a Work Experience or Education entry it is where THAT job or school
    # was, and the page does not always say which section it is in. Only a question that is plainly about the owner.
    asks_where = re.fullmatch(r"\W*(?:your|current)\s+(?:current\s+)?location(?:\s*\(city\))?\W*", q) \
        or re.search(r"\bfrom where do you (?:intend|plan|expect) to work\b|\bwhere (?:are|do) you (?:currently )?(?:located|based|live)\b", q)
    if not asks_where:
        return None
    if re.search(r"experience|employment|work history|education|school|degree|\bjobs?\b|position|employer", ask.container.lower()):
        return None                                   # inside a job or degree entry, "Location" is where THAT job was
    profile = ask.profile
    if ask.options:                                   # a list of countries or cities
        state = _say(profile, "state")
        for option in ask.options:                    # "US - Virginia": the place after the dash, never "West Virginia"
            tail = re.split(r"\s+[-–]\s+", option)[-1]
            if state and tail != option and geo_reference.same_us_state(tail, state) \
                    and geo_reference.same_country(option.split(" - ")[0], _say(profile, "country") or geo_reference.country_names("US")[0]):
                return option, "profile.current_location"
        for wanted in (_say(profile, "country"), state, _say(profile, "city")):
            label = pick(ask.options, wanted) if wanted else ""
            if label:
                return label, "profile.current_location"
        return None
    city, state, country = _say(profile, "city"), _say(profile, "state"), _say(profile, "country")
    value = ", ".join(part for part in (city, state, country) if part) or _say(profile, "current_location")
    return (value, "profile.current_location") if value else None


def available_locations(ask: Ask, q: str):
    """'In what location(s) are you available to work?' with a list of offices: the ones the owner named."""
    if not ask.options or not re.search(r"\b(?:what|which)\b[^?]{0,20}\blocations?\b[^?]{0,40}\b(?:available|willing|open)\b", q):
        return None
    places = [p.strip().lower() for p in re.split(r"[,;]", _say(ask.profile, "available_work_locations")) if p.strip()]
    places += [str(p).strip().lower() for p in (getattr(ask.profile, "locations", ()) or ())]
    chosen = []
    for option in ask.options:
        name = re.sub(r"\s*\(.*?\)", "", option).strip().lower()
        if name and any(name == p or name.startswith(p + " ") or p.startswith(name + " ") for p in places):
            chosen.append(option)
    return ("; ".join(chosen), "profile.available_work_locations") if chosen else None


_FLUENCY = re.compile(r"\b(?:fluen\w*|proficien\w*|speak|spoken|native|conversational|business[- ]level|advanced (?:level )?of?|english level)\b")


def language_fluency(ask: Ask, q: str):
    owner = {lang.lower() for lang in (getattr(ask.profile, "languages", ()) or ())}
    if not owner:
        return None
    named = [lang for lang in LANGUAGES if re.search(rf"\b{lang}\b", q)]
    if named and _FLUENCY.search(q):
        have = [lang in owner or (lang == "bahasa" and "indonesian" in owner) for lang in named]
        every = bool(re.search(r"\bboth\b|\band\b", q)) and not re.search(r"\bor\b", q)
        return yes_no(ask, all(have) if every else any(have), "profile.languages")
    if re.search(r"\blanguages?\b[^?]{0,40}\b(?:speak|fluen\w*|proficien\w*|know|spoken)\b|\bwhich languages\b", q) and ask.options:
        chosen = [o for o in ask.options if o.strip().lower() in owner]
        return ("; ".join(chosen), "profile.languages") if chosen else None
    return None


_WORK_IN = re.compile(r"\b(?:entitled|authori[sz]ed|eligible|permitted|allowed|able|right)\s+to\s+work\b")
_NATIONALITY = re.compile(r"\b(?:citizens?|citizenship|permanent residen\w+|nationals? of)\b")


def _countries(text: str) -> list[str]:
    found = []
    for place in geo_reference.places_named_in(text):
        code = geo_reference.country_code(place)
        if code and code not in found:
            found.append(code)
    return found


def country_eligibility(ask: Ask, q: str):
    profile = ask.profile
    if _WORK_IN.search(q):
        named = _countries(q)
        home = geo_reference.country_code(_say(profile, "country") or "")
        authorized = (_yes(_say(profile, "legally_eligible_to_work")) or _yes(_say(profile, "authorized_for_any_employer")))
        if not named or not home or authorized is None:
            return None
        return yes_no(ask, bool(authorized) and home in named, "profile.legally_eligible_to_work")
    if _NATIONALITY.search(q):
        named = _countries(q)
        citizen_of = geo_reference.country_code(_say(profile, "country_of_citizenship") or "")
        if named and citizen_of:
            return yes_no(ask, citizen_of in named, "profile.country_of_citizenship")
    return None


_RELATIVES = re.compile(r"\b(?:family members?|relatives?|familial|spouse|close relative)\b[^?]{0,90}\b(?:employ\w*|work\w*|vendor|supplier|business\s+partner|partner\s+(?:at|of|with))\b"
                        r"|\b(?:personal|familial)(?:\s*/\s*(?:personal|familial))?\s+relationships?\b")
_OUTSIDE = re.compile(r"\boutside business (?:activit\w+|interests?)\b|\bside business\b|\bboard roles?\b")
_WORKED_HERE = re.compile(r"\b(?:current|former|previous(?:ly)?|past)\b[^?]{0,40}\b(?:employee|employed|engaged|worked|contractor|intern)\b"
                          r"|\bemployed,? or otherwise engaged\b")


def ties_to_the_company(ask: Ask, q: str):
    profile = ask.profile
    if "government official" in q or "public official" in q:
        return None                                   # relatives who hold public office: a different fact from employment ties
    facts = []
    if _RELATIVES.search(q):
        facts.append(_yes(_say(profile, "relatives_employed_here")))
    if _OUTSIDE.search(q):
        facts.append(_yes(_say(profile, "outside_business_interests_with_competitors")))
    if facts:
        if None in facts:
            return None
        return yes_no(ask, any(facts), "profile.relatives_employed_here")
    if _WORKED_HERE.search(q):
        company = ask.company or ""
        said = employment_history.answer(ask.question, company, ask.employers)
        if not said:
            # "Are you a current MongoDB employee?", "employed, or otherwise engaged, by an Asana entity": the
            # organisation is named inside the words, so ask it as the plain question employment_history knows.
            named = re.search(r"\b(?:current|former|previous|past)\s+(?P<org>[A-Za-z0-9&.' -]{2,40}?)\s+(?:employee|contractor|intern)\b", ask.question) \
                or re.search(r"\b(?:by|with|at|for)\s+(?:an?\s+)?(?P<org>[A-Z][A-Za-z0-9&.' -]{1,40}?)\s+(?:entity|company|group|affiliate)\b", ask.question)
            if named:
                said = employment_history.answer(f"Have you worked at {named.group('org').strip()}?", company, ask.employers)
        if said in ("Yes", "No"):
            return yes_no(ask, said == "Yes", "profile.work_history")
    return None


_DEGREES = {"phd": 3, "doctorate": 3, "doctoral": 3, "master": 2, "masters": 2, "mba": 2, "bachelor": 1, "bachelors": 1}


def degree(ask: Ask, q: str):
    if _count(q) is not None or re.search(r"\bfollowing fields?\b|\bin one of\b|\bfield of study\b", q):
        return None                                   # a degree plus years, or in named fields: more than the level
    asked = [rank for word, rank in _DEGREES.items() if re.search(rf"\b{word}(?:'s)?\b", q)]
    if not asked or not re.search(r"\b(?:completed|earned|hold|have|obtained|pursuing|degree)\b", q):
        return None
    owner = [lvl.lower() for lvl, *_ in (getattr(ask.profile, "education", ()) or ())]
    if not owner:
        return None
    best = max([next((rank for word, rank in _DEGREES.items() if word in level), 0) for level in owner] or [0])
    return yes_no(ask, best >= max(asked), "profile.education") if best else None


_HEARD = re.compile(r"\b(?:how|where)\s+did\s+you\b[^?]{0,15}\b(?:hear|learn|find|come across|discover|see)\w*\b[^?]{0,40}\b(?:about|of)\b"
                    r"|\bhow\s+you\s+(?:heard|learned|found)\b")
_HEARD_ORDER = ("linkedin", "job board", "indeed", "careers", "career site", "company website", "website", "search", "online", "social", "other")


def how_heard(ask: Ask, q: str):
    if not _HEARD.search(q):
        return None
    preferred = _say(ask.profile, "how_did_you_hear") or "LinkedIn"
    if not ask.options:
        return preferred, "profile.how_did_you_hear"
    label = pick(ask.options, preferred)
    if label:
        return label, "profile.how_did_you_hear"
    for key in _HEARD_ORDER:
        for option in ask.options:
            if key in option.lower() and "referr" not in option.lower() and "employee" not in option.lower():
                return option, "profile.how_did_you_hear"
    return None


def communication(ask: Ask, q: str):
    if not re.search(r"\bhow (?:should|would|do) (?:we|you)\b[^?]{0,30}\b(?:communicate|contact|reach)\b|\bpreferred (?:method|way) of (?:contact|communication)\b", q):
        return None
    wanted = _say(ask.profile, "preferred_contact_method")
    label = pick(ask.options, wanted) if ask.options else wanted
    return (label, "profile.preferred_contact_method") if label else None


def work_arrangement(ask: Ask, q: str):
    profile = ask.profile
    if re.search(r"\b(?:plan|intend|want|prefer|wish)\s+to\s+work\s+(?:remotely|from home|from a remote)\b", q):
        arrangements = " ".join(getattr(profile, "work_arrangements", ()) or ()).lower()
        if "remote" in arrangements:
            return yes_no(ask, True, "profile.work_arrangements")
        return None
    if re.search(r"\bwilling to work (?:from|in) the office", q):
        fact = _yes(_say(profile, "willing_to_work_onsite_three_days"))
        return yes_no(ask, fact, "profile.willing_to_work_onsite_three_days") if fact is not None else None
    return None


_ALERTS = re.compile(r"\b(?:receive|get|send me|sign up for)\b[^?]{0,20}\b(?:alerts?|updates|newsletters?)\b|\bstay up to date\b|\bsimilar jobs\b")


def marketing_alerts(ask: Ask, q: str):
    if not _ALERTS.search(q):
        return None
    fact = _yes(_say(ask.profile, "marketing_opt_in"))
    return yes_no(ask, fact, "profile.marketing_opt_in") if fact is not None else None


def clearance(ask: Ask, q: str):
    if re.search(r"\b(?:ever|previously|formerly|past|used to|held|possessed|maintained)\b|\b(?:did|had)\s+you\b", q):
        return None  # Current possession does not establish clearance history.
    if re.search(r"\b(?:hold|possess|have|maintain)\b[^?]{0,30}\bclearance\b", q) and not re.search(r"\b(?:willing|able|obtain|eligible)\b", q):
        held = _say(ask.profile, "security_clearance")
        fact = _yes(held)
        return yes_no(ask, fact, "profile.security_clearance") if fact is not None else None
    return None


def programming_language(ask: Ask, q: str):
    if not re.search(r"\b(?:most proficient|favou?rite|preferred|primary|main|strongest)\b[^?]{0,25}\bprogramming language\b|\bprogramming language\b[^?]{0,40}\b(?:use|choose)\b[^?]{0,30}\binterview\b", q):
        return None
    text = (ask.resume_text or "").lower()
    counts = {lang: len(re.findall(rf"(?<![\w+#]){re.escape(lang)}(?![\w+#])", text)) for lang in PROGRAMMING}
    best = max(counts, key=counts.get) if counts else ""
    if not best or counts[best] < 1:
        return None
    return ({"golang": "Go"}.get(best, best.capitalize() if best.isalpha() else best.upper()), "resume.programming_languages")


def _terms(q: str) -> list[str]:
    phrase = re.sub(r"\([^)]*\)", " ", q)
    words = [w.strip(".-") for w in re.findall(r"[a-z0-9&+#.-]+", phrase)
             if w not in STOP and w not in NUMBER_WORDS and not re.fullmatch(r"\d+\+?", w) and len(w) > 1]
    return list(dict.fromkeys(words))


_SUFFIXES = ("ings", "ing", "ers", "er", "ed", "es", "s")


def _stem(word: str) -> str:
    """A word without its endings, the same way for the question and the resume: engineering, engineers and engineer
    meet at one stem, networking and network at another."""
    word = word.lower()
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                word, changed = word[: -len(suffix)], True
                break
    return word


def _stems(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9&+#]+", re.sub(r"[-/]", " ", (text or "").lower()))]


def _in_order(stems: list[str], tokens: list[str], skipped: int = 1) -> bool:
    for i, token in enumerate(tokens):
        if token != stems[0]:
            continue
        at, ok = i, True
        for stem in stems[1:]:
            found = next((j for j in range(at + 1, min(at + 2 + skipped, len(tokens))) if tokens[j] == stem), None)
            if found is None:
                ok = False
                break
            at = found
        if ok:
            return True
    return False


def _supported(terms: list[str], resume: str) -> bool:
    """A claim stands only when the resume itself shows it: a short phrase must appear as a phrase, in order with at
    most one word between (so 'software engineer' is not proved by a network engineer who once read about software),
    a long list by most of its terms."""
    if not terms:
        return True
    stems = _stems(" ".join(terms))
    tokens = _stems(resume)
    if not stems:
        return True
    if len(stems) <= 3:
        return _in_order(stems, tokens)
    present = set(tokens)
    return sum(1 for s in stems if s in present) / len(stems) >= 0.75


def experience(ask: Ask, q: str):
    if not re.match(r"\W*(?:do|have|did)\s+you\b", q):
        return None
    claims_work = re.match(r"\W*have you\s+(?:ever\s+)?(?:\w+ly\s+)?(?:owned|presented|partnered|supported|managed|led|built|developed|"
                           r"been accountable|delivered|designed|worked (?:with|on))\b", q) \
        or re.match(r"\W*do you\s+(?:have|possess|hold)\s+(?:an?\s+)?(?:\w+\s+)?(?:working\s+)?(?:knowledge|familiarity|expertise|"
                    r"proficiency|established)\b", q)
    if not claims_work and "experience" not in q and not re.search(r"\b\d+\s*\+?\s*years?\b", q):
        return None
    if re.search(r"\b(?:visa|sponsor|clearance|relocat|willing|authori[sz]ed|eligible|citizen|degree|accommodation|relatives?|family)\b", q):
        return None
    profile = ask.profile
    if re.search(r"\b(?:manag\w+|supervis\w+|lead\w*)\b[^?]{0,40}\b(?:employees|people|team|staff|reports)\b", q):
        managed = _say(profile, "people_managed")
        if managed:
            return yes_no(ask, managed.lower() not in ("0", "no", "none"), "profile.people_managed")
    years = _count(q)
    try:
        total = float(_say(profile, "years_experience") or 0)
    except ValueError:
        total = 0.0
    terms = _terms(q)
    if years is not None and total and years > total:
        proven = False
    else:
        proven = bool(ask.resume_text) and _supported(terms, ask.resume_text)
        if not terms and years is not None:
            proven = total >= years > 0
    if proven:
        return yes_no(ask, True, "resume.experience")
    stance = _say(profile, "unproven_experience_answer") or "No"
    if stance.lower() == "no" and ask.resume_text:
        return yes_no(ask, False, "resume.experience")        # the resume does not show it: said so, never claimed
    return None


def years_bins(ask: Ask, q: str):
    """'How many years of ... experience do you have?' with ranges on offer: the range the resume supports."""
    if not ask.options or not re.match(r"\W*how many years\b", q):
        return None
    try:
        total = float(_say(ask.profile, "years_experience") or 0)
    except ValueError:
        total = 0.0
    terms = _terms(q)
    years = total if (ask.resume_text and _supported(terms, ask.resume_text)) else 0.0
    for option in ask.options:
        text = option.lower()
        below = re.search(r"(?:less than|under|fewer than|<)\s*(\d+)", text)
        plus = re.search(r"(\d+)\s*(?:\+|or more|and (?:above|over))", text)
        span = re.search(r"(\d+)\s*(?:-|to|–)\s*(\d+)", text)
        if below and years < float(below.group(1)) or plus and years >= float(plus.group(1)) \
                or span and float(span.group(1)) <= years <= float(span.group(2)) or re.fullmatch(r"\s*(?:none|0|zero)\s*", text) and years == 0:
            return option, "resume.experience"
    return None


_ACK = re.compile(r"^\W*(?:yes,?\s+)?i\s+(?:acknowledge|agree|have read|understand|consent|confirm|certify)\b", re.IGNORECASE)
_NOT_AN_ACK = re.compile(r"\b(?:share|sell|marketing|newsletter|promotion\w*|partners?|third[- ]part\w+|arbitrat\w+|waive\w*|"
                         r"text messag\w+|sms|recurring)\b", re.IGNORECASE)


def acknowledgement(ask: Ask, q: str):
    """A list with exactly one 'I acknowledge / I agree ...' option: the owner's standing decision decides it. Privacy
    notices and terms are accepted (accept_application_privacy_prompts); a declaration that the application is true
    only when sign_attestations is on. Anything that shares or sells data, markets, waives rights (arbitration) or
    agrees to texts is never taken here."""
    if not ask.options:
        return None
    acks = [o for o in ask.options if _ACK.search(o)]
    if len(acks) != 1 or _NOT_AN_ACK.search(acks[0]) or _NOT_AN_ACK.search(q):
        return None
    if re.search(r"\b(?:certif\w+|attest\w*|true and (?:correct|accurate)|under penalty)\b", acks[0] + " " + q, re.IGNORECASE):
        return None          # a declaration about the owner is the signing gate's, which reads the statement itself
    if not _yes(_say(ask.profile, "accept_application_privacy_prompts")):
        return None
    return acks[0], "profile.accept_application_privacy_prompts"


FAMILIES = (employer_name, names, current_location, available_locations, language_fluency, country_eligibility, ties_to_the_company, degree,
            how_heard, communication, work_arrangement, marketing_alerts, clearance, programming_language, years_bins, experience, acknowledgement)


def answer(ask: Ask) -> tuple[str, str]:
    q = clean(ask.question)
    if not q:
        return "", ""
    import concept_matcher                              # who the owner is (veteran, gender, race, disability) is only ever the owner's own answer
    if concept_matcher.is_self_identification(q):
        return "", ""
    for family in FAMILIES:
        try:
            found = family(ask, q)
        except Exception:
            found = None
        if found and found[0]:
            return found
    return "", ""
