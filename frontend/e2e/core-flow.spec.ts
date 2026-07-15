import { expect, test } from '@playwright/test';
const request = { city: '上海', monthly_rent_max: 6000, monthly_total_max: 6800, commute_mode: 'transit', destinations: [{ label: '我的公司', address: '上海市浦东新区陆家嘴', weight: 1, max_minutes: 45 }], soft_preferences: ['近地铁'] };
test.beforeEach(async ({ page }) => {
  await page.route('**/api/anonymous/session', route => route.fulfill({ json: { anonymous_user_id: '00000000-0000-0000-0000-000000000001', access_token: 'test-token' } }));
  await page.route('**/api/profile', route => route.fulfill({ json: null })); await page.route('**/api/favorites', route => route.fulfill({ json: [] })); await page.route('**/api/contracts/reviews', route => route.fulfill({ json: [] }));
  await page.route('**/api/search-history', route => route.fulfill({ json: [{ id: 'history-1', request, summary: { total_candidates: 6, top_listing_ids: ['SH-PD-001'] }, provider: 'mock-shanghai + amap', created_at: '2026-07-13T08:00:00Z' }] }));
});
test('restores anonymous memory and opens a historical decision', async ({ page }) => {
  await page.goto('/'); await expect(page.getByRole('link', { name: 'RentWise' })).toBeVisible(); await page.getByRole('button', { name: /历史 1/ }).click(); await expect(page.getByText('过去的租房方案')).toBeVisible(); await page.getByRole('button', { name: /上海 · ¥6,000/ }).click(); await expect(page.getByText('当时的租房方案')).toBeVisible(); await expect(page.getByRole('button', { name: '按原条件重新计算' })).toBeVisible();
});
test('opens contract review and switches language', async ({ page }) => {
  await page.goto('/'); await page.getByRole('button', { name: '合同核验' }).click(); await expect(page.getByText('拍下每一页，也能核验合同')).toBeVisible(); await page.getByRole('button', { name: 'EN' }).click(); await expect(page.getByText('Photograph every page. Review the contract.')).toBeVisible();
});

test('allows every numeric field to be cleared before entering a replacement', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'EN' }).click();
  for (const [label, replacement] of [['Listed rent cap', '5000'], ['All-in monthly housing cap', '5600'], ['Bedrooms', '2'], ['Min area m²', '45']] as const) {
    const input = page.locator('label').filter({ hasText: label }).locator('input[type="number"]');
    await input.fill('');
    await expect(input).toHaveValue('');
    await input.fill(replacement);
    await expect(input).toHaveValue(replacement);
  }
  await page.getByRole('button', { name: 'Continue' }).click();
  for (const [label, replacement] of [['Weight', '0.7'], ['Max one-way minutes', '60']] as const) {
    const input = page.locator('label').filter({ hasText: label }).locator('input[type="number"]');
    await input.fill('');
    await expect(input).toHaveValue('');
    await input.fill(replacement);
    await expect(input).toHaveValue(replacement);
  }
});
