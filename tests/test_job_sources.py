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
