"""One structural test for "this click only opens the application": a job posting's own Apply button (f269).

submission_guard denies any button that reads as a final submission. A posting's "Apply now" is not one -- it only
opens the application -- but until now each portal had its own narrow test for it (sites/adp.py, greenhouse.py,
bankofamerica.py), so every new employer site stopped the run at its first page (Walmart, 27 October 2026).

This test needs no portal. It holds only when the page itself shows there is nothing to submit:
  * the control is a button or link outside any form, labelled exactly Apply / Apply now / Apply for this job;
  * no application form or dialog is visible, no input is visible outside the site's own header, navigation or
    search landmarks (a careers site's search box is not an application), and no frame that could hold one (a frame
    that is empty and of this page is harmless -- a map placeholder; a frame with an address of its own is not);
  * the address is not a job board or an auto-apply aggregator, whose one-click Apply can submit for the person
    (job_sources lists them; the owner applies on the employer's own site).
A page that shows any application control, or a label that says submit, is still guarded.
"""
from __future__ import annotations

import json

import job_sources

_TEMPLATE = r"""element => {
  const host = location.hostname.toLowerCase();
  if (JOB_BOARD_SUBSTRINGS.some(part => host.includes(part))
      || JOB_BOARD_HOSTS.some(name => host === name || host.endsWith('.' + name))) return false;
  if (!element.matches('button, [role="button"], a')
      || element.closest('form') || element.hasAttribute('form')) return false;
  const labels = [element.getAttribute('aria-label'), element.textContent]
    .map(value => (value || '').trim()).filter(Boolean);
  const isApplyLabel = value => /^(?:apply|apply now(?:\s+\S.*)?|apply (?:for|to) (?:this job|\S.*))$/i.test(value)
    && !/\b(?:submit|with|using|via|linkedin|indeed|facebook|filter|filters|search)\b/i.test(value);
  if (!labels.length || !labels.every(isApplyLabel)) return false;
  const nonAppWidget = (node, allowContainingElement = false) => {
    for (let n = node.parentElement; n && n !== document.body; n = n.parentElement) {
      if (!allowContainingElement && n.contains(element)) continue;
      const role = (n.getAttribute('role') || '').toLowerCase();
      const tag = n.tagName.toLowerCase();
      if (!['region', 'complementary', 'group', 'form'].includes(role)
          && !['section', 'aside', 'form'].includes(tag)) continue;
      if (n.querySelector('input[type="password"], input[type="file"]')) continue;
      const heading = (n.querySelector('h1, h2, h3, [role="heading"]')?.textContent || '').trim();
      const text = ((n.getAttribute('aria-label') || '') + ' ' + heading + ' ' + (n.textContent || '').slice(0, 240)).trim();
      if (/\b(?:get notified for similar jobs|sign up (?:to receive |for )?job alerts?|receive job alerts?|create (?:a )?job alert|manage alerts|join (?:our |the )?talent (?:community|network)|similar jobs|recommended jobs|jobs you may like)\b/i.test(text)) {
        return true;
      }
    }
    return false;
  };
  if (nonAppWidget(element, true)) return false;
  const siteChrome = node => Boolean(node.closest('header, nav, footer, [role="banner"], [role="navigation"], [role="search"], [role="contentinfo"]')) || nonAppWidget(node);
  // A frame is harmless only when it can be looked into (same origin), has no address of its own to load, and holds
  // no form control; a cross-origin or still-loading frame may be an embedded application, so it blocks.
  const frameHoldsAnApplication = frame => {
    const src = frame.getAttribute('src');
    if (src && src !== 'about:blank') return true;
    try {
      const inner = frame.contentDocument;
      return !inner || Boolean(inner.querySelector('form, [role="dialog"], input:not([type="hidden"]), textarea, select'));
    } catch (_) { return true; }
  };
  const holdsAnApplication = node => node.getClientRects().length > 0 && !siteChrome(node)
    && (node.tagName === 'IFRAME' ? frameHoldsAnApplication(node) : true);
  return !Array.from(document.querySelectorAll(
    'form, [role="dialog"], iframe, input:not([type="hidden"]), textarea, select'
  )).some(holdsAnApplication);
}"""


def posting_entry_js() -> str:
    """The test above, with job_sources' lists of job boards filled in (data lives there, not here)."""
    return (_TEMPLATE
            .replace("JOB_BOARD_SUBSTRINGS", json.dumps(list(job_sources.BLOCKED_SOURCES + job_sources.AGGREGATORS)))
            .replace("JOB_BOARD_HOSTS", json.dumps(list(job_sources.SOCIAL_HOSTS))))
