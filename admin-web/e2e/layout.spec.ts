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

  // Terminology: Quota is 配额 (what is counted); 额度 only names amounts (等级额度).
  const nav = (await page.locator('.nav-btn').allTextContents()).map(t => t.trim());
  expect(nav).toEqual(expect.arrayContaining(['Quota 配额', '等级额度']));
  expect(nav.join()).not.toContain('额度规则');
  await expect(page.locator('.stat').filter({ hasText: '当前会员' })).toHaveCount(1);

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
    for (const height of await page.locator('.search-bar button').evaluateAll(els => els.map(el => el.getBoundingClientRect().height)))
      expect(height, `${name} search button height`).toBe(CONTROL_HEIGHT);
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

test('looks up one user\'s usage and drills from a Level to its 等级额度', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  const search = (label: string) => page.locator('.search-bar label').filter({ hasText: label }).locator('input');

  await page.getByRole('button', { name: '用量', exact: true }).click();
  await search('用户 ID').fill('42');
  await page.locator('.search-bar').getByRole('button', { name: '查询' }).click();
  const card = page.getByRole('region', { name: '用户 42 的配额使用情况' });
  await expect(card).toContainText('basic');
  const visits = card.locator('tbody tr').filter({ hasText: 'visits' });
  await expect(visits).toContainText('5');
  await expect(visits).toContainText('每周');

  await search('用户 ID').fill('7');
  await page.locator('.search-bar').getByRole('button', { name: '查询' }).click();
  await expect(page.getByText('用户 7 当前不是会员')).toBeVisible();

  await page.getByRole('button', { name: 'Level 等级' }).click();
  await page.locator('tbody tr').filter({ hasText: 'basic' }).getByRole('button', { name: /1 项配额/ }).click();
  await expect(page.getByRole('heading', { name: '等级额度', level: 1 })).toBeVisible();
  await expect(search('Level')).toHaveValue('basic');
  await expect(page.locator('.panel:not(.relationship-overview) tbody tr')).toHaveCount(1);
  await page.locator('.search-bar').getByRole('button', { name: '清除' }).click();
  await expect(search('Level')).toHaveValue('');
});

