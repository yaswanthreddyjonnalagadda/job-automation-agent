# How job portals build their application fields

One JSON file per portal, from real application forms opened read-only (nothing typed, nothing submitted) on
29 September 2026, and from the agent's own page recordings. Each says how the portal builds each kind of field,
how its value shows, and how a person picks one. The agent's field handling is built from this catalogue.

| Portal | File | How studied |
|---|---|---|
| Greenhouse | greenhouse.json | live form (ePlus) |
| Ashby | ashby.json | live form (Skylo) |
| BambooHR | bamboohr.json | live form (Secunetics) |
| Lever | lever.json | live form (Palantir) |
| SmartRecruiters | smartrecruiters.json | live form (Experian) |
| Rippling | rippling.json | live form (CommandLink) |
| Breezy HR | breezy.json | live form (CASM) |
| Dayforce | dayforce.json | live form (Segra / Lumos) |
| Oracle Recruiting Cloud | oracle.json | first step only (the form is behind an emailed code) |
| Workday | workday.json | recordings (Rackspace, Aristocrat, BCBS Louisiana) + sites/workday.py |
| Avature | avature.json | recordings (Steelcase, two runs) |

Not yet studied: Workable (the live form did not open without an account), iCIMS and SAP SuccessFactors (an
account comes first), Taleo, Jobvite, Paylocity, ADP, UKG -- each to be added from the next application on it.

## The kinds of field, across portals

**Text** -- `input type=text/email/tel` and `textarea`. Labels are linked by `<label for>` (Greenhouse, Lever,
Ashby, Dayforce), by `aria-labelledby`, by a `<p>` several levels up with no link (Rippling custom questions), or
only by the placeholder (Breezy). Email is sometimes asked twice (SmartRecruiters, Dayforce).

**Single choice** -- seven builds seen:
native `<select>` (Lever, Avature) · react-select combobox (Greenhouse) · Ant Design select, virtualised
(Dayforce) · a button that opens a list: Workday 'Select One', BambooHR fab-SelectToggle (items are
`role=menuitem`, with a search box) · `div role=combobox` named only 'Select' (Rippling) · Shadow-DOM web
components (SmartRecruiters) · Yes/No as two `aria-pressed` buttons over a hidden checkbox (Ashby).

**Search and pick** (country, state, city, school, location) -- type, wait for the server's suggestions, click
one; the pick fills a hidden field or shows as a tag, and the typed text alone does not count: Lever location
(`selectedLocation`), Rippling location (`externalPlaceId`), SmartRecruiters city, Workday 'Search' prompts
('items selected' listbox), Avature Institution (a child textbox named by the choice).

**Multi-select** -- checkbox lists (Lever languages), a prompt allowing several items (Workday skills), a list
box (Avature Areas of Interest).

**Country -> State -> City** -- State's list depends on Country (Dayforce twice: address and school);
State lists include territories (BambooHR: 60 entries); 'Virginia' and 'West Virginia' both contain 'Virginia',
so a pick must be exact; country options may carry a dial code ('United States +1', Greenhouse) or a long
name ('United States of America (+1)', Workday).

**University** -- a native select of 3302 schools (Lever), a search prompt (Workday), a search combobox (Avature),
or free text (Dayforce, Breezy). An abbreviation ('JNTU') matches no list entry; the profile must hold the full
official name and campus ('Jawaharlal Nehru Technological University - Hyderabad'). 'Other - School Not Listed'
exists on some lists.

**Degree** -- levels differ per portal ('Master' vs 'Masters of Science'; 'Bachelor', 'Ph.D.'); the answer must
be mapped to the list's own wording.

**Dates** -- native date input (Avature), text 'mm/dd/yyyy' with a picker (BambooHR), Workday's composite
month/year spinbuttons (type the digits as one stream), year only for education.

**Files** -- always an `input type=file`, usually hidden behind a button or drop zone ('Attach', 'Choose File*',
'Drop or select', 'From Device', 'Upload a file'). The resume one is told apart by the section it is in, rarely by
its own words (in the agent's recordings only 4 of 30 upload buttons had 'resume' nearby). Several portals have
a second, 'autofill from resume' upload that re-fills the form (Ashby, Workday, Dayforce, Avature).

**Traps** -- honeypot fields on almost every portal: hidden textareas (Greenhouse, Ashby), 'Please leave this
field blank' (BambooHR), a box labelled 'honeypot' (Oracle), an unlabelled 'hp_…' input (Breezy). A field that is
not visible, or says to leave it blank, is never filled.

**Blocking dialogs** -- consent or cookie dialogs that take every click until answered (Dayforce SMS consent,
cookie banners). Consent is the owner's decision.

**Opening a list** -- react-select and Ant ignore key presses sent from page script; only a real mouse press (or
Playwright's click) opens them. Never press Enter to pick: it takes the first row.
