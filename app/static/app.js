// Small helpers only; every rule is still checked on the server.
document.addEventListener('DOMContentLoaded', () => {
  // Live character counter under textareas that have a maxlength
  document.querySelectorAll('textarea[data-counter][maxlength]').forEach((ta) => {
    const out = document.createElement('p');
    out.className = 'counter';
    out.setAttribute('aria-live', 'polite');
    const update = () => { out.textContent = `${ta.value.length} / ${ta.maxLength} characters`; };
    ta.insertAdjacentElement('afterend', out);
    ta.addEventListener('input', update);
    update();
  });
  // Admin: the agency box only matters for agency staff
  const role = document.querySelector('select[data-role-select]');
  if (role) {
    const agency = document.getElementById(role.id.replace('role', 'agency_id'));
    const sync = () => {
      if (role.value !== 'AGENCY_STAFF') agency.value = '0';
    };
    role.addEventListener('change', sync);
    sync();
  }
  // Move focus to the error summary so keyboard and screen-reader users hear it first
  const summary = document.querySelector('.error-summary');
  if (summary) summary.focus();
});