test('edits ordered Level packages inside a user and retains independent bindings', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.locator('.nav-btn').filter({ hasText: '用户等级' }).click();
  await expect(page.getByRole('heading', { name: '辅助额度包模板', exact: true })).toHaveCount(0);
  const user = page.getByRole('row').filter({ has: page.getByRole('cell', { name: '42', exact: true }) });
  await user.getByRole('button', { name: '编辑额度包' }).click();
  let dialog = page.getByRole('dialog');
  await expect(dialog.locator('.user-package')).toHaveCount(1);
  const issued = dialog.locator('.user-package').first();
  for (const label of ['生效时间', '到期时间']) {
    await expect(issued.getByLabel(label, { exact: true })).toHaveAttribute('readonly', '');
  }
  const removeColors = await issued.getByRole('button', { name: '移除', exact: true }).evaluate(el => {
    const style = getComputedStyle(el); return { text: style.color, background: style.backgroundColor };
  });
  expect(removeColors).toEqual({ text: 'rgb(255, 255, 255)', background: 'rgb(180, 35, 24)' });
  for (let i = 2; i <= 3; i++) {
    await dialog.getByRole('button', { name: '添加等级额度包', exact: true }).click();
    await dialog.getByLabel(`等级额度包 ${i}`, { exact: true }).click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: 'basic' }).click();
    await expect(page.locator('.ant-select-dropdown:visible')).toHaveCount(0);
  }
  expectAligned('user ordered packages', await measure(page, '.drawer-panel'));
  await dialog.getByRole('button', { name: '保存', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(user.getByRole('cell', { name: '3', exact: true })).toBeVisible();
  await user.getByRole('button', { name: '编辑额度包' }).click();
  dialog = page.getByRole('dialog');
  await expect(dialog.locator('.user-package')).toHaveCount(3);
  const originalId = await page.request.get('/user-quota/v1/admin/tenants/e2e-business/subjects/42/level-packages', { headers: { 'X-Admin-Request': '1' } }).then(r => r.json()).then(data => data.packages[0].id);
  await dialog.getByRole('button', { name: '下移额度包 1', exact: true }).click();
  await dialog.getByRole('button', { name: '保存', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const state = await page.request.get('/user-quota/v1/admin/tenants/e2e-business/subjects/42/level-packages', { headers: { 'X-Admin-Request': '1' } }).then(r => r.json());
  expect(state.packages[1].id).toBe(originalId);
  expect(new Set(state.packages.map((p: { id: number }) => p.id)).size).toBe(3);
  await user.getByRole('button', { name: '编辑额度包' }).click();
  dialog = page.getByRole('dialog');
  await dialog.locator('.user-package').last().getByRole('button', { name: '移除', exact: true }).click();
  await dialog.getByRole('button', { name: '保存', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(user.getByRole('cell', { name: '2', exact: true })).toBeVisible();
  expect(await page.locator('body').evaluate(el => parseFloat(getComputedStyle(el).fontSize))).toBe(12.8);
});

test.describe('adding a user\'s Level packages', () => {
  const url = (id: string) => `/user-quota/v1/admin/tenants/e2e-business/subjects/${id}/level-packages`;
  const admin = { 'X-Admin-Request': '1' };
  async function openAdd(page: Page) {
    await page.goto('/user-quota/admin');
    await page.getByLabel('账号').fill('e2e');
    await page.getByLabel('密码').fill('e2e-console-password');
    await page.getByRole('button', { name: /登录管理平台/ }).click();
    await page.locator('.nav-btn').filter({ hasText: '用户等级' }).click();
    await page.getByRole('button', { name: '设置用户额度包', exact: true }).click();
    return page.getByRole('dialog');
  }
  async function addBasic(page: Page, dialog: ReturnType<Page['getByRole']>) {
    await dialog.getByRole('button', { name: '添加等级额度包', exact: true }).click();
    await dialog.getByLabel('等级额度包 1', { exact: true }).click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: 'basic' }).click();
  }

  test('saves for a user whose empty record already exists, sending the current revision', async ({ page }) => {
    const dialog = await openAdd(page);
    const created = await page.request.put(url('empty-record'), { headers: admin, data: { revision: 0, packages: [] } });
    expect(created.status(), await created.text()).toBe(200);
    const existing = await page.request.get(url('empty-record'), { headers: admin }).then(r => r.json());
    expect(existing).toMatchObject({ revision: 1, packages: [] });
    await dialog.getByLabel('用户 ID').fill('empty-record');
    await addBasic(page, dialog);
    const put = page.waitForRequest(r => r.method() === 'PUT' && r.url().endsWith(url('empty-record')));
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    expect((await put).postDataJSON().revision).toBe(1);
    await expect(dialog).toHaveCount(0);
    const saved = await page.request.get(url('empty-record'), { headers: admin }).then(r => r.json());
    expect(saved.packages).toHaveLength(1);
  });

  test('still refuses a user who already has packages', async ({ page }) => {
    const dialog = await openAdd(page);
    await dialog.getByLabel('用户 ID').fill('42');
    await addBasic(page, dialog);
    let puts = 0;
    page.on('request', r => { if (r.method() === 'PUT') puts++; });
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText('该用户已有记录，请在用户列表中编辑');
    expect(puts).toBe(0);
  });

  test('requires at least one package before saving', async ({ page }) => {
    const dialog = await openAdd(page);
    await dialog.getByLabel('用户 ID').fill('no-packages');
    let requests = 0;
    page.on('request', r => { if (r.url().endsWith(url('no-packages'))) requests++; });
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText('请至少添加一份额度包');
    expect(requests).toBe(0);
  });
});

test('Chinese descriptions persist through all four configuration editors', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  for (const [nav, code, description] of [
    ['Service 服务', 'child', '基础体检服务\n按会员等级核对权益'],
    ['Level 等级', 'basic', '缴费业主免费，家庭2人'],
    ['Quota 配额', 'visits', '基础体检次数'],
    ['等级额度', 'basic', '每月4次；每天表示不限次数'],
  ]) {
    await page.locator('.nav-btn').filter({ hasText: nav }).click();
    const row = page.locator('tbody tr').filter({ has: page.locator('td .code').filter({ hasText: new RegExp(`^${code}$`) }) }).first();
    await row.getByRole('button', { name: '操作菜单' }).click();
    await page.getByRole('menuitem', { name: '编辑', exact: true }).click();
    const input = page.locator('.drawer-panel').getByRole('textbox', { name: nav === '等级额度' ? /额度说明/ : /中文说明/ });
    await expect(input).toHaveAttribute('maxlength', '2000');
    await input.fill(description);
    await page.locator('.drawer-panel').getByRole('button', { name: '保存', exact: true }).click();
    await expect(page.locator('.drawer-panel')).toHaveCount(0);
    if (nav === '等级额度') {
      await row.getByRole('button', { name: /额度说明/ }).hover();
      await expect(page.getByRole('tooltip')).toHaveText(description);
    } else await expect(row.locator('.description-text')).toHaveText(description);
    await page.reload();
    await page.locator('.nav-btn').filter({ hasText: nav }).click();
    await row.getByRole('button', { name: '操作菜单' }).click();
    await page.getByRole('menuitem', { name: '编辑', exact: true }).click();
    await expect(input).toHaveValue(description);
    await page.locator('.drawer-head button').click();
  }
});


test('limits distinguish service help from quota rules and show shared services', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.locator('.nav-btn').filter({ hasText: '等级额度' }).click();
  await expect(page.getByRole('columnheader', { name: '关联 Service（服务说明）' })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: '额度说明', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: '服务、配额与等级关系' })).toHaveCount(0);
  const row = page.locator('tbody tr').filter({ hasText: 'visits' });
  await expect(row).toContainText('basic');
  await expect(row).toContainText('每周');
  await expect(row.locator('.related-service .code')).toHaveText(['medication_ai', 'ai_chat', 'session']);
  const help = row.getByRole('button', { name: 'ai_chat 服务说明', exact: true });
  await help.hover();
  await expect(page.getByRole('tooltip')).toHaveText('健康管家与报告解读，共享每月AI额度');
  await page.mouse.move(0, 0);
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await help.focus();
  await expect(page.getByRole('tooltip')).toHaveText('健康管家与报告解读，共享每月AI额度');
  await expect(page.locator('.ant-tooltip:not(.ant-tooltip-hidden)')).toHaveCSS('opacity', '1');
  await page.screenshot({ path: 'test-results/service-help.png', fullPage: true });
});


