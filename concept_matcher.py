"""concept_matcher.py -- Universal ATS Form Concept & Synonym Engine.

A 100% offline, zero-API-cost, ultra-low-RAM (<10KB) ontology engine directly
in Python that matches field labels across all ATS portals (Workday, Dayforce,
Greenhouse, Lever, Taleo, iCIMS, SuccessFactors, SmartRecruiters, Jobvite, etc.).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional


def clean_text(text: str) -> str:
    """Normalize label or question text for concept matching."""
    if not text:
        return ""
    # Split camelCase / PascalCase into separate words (e.g. VeteranFormAnswers -> Veteran Form Answers)
    t = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    t = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", t)
    t = t.lower()
    # Remove required markers and asterisks
    t = re.sub(r"\(?\s*required\s*\)?|\*", " ", t)
    # Replace non-alphanumeric (except hyphens/apostrophes) with spaces
    t = re.sub(r"[^\w\s-]", " ", t)
    # Collapse whitespace
    return " ".join(t.split())


# US State to abbreviation mapping (and vice versa)
STATE_MAP = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca",
    "colorado": "co", "connecticut": "ct", "delaware": "de", "florida": "fl", "georgia": "ga",
    "hawaii": "hi", "idaho": "id", "illinois": "il", "indiana": "in", "iowa": "ia",
    "kansas": "ks", "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms",
    "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv", "new hampshire": "nh",
    "new jersey": "nj", "new mexico": "nm", "new york": "ny", "north carolina": "nc",
    "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or", "pennsylvania": "pa",
    "rhode island": "ri", "south carolina": "sc", "south dakota": "sd", "tennessee": "tn",
    "texas": "tx", "utah": "ut", "vermont": "vt", "virginia": "va", "washington": "wa",
    "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy", "district of columbia": "dc",
}
REV_STATE_MAP = {v: k.title() for k, v in STATE_MAP.items()}


# Concepts and their matching patterns, negative guards, and container boosts
CONCEPTS: dict[str, dict[str, Any]] = {
    "FIRST_NAME": {
        "patterns": [
            r"^\W*(?:legal\s+|given\s+|applicant\s+|candidate\s+)?first\s*name\b",
            r"^\W*given\s*name\b",
            r"^\W*forename\b",
            r"^\W*pr[eé]nom\b",
            r"^\W*first\b",
        ],
        "negative": r"company|employer|school|university|reference|referr|supervisor|manager|emergency|previous|middle|last",
    },
    "LAST_NAME": {
        "patterns": [
            r"^\W*(?:legal\s+|family\s+|sur|applicant\s+|candidate\s+)?last\s*name\b",
            r"^\W*family\s*name\b",
            r"^\W*surname\b",
            r"^\W*nom\b",
            r"^\W*last\b",
        ],
        "negative": r"company|employer|school|university|reference|referr|supervisor|manager|emergency|previous|first|middle",
    },
    "MIDDLE_NAME": {
        "patterns": [
            r"^\W*(?:legal\s+)?middle\s*(?:name|initial)?\b",
            r"^\W*second\s*name\b",
        ],
        "negative": r"company|employer|school|university|reference|supervisor|manager|emergency|first|last",
    },
    "FULL_NAME": {
        "patterns": [
            r"^\W*(?:full|legal|complete|applicant|candidate)\s*(?:legal\s*)?name\b",
            r"^\W*your\s*name\b",
            r"^\W*name\s*\(\s*first\s+and\s+last\s*\)\b",
        ],
        "negative": r"company|employer|school|university|reference|supervisor|manager|emergency|contact person",
    },
    "EMAIL": {
        "patterns": [
            r"\b(?:confirm\s+)?e-?mail(?:\s*address)?\b",
            r"\belectronic\s*mail\b",
            r"\bprimary\s*e-?mail\b",
            r"\bcontact\s*e-?mail\b",
        ],
        "negative": r"employer\s*e-?mail|supervisor\s*e-?mail|company\s*e-?mail|reference\s*e-?mail",
    },
    "PHONE_MOBILE": {
        "patterns": [
            r"\b(?:mobile|cell|cellphone|primary\s*phone|contact\s*number|phone\s*number|telephone)\b",
            r"^\W*phone\W*$",
        ],
        "negative": r"home\s*phone|work\s*phone|employer\s*phone|office\s*phone|supervisor|emergency|fax",
    },
    "PHONE_COUNTRY_CODE": {
        "patterns": [
            r"\b(?:phone\s*)?(?:country|dialing|dial)\s*code\b",
        ],
        "negative": r"postal|zip",
    },
    "STREET_ADDRESS": {
        "patterns": [
            r"^\W*(?:street\s+|home\s+|mailing\s+|residential\s+)?address(?:\s*(?:line\s*)?1)?\W*$",
            r"^\W*address\s*(?:line\s*)?1\b",
            r"^\W*street\b",
        ],
        "negative": r"email|e-mail|web|employer|company|school|work|supervisor|line\s*2",
    },
    "CITY": {
        "patterns": [
            r"^\W*(?:city|town|municipality|city\s*\/\s*town)\b",
            r"\bcity\s*of\s*residence\b",
        ],
        "negative": r"employer|company|school|university|previous|supervisor",
    },
    "STATE_PROVINCE": {
        "patterns": [
            r"\b(?:state\s*\/\s*province|state\s+or\s+province|province\s*\/\s*territory|state|province|region|territory)\b",
            r"\bstate\s*of\s*residence\b",
        ],
        "negative": r"employer|company|school|university|previous|statement|united\s*states",
    },
    "POSTAL_CODE": {
        "patterns": [
            r"\b(?:zip\s*(?:code)?|postal\s*(?:code)?|postcode|pin\s*(?:code)?|pincode|zip\s*\/\s*postal)\b",
        ],
        "negative": r"employer|company|school|university|previous",
    },
    "COUNTRY": {
        "patterns": [
            r"\b(?:country\s*of\s*residence|residence\s*country|country|nation|domicile)\b",
        ],
        "negative": r"citizenship|nationality|employer|school",
    },
    "CURRENT_JOB_TITLE": {
        "patterns": [
            r"\b(?:job\s*title|position\s*title|current\s*title|current\s*position\s*title|current\s*role|role\s*title|designation|occupation|current\s*occupation|headline)\b",
            r"^\W*(?:title|position|role)\W*$",
        ],
        "negative": r"preferred|desired|supervisor|reference|manager",
        "container_boost": r"experience|employment|work history|current job|latest role|job \d|experience \d",
    },
    "CURRENT_EMPLOYER": {
        "patterns": [
            r"\b(?:employer\s*name|company\s*name|current\s*employer|current\s*company|organization\s*name|current\s*organization|workplace|business\s*name)\b",
            r"^\W*(?:employer|company|organization|firm)\W*$",
        ],
        "negative": r"worked\s*for\s*us|applied|previous|supervisor|reference|gap",
        "container_boost": r"experience|employment|work history|current job|job \d|experience \d",
    },
    "SCHOOL_UNIVERSITY": {
        "patterns": [
            r"\b(?:school\s*name|university\s*name|college\s*name|institution\s*name|educational\s*institution|institution|alma\s*mater|academy|university|college|school)\b",
            r"^\W*(?:institution\s*|school\s*|college\s*|university\s*)?name\W*$",
        ],
        "negative": r"high\s*school|elementary|secondary|middle\s*school|first\s*name|last\s*name",
        "container_boost": r"education|academic|degree|qualification|studies",
    },
    "DEGREE_LEVEL": {
        "patterns": [
            r"\b(?:highest\s*level\s*of\s*education|educational\s*attainment|education\s*achieved|degree\s*level|highest\s*degree|degree\s*type|qualification|degree|degrees)\b",
        ],
        "negative": r"major|field|subject|school|university",
        "container_boost": r"education|academic|qualification",
    },
    "MAJOR_FIELD_OF_STUDY": {
        "patterns": [
            r"\b(?:field\s*of\s*study|course\s*of\s*study|degree\s*subject|specialization|discipline|area\s*of\s*study|academic\s*major|concentration|major)\b",
        ],
        "negative": r"school|university|degree\s*level",
        "container_boost": r"education|academic|degree|qualification",
    },
    "GRADUATION_YEAR": {
        "patterns": [
            r"\b(?:graduation\s*year|year\s*of\s*graduation|year\s*completed|completion\s*year|year\s*of\s*completion|graduation\s*date)\b",
        ],
        "negative": r"high\s*school",
        "container_boost": r"education|academic",
    },
    "WORK_AUTHORIZATION": {
        "patterns": [
            r"\b(?:legally\s+(?:eligible|authori[sz]ed)|authori[sz]ed\s+to\s+work|right\s+to\s+work|eligible\s+to\s+work|work\s+authori[sz]ation|lawfully\s+authori[sz]ed)\b",
            r"authori[sz]ed to work in the (?:u\.s\.|united states) for any employer",
        ],
        "negative": r"sponsor",
    },
    "VISA_SPONSORSHIP": {
        "patterns": [
            r"\b(?:visa\s+sponsorship|require\s+sponsorship|need\s+sponsorship|future\s+sponsorship|sponsorship\s+now\s+or\s+in\s+the\s+future|sponsor\s+you\s+for\s+a\s+visa|sponsor\s+for\s+employment)\b",
            r"\bsponsor\b",
        ],
        "negative": r"legally\s+authori[sz]ed|eligible\s+to\s+work",
    },
    "CITIZENSHIP": {
        "patterns": [
            r"\b(?:citizen(?:ship)?|nationality|country\s*of\s*citizenship)\b",
        ],
        "negative": r"authorized|sponsor",
    },
    "VETERAN_STATUS": {
        "patterns": [
            r"\b(?:protected\s+veteran|veteran\s+status|military\s+service|armed\s+forces|military\s+veteran)\b",
            r"\bveteran\b",
        ],
    },
    "DISABILITY_STATUS": {
        "patterns": [
            r"\b(?:disability\s+status|physical\s+or\s+mental\s+impairment|differently\s+abled|self-identif(?:y|ication).{0,30}disab)\b",
            r"\bdisabilit(?:y|ies)\b",
        ],
    },
    "GENDER": {
        "patterns": [
            r"\b(?:gender\s*identity|sex\s*at\s*birth|biological\s*sex)\b",
            r"^\W*gender\W*$",
            r"^\W*sex\W*$",
        ],
    },
    "HISPANIC_OR_LATINO": {
        "patterns": [
            r"\b(?:hispanic\s*or\s*latino|hispanic/latino|hispanic|latino|spanish\s*origin)\b",
        ],
    },
    "ETHNICITY_RACE": {
        "patterns": [
            r"\b(?:ethnic(?:ity)?|race|racial\s*origin|ethnic\s*background)\b",
        ],
        "negative": r"hispanic|latino",
    },
    "LEGAL_AGE_18": {
        "patterns": [
            r"\b(?:at\s*least\s*18|over\s*18|eighteen\s*years|18\s*years\s*of\s*age|18\s*or\s*older|legal\s*age\s*to\s*work)\b",
        ],
    },
    "BACKGROUND_CHECK": {
        "patterns": [
            r"\b(?:pre-?employment\s+background\s+check|background\s+check|background\s+screening|submit\s+to\s+a\s+background\s+check)\b",
        ],
    },
    "DRUG_TEST": {
        "patterns": [
            r"\b(?:drug\s*(?:screen|test|screening)|substance\s*screening|physical\s*exam)\b",
        ],
    },
    "CRIMINAL_CONVICTION": {
        "patterns": [
            r"\b(?:felony|criminal\s+conviction|convicted\s+of\s+a\s+crime|criminal\s+record)\b",
        ],
    },
    "RELOCATION": {
        "patterns": [
            r"\b(?:willing\s+to\s+relocate|open\s+to\s+relocation|relocate\s+for\s+this\s+role|relocation)\b",
        ],
    },
    "TRAVEL": {
        "patterns": [
            r"\b(?:willing\s+to\s+travel|travel\s+requirements?|travel\s+(?:for|up\s+to)|travel\s+percentage)\b",
        ],
    },
    "NOTICE_PERIOD": {
        "patterns": [
            r"\b(?:available\s+to\s+start|when\s+can\s+you\s+start|notice\s+period|earliest\s+start\s+date|availability\s+to\s+start|available\s+start\s+date|target\s+start\s+date)\b",
        ],
        "negative": r"employer|company|school|university|education|work|employment|job|experience|from\s+date",
    },
    "LINKEDIN_URL": {
        "patterns": [
            r"\b(?:linked\s*in(?:\s*profile|\s*url)?)\b",
        ],
    },
    "CURRENT_JOB": {
        "patterns": [
            r"\b(?:current\s*job|currently\s*work(?:ing)?\s*here|current\s*employer|i\s*currently\s*work\s*here|presently\s*employed)\b",
        ],
    },
    "WORK_START_DATE": {
        "patterns": [
            r"\b(?:start\s*date|date\s*from|from\s*date|employment\s*start\s*date)\b",
            r"^\W*(?:start\s*date|from)\W*$",
        ],
        "container_boost": r"experience|employment|work history|job",
        "negative": r"education|school|university|degree|college|notice|availability|available",
    },
    "WORK_REASON_FOR_LEAVING": {
        "patterns": [
            r"\b(?:reason\s*for\s*leaving(?:\?)?|why\s*did\s*you\s*leave)\b",
        ],
    },
    "EDUCATION_END_DATE": {
        "patterns": [
            r"\b(?:graduation\s*date|end\s*date|completion\s*date|date\s*attended\s*to)\b",
        ],
        "container_boost": r"education|school|university|degree|college",
        "negative": r"experience|employment|work|job",
    },
    "DESIRED_SALARY": {
        "patterns": [
            r"\b(?:desired\s*(?:salary|pay|compensation)|salary\s*(?:expectation|range|requirement|desired)|compensation\s*expectation|target\s*salary)\b",
        ],
    },
    "HOW_DID_YOU_HEAR": {
        "patterns": [
            r"\b(?:how\s+did\s+you\s+hear|referral\s*source|source\s+of\s*(?:this\s+)?(?:job|application)|where\s+did\s+you\s+(?:hear|find)|how\s+did\s+you\s+learn)\b",
        ],
    },
    "APPLIED_BEFORE": {
        "patterns": [
            r"\b(?:(?:ever\s+)?(?:applied|been\s+interviewed|interviewed)\s+(?:at|with|for|here)|previously\s+applied|former\s+employee|worked\s+here\s+before)\b",
        ],
    },
    "RELATIVES_EMPLOYED": {
        "patterns": [
            r"\b(?:(?:relative|family\s*member).{0,40}(?:employ|work)|relatives\s+(?:currently\s+)?employed)\b",
        ],
    },
    "NON_COMPETE": {
        "patterns": [
            r"\b(?:non-?compete|restrictive\s+covenant|non-?solicitation|bound\s+by\s+non-?compete)\b",
        ],
    },
    "PREFERRED_CONTACT": {
        "patterns": [
            r"\b(?:preferred\s+contact\s+method|how\s+do\s+you\s+prefer\s+to\s+be\s+contacted|contact\s+preference)\b",
        ],
    },
    "TODAYS_DATE": {
        "patterns": [
            r"\b(?:today'?s?\s*date|application\s*date|current\s*date|date\s*of\s*application)\b",
        ],
    },
    "RESUME_CV": {
        "patterns": [
            r"\b(?:resume|\bcv\b|curriculum\s*vitae)\b",
        ],
        "negative": r"cover\s*letter",
    },
    "COVER_LETTER": {
        "patterns": [
            r"\b(?:cover\s*letter|covering\s*letter|letter\s*of\s*intent)\b",
        ],
    },
}


def match_concept(
    question: str,
    container: str = "",
    context: str = "",
    name: str = "",
) -> Optional[str]:
    """Identify the universal ATS concept for a given form field.

    Examines the question, container section, context, and control name.
    Applies negative filters and container boosts to disambiguate.
    """
    clean_q = clean_text(question)
    clean_c = clean_text(container)
    clean_ctx = clean_text(context)
    clean_n = clean_text(name)
    combined = f"{clean_q} {clean_c} {clean_ctx} {clean_n}".strip()

    if not combined:
        return None

    # Step 1: Specific pattern checks with negative guardrails
    best_concept = None
    best_score = 0

    for concept_name, defn in CONCEPTS.items():
        patterns = defn.get("patterns", [])
        negative = defn.get("negative")
        container_boost = defn.get("container_boost")

        # Check negative guardrails first against combined text
        if negative and re.search(negative, combined, re.IGNORECASE):
            continue

        for pat in patterns:
            # Pattern match against clean_q, clean_n, or combined
            match_q = re.search(pat, clean_q, re.IGNORECASE)
            match_n = re.search(pat, clean_n, re.IGNORECASE)
            match_comb = re.search(pat, combined, re.IGNORECASE)

            if match_q or match_n or match_comb:
                score = 10
                if match_q:
                    score += 15
                if match_n:
                    score += 10
                # Container boost gives high confidence for generic names
                # (e.g., name="Title" in container="Work Experience")
                if container_boost and re.search(container_boost, clean_c, re.IGNORECASE):
                    score += 20
                if score > best_score:
                    best_score = score
                    best_concept = concept_name

    return best_concept


def best_option_match(desired: str, options: list[str]) -> Optional[str]:
    """Find the best match from a select/dropdown's options."""
    if not desired or not options:
        return None
    desired_clean = clean_text(desired)
    desired_lower = desired.strip().lower()

    # Exact or stripped match
    for opt in options:
        clean_opt = clean_text(opt)
        if clean_opt == desired_clean or opt.strip().lower() == desired_lower:
            return opt

    # State abbreviation handling: "Virginia" vs "VA"
    if desired_lower in STATE_MAP:
        abbr = STATE_MAP[desired_lower]
        for opt in options:
            if opt.strip().lower() == abbr or clean_text(opt) == abbr:
                return opt
    if desired_lower in REV_STATE_MAP:
        full_st = REV_STATE_MAP[desired_lower].lower()
        for opt in options:
            if clean_text(opt) == full_st:
                return opt

    # Country variations: "United States" vs "United States of America" / "USA" / "US"
    if desired_lower in ("united states", "usa", "us", "united states of america"):
        for opt in options:
            clean_opt = clean_text(opt)
            if clean_opt in ("united states", "united states of america", "usa", "us"):
                return opt

    # Substring / prefix match
    for opt in options:
        clean_opt = clean_text(opt)
        if not clean_opt:
            continue
        if desired_clean.startswith(clean_opt) or clean_opt.startswith(desired_clean):
            return opt

    # Negative veteran status matching (e.g. "I am not a veteran" vs "I am not a protected veteran")
    if re.search(r"\b(?:not|no|non)[-\s]+(?:a\s+)?veteran\b", desired_lower):
        for opt in options:
            low = opt.strip().lower()
            if re.search(r"\b(?:not|no|non)[-\s]+(?:a\s+)?(?:protected\s+)?veteran\b", low) or \
               re.search(r"\bnot\s+a\s+(?:protected\s+)?veteran\b", low) or \
               re.search(r"\bdo\s+not\s+identify\s+as\s+(?:a\s+)?(?:protected\s+)?veteran\b", low):
                return opt
        for opt in options:
            low = opt.strip().lower()
            if re.fullmatch(r"no|non-?veteran|not\s+a\s+veteran", low):
                return opt

    # Negative disability matching (e.g. "No, I do not have a disability" vs long OFCCP text)
    if re.search(r"\b(?:no|not|don'?t|do\s+not)\b.*?\bdisabilit", desired_lower):
        for opt in options:
            low = opt.strip().lower()
            if re.search(r"\b(?:no|not|don'?t|do\s+not)\b.*?\bdisabilit", low):
                return opt

    # Gender matching
    if desired_lower in ("male", "man"):
        for opt in options:
            low = opt.strip().lower()
            if re.search(r"\bmale\b", low) and not re.search(r"\bfemale\b", low):
                return opt
            if low == "man":
                return opt
    elif desired_lower in ("female", "woman"):
        for opt in options:
            low = opt.strip().lower()
            if re.search(r"\bfemale\b", low) or low == "woman":
                return opt

    # Ethnicity matching (e.g. "Asian" vs "Asian (United States of America)" / "Asian (Not Hispanic or Latino)")
    if desired_lower == "asian":
        for opt in options:
            low = opt.strip().lower()
            if re.search(r"\basian\b", low) and not re.search(r"\bcaucasian\b", low):
                return opt

    # Yes / No matching
    if desired_lower in ("yes", "true", "1"):
        for opt in options:
            low = opt.strip().lower()
            if re.fullmatch(r"yes(\s*\(.*\))?|true", low) or low.startswith("yes"):
                return opt
    elif desired_lower in ("no", "false", "0"):
        for opt in options:
            low = opt.strip().lower()
            if re.fullmatch(r"no(\s*\(.*\))?|false", low) or low.startswith("no"):
                return opt

    return None


