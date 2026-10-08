import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

// The console shows the running server's version, whose only source is pyproject.toml.
const version = /^version = "([^"]+)"/m.exec(readFileSync(new URL('../../pyproject.toml', import.meta.url), 'utf8'))![1];

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

  // Sidebar caption: no letter-spacing between characters, and the console version.
  const caption = page.locator('.aside-caption');
  await expect(caption).toHaveText(`配额管理平台 v${version}`);
  expect(await caption.evaluate(el => getComputedStyle(el).letterSpacing)).toMatch(/^(normal|0px)$/);

  const navs = page.locator('.nav-btn');
  const count = await navs.count();
  let drawers = 0;
  for (let i = 0; i < count; i++) {
    const nav = navs.nth(i);
    const name = (await nav.textContent())?.trim() || `page ${i}`;
    await nav.click();
    const pageForm = await measure(page, '.main form');
    if (pageForm.length) expectAligned(`${name} (page)`, pageForm);
    const buttons = page.locator('.panel-head button');
    for (let b = 0; b < await buttons.count(); b++) {
      const label = (await buttons.nth(b).textContent())?.trim() || `button ${b}`;
      await buttons.nth(b).click();
      await expect(page.locator('.drawer-panel')).toBeVisible();
      // Typed text is where clipping showed up, so type into the first dropdown that has one.
      const search = page.locator('.drawer-panel .ant-select input:not([disabled])').first();
      if (await search.count()) {
        await search.click();
        await page.keyboard.type('shu kang zhi yi');
      }
      expectAligned(`${name} / ${label}`, await measure(page, '.drawer-panel'));
      // Escape closes an open dropdown first; close the drawer if it is still there.
      await page.keyboard.press('Escape');
      if (await page.locator('.drawer-panel').count()) await page.locator('.drawer-head button').click();
      await expect(page.locator('.drawer-panel')).toHaveCount(0);
      drawers++;
    }
  }
  expect(drawers).toBeGreaterThanOrEqual(8);
});

test('sets the user ID definition, issues and deletes a member-sync credential', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();

  // An explicit place for the definition, independent of issuing.
  await page.getByRole('button', { name: '业务系统', exact: true }).click();
  const field = (scope: string, label: string) => page.locator(`${scope} label`).filter({ hasText: label }).locator('input');
  await expect(field('.main form', '用户 ID 定义')).toHaveValue('');
  await field('.main form', '用户 ID 定义').fill('user_table.id');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('已保存');

  // A dedicated button opens the form with the member-sync role and the stored definition.
  await page.getByRole('button', { name: '客户端凭据' }).click();
  await page.getByRole('button', { name: '签发会员同步凭据' }).click();
  await expect(page.locator('.drawer-panel .ant-select').filter({ hasText: '会员同步' })).toHaveCount(1);
  await expect(page.locator('.drawer-panel label').filter({ hasText: 'Service' })).toHaveCount(0);
  await expect(field('.drawer-panel', '用户 ID 定义')).toHaveValue('user_table.id');
  await expect(field('.drawer-panel', '用户 ID 定义')).toBeDisabled();
  await field('.drawer-panel', 'Client ID').fill('e2e-members');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '生成并下载' }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe('e2e-members-quotakit.md');
  const text = await (await file.createReadStream()).toArray().then(chunks => Buffer.concat(chunks).toString('utf8'));
  expect(text).toContain('"schema": "tekes-quotakit-membership/v1"');
  expect(text).toContain('> user_table.id');
  const row = page.locator('tbody tr').filter({ hasText: 'e2e-members' });
  await expect(row).toContainText('不绑定（会员同步）');

  // Deleting asks for the Client ID and removes the row.
  page.once('dialog', dialog => dialog.accept('e2e-members'));
  await row.getByRole('button', { name: '操作菜单' }).click();
  await page.getByRole('menuitem', { name: '删除' }).click();
  await expect(page.getByRole('status')).toContainText('已删除凭据 e2e-members');
  await expect(page.locator('tbody tr').filter({ hasText: 'e2e-members' })).toHaveCount(0);
});

test('disabled controls look alike, and rotation downloads without a form', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  const field = (scope: string, label: string) => page.locator(`${scope} label`).filter({ hasText: label }).locator('input');

  await page.getByRole('button', { name: '业务系统', exact: true }).click();
  const definition = field('.main form', '用户 ID 定义');
  if (!(await definition.inputValue())) {
    await definition.fill('user_table.id');
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('已保存');
  }

  // The member-sync form has a locked role (disabled antd Select) and a locked definition
  // (disabled input). Both must share antd's disabled background, text colour and border.
  await page.getByRole('button', { name: '客户端凭据' }).click();
  await page.getByRole('button', { name: '签发会员同步凭据' }).click();
  const looks = await page.evaluate(() => [...document.querySelectorAll('.drawer-panel label')].flatMap(label => {
    const select = label.querySelector<HTMLElement>('.ant-select.ant-select-disabled');
    const input = label.querySelector<HTMLInputElement>(':scope > input:disabled');
    const box = select || input;
    if (!box) return [];
    const css = getComputedStyle(box);
    const textEl = select ? (select.querySelector('.ant-select-content') as HTMLElement) || select : input!;
    return [{ field: label.firstChild?.textContent?.trim(), kind: select ? 'select' : 'input', background: css.backgroundColor, border: css.borderTopColor, color: getComputedStyle(textEl).color, cursor: css.cursor }];
  }));
  expect(looks.map(l => l.kind).sort()).toEqual(['input', 'select']);
  const [first, ...rest] = looks;
  for (const look of looks) expect(look.cursor, `${look.field} cursor`).toBe('not-allowed');
  for (const look of rest) {
    expect({ background: look.background, border: look.border, color: look.color }, `${look.field} vs ${first.field}`)
      .toEqual({ background: first.background, border: first.border, color: first.color });
  }

  // Issue, then rotate: rotation asks once and downloads straight away, with no form.
  await field('.drawer-panel', 'Client ID').fill('e2e-rotate');
  let download = page.waitForEvent('download');
  await page.getByRole('button', { name: '生成并下载' }).click();
  await download;
  const row = page.locator('tbody tr').filter({ hasText: 'e2e-rotate' });
  let prompt = '';
  page.once('dialog', dialog => { prompt = dialog.message(); void dialog.accept(); });
  download = page.waitForEvent('download');
  await row.getByRole('button', { name: '操作菜单' }).click();
  await page.getByRole('menuitem', { name: '轮换密钥' }).click();
  const file = await download;
  expect(prompt).toContain('旧密钥立即失效');
  expect(file.suggestedFilename()).toBe('e2e-rotate-quotakit.md');
  await expect(page.locator('.drawer-panel')).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText('已轮换 e2e-rotate');
});