test('large limits use thousands separators while editors keep numeric values', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.locator('.nav-btn').filter({ hasText: '等级额度' }).click();
  const row = page.locator('tbody tr').filter({ hasText: 'visits' });
  await row.getByRole('button', { name: '操作菜单' }).click();
  await page.getByRole('menuitem', { name: '编辑', exact: true }).click();
  const limit = page.locator('.drawer-panel input[type=number]');
  await limit.fill('1000000');
  await page.locator('.drawer-panel').getByRole('button', { name: '保存', exact: true }).click();
  await expect(page.locator('.drawer-panel')).toHaveCount(0);
  await expect(row.getByRole('cell', { name: '1,000,000', exact: true })).toBeVisible();
  await row.getByRole('button', { name: '操作菜单' }).click();
  await page.getByRole('menuitem', { name: '编辑', exact: true }).click();
  await expect(limit).toHaveValue('1000000');
});


test('service switch persists and editing notes cannot silently re-enable it', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.locator('.nav-btn').filter({ hasText: 'Service 服务' }).click();
  const toggle = page.getByRole('switch', { name: 'medication_ai 启用状态', exact: true });
  await expect(toggle).toBeChecked();
  const before = await toggle.boundingBox();
  expect(before!.height).toBe(22);
  const originalRow = await toggle.locator('xpath=ancestor::tr').elementHandle();
  const originalSwitch = await toggle.elementHandle();
  const reads: string[] = [];
  page.on('request', request => { if (request.method() === 'GET' && request.url().includes('/v1/admin/')) reads.push(request.url()); });
  await toggle.scrollIntoViewIfNeeded();
  const scroll = await page.evaluate(() => ({ x: window.scrollX, y: window.scrollY }));
  await toggle.click();
  await expect(toggle).not.toBeChecked();
  await expect(toggle).toBeEnabled();
  expect(reads).toEqual([]);
  expect(await originalRow!.evaluate(el => el.isConnected)).toBe(true);
  expect(await originalSwitch!.evaluate(el => el.isConnected)).toBe(true);
  expect(await page.evaluate(() => ({ x: window.scrollX, y: window.scrollY }))).toEqual(scroll);
  // Opening the editor immediately must read the row's new state, without a refresh.
  await toggle.locator('xpath=ancestor::tr').getByRole('button', { name: '操作菜单' }).click();
  await page.getByRole('menuitem', { name: '编辑', exact: true }).click();
  await expect(page.getByLabel('启用服务', { exact: false })).not.toBeChecked();
  await page.locator('.drawer-head button').click();
  await page.reload();
  await page.locator('.nav-btn').filter({ hasText: 'Service 服务' }).click();
  await expect(toggle).not.toBeChecked();
  const row = page.locator('tbody tr').filter({ has: toggle });
  await row.getByRole('button', { name: '操作菜单' }).click();
  await page.getByRole('menuitem', { name: '编辑', exact: true }).click();
  await expect(page.getByLabel('启用服务', { exact: false })).not.toBeChecked();
  await page.locator('.drawer-panel').getByRole('button', { name: '保存', exact: true }).click();
  await expect(page.locator('.drawer-panel')).toHaveCount(0);
  await expect(toggle).not.toBeChecked();
  await page.screenshot({ path: 'test-results/service-disabled.png', fullPage: true });
  await toggle.click();
  await expect(toggle).toBeChecked();
});