def resolve_profile_value(
    concept: str,
    profile: Any,
    options: Optional[list[str]] = None,
) -> tuple[str, str]:
    """Retrieve the value and source for a given concept from the user profile.

    Supports intelligent dropdown option normalization when options are provided.
    """
    if not concept or profile is None:
        return "", ""

    val = ""
    src = f"profile.{concept.lower()}"

    if concept == "FIRST_NAME":
        val = str(getattr(profile, "first_name", "") or "").strip()
        src = "profile.first_name"
    elif concept == "LAST_NAME":
        val = str(getattr(profile, "last_name", "") or "").strip()
        src = "profile.last_name"
    elif concept == "MIDDLE_NAME":
        val = str(getattr(profile, "middle_name", "") or "").strip()
        src = "profile.middle_name"
    elif concept == "FULL_NAME":
        val = str(getattr(profile, "full_name", "") or "").strip()
        src = "profile.full_name"
    elif concept == "EMAIL":
        val = str(getattr(profile, "email", "") or "").strip()
        src = "profile.email"
    elif concept == "PHONE_MOBILE":
        val = str(getattr(profile, "phone_mobile", "") or getattr(profile, "phone", "") or "").strip()
        src = "profile.phone_mobile"
    elif concept == "PHONE_COUNTRY_CODE":
        val = str(getattr(profile, "phone_country_code", "") or "+1").strip()
        src = "profile.phone_country_code"
    elif concept == "STREET_ADDRESS":
        val = str(getattr(profile, "address_line1", "") or "").strip()
        src = "profile.address_line1"
    elif concept == "CITY":
        val = str(getattr(profile, "city", "") or "").strip()
        src = "profile.city"
    elif concept == "STATE_PROVINCE":
        val = str(getattr(profile, "state", "") or "").strip()
        src = "profile.state"
    elif concept == "POSTAL_CODE":
        val = str(getattr(profile, "postal_code", "") or "").strip()
        src = "profile.postal_code"
    elif concept == "COUNTRY":
        val = str(getattr(profile, "country", "") or "").strip()
        src = "profile.country"
    elif concept == "CURRENT_JOB_TITLE":
        val = str(getattr(profile, "current_position_title", "") or "").strip()
        src = "profile.current_position_title"
    elif concept == "CURRENT_EMPLOYER":
        val = str(getattr(profile, "current_employer", "") or "").strip()
        src = "profile.current_employer"
    elif concept == "SCHOOL_UNIVERSITY":
        education = getattr(profile, "education", ()) or ()
        if education and len(education[0]) > 2:
            val = education[0][2]
            src = "profile.education.school"
    elif concept == "DEGREE_LEVEL":
        education = getattr(profile, "education", ()) or ()
        if education and len(education[0]) > 0:
            val = education[0][0]
            src = "profile.education.degree"
    elif concept == "MAJOR_FIELD_OF_STUDY":
        education = getattr(profile, "education", ()) or ()
        if education and len(education[0]) > 1:
            val = education[0][1]
            src = "profile.education.major"
    elif concept == "GRADUATION_YEAR":
        education = getattr(profile, "education", ()) or ()
        if education and len(education[0]) > 3:
            val = education[0][3]
            src = "profile.education.graduation_year"
    elif concept == "WORK_AUTHORIZATION":
        val = str(getattr(profile, "authorized_for_any_employer", "") or getattr(profile, "legally_eligible_to_work", "Yes")).strip()
        src = "profile.authorized_for_any_employer"
    elif concept == "VISA_SPONSORSHIP":
        sponsorship = getattr(profile, "requires_visa_sponsorship", False)
        val = "Yes" if sponsorship else "No"
        src = "profile.requires_visa_sponsorship"
    elif concept == "CITIZENSHIP":
        val = str(getattr(profile, "country_of_citizenship", "") or "").strip()
        src = "profile.country_of_citizenship"
    elif concept == "VETERAN_STATUS":
        val = str(getattr(profile, "veteran_status", "") or "").strip()
        src = "profile.veteran_status"
    elif concept == "DISABILITY_STATUS":
        val = str(getattr(profile, "disability_status", "") or "").strip()
        src = "profile.disability_status"
    elif concept == "GENDER":
        val = str(getattr(profile, "gender", "") or "").strip()
        src = "profile.gender"
    elif concept == "HISPANIC_OR_LATINO":
        val = str(getattr(profile, "hispanic_or_latino", "") or "No").strip()
        src = "profile.hispanic_or_latino"
        if options:
            matched_opt = best_option_match(val, options)
            if matched_opt:
                return matched_opt, src
            val_lower = val.lower()
            if val_lower in ("no", "not hispanic or latino", "not hispanic/latino", "not hispanic"):
                for opt in options:
                    if re.search(r"\bnot\s+hispanic\b|\bno\b", opt, re.IGNORECASE) and not re.search(r"\byes\b", opt, re.IGNORECASE):
                        return opt, src
            elif val_lower in ("yes", "hispanic or latino", "hispanic/latino", "hispanic"):
                for opt in options:
                    if re.search(r"\byes\b|^hispanic\b", opt, re.IGNORECASE) and not re.search(r"\bnot\b", opt, re.IGNORECASE):
                        return opt, src
    elif concept == "ETHNICITY_RACE":
        val = str(getattr(profile, "ethnicity", "") or "").strip()
        src = "profile.ethnicity"
    elif concept == "LEGAL_AGE_18":
        val = "Yes" if getattr(profile, "at_least_18", True) else "No"
        src = "profile.at_least_18"
    elif concept == "BACKGROUND_CHECK":
        val = str(getattr(profile, "willing_to_submit_to_pre_employment_background_check", "Yes") or "Yes").strip()
        src = "profile.willing_to_submit_to_pre_employment_background_check"
    elif concept == "DRUG_TEST":
        val = "Yes" if getattr(profile, "willing_drug_test_and_physical", True) else "No"
        src = "profile.willing_drug_test_and_physical"
    elif concept == "CRIMINAL_CONVICTION":
        val = "No" if not getattr(profile, "felony_conviction", False) else "Yes"
        src = "profile.felony_conviction"
    elif concept == "RELOCATION":
        val = "Yes" if getattr(profile, "open_to_relocation", True) else "No"
        src = "profile.open_to_relocation"
    elif concept == "TRAVEL":
        val = str(getattr(profile, "willing_to_travel", "") or "").strip()
        src = "profile.willing_to_travel"
    elif concept == "NOTICE_PERIOD":
        val = str(getattr(profile, "availability_to_start", "") or "").strip()
        src = "profile.availability_to_start"
    elif concept == "DESIRED_SALARY":
        minimum = str(getattr(profile, "salary_min", "") or "").strip()
        maximum = str(getattr(profile, "salary_max", "") or "").strip()
        if minimum and maximum:
            val = f"${int(float(minimum)):,} - ${int(float(maximum)):,}"
        elif minimum:
            val = str(minimum)
        src = "profile.salary"
    elif concept == "HOW_DID_YOU_HEAR":
        val = str(getattr(profile, "how_did_you_hear", "") or "LinkedIn").strip()
        src = "profile.how_did_you_hear"
    elif concept == "APPLIED_BEFORE":
        val = "No" if not getattr(profile, "applied_here_before", False) else "Yes"
        src = "profile.applied_here_before"
    elif concept == "RELATIVES_EMPLOYED":
        val = str(getattr(profile, "relatives_employed_here", "No") or "No").strip()
        src = "profile.relatives_employed_here"
    elif concept == "NON_COMPETE":
        val = str(getattr(profile, "bound_by_non_compete", "No") or "No").strip()
        src = "profile.bound_by_non_compete"
    elif concept == "PREFERRED_CONTACT":
        val = str(getattr(profile, "preferred_contact_method", "Email") or "Email").strip()
        src = "profile.preferred_contact_method"
    elif concept == "LINKEDIN_URL":
        val = str(getattr(profile, "linkedin_url", "") or "").strip()
        src = "profile.linkedin_url"
    elif concept == "CURRENT_JOB":
        val = "Yes"
        src = "profile.current_job"
    elif concept == "WORK_START_DATE":
        dates = str(getattr(profile, "current_employment_dates", "") or "").strip()
        val = dates.split("-")[0].strip() if "-" in dates else (dates or "February 2025")
        src = "profile.current_employment_dates"
    elif concept == "WORK_REASON_FOR_LEAVING":
        val = str(getattr(profile, "reason_for_leaving", "") or "Contract ending").strip()
        src = "profile.reason_for_leaving"
    elif concept == "EDUCATION_END_DATE":
        ed_dates = getattr(profile, "education_dates", ()) or ()
        if ed_dates and len(ed_dates[0]) > 2:
            val = ed_dates[0][2]
            src = "profile.education_dates"
        else:
            val = "December 2022"
            src = "profile.education.end_date"
    elif concept == "TODAYS_DATE":
        val = date.today().isoformat()
        src = "profile.application_date"

    if val and options:
        matched_opt = best_option_match(val, options)
        if matched_opt:
            return matched_opt, src

    return val, src
