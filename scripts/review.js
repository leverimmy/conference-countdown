// Included only by the local server. Static reports have no write controls.
let saving = false;
document.addEventListener('click', event => {
  if (event.target.matches('[data-defer]'))
    event.target.closest('form').querySelector('[role="status"]').textContent = '未修改，仍为待审核。';
});
document.addEventListener('submit', async event => {
  const form = event.target;
  if (!form.matches('.decision-form')) return;
  event.preventDefault();
  if (saving || !event.submitter) return;
  const action = event.submitter.value;
  const message = form.querySelector('[role="status"]');
  saving = true;
  const buttons = [...document.querySelectorAll('.decision-form button:not(:disabled)')];
  buttons.forEach(button => button.disabled = true);
  message.textContent = '正在校验并保存…';
  try {
    const response = await fetch('/decision', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Review-Token': document.querySelector('meta[name="review-token"]').content},
      body: JSON.stringify({
        conference: form.dataset.conference, target: form.dataset.target, action,
        revision: document.querySelector('meta[name="review-revision"]').content,
        reason: form.elements.reason.value,
      }),
    });
    const result = await response.json();
    if (!response.ok || !result.saved) throw new Error(result.error || '未能确认保存结果，请刷新检查。');
    location.replace('/#' + encodeURIComponent(result.conference));
    location.reload();
  } catch (error) {
    message.textContent = error.message + '（页面不会自动重试）';
    saving = false;
    buttons.forEach(button => button.disabled = false);
  }
});
