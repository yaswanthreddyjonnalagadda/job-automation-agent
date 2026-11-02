"""What a page says when the employer has received the application -- one definition, for every reader (f297).

The agent kept several lists of such wording (the check that runs while the owner presses Submit, the page reader, each
job site's module, the submission guard's probe) and they drifted apart: Fastly's receipt ("Your job application has
been received.") was known to one and missed by the others, so a submitted application stayed "ready to submit".

A receipt is a statement, in the past tense, that THIS application arrived. These do not count, and are kept out on
purpose: "thank you for your interest" alone (a create-account page opens with it: HonorHealth, recorded as a submitted
application), a future or conditional sentence ("once you submit, your application will be received"), a policy line
("applications received after 5pm are reviewed the next day"), and a receipt that goes on to tell the visitor to do
something ("Verify your email address to finish applying", f286).
"""
from __future__ import annotations

import re

# The words themselves. "job" may stand between "your" and "application" (Fastly: "Your job application has been
# received"); "applications received" in the plural, a bare "application will be received" and the like are not here.
_CORE = re.compile(
    r"(?:"
    r"(?:your|the|this)\s+(?:job\s+)?application\s+(?:has\s+been|was|is\s+now)\s+(?:successfully\s+)?"
    r"(?:received|submitted|sent)"
    r"|(?:we(?:['’]ve|\s+have)|our\s+team\s+has)\s+(?:successfully\s+)?received\s+your\s+(?:job\s+)?application"
    r"|(?:thank\s+you|thanks)\s+for\s+(?:applying|submitting\s+your\s+application|your\s+application)"
    r"|application\s+(?:received|submitted)(?:\s+successfully)?"
    r"|submission\s+received"
    r")",
    re.IGNORECASE)

# A clause that makes the statement a condition or a promise, not a fact: "once", "if", "after", "will be ...".
_NOT_YET = re.compile(r"\b(?:once|when|whenever|if|after|until|before|unless|only|will|would|should|can|may|must|"
                      r"how\s+long|how\s+do)\b", re.IGNORECASE)

# The words that say something is still to be done before the application counts.
STILL_TO_DO = re.compile(
    r"\b(to|and|then) (complete|finish|continue|submit) (your|the|this) application\b|"
    r"\bbefore (your|the) application (is|can be) (complete|submitted|considered)", re.IGNORECASE)

# A statement that opens with these (or with nothing) is the page's own: "Thank you. Your application was received."
_LEAD_IN = re.compile(r"^(?:thank\s+you|thanks|success|great|congratulations|done|all\s+set|and)?[\s,!.:;-]*$",
                      re.IGNORECASE)

# What may follow the receipt in the same block: the employer speaking about what THEY will do, or signing off. Never
# an instruction to the visitor ("Verify your email address to finish applying") -- then it is not yet a receipt (f286).
_EMPLOYER_VOICE = re.compile(
    r"^(?:we(?:['’]ll|\s+will|\s+are|\s+have)\b"
    r"|our\s+(?:\w+\s+){0,3}(?:will|team|is|are)\b"
    r"|(?:a|the|one|someone|somebody)\s+(?:\w+\s+){0,4}(?:will|be\s+in\s+touch|reach|review|contact)\b"
    r"|your\s+(?:application|resume|information|details)\s+(?:will|is|has)\b"
    r"|you(?:['’]ll|\s+will|\s+should)\s+(?:hear|receive|get|be\s+contacted|see)\b"
    r"|(?:good|best\s+of)\s+luck\b|thank(?:s|\s+you)\b|stay\s+tuned\b|we\s+appreciate\b)",
    re.IGNORECASE)
# ... and the words that mean there is still something for the visitor to do.
_ADVICE = re.compile(r"\b(?:please|verify|confirm|activate|click|log\s*in|sign\s*in|create\s+an?\s+account|"
                     r"to\s+(?:finish|complete|confirm|activate|verify))\b", re.IGNORECASE)
# Words that turn the rest of the same sentence into a condition ("... only once you finish it").
_CONDITION = re.compile(r"\b(?:only|once|until|unless|after|if|when)\b", re.IGNORECASE)


def says_received(text: str) -> bool:
    """True when the text states, as a fact, that the application was received or submitted.

    The wording must start its sentence (after at most a thank-you) and carry nothing that makes it conditional or
    future. What follows it in the same block must be the employer's own words about what they will do, or a
    sign-off: an instruction to the visitor means there is still something to do, so it is not a receipt.
    """
    text = " ".join((text or "").split())
    for found in _CORE.finditer(text):
        # The sentence this wording sits in: from the last full stop, question or exclamation mark before it.
        starts = [m.end() for m in re.finditer(r"[.!?]\s+", text[:found.start()])]
        lead = text[(starts[-1] if starts else 0):found.start()]
        if _NOT_YET.search(lead):
            continue
        if not _LEAD_IN.match(lead) and len(lead.split()) > 3:
            continue
        # The rest of this sentence ("... to our hiring team", "... and is being reviewed"), then whole sentences.
        pieces = re.split(r"(?<=[.!?])\s+", text[found.end():].strip())
        tail = re.sub(r"^[\s,;]*", "", pieces[0]) if pieces else ""
        if tail and _CONDITION.search(tail):
            continue
        if _ADVICE.search(" ".join(pieces)):
            continue
        sentences = [x for x in pieces[1:] if re.search(r"[A-Za-z]", x)]
        if all(_EMPLOYER_VOICE.match(sentence.strip()) for sentence in sentences):
            return True
    return False
