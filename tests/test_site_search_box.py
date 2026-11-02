"""A site's own job-search box is not an application question (f220)."""
import page_agent

LISTING = """- generic [ref=e1]:
  - banner [ref=e2]:
    - button "Sign In" [ref=e3] [cursor=pointer]
  - generic [ref=e4]:
    - search [ref=e5]:
      - generic [ref=e6]:
        - textbox "Search" [ref=e7]:
          - /placeholder: Search for jobs or keywords
      - button "Search" [ref=e8] [cursor=pointer]
"""

FORM = """- generic [ref=e1]:
  - textbox "First name" [ref=e2]
  - search [ref=e3]:
    - searchbox "Find a school" [ref=e4]
  - textbox "Last name" [ref=e5]
"""


def test_textbox_inside_a_search_landmark_is_not_a_question():
    roles = [(c.role, c.name) for c in page_agent.parse_snapshot(LISTING)]
    assert ("textbox", "Search") not in roles
    assert ("button", "Search") in roles       # still pressable; only the box is not answerable


def test_boxes_not_called_search_stay_questions_and_order_is_kept():
    names = [c.name for c in page_agent.parse_snapshot(FORM) if c.role in ("textbox", "searchbox")]
    assert names == ["First name", "Find a school", "Last name"]   # a real autocomplete in a search section stays


def test_a_page_of_search_results_is_not_a_page_to_resume_at():
    import apply_flow
    listing = "https://rocket.wd5.myworkdayjobs.com/en-US/rocket_careers?q=I%20have%206%20years"
    assert not apply_flow.worth_returning_to(listing)
    assert not apply_flow.worth_returning_to("https://x.example/jobs?Keywords=network")
    assert apply_flow.worth_returning_to(
        "https://rocket.wd5.myworkdayjobs.com/en-US/rocket_careers/job/Remote/Senior-Network-Engineer_R1/apply")
