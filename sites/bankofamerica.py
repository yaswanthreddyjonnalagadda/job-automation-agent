"""Bank of America career-posting navigation; employer forms remain guarded."""

# Public career pages open a disclosure popup before following a same-job
# Workday link. This predicate authorizes only that posting entry, not a form.
POSTING_ENTRY_JS = r"""element => {
  if (location.hostname !== 'careers.bankofamerica.com') return false;
  const job = location.pathname.match(/^\/en-us\/job-detail\/(\d+)\//i);
  if (!job || !element.matches('button, [role="button"]')
      || element.closest('form') || element.hasAttribute('form')) return false;
  const labels = [element.getAttribute('aria-label'), element.textContent]
    .map(text => (text || '').trim()).filter(Boolean);
  if (!labels.length || !labels.every(text => /^apply$/i.test(text))) return false;
  if (element.getAttribute('data-popup-target') !== 'apply-popup') return false;
  const parts = (element.getAttribute('data-bactmln') || '').split('-_-');
  if (parts.length !== 3 || parts[0] !== 'Job_Actions' || parts[1] !== 'Apply') return false;
  let target;
  try { target = new URL(parts[2]); } catch (_) { return false; }
  if (target.protocol !== 'https:' || target.hostname !== 'ghr.wd1.myworkdayjobs.com'
      || target.port || target.username || target.password || target.search || target.hash
      || !new RegExp('^/(?:en-us/)?Lateral-US/job/.+_' + job[1] + '$', 'i').test(target.pathname)) return false;
  if (!Array.from(document.querySelectorAll('a[href]')).some(link => link.href === target.href)) return false;
  return !Array.from(document.querySelectorAll(
    'form, [role="dialog"], iframe, input:not([type="hidden"]), textarea, select'
  )).some(node => node.getClientRects().length > 0);
}"""
