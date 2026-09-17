"""Reading a posting: what gets recorded as the job, the employer and where.

The company name is not cosmetic -- it is part of the duplicate check, the
resume tailoring prompt and the folder the evidence is written to.
"""

import job_sources


AMAZON_PAGE = """
<html><head>
  <title>Support Engineer, Amazon Leo Enterprise Customer Support - Job ID: 10539098 | Amazon.jobs</title>
  <meta property="og:title" content="Support Engineer, Amazon Leo Enterprise Customer Support" />
</head><body>
  <div class="location">USA, VA, Arlington - 95,600.00 - 160,000.00 USD annually</div>
  <h2>Basic Qualifications</h2>
  <p>%s</p>
</body></html>
""" % ("Experience in ISP network operations and network engineering. " * 20)


class _Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def test_an_amazon_posting_is_read_as_amazon(monkeypatch):
    """The page title carries " - Job ID: N | Amazon.jobs", and the host's
    first label is "www": the posting was recorded as a job at a company
    called "Www", with the job id inside its title."""
    monkeypatch.setattr(job_sources.requests, "get", lambda *a, **k: _Response(AMAZON_PAGE))
    job = job_sources.resolve_job(
        "https://www.amazon.jobs/en/jobs/10539098/support-engineer-amazon-leo-enterprise-customer-support")
    assert job["company"] == "Amazon"
    assert job["title"] == "Support Engineer, Amazon Leo Enterprise Customer Support"
    assert job["location"] == "USA, VA, Arlington"


def test_a_page_title_is_not_used_as_a_job_title(monkeypatch):
    """The same faults on a site with no reader of its own."""
    page = AMAZON_PAGE.replace('<meta property="og:title" content="Support Engineer, Amazon Leo Enterprise Customer Support" />', "")
    monkeypatch.setattr(job_sources.requests, "get", lambda *a, **k: _Response(page))
    job = job_sources.fetch_generic_job("https://www.example-corp.com/careers/10539098")
    assert job["company"] == "Example Corp"
    assert "Job ID" not in job["title"]
    assert "|" not in job["title"]


def test_a_tenant_hosted_portal_names_the_employer_not_the_vendor():
    """Dayforce gives every client a path of its own, so the host is the
    software vendor and the path is the employer. Read the other way round,
    an application was tracked against "Dayforcehcm"."""
    assert job_sources._company_from_url(
        "https://jobs.dayforcehcm.com/en-US/lumos/CANDIDATEPORTAL/jobs/9416") == "Lumos"
    assert job_sources._company_from_url(
        "https://acme.wd1.myworkdayjobs.com/en-US/careers/job/123") == "Acme"


def test_a_plain_host_is_still_read_as_the_company():
    assert job_sources._company_from_url("https://careers.example-corp.com/jobs/7") == "Example Corp"


def test_a_page_title_is_reduced_to_a_job_title():
    assert job_sources._clean_page_title(
        "Support Engineer, Leo - Job ID: 10539098 | Amazon.jobs") == "Support Engineer, Leo"
    assert job_sources._clean_page_title("Network Provisioning Engineer") == "Network Provisioning Engineer"


def test_a_posting_that_needs_rendering_is_not_read_from_the_served_html(monkeypatch):
    """Dayforce serves 136 characters of text and builds the rest in the
    browser: every reader that works on the served HTML found nothing, and the
    run stopped at "Could not read a job description"."""
    served = "<html><head><title>Job Details | Dayforce Jobs</title></head><body>Skip to Content Sign In</body></html>"
    monkeypatch.setattr(job_sources.requests, "get", lambda *a, **k: _Response(served))
    monkeypatch.setattr(job_sources, "fetch_rendered_job", lambda url: {"title": "rendered"})
    assert job_sources.fetch_generic_job("https://jobs.dayforcehcm.com/en-US/lumos/CANDIDATEPORTAL/jobs/9416") is None
    assert job_sources.resolve_job(
        "https://jobs.dayforcehcm.com/en-US/lumos/CANDIDATEPORTAL/jobs/9416") == {"title": "rendered"}


def test_the_employer_is_read_from_its_equal_opportunity_statement():
    """Casey's ADP page is titled "Career Site", its logo is labelled
    "Corporate Positions" and its address says "caseysstoresupportcenter";
    its footer says "Casey's Is an Equal Opportunity Employer"."""
    assert job_sources._employer_named_in(
        "Legal links Casey’s Is an Equal Opportunity Employer Family and Medical") == "Casey’s"
    assert job_sources._employer_named_in("Capital One is an equal opportunity employer.") == "Capital One"
    assert job_sources._employer_named_in("We are an equal opportunity employer.") == ""


def test_the_resume_shows_the_phone_with_its_country_code():
    """The owner's decision: +1 in front of the number on the resume, so a site
    filling its form from the resume takes the country along -- and never twice."""
    import apply_flow
    from config import get_user_profile

    profile = get_user_profile()
    for written in ("(571) 354-5212", "+1 (571) 354-5212"):
        header = f"NAME\nTitle\nFairfax, VA | {written} | x@y.com\n\nSUMMARY"
        line = apply_flow.normalise_contact_details(header, profile).splitlines()[2]
        assert "+1 (571) 354-5212" in line
        assert "+1 +1" not in line