test('enabled and disabled switch text and handle are vertically centered', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.locator('.nav-btn').filter({ hasText: 'Service 服务' }).click();
  const toggle = page.getByRole('switch', { name: 'medication_ai 启用状态', exact: true });
  for (const enabled of [false, true]) {
    await toggle.click();
    await expect(toggle).toBeChecked({ checked: enabled });
    await expect.poll(() => toggle.evaluate(el => {
      const checked = el.getAttribute('aria-checked') === 'true';
      const track = el.getBoundingClientRect();
      const label = el.querySelector(checked ? '.ant-switch-inner-checked' : '.ant-switch-inner-unchecked')!;
      const range = document.createRange(); range.selectNodeContents(label);
      const text = range.getBoundingClientRect();
      const handle = el.querySelector('.ant-switch-handle')!.getBoundingClientRect();
      const center = (track.top + track.bottom) / 2;
      return Math.max(Math.abs((text.top + text.bottom) / 2 - center), Math.abs((handle.top + handle.bottom) / 2 - center));
    })).toBeLessThanOrEqual(1);
  }
});


test('failed switch save keeps its previous value and retries without refreshing', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.locator('.nav-btn').filter({ hasText: 'Service 服务' }).click();
  const toggle = page.getByRole('switch', { name: 'medication_ai 启用状态', exact: true });
  await expect(toggle).toBeChecked();
  await page.route('**/services/medication_ai/status', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ message: '测试保存失败' }) }));
  await toggle.click();
  await expect(page.getByRole('alert')).toContainText('测试保存失败');
  await expect(toggle).toBeChecked();
  await expect(toggle).toBeEnabled();
  await page.unroute('**/services/medication_ai/status');
  await toggle.click();
  await expect(toggle).not.toBeChecked();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await toggle.click();
  await expect(toggle).toBeChecked();
});


test('usage formats thousands, current caps and consumed percentages including unlimited and zero', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.locator('.nav-btn').filter({ hasText: '用量' }).click();
  const table = page.locator('.panel > .table-wrap');
  await expect(table.getByRole('columnheader', { name: '上限', exact: true })).toBeVisible();
  await expect(table.getByRole('columnheader', { name: '消耗百分比', exact: true })).toBeVisible();
  for (const [id, used, cap, percent] of [
    ['10139', '152,159', '500,000', '30.4%'],
    ['10140', '152,159', '不限', '—'],
    ['10141', '0', '0', '—'],
    ['10142', '600,000', '500,000', '120.0%'],
  ]) {
    const row = table.getByRole('row').filter({ has: page.getByRole('cell', { name: id, exact: true }) });
    await expect(row.locator('td').nth(2)).toHaveText(used);
    await expect(row.locator('td').nth(3)).toHaveText(cap);
    await expect(row.locator('td').nth(4)).toHaveText(percent);
  }
  await page.screenshot({ path: 'test-results/usage-limits.png', fullPage: true });
  await page.locator('.search-bar label').filter({ hasText: '用户 ID' }).locator('input').fill('10139');
  await page.locator('.search-bar').getByRole('button', { name: '查询', exact: true }).click();
  const summary = page.getByRole('region', { name: '用户 10139 的配额使用情况' });
  await expect(summary).toContainText('152,159');
  await expect(summary).toContainText('500,000');
  await expect(summary).toContainText('30.4%');
});
