// Use the product's real tab navigation; a newly created record opens on Preparation.
export async function verifyApplicationDetail(page, application, setStage) {
  setStage('form-dismissal');
  await page.getByRole('dialog', { name: '添加投递', exact: true }).waitFor({ state: 'hidden' });
  setStage('heading');
  await page.getByRole('heading', { level: 3,
    name: `${application.company_name} · ${application.position_name}`, exact: true }).waitFor({ state: 'visible' });
  setStage('overview-tab');
  await page.getByRole('tablist', { name: '投递详情分段', exact: true })
    .getByRole('tab', { name: '概览', exact: true }).click();
  setStage('overview-panel');
  const overview = page.getByRole('tabpanel', { name: '概览', exact: true });
  await overview.waitFor({ state: 'visible' });
  setStage('notes');
  await overview.getByText(application.notes, { exact: true }).waitFor({ state: 'visible' });
}
