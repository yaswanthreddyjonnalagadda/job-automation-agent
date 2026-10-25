"""The employer posting opener must not be mistaken for final submission."""
import pytest
from test_submission_firewall import browser, page, guarded


@pytest.mark.parametrize("variant,allowed", [
    ("posting", True), ("form", False), ("input", False), ("dialog", False),
    ("other_host", False), ("other_job", False), ("other_tenant", False),
    ("submit", False), ("form_attribute", False), ("iframe", False),
])
def test_bankofamerica_apply_only_opens_the_same_job(page, variant, allowed):
    host = 'example.test' if variant == 'other_host' else 'careers.bankofamerica.com'
    target_host = 'other.wd1.myworkdayjobs.com' if variant == 'other_tenant' else 'ghr.wd1.myworkdayjobs.com'
    job_id = '99999999' if variant == 'other_job' else '26038771'
    destination = f'https://{target_host}/Lateral-US/job/Example/Network-Engineer_{job_id}'
    label = 'Submit Application' if variant == 'submit' else 'Apply'
    attrs = 'form="application"' if variant == 'form_attribute' else ''
    button = f'''<button {attrs} data-popup-target="apply-popup"
      data-bactmln="Job_Actions-_-Apply-_-{destination}"
      onclick="window.opened=true">{label}</button>'''
    if variant == 'form':
        button = '<form>' + button + '</form>'
    markup = f'<h1>Network Engineer</h1>{button}<a href="{destination}">Acknowledge</a>'
    if variant == 'input': markup += '<input aria-label="Name">'
    if variant == 'dialog': markup += '<div role="dialog">Review application</div>'
    if variant == 'iframe': markup += '<iframe title="Application"></iframe>'
    page.route('**/*', lambda route: route.fulfill(body=markup, content_type='text/html'))
    page.goto(f'https://{host}/en-us/job-detail/26038771/network-engineer')
    guard = guarded(page)
    page.get_by_role('button', name=label, exact=True).click()
    assert bool(page.evaluate('window.opened')) is allowed
    assert bool(guard.denials(page)) is (not allowed)
