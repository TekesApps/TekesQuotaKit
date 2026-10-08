import { expect, test, type Page } from '@playwright/test';

// Guards against controls whose text is clipped or misaligned. The Shukang admin stylesheet
// styles every bare <input>, which once made antd's inner inputs taller than their frames.
const CONTROL_HEIGHT = 38;

type Measure = { field: string; height: number; clipped: boolean; offCenter: number };

async function measure(page: Page, scope: string): Promise<Measure[]> {
  return page.evaluate(scope => [...document.querySelectorAll(`${scope} label`)].flatMap(label => {
    const control = label.querySelector<HTMLElement>('.ant-select, :scope > input:not([type=checkbox])');
    if (!control || !control.offsetParent) return [];
    const frame = control.getBoundingClientRect();
    const inner = (control.matches('input') ? control : control.querySelector('input')) as HTMLInputElement;
    const box = inner.getBoundingClientRect(), css = getComputedStyle(inner);
    const content = inner.clientHeight - parseFloat(css.paddingTop) - parseFloat(css.paddingBottom);
    return [{
      field: label.firstChild?.textContent?.trim() || '?',
      height: Math.round(frame.height * 10) / 10,
      clipped: content < Math.ceil(parseFloat(css.fontSize) * 1.2) || box.bottom > frame.bottom + 0.5 || box.top < frame.top - 0.5,
      offCenter: Math.abs((box.top + box.bottom) / 2 - (frame.top + frame.bottom) / 2),
    }];
  }), scope);
}

function expectAligned(where: string, rows: Measure[]) {
  expect(rows.length, `${where}: no controls found`).toBeGreaterThan(0);
  for (const row of rows) {
    expect(row.height, `${where} / ${row.field} height`).toBe(CONTROL_HEIGHT);
    expect(row.clipped, `${where} / ${row.field} text clipped`).toBe(false);
    expect(row.offCenter, `${where} / ${row.field} text off-center`).toBeLessThanOrEqual(1);
  }
}

test('every editor control is the same height, unclipped and centered', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await expect(page.getByText('端到端测试')).toBeVisible();

  const navs = page.locator('.nav-btn');
  const count = await navs.count();
  let drawers = 0;
  for (let i = 0; i < count; i++) {
    const nav = navs.nth(i);
    const name = (await nav.textContent())?.trim() || `page ${i}`;
    await nav.click();
    const add = page.locator('.panel-head button').first();
    if (!(await add.count())) continue;
    await add.click();
    await expect(page.locator('.drawer-panel')).toBeVisible();
    // Typed text is where clipping showed up, so type into the first dropdown that has one.
    const search = page.locator('.drawer-panel .ant-select input:not([disabled])').first();
    if (await search.count()) {
      await search.click();
      await page.keyboard.type('shu kang zhi yi');
    }
    expectAligned(name, await measure(page, '.drawer-panel'));
    // Escape closes an open dropdown first; close the drawer if it is still there.
    await page.keyboard.press('Escape');
    if (await page.locator('.drawer-panel').count()) await page.locator('.drawer-head button').click();
    await expect(page.locator('.drawer-panel')).toHaveCount(0);
    drawers++;
  }
  expect(drawers).toBeGreaterThanOrEqual(7);
});

test('issues a member-sync credential without a Service and downloads its contract', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.getByRole('button', { name: '客户端凭据' }).click();
  await page.getByRole('button', { name: '签发凭据' }).click();
  await page.locator('.drawer-panel label').filter({ hasText: 'Client ID' }).locator('input').fill('e2e-members');
  await page.getByLabel('角色').click();
  await page.getByTitle('会员同步（membership）').click();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '生成并下载' }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe('e2e-members-quotakit.md');
  const text = await (await file.createReadStream()).toArray().then(chunks => Buffer.concat(chunks).toString('utf8'));
  expect(text).toContain('"schema": "tekes-quotakit-membership/v1"');
  expect(text).toContain('/v1/members/batch');
  await expect(page.getByRole('status')).toContainText('e2e-members');
  await expect(page.locator('tbody')).toContainText('不绑定（会员同步）');
});
