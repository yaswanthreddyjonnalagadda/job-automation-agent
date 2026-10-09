"""Durable local state and fail-closed submission dispatch tests."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import safety
from jd_analyzer import dedup_key_for_url, submission_effect_key_for_url
from job_tracker import JobTracker
from submission_guard import SubmissionGuardV0, SubmissionProbe


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def setup(tmp_path, browser):
    database = tmp_path / "applications.db"
    tracker = JobTracker(database)
    tracker.create(
        dedup_key="application-identity",
        title="Synthetic Engineer",
        company="Synthetic Employer",
        url="https://jobs.example.test/application/42",
    )
    context = browser.new_context()
    page = context.new_page()
    page.set_content("""
      <form onsubmit="window.submitted = (window.submitted || 0) + 1; return false">
        <button type="submit">Submit Application</button>
      </form>
    """)
    guard = SubmissionGuardV0(context)
    guard.activate(page)
    yield tracker, database, context, page, guard
    context.close()


def authorized_decision(tmp_path):
    screenshot = tmp_path / "authorization.png"
    html = tmp_path / "authorization.html"
    screenshot.write_bytes(b"synthetic screenshot")
    html.write_text("<html>synthetic evidence</html>", encoding="utf-8")
    return safety.AutoSubmitDecision(
        eligible=True,
        field_comparisons=[
            safety.FieldComparison("Name", "Synthetic Owner", "Synthetic Owner", "profile.name", True)
        ],
        evidence_paths={"screenshot": str(screenshot), "html": str(html)},
    )


class ClickSpy:
    def __init__(self, locator, before_click=lambda: None):
        self.locator = locator
        self.before_click = before_click
        self.clicks = 0

    def evaluate(self, *args):
        return self.locator.evaluate(*args)

    def click(self, **kwargs):
        self.before_click()
        self.clicks += 1
        return self.locator.click(**kwargs)


def test_dispatched_and_event_are_committed_before_browser_effect(setup, tmp_path):
    tracker, _database, _context, page, guard = setup
    button = page.get_by_role("button", name="Submit Application")
    spy = ClickSpy(button, lambda: (
        _assert_dispatch_committed(tracker),
    ))

    assert guard.submit_verified(
        page, spy, authorized_decision(tmp_path), tracker=tracker, dedup_key="application-identity"
    )
    assert spy.clicks == 1
    assert page.evaluate("window.submitted") == 1
    assert tracker.get_submission_effect_state("application-identity") == "DISPATCHED"


def _assert_dispatch_committed(tracker):
    assert tracker.get_submission_effect_state("application-identity") == "DISPATCHED"
    kinds = [event["kind"] for event in tracker.submission_safety_events("application-identity")]
    assert kinds[-2:] == ["SUBMISSION_AUTHORIZED", "SUBMISSION_DISPATCHED"]


def test_database_write_failure_rolls_back_and_prevents_browser_effect(setup, tmp_path, monkeypatch):
    tracker, _database, _context, page, guard = setup
    append = tracker._submission_event

    def fail_dispatch_event(conn, dedup_key, kind, payload):
        if kind == "SUBMISSION_DISPATCHED":
            raise sqlite3.OperationalError("database is locked")
        append(conn, dedup_key, kind, payload)

    monkeypatch.setattr(tracker, "_submission_event", fail_dispatch_event)
    spy = ClickSpy(page.get_by_role("button", name="Submit Application"))

    assert not guard.submit_verified(
        page, spy, authorized_decision(tmp_path), tracker=tracker, dedup_key="application-identity"
    )
    assert spy.clicks == 0
    assert page.evaluate("window.submitted") is None
    assert tracker.get_submission_effect_state("application-identity") is None
    assert tracker.submission_safety_events("application-identity")[-1]["kind"] == \
        "SUBMISSION_STORAGE_FAILURE"


def test_sqlite_database_lock_prevents_browser_effect(setup, tmp_path, monkeypatch):
    tracker, database, _context, page, guard = setup
    original_connect = sqlite3.connect
    lock = original_connect(str(database), timeout=0.1)
    lock.execute("BEGIN EXCLUSIVE")
    monkeypatch.setattr(
        sqlite3, "connect",
        lambda *args, **kwargs: original_connect(*args, **{**kwargs, "timeout": 0.01}),
    )
    spy = ClickSpy(page.get_by_role("button", name="Submit Application"))

    try:
        assert not guard.submit_verified(
            page, spy, authorized_decision(tmp_path), tracker=tracker, dedup_key="application-identity"
        )
        assert spy.clicks == 0
        assert page.evaluate("window.submitted") is None
    finally:
        lock.rollback()
        lock.close()
    assert tracker.get_submission_effect_state("application-identity") is None


def test_persisted_authorized_state_is_revalidated_before_dispatch(setup, tmp_path):
    tracker, _database, _context, page, guard = setup
    with tracker._connect() as connection:
        connection.execute(
            "INSERT INTO submission_effects (dedup_key, state, updated_at) "
            "VALUES (?, 'AUTHORIZED', '2026-10-06T00:00:00+00:00')",
            ("application-identity",),
        )
    spy = ClickSpy(page.get_by_role("button", name="Submit Application"))

    assert guard.submit_verified(
        page, spy, authorized_decision(tmp_path), tracker=tracker,
        dedup_key="application-identity", application_tracker=tracker,
    )
    assert spy.clicks == 1
    assert tracker.get_submission_effect_state("application-identity") == "DISPATCHED"


def test_invalid_authorization_does_not_promote_authorized_state(setup):
    tracker, _database, _context, page, guard = setup
    with tracker._connect() as connection:
        connection.execute(
            "INSERT INTO submission_effects (dedup_key, state, updated_at) "
            "VALUES (?, 'AUTHORIZED', '2026-10-06T00:00:00+00:00')",
            ("application-identity",),
        )
    spy = ClickSpy(page.get_by_role("button", name="Submit Application"))

    assert not guard.submit_verified(
        page, spy, True, tracker=tracker, dedup_key="application-identity",
        application_tracker=tracker,
    )
    assert spy.clicks == 0
    assert tracker.get_submission_effect_state("application-identity") == "AUTHORIZED"


@pytest.mark.parametrize("terminal_state", ["DISPATCHED", "CONFIRMED", "UNCERTAIN"])
def test_terminal_state_survives_tracker_and_browser_context_recreation(
    setup, tmp_path, browser, terminal_state
):
    tracker, database, context, page, _guard = setup
    tracker.begin_submission_dispatch("application-identity")
    if terminal_state != "DISPATCHED":
        tracker.finish_submission_effect("application-identity", terminal_state)

    recreated_tracker = JobTracker(database)
    if terminal_state == "DISPATCHED":
        import apply_flow
        assert apply_flow.refuse_submission_replay(
            recreated_tracker, "application-identity", "simulated_restart"
        ) == "UNCERTAIN"
    new_context = browser.new_context()
    try:
        new_page = new_context.new_page()
        new_page.set_content("""
          <form onsubmit="window.submitted = true; return false">
            <button type="submit">Submit Application</button>
          </form>
        """)
        recreated_guard = SubmissionGuardV0(new_context)
        recreated_guard.activate(new_page)
        spy = ClickSpy(new_page.get_by_role("button", name="Submit Application"))

        assert not recreated_guard.submit_verified(
            new_page, spy, authorized_decision(tmp_path), tracker=recreated_tracker,
            dedup_key="application-identity",
        )
        assert spy.clicks == 0
        assert new_page.evaluate("window.submitted") is None
        expected_state = "UNCERTAIN" if terminal_state == "DISPATCHED" else terminal_state
        assert recreated_tracker.get_submission_effect_state("application-identity") == expected_state
        assert recreated_tracker.submission_safety_events("application-identity")[-1]["kind"] == \
            "SUBMISSION_REPLAY_BLOCKED"
    finally:
        new_context.close()


def test_unknown_state_cannot_be_reset_or_reauthorized(setup):
    tracker, _database, _context, _page, _guard = setup
    tracker.begin_submission_dispatch("application-identity")
    tracker.finish_submission_effect("application-identity", "UNCERTAIN")

    with pytest.raises(ValueError, match="Invalid submission outcome"):
        tracker.finish_submission_effect("application-identity", "DISPATCHED")
    with pytest.raises(RuntimeError, match="replay blocked"):
        tracker.begin_submission_dispatch("application-identity")
    assert tracker.get_submission_effect_state("application-identity") == "UNCERTAIN"


def test_restart_reclassifies_unresolved_dispatch_as_uncertain(setup):
    import apply_flow

    tracker, _database, _context, _page, _guard = setup
    tracker.begin_submission_dispatch("application-identity")

    assert apply_flow.refuse_submission_replay(
        tracker, "application-identity", "simulated_restart"
    ) == "UNCERTAIN"
    assert tracker.get_submission_effect_state("application-identity") == "UNCERTAIN"


def test_a_crash_right_after_dispatch_blocks_a_resumed_runs_second_submit_attempt(setup, tmp_path):
    """Cross-phase matrix cell B (B1 x B3, Phase 0 final review): the FULL resume path through
    `apply_flow.submit_verified`, not only the tracker/guard unit level already covered by
    `test_restart_reclassifies_unresolved_dispatch_as_uncertain` and
    `test_retry_after_dispatch_cannot_issue_a_second_click`. Simulates a crash immediately after
    `begin_submission_dispatch` -- no confirmation is ever recorded -- then a freshly-constructed
    assistant (standing in for a resumed run) calls `apply_flow.submit_verified` again. It must
    be refused by `refuse_submission_replay` before any click is attempted, never issue a second
    dispatch."""
    import apply_flow
    import safety

    tracker, _database, _context, _page, _guard = setup
    tracker.begin_submission_dispatch("application-identity")  # the crash happens right here

    class ResumedRunAssistant:
        def click_verified_submit(self, page, authorization):
            raise AssertionError("a resumed run must never re-click submit after an unresolved dispatch")

    job = SimpleNamespace(title="Synthetic Engineer", company="Synthetic Employer")
    ok = apply_flow.submit_verified(
        ResumedRunAssistant(), None, tracker, "application-identity", job, tmp_path,
        safety.AutoSubmitDecision(eligible=True),
    )

    assert ok is False
    assert tracker.get_submission_effect_state("application-identity") == "UNCERTAIN"


def test_confirmation_requires_dispatch_and_uncertain_confirmation_requires_probe_reconciliation(setup):
    tracker, _database, _context, _page, _guard = setup

    with pytest.raises(RuntimeError):
        tracker.finish_submission_effect("application-identity", "CONFIRMED")
    tracker.begin_submission_dispatch("application-identity")
    tracker.finish_submission_effect("application-identity", "UNCERTAIN")
    with pytest.raises(RuntimeError):
        tracker.finish_submission_effect("application-identity", "CONFIRMED")
    tracker.finish_submission_effect("application-identity", "CONFIRMED", reconciled=True)
    assert tracker.get_submission_effect_state("application-identity") == "CONFIRMED"


def test_retry_after_dispatch_cannot_issue_a_second_click(setup, tmp_path):
    tracker, _database, _context, page, guard = setup
    button = page.get_by_role("button", name="Submit Application")
    first = ClickSpy(button)
    decision = authorized_decision(tmp_path)

    assert guard.submit_verified(page, first, decision, tracker=tracker, dedup_key="application-identity")
    second = ClickSpy(button)
    assert not guard.submit_verified(page, second, decision, tracker=tracker, dedup_key="application-identity")
    assert first.clicks == 1 and second.clicks == 0
    assert page.evaluate("window.submitted") == 1


def test_effect_identity_ignores_known_tracking_and_url_format_variants():
    urls = (
        "https://jobs.example.test/apply/42?job=7&team=network",
        "HTTPS://JOBS.EXAMPLE.TEST/apply/42/?utm_source=board&team=network&job=7#apply",
        "http://jobs.example.test/apply/42?team=network&job=7",
    )
    assert len({submission_effect_key_for_url(url) for url in urls}) == 1
    assert submission_effect_key_for_url(
        "https://jobs.example.test/apply/43?job=7&team=network"
    ) != submission_effect_key_for_url(urls[0])


def test_effect_identity_requires_a_specific_absolute_application_url():
    with pytest.raises(ValueError, match="specific posting"):
        submission_effect_key_for_url("https://jobs.example.test/")
    with pytest.raises(ValueError, match="valid application URL"):
        submission_effect_key_for_url("not a URL")


def test_prior_application_alias_for_normalized_url_is_found(setup):
    tracker, _database, _context, _page, _guard = setup
    assert tracker.submission_keys_for_url(
        "http://JOBS.example.test/application/42/?utm_source=listing"
    ) == ["application-identity"]


def test_legacy_effect_alias_blocks_submit_for_url_variant(setup, tmp_path):
    tracker, _database, _context, page, guard = setup
    legacy_url = "https://jobs.example.test/application/42/?utm_source=listing"
    legacy_key = dedup_key_for_url(legacy_url)
    effect_key = submission_effect_key_for_url(
        "https://jobs.example.test/application/42"
    )
    tracker.begin_submission_dispatch(legacy_key)
    spy = ClickSpy(page.get_by_role("button", name="Submit Application"))

    assert not guard.submit_verified(
        page,
        spy,
        authorized_decision(tmp_path),
        tracker=tracker,
        dedup_key=effect_key,
        application_tracker=tracker,
        application_key="application-identity",
        aliases=[legacy_key],
    )
    assert spy.clicks == 0
    assert tracker.get_submission_effect_state(legacy_key) == "DISPATCHED"
    assert tracker.get_submission_effect_state(effect_key) is None


def test_submission_event_metadata_is_kind_and_field_allowlisted(setup):
    tracker, _database, _context, _page, _guard = setup
    with pytest.raises(ValueError, match="Invalid metadata"):
        tracker.record_submission_safety_event(
            "application-identity",
            "SUBMISSION_BLOCKED",
            {"reason": "click", "candidate_profile": {"name": "Synthetic"}},
        )
    with pytest.raises(ValueError, match="Invalid submission safety event"):
        tracker.record_submission_safety_event(
            "application-identity", "SUBMISSION_CUSTOM", {"reason": "anything"}
        )


@pytest.mark.parametrize(("evidence", "expected_state", "expected_result"), [
    (None, "UNCERTAIN", False),
    ("the page confirmed it", "CONFIRMED", True),
])
def test_post_click_result_is_durably_uncertain_or_confirmed(
    setup, tmp_path, evidence, expected_state, expected_result
):
    import apply_flow

    tracker, _database, _context, page, guard = setup
    decision = authorized_decision(tmp_path)
    button = page.get_by_role("button", name="Submit Application")

    class Assistant:
        def click_verified_submit(self, _page, authorization):
            return guard.submit_verified(
                page, button, authorization, tracker=tracker,
                dedup_key="application-identity", application_tracker=tracker,
            )

        def wait_for_submission_evidence(self, _page, _job_title):
            return evidence

    class Job:
        title = "Synthetic Engineer"
        company = "Synthetic Employer"

    assistant = Assistant()
    assistant.submission_state_store = tracker
    assistant.tracker = tracker
    assistant.application_key = "application-identity"
    result = apply_flow.submit_verified(
        assistant, page, tracker, "application-identity", Job(), tmp_path, decision
    )
    assert result is expected_result
    assert tracker.get_submission_effect_state("application-identity") == expected_state
    terminal_event = tracker.submission_safety_events("application-identity")[-1]
    assert terminal_event["kind"] == f"SUBMISSION_{expected_state}"
    if expected_state == "CONFIRMED":
        assert terminal_event["payload"]["evidence_kind"] == "independent_page_or_email"
        assert tracker.get("application-identity").status == "submitted"
    else:
        assert tracker.get("application-identity").status == "needs_user_review"


def test_application_deletion_does_not_delete_submission_replay_record(setup):
    tracker, _database, _context, _page, _guard = setup
    tracker.begin_submission_dispatch("application-identity")
    tracker.delete("application-identity")
    tracker.create(
        dedup_key="application-identity", title="Synthetic Engineer", company="Synthetic Employer"
    )
    with pytest.raises(RuntimeError, match="replay blocked"):
        tracker.begin_submission_dispatch("application-identity")
    assert tracker.get_submission_effect_state("application-identity") == "DISPATCHED"


def test_concurrent_invocations_allow_only_one_durable_dispatch(setup):
    tracker, database, _context, _page, _guard = setup
    other_tracker = JobTracker(database)

    def begin(store):
        try:
            return store.begin_submission_dispatch("application-identity")
        except RuntimeError:
            return "BLOCKED"

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(begin, (tracker, other_tracker)))

    assert sorted(results) == ["BLOCKED", "DISPATCHED"]
    assert tracker.get_submission_effect_state("application-identity") == "DISPATCHED"
    kinds = [event["kind"] for event in tracker.submission_safety_events("application-identity")]
    assert kinds.count("SUBMISSION_DISPATCHED") == 1


def test_navigating_during_a_run_records_the_visited_host_as_a_known_alias(tmp_path):
    # The wiring behind A: apply_flow.remember_progress is already called "on every pass"; it
    # must durably tie whatever host/path the browser is now on to the run's one bound
    # submission_key (never recomputed from the live page), so a later invocation that starts
    # from that host/path is recognized as the same identity.
    import apply_flow

    tracker = JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="application-identity", title="Synthetic Engineer", company="Synthetic Employer",
                   url="https://jobs.example.test/application/42")
    bound_key = submission_effect_key_for_url("https://jobs.example.test/application/42")
    assistant = SimpleNamespace(submission_state_store=tracker, submission_key=bound_key)
    page = SimpleNamespace(url="https://ats.example.test/boards/synthetic/apply/42")

    apply_flow.remember_progress(tracker, "application-identity", page, assistant=assistant)

    alias_key = submission_effect_key_for_url(page.url)
    assert alias_key in tracker.submission_identity_group(bound_key)


def test_redirect_to_a_different_host_stays_bound_to_the_same_effect_history(setup):
    # A: a redirect seen while working this application is recorded as an alias of the one
    # stable identity apply_flow.py bound at the start of the run; dispatching through either
    # name reaches the same durable history (no second authority is created).
    tracker, _database, _context, _page, _guard = setup
    redirect_key = submission_effect_key_for_url("https://ats.example.test/boards/synthetic/apply/42")
    tracker.record_submission_identity_alias("application-identity", redirect_key)

    tracker.begin_submission_dispatch("application-identity")
    # Canonical resolution: the alias observes the SAME state as the primary it was recorded
    # under, not a separate, empty row of its own -- that is what "the same effect history" means.
    assert tracker.get_submission_effect_state(redirect_key) == "DISPATCHED"
    assert set(tracker.submission_identity_group(redirect_key)) == set(
        tracker.submission_identity_group("application-identity")
    )


def test_a_different_path_after_dispatch_cannot_create_a_fresh_authority(setup):
    # B/D: once the bound identity has DISPATCHED and a later host/path is a known alias of it,
    # a second invocation that only knows that alias must not be able to dispatch again.
    tracker, _database, _context, _page, _guard = setup
    alias_key = submission_effect_key_for_url("https://ats.example.test/boards/synthetic/apply/42")
    tracker.begin_submission_dispatch("application-identity")
    tracker.record_submission_identity_alias("application-identity", alias_key)

    with pytest.raises(RuntimeError, match="replay blocked"):
        tracker.begin_submission_dispatch(alias_key)
    # The blocked attempt does not create its own row either: both names still observe the one
    # canonical DISPATCHED state, not a separate ("no row" vs "a row") story for each.
    assert tracker.get_submission_effect_state(alias_key) == "DISPATCHED"
    assert tracker.get_submission_effect_state("application-identity") == "DISPATCHED"


def test_apply_flow_preflight_check_also_fails_closed_on_a_recorded_alias(setup):
    # B/D, apply_flow-level: the run-start pre-flight check (refuse_submission_replay) must find
    # the same durable alias even though the caller passes no `aliases` of its own -- the fail-
    # closed identity rule lives in the data layer, not only in what each call site remembers to
    # compute.
    import apply_flow

    tracker, _database, _context, _page, _guard = setup
    alias_key = submission_effect_key_for_url("https://ats.example.test/boards/synthetic/apply/42")
    tracker.begin_submission_dispatch("application-identity")
    tracker.record_submission_identity_alias("application-identity", alias_key)

    assert apply_flow.refuse_submission_replay(tracker, alias_key, "application_start") == "UNCERTAIN"
    assert tracker.get_submission_effect_state("application-identity") == "UNCERTAIN"


def test_uncertain_then_cross_host_redirect_blocks_a_second_dispatch(setup):
    # C: an ambiguous outcome recorded under the original key still blocks a later invocation
    # presented under a recorded alias host/path.
    tracker, _database, _context, _page, _guard = setup
    alias_key = submission_effect_key_for_url("https://ats.example.test/boards/synthetic/apply/42")
    tracker.begin_submission_dispatch("application-identity")
    tracker.finish_submission_effect("application-identity", "UNCERTAIN")
    tracker.record_submission_identity_alias("application-identity", alias_key)

    with pytest.raises(RuntimeError, match="replay blocked"):
        tracker.begin_submission_dispatch(alias_key)


def test_confirmed_then_alternate_url_blocks_a_second_dispatch(setup):
    # E: a confirmed submission recorded under the original key still blocks a later invocation
    # presented under a recorded alias host/path.
    tracker, _database, _context, _page, _guard = setup
    alias_key = submission_effect_key_for_url("https://ats.example.test/boards/synthetic/apply/42")
    tracker.begin_submission_dispatch("application-identity")
    tracker.finish_submission_effect("application-identity", "CONFIRMED", reconciled=True)
    tracker.record_submission_identity_alias("application-identity", alias_key)

    with pytest.raises(RuntimeError, match="replay blocked"):
        tracker.begin_submission_dispatch(alias_key)


def test_an_alias_with_its_own_independent_history_is_not_guessed_into_another_identity(setup):
    # F: fail closed on the side of never merging two identities that cannot be proven related.
    # An alias candidate that already has its own independent dispatch history keeps that
    # history untouched rather than being folded into a different primary's identity group.
    tracker, _database, _context, _page, _guard = setup
    other_key = submission_effect_key_for_url("https://unrelated.example.test/careers/apply/9")
    tracker.begin_submission_dispatch("application-identity")
    tracker.begin_submission_dispatch(other_key)

    tracker.record_submission_identity_alias("application-identity", other_key)

    assert tracker.get_submission_effect_state(other_key) == "DISPATCHED"
    assert other_key not in tracker.submission_identity_group("application-identity")
    # other_key's own history is undisturbed: it is still free to be read on its own terms.
    assert set(tracker.submission_identity_group(other_key)) == {other_key}


def test_an_explicitly_recorded_alias_does_not_block_the_primarys_own_dispatch(setup):
    # G: recording an alias ahead of time (an ordinary pre-submit ATS redirect) must not itself
    # block the bound identity's own, legitimate first dispatch.
    tracker, _database, _context, _page, _guard = setup
    alias_key = submission_effect_key_for_url("https://ats.example.test/boards/synthetic/apply/42")
    tracker.record_submission_identity_alias("application-identity", alias_key)

    assert tracker.begin_submission_dispatch("application-identity") == "DISPATCHED"
    # Canonical resolution: the pre-recorded alias now correctly observes the same DISPATCHED
    # state as the primary, proving it would itself be refused a second dispatch -- it was never
    # isolated from it, it just had not been read yet.
    assert tracker.get_submission_effect_state(alias_key) == "DISPATCHED"


def test_annexing_a_root_with_an_existing_dependent_still_blocks_that_dependent(setup):
    # Pass 4, Finding 2 (adversarial review, 7 October 2026): B owns B_alias; A then annexes B
    # (A's run coincidentally visits B's exact root URL); A dispatches. Direct dispatch attempts
    # using B, B_alias, A, or any other member of the merged identity must all resolve to the
    # same canonical identity and be refused -- this is the exact reproduction that previously
    # let begin_submission_dispatch("B_alias") succeed with a fresh, unblocked DISPATCHED row.
    tracker, _database, _context, _page, _guard = setup
    a_root = submission_effect_key_for_url("https://jobs.lever.co/company/123")
    b_root = submission_effect_key_for_url("https://company.wd1.myworkdayjobs.com/Careers")
    b_alias = submission_effect_key_for_url(
        "https://company.wd1.myworkdayjobs.com/Careers/job/123/apply"
    )
    tracker.record_submission_identity_alias(b_root, b_alias)   # B owns B_alias
    tracker.record_submission_identity_alias(a_root, b_root)    # A annexes B
    tracker.begin_submission_dispatch(a_root)

    for key in (a_root, b_root, b_alias):
        with pytest.raises(RuntimeError, match="replay blocked"):
            tracker.begin_submission_dispatch(key)
        assert tracker.get_submission_effect_state(key) == "DISPATCHED"


def test_canonical_resolution_handles_a_four_level_stored_chain(setup):
    # alias3 -> alias2 -> alias1 -> root: every member resolves to the same complete group and
    # observes the same dispatch state, starting from any point in the chain. Constructed with
    # raw rows (the public API eagerly flattens on insert) to prove the RESOLVER is correct for an
    # arbitrary stored topology, not only the shapes the API happens to produce.
    tracker, _database, _context, _page, _guard = setup
    with tracker._connect() as conn:
        conn.execute(
            "INSERT INTO submission_identity_aliases VALUES ('chain-alias1','application-identity','t')"
        )
        conn.execute("INSERT INTO submission_identity_aliases VALUES ('chain-alias2','chain-alias1','t')")
        conn.execute("INSERT INTO submission_identity_aliases VALUES ('chain-alias3','chain-alias2','t')")

    expected = {"application-identity", "chain-alias1", "chain-alias2", "chain-alias3"}
    for key in expected:
        assert set(tracker.submission_identity_group(key)) == expected

    tracker.begin_submission_dispatch("application-identity")
    for key in ("chain-alias1", "chain-alias2", "chain-alias3"):
        with pytest.raises(RuntimeError, match="replay blocked"):
            tracker.begin_submission_dispatch(key)


def test_a_root_annexed_beneath_another_root_still_blocks_its_own_dependents(setup):
    # The other direction named explicitly: build leaf -> mid normally, THEN mid (a root with its
    # own dependent) is itself annexed beneath an even higher root, top. leaf must still resolve
    # through to top and observe top's dispatch.
    tracker, _database, _context, _page, _guard = setup
    mid, leaf = "annex-mid-root", "annex-leaf"
    tracker.record_submission_identity_alias(mid, leaf)                 # leaf -> mid
    tracker.record_submission_identity_alias("application-identity", mid)  # mid -> application-identity

    tracker.begin_submission_dispatch("application-identity")
    with pytest.raises(RuntimeError, match="replay blocked"):
        tracker.begin_submission_dispatch(leaf)
    with pytest.raises(RuntimeError, match="replay blocked"):
        tracker.begin_submission_dispatch(mid)


@pytest.mark.parametrize("tracking_param", [
    "trk=linkedin_jobs", "fbclid=abc123", "gclid=xyz", "msclkid=xyz", "twclid=xyz",
    "li_fat_id=abc", "mc_cid=abc", "rb_clickid=abc", "shared_id=abc", "dclid=abc",
])
def test_platform_click_tracking_parameters_do_not_fragment_identity(tracking_param):
    # Pass 4, Finding 4 (adversarial review, 7 October 2026): trk/fbclid/gclid/etc. were not in
    # the stripped list, so a referral link and the bare posting URL hashed to two different
    # effect keys -- a second, undetected submission authority for the same posting.
    base = "https://example.com/job/123?job=123"
    tracked = f"{base}&{tracking_param}"
    assert submission_effect_key_for_url(tracked) == submission_effect_key_for_url(base)


def test_tracking_parameter_stripping_does_not_remove_identifying_query_params():
    # The flip side of Finding 4's fix: a parameter this does not recognize as tracking noise is
    # always kept, because it may be exactly how two distinct postings/applications are told
    # apart (a requisition id, a job id, a step token).
    base = "https://example.com/job/123?job=123"
    distinct_job = "https://example.com/job/123?job=999"
    distinct_requisition = "https://example.com/job/123?job=123&requisition_id=999"
    assert submission_effect_key_for_url(distinct_job) != submission_effect_key_for_url(base)
    assert submission_effect_key_for_url(distinct_requisition) != submission_effect_key_for_url(base)


def test_login_host_hop_is_now_recorded_as_an_alias(tmp_path):
    # Pass 4, Finding 5 (adversarial review, 7 October 2026): apply_flow._remember_submission_
    # identity_alias used to be gated by worth_returning_to(), which excludes "/login" and
    # similar URLs for an unrelated Resume-UX reason -- silently dropping the login-host leg of
    # the posting -> login -> ATS -> application -> review chain from the alias graph.
    import apply_flow

    tracker = JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="app-1", title="Engineer", company="Acme",
                    url="https://boards.greenhouse.io/acme/jobs/555")
    bound_key = submission_effect_key_for_url("https://boards.greenhouse.io/acme/jobs/555")
    assistant = SimpleNamespace(submission_state_store=tracker, submission_key=bound_key)

    login_url = "https://acme.greenhouse.io/login?return=%2Fapply%2F555"
    apply_flow.remember_progress(tracker, "app-1", SimpleNamespace(url=login_url), assistant=assistant)

    login_key = submission_effect_key_for_url(login_url)
    assert login_key in tracker.submission_identity_group(bound_key)
    # Resuming the dashboard's "last page" at a login URL is unaffected: update_last_page is
    # still gated by worth_returning_to(), since reopening a human at a stale login page is a
    # separate, pre-existing concern from identity continuity.
    assert tracker.get("app-1").last_page_url in (None, "")


def test_same_host_normalized_url_cases_still_find_the_prior_application(setup):
    # H: existing normalization (tracking params, trailing slash, scheme, host case) keeps
    # working once identity-alias resolution is layered on top of it.
    tracker, _database, _context, _page, _guard = setup
    assert tracker.submission_keys_for_url(
        "HTTP://Jobs.Example.Test/application/42/?utm_source=listing&utm_campaign=x"
    ) == ["application-identity"]


def test_probe_can_read_reconciliation_data_without_submit_authority(setup):
    tracker, _database, _context, page, _guard = setup
    tracker.begin_submission_dispatch("application-identity")
    page.set_content("<h1>Application Received</h1>")
    snapshot = SubmissionProbe.inspect(page, tracker, "application-identity")

    assert snapshot["state"] == "DISPATCHED"
    assert snapshot["url"] == page.url
    assert snapshot["confirmation_text"] == "application received"
    assert not hasattr(SubmissionProbe, "submit")
    assert not hasattr(SubmissionProbe, "click")
