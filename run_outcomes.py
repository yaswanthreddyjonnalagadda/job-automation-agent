"""Stable CLI outcomes shared by the application launcher and dashboard."""

ALREADY_SUBMITTED = 3
SKIPPED_SPONSORSHIP = 4
EXPECTED_STOPS = {ALREADY_SUBMITTED, SKIPPED_SPONSORSHIP}


def display_state(returncode):
    if returncode == ALREADY_SUBMITTED:
        return "finished -- already submitted"
    if returncode == SKIPPED_SPONSORSHIP:
        return "skipped -- sponsorship not available"
    return "finished" if returncode == 0 else f"failed (exit {returncode})"
