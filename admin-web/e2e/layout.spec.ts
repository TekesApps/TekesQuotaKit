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
    const control = label.querySelector<HTMLElement>('.ant-select, :scope > input:not([type=checkbox]):not([type=radio])');
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

  // Terminology: Quota is 配额 (what is counted); membership amounts are 档位额度.
  const nav = (await page.locator('.nav-btn').allTextContents()).map(t => t.trim());
  expect(nav).toEqual(expect.arrayContaining(['Quota 配额', '会员档位', '档位额度', '权益方案', '用户权益']));
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
    if (name === '权益方案') await expect(page.getByRole('button', { name: '新增方案', exact: true })).toBeEnabled();
    if (name === '客户端凭据') await expect(page.getByRole('button', { name: '签发服务凭据', exact: true })).toBeEnabled();
    const pageForm = await measure(page, '.main form');
    if (pageForm.length) expectAligned(`${name} (page)`, pageForm);
    for (const height of await page.locator('.search-bar button').evaluateAll(els => els.map(el => el.getBoundingClientRect().height)))
      expect(height, `${name} search button height`).toBe(CONTROL_HEIGHT);
    const buttons = page.locator('.panel-head button');
    for (let b = 0; b < await buttons.count(); b++) {
      if (!(await buttons.nth(b).isEnabled())) continue;
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

test('looks up one user\'s usage and drills from a member tier to 档位额度', async ({ page }) => {
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

  await page.getByRole('button', { name: '会员档位' }).click();
  await page.locator('tbody tr').filter({ hasText: 'basic' }).getByRole('button', { name: /1 项配额/ }).click();
  await expect(page.getByRole('heading', { name: '档位额度', level: 1 })).toBeVisible();
  await expect(search('会员档位')).toHaveValue('basic');
  await expect(page.locator('.panel:not(.relationship-overview) tbody tr')).toHaveCount(1);
  await page.locator('.search-bar').getByRole('button', { name: '清除' }).click();
  await expect(search('会员档位')).toHaveValue('');
});

test('edits ordered member entitlements for a user and retains independent bindings', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.locator('.nav-btn').filter({ hasText: '用户权益' }).click();
  await expect(page.getByRole('heading', { name: '辅助额度包模板', exact: true })).toHaveCount(0);
  const user = page.getByRole('row').filter({ has: page.getByRole('cell', { name: '42', exact: true }) });
  await user.getByRole('button', { name: '编辑会员权益' }).click();
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
    await dialog.getByRole('button', { name: '添加会员权益', exact: true }).click();
    await dialog.getByRole('combobox', { name: `会员权益 ${i}`, exact: true }).click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: 'basic' }).click();
    await expect(page.locator('.ant-select-dropdown:visible')).toHaveCount(0);
  }
  expectAligned('user ordered packages', await measure(page, '.drawer-panel'));
  await dialog.getByRole('button', { name: '保存', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(user.getByRole('cell', { name: '3', exact: true })).toBeVisible();
  await user.getByRole('button', { name: '编辑会员权益' }).click();
  dialog = page.getByRole('dialog');
  await expect(dialog.locator('.user-package')).toHaveCount(3);
  const originalId = await page.request.get('/user-quota/v1/admin/tenants/e2e-business/subjects/42/level-packages', { headers: { 'X-Admin-Request': '1' } }).then(r => r.json()).then(data => data.packages[0].id);
  await dialog.getByRole('button', { name: '下移会员权益 1', exact: true }).click();
  await dialog.getByRole('button', { name: '保存', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const state = await page.request.get('/user-quota/v1/admin/tenants/e2e-business/subjects/42/level-packages', { headers: { 'X-Admin-Request': '1' } }).then(r => r.json());
  expect(state.packages[1].id).toBe(originalId);
  expect(new Set(state.packages.map((p: { id: number }) => p.id)).size).toBe(3);
  await user.getByRole('button', { name: '编辑会员权益' }).click();
  dialog = page.getByRole('dialog');
  await dialog.locator('.user-package').last().getByRole('button', { name: '移除', exact: true }).click();
  await dialog.getByRole('button', { name: '保存', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(user.getByRole('cell', { name: '2', exact: true })).toBeVisible();
  expect(await page.locator('body').evaluate(el => parseFloat(getComputedStyle(el).fontSize))).toBe(12.8);
});

test.describe('adding a user\'s member entitlements', () => {
  const url = (id: string) => `/user-quota/v1/admin/tenants/e2e-business/subjects/${id}/level-packages`;
  const admin = { 'X-Admin-Request': '1' };
  async function openAdd(page: Page) {
    await page.goto('/user-quota/admin');
    await page.getByLabel('账号').fill('e2e');
    await page.getByLabel('密码').fill('e2e-console-password');
    await page.getByRole('button', { name: /登录管理平台/ }).click();
    await page.locator('.nav-btn').filter({ hasText: '用户权益' }).click();
    await page.getByRole('button', { name: '设置用户权益', exact: true }).click();
    return page.getByRole('dialog');
  }
  async function addBasic(page: Page, dialog: ReturnType<Page['getByRole']>) {
    await dialog.getByRole('button', { name: '添加会员权益', exact: true }).click();
    await dialog.getByRole('combobox', { name: '会员权益 1', exact: true }).click();
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
    await expect(dialog.getByRole('alert')).toContainText('请至少添加一项会员权益');
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
    ['会员档位', 'basic', '缴费业主免费，家庭2人'],
    ['Quota 配额', 'visits', '基础体检次数'],
    ['档位额度', 'basic', '每月4次；每天表示不限次数'],
  ]) {
    await page.locator('.nav-btn').filter({ hasText: nav }).click();
    const row = page.locator('tbody tr').filter({ has: page.locator('td .code').filter({ hasText: new RegExp(`^${code}$`) }) }).first();
    await row.getByRole('button', { name: '操作菜单' }).click();
    await page.getByRole('menuitem', { name: '编辑', exact: true }).click();
    const input = page.locator('.drawer-panel').getByRole('textbox', { name: nav === '档位额度' ? /额度说明/ : /中文说明/ });
    await expect(input).toHaveAttribute('maxlength', '2000');
    await input.fill(description);
    await page.locator('.drawer-panel').getByRole('button', { name: '保存', exact: true }).click();
    await expect(page.locator('.drawer-panel')).toHaveCount(0);
    if (nav === '档位额度') {
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
  await page.locator('.nav-btn').filter({ hasText: '档位额度' }).click();
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
  await page.locator('.nav-btn').filter({ hasText: '档位额度' }).click();
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


async function openServiceStatus(page: Page) {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.locator('.nav-btn').filter({ hasText: 'Service 服务' }).click();
  const control = page.getByRole('combobox', { name: 'medication_ai 服务状态', exact: true });
  const row = page.locator('tbody tr').filter({ has: control });
  return { control, row };
}
async function chooseServiceStatus(page: Page, control: ReturnType<Page['getByRole']>, label: string) {
  await control.click();
  await page.locator('.ant-select-dropdown').getByText(label, { exact: true }).click();
  await expect(control).toBeEnabled();
}

test('incoming service status persists through notes edit and page reload', async ({ page }) => {
  const {control, row} = await openServiceStatus(page);
  await chooseServiceStatus(page, control, '即将开放（incoming）');
  await expect(row).toContainText('即将开放（incoming）');
  await row.getByRole('button', { name: '操作菜单' }).click();
  await page.getByRole('menuitem', { name: '编辑', exact: true }).click();
  await expect(page.locator('.drawer-panel')).toContainText('即将开放（incoming）');
  await page.locator('.drawer-panel').getByRole('button', { name: '保存', exact: true }).click();
  await expect(page.locator('.drawer-panel')).toHaveCount(0);
  await page.reload();
  await page.locator('.nav-btn').filter({ hasText: 'Service 服务' }).click();
  await expect(row).toContainText('即将开放（incoming）');
  await page.screenshot({path:'test-results/service-incoming.png',fullPage:true});
  await chooseServiceStatus(page, control, '已开放（enabled）');
});

test('all three service states save without replacing the row', async ({ page }) => {
  const {control, row} = await openServiceStatus(page);
  const originalRow = await row.elementHandle();
  for (const label of ['隐藏停用（disabled）', '即将开放（incoming）', '已开放（enabled）']) {
    await chooseServiceStatus(page, control, label);
    await expect(row).toContainText(label);
    expect(await originalRow!.evaluate(el => el.isConnected)).toBe(true);
  }
});

test('failed service status save preserves the original value and retries', async ({ page }) => {
  const {control,row} = await openServiceStatus(page);
  await page.route('**/services/medication_ai/status', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ message: '测试保存失败' }) }));
  await chooseServiceStatus(page, control, '即将开放（incoming）');
  await expect(page.getByRole('alert')).toContainText('测试保存失败');
  await expect(row).toContainText('已开放（enabled）');
  await page.unroute('**/services/medication_ai/status');
  await chooseServiceStatus(page, control, '即将开放（incoming）');
  await expect(row).toContainText('即将开放（incoming）');
  await expect(page.getByRole('alert')).toHaveCount(0);
  await chooseServiceStatus(page, control, '已开放（enabled）');
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


test('usage defaults to highest consumption and can switch order while keeping filters', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.locator('.nav-btn').filter({ hasText: '用量' }).click();
  const table = page.locator('.panel > .table-wrap');
  const ids = table.locator('tbody tr td:first-child');
  const order = page.getByRole('combobox', { name: '消耗百分比排序' });
  await expect(page.locator('.toolbar')).toContainText('从高到低');
  await expect(ids).toHaveText(['10142', '10139', '10141', '10140']);
  // Unknown percentages stay last in both directions; a Quota filter survives the change.
  const quota = page.locator('.search-bar label').filter({ hasText: 'Quota 配额' }).locator('input');
  await quota.fill('ai_tokens');
  await page.locator('.search-bar').getByRole('button', { name: '查询', exact: true }).click();
  await order.click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '从低到高' }).click();
  await expect(ids).toHaveText(['10139', '10142', '10141', '10140']);
  await expect(quota).toHaveValue('ai_tokens');
  await order.click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '从高到低' }).click();
  await expect(ids).toHaveText(['10142', '10139', '10141', '10140']);
  await page.screenshot({ path: 'test-results/usage-sorted.png', fullPage: true });
});


test('会员档位 test/formal publication survives editing and can be reversed', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.locator('.nav-btn').filter({ hasText: '会员档位' }).click();
  const row=page.locator('tbody tr').filter({ has:page.getByRole('cell',{name:'basic',exact:true}) });
  for(const [label,text] of [['正式档位（formal）','正式档位'],['测试档位（test）','测试档位']]) {
    await row.getByRole('button',{name:'操作菜单'}).click();
    await page.getByRole('menuitem',{name:'编辑',exact:true}).click();
    await page.getByLabel('档位类型',{exact:false}).click();
    await page.locator('.ant-select-dropdown').getByText(label,{exact:true}).click();
    await page.locator('.drawer-panel').getByRole('button',{name:'保存',exact:true}).click();
    await expect(page.locator('.drawer-panel')).toHaveCount(0);
    await expect(row).toContainText(text);
    await page.reload();
    await page.locator('.nav-btn').filter({hasText:'会员档位'}).click();
    await expect(row).toContainText(text);
  }
  await page.screenshot({path:'test-results/level-publication.png',fullPage:true});
});

test('creates and edits an entitlement plan while preserving generated package identities', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.getByRole('button', { name: '权益方案', exact: true }).click();
  await expect(page.getByRole('button', { name: '新增方案', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '新增方案', exact: true }).click();
  let dialog = page.getByRole('dialog', { name: '新增权益方案', exact: true });
  await expect(dialog.locator('section[aria-label^="会员权益 "]')).toHaveCount(0);
  await dialog.getByLabel('方案名称').fill('居民首次绑定');

  const addLevel = async (code: string) => {
    await dialog.getByRole('combobox', { name: '添加会员权益', exact: true }).click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: code }).first().click();
    await expect(dialog.locator('.ant-select-selection-item')).toHaveCount(0);
  };
  await addLevel('basic');
  await expect(dialog.getByLabel('会员权益 1', { exact: true })).toContainText('basic');
  await expect(dialog.getByLabel('会员权益 1', { exact: true }).getByRole('combobox')).toHaveCount(0);
  const firstDuration = dialog.getByRole('radio', { name: '会员权益 1 按天', exact: true });
  await expect(dialog.getByRole('radio', { name: '会员权益 1 永久', exact: true })).toBeChecked();
  await expect(firstDuration).not.toBeChecked();
  await expect(dialog.getByLabel('会员权益 1 有效天数', { exact: true })).toHaveCount(0);
  await firstDuration.check();
  const firstDays = dialog.getByLabel('会员权益 1 有效天数', { exact: true });
  await expect(firstDays).toHaveValue('');
  await expect(firstDays).toHaveAttribute('required', '');
  let invalidCreates = 0;
  const countInvalidCreates = (request: import('@playwright/test').Request) => {
    if (request.method() === 'POST' && request.url().endsWith('/default-plans')) invalidCreates++;
  };
  page.on('request', countInvalidCreates);
  for (const invalid of ['', '0', '3661', '1.5']) {
    await firstDays.fill(invalid);
    await dialog.getByRole('button', { name: '保存方案', exact: true }).click();
    expect(await firstDays.evaluate((input: HTMLInputElement) => input.validity.valid)).toBe(false);
  }
  expect(invalidCreates).toBe(0);
  page.off('request', countInvalidCreates);
  await firstDays.fill('30');
  await addLevel('basic');
  await dialog.getByRole('radio', { name: '会员权益 2 按天', exact: true }).check();
  await dialog.getByLabel('会员权益 2 有效天数', { exact: true }).fill('7');
  await addLevel('usage-plan');
  await expect(dialog.locator('section[aria-label^="会员权益 "]')).toHaveCount(3);
  expect(await dialog.locator('section[aria-label^="会员权益 "]').allTextContents()).toEqual([
    expect.stringContaining('basic'), expect.stringContaining('basic'), expect.stringContaining('usage-plan'),
  ]);
  await expect(dialog.getByRole('textbox')).toHaveCount(1);

  const createRequestPromise = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/default-plans'));
  await dialog.getByRole('button', { name: '保存方案', exact: true }).click();
  const createRequest = await createRequestPromise;
  const createdPackages = createRequest.postDataJSON().packages as Array<{ code: string; name: string; level_code: string; validity_days: number | null }>;
  expect(createdPackages.map(item => item.level_code)).toEqual(['basic', 'basic', 'usage-plan']);
  expect(createdPackages.map(item => item.name)).toEqual(['basic', 'basic', 'usage-plan']);
  expect(createdPackages.map(item => item.validity_days)).toEqual([30, 7, null]);
  expect(createdPackages.map(item => item.code).every(code => /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(code))).toBe(true);
  expect(new Set(createdPackages.map(item => item.code)).size).toBe(3);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  let row = page.locator('tbody tr').filter({ hasText: '居民首次绑定' });
  await expect(row).toContainText('basic');
  await expect(row).toContainText('usage-plan');
  await expect(row).toContainText('30 天');
  const initialId = await row.locator('td').nth(1).locator('.code').textContent();
  expect(initialId).toBeTruthy();

  await row.getByRole('button', { name: '停用' }).click();
  await expect(row).toContainText('停用');
  await expect(row.getByRole('button', { name: '启用' })).toBeVisible();
  await row.getByRole('button', { name: '启用' }).click();
  await row.getByRole('button', { name: '编辑' }).click();
  dialog = page.getByRole('dialog', { name: '编辑权益方案', exact: true });
  await expect(dialog.locator('section[aria-label^="会员权益 "]')).toHaveCount(3);
  await expect(dialog.getByLabel('会员权益 1', { exact: true })).toContainText('basic');
  await expect(dialog.getByLabel('会员权益 2', { exact: true })).toContainText('basic');
  await dialog.getByLabel('方案名称').fill('居民首次绑定新');
  await dialog.getByLabel('会员权益 1', { exact: true }).getByRole('button', { name: '下移', exact: true }).click();
  const editRequestPromise = page.waitForRequest(request => request.method() === 'PUT' && request.url().endsWith(`/default-plans/${initialId}`));
  await dialog.getByRole('button', { name: '保存方案', exact: true }).click();
  const editRequest = await editRequestPromise;
  const editedPackages = editRequest.postDataJSON().packages as typeof createdPackages;
  expect(editedPackages.map(item => item.code)).toEqual([createdPackages[1].code, createdPackages[0].code, createdPackages[2].code]);
  expect(editedPackages.map(item => item.name)).toEqual(['basic', 'basic', 'usage-plan']);
  expect(editedPackages.map(item => item.level_code)).toEqual(['basic', 'basic', 'usage-plan']);
  row = page.locator('tbody tr').filter({ hasText: '居民首次绑定新' });
  await expect(row).toContainText('usage-plan');
  await expect(row.locator('td').nth(1).locator('.code')).toHaveText(initialId!);

  const admin = { 'X-Admin-Request': '1' };
  const listUrl = '/user-quota/v1/admin/tenants/e2e-business/default-plans?limit=100&offset=0';
  const listBeforeCancel = await page.request.get(listUrl, { headers: admin }).then(response => response.json());
  const savedBeforeCancel = listBeforeCancel.items.find((item: { id: string }) => item.id === initialId);
  expect(savedBeforeCancel.packages.map((item: { code: string }) => item.code)).toEqual(editedPackages.map(item => item.code));

  let writes = 0;
  const countWrites = (request: import('@playwright/test').Request) => {
    if (request.method() === 'PUT' && request.url().endsWith(`/default-plans/${initialId}`)) writes++;
  };
  page.on('request', countWrites);
  await row.getByRole('button', { name: '编辑' }).click();
  dialog = page.getByRole('dialog', { name: '编辑权益方案', exact: true });
  await addLevel('basic');
  await expect(dialog.locator('section[aria-label^="会员权益 "]')).toHaveCount(4);
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  page.off('request', countWrites);
  expect(writes).toBe(0);
  const listAfterCancel = await page.request.get(listUrl, { headers: admin }).then(response => response.json());
  const savedAfterCancel = listAfterCancel.items.find((item: { id: string }) => item.id === initialId);
  expect(savedAfterCancel).toEqual(savedBeforeCancel);

  const plansUrl = '/user-quota/v1/admin/tenants/e2e-business/default-plans';
  const legacyResponse = await page.request.post(plansUrl, { headers: admin, data: {
    name: 'Legacy package identities', enabled: true, packages: [
      { code: 'vip-seven-days', name: 'Legacy VIP custom name', level_code: 'basic', validity_days: 7 },
      { code: 'star-base', name: 'Legacy Star custom name', level_code: 'usage-plan', validity_days: null },
    ],
  } });
  expect(legacyResponse.ok(), await legacyResponse.text()).toBe(true);
  const legacyCreated = await legacyResponse.json();
  await page.reload();
  const legacyRow = page.locator('tbody tr').filter({ hasText: 'Legacy package identities' });
  await legacyRow.getByRole('button', { name: '编辑' }).click();
  dialog = page.getByRole('dialog', { name: '编辑权益方案', exact: true });
  await expect(dialog.getByLabel('会员权益 1', { exact: true })).toContainText('Legacy VIP custom name');
  await expect(dialog.getByLabel('会员权益 2', { exact: true })).toContainText('Legacy Star custom name');
  await expect(dialog.getByRole('radio', { name: '会员权益 1 按天', exact: true })).toBeChecked();
  await expect(dialog.getByLabel('会员权益 1 有效天数', { exact: true })).toHaveValue('7');
  const starDuration = dialog.getByRole('radio', { name: '会员权益 2 永久', exact: true });
  await expect(starDuration).toBeChecked();
  await dialog.getByRole('radio', { name: '会员权益 2 按天', exact: true }).check();
  await expect(starDuration).not.toBeChecked();
  await dialog.getByLabel('会员权益 2 有效天数', { exact: true }).fill('5');
  await starDuration.check();
  await expect(dialog.getByRole('radio', { name: '会员权益 2 按天', exact: true })).not.toBeChecked();
  await expect(dialog.getByLabel('会员权益 2 有效天数', { exact: true })).toHaveCount(0);
  await dialog.getByLabel('会员权益 1', { exact: true }).getByRole('button', { name: '下移', exact: true }).click();
  await dialog.getByRole('button', { name: '保存方案', exact: true }).click();
  const legacyList = await page.request.get(listUrl, { headers: admin }).then(response => response.json());
  const legacySaved = legacyList.items.find((item: { id: string }) => item.id === legacyCreated.id);
  expect(legacySaved.id).toBe(legacyCreated.id);
  expect(legacySaved.packages.map((item: { code: string; name: string; level_code: string; validity_days: number | null }) => ({
    code: item.code, name: item.name, level_code: item.level_code, validity_days: item.validity_days,
  }))).toEqual([
    { code: 'star-base', name: 'Legacy Star custom name', level_code: 'usage-plan', validity_days: null },
    { code: 'vip-seven-days', name: 'Legacy VIP custom name', level_code: 'basic', validity_days: 7 },
  ]);
});

test('entitlement-plan editor fills desktop and 390px viewports without horizontal clipping', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.getByRole('button', { name: '权益方案', exact: true }).click();
  await expect(page.getByRole('button', { name: '新增方案', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '新增方案', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '新增权益方案', exact: true });
  for (const code of ['basic', 'usage-plan']) {
    await dialog.getByRole('combobox', { name: '添加会员权益', exact: true }).click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: code }).first().click();
  }
  await expect(dialog.locator('section[aria-label^="会员权益 "]')).toHaveCount(2);
  await dialog.getByRole('radio', { name: '会员权益 1 按天', exact: true }).check();
  await dialog.getByLabel('会员权益 1 有效天数', { exact: true }).fill('7');
  // Wait for the desktop-width dropdown's leave animation before resizing.
  await expect(page.locator('.ant-select-dropdown:visible')).toHaveCount(0);
  const checkSize = async (width: number) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 800 });
    const dimensions = await dialog.evaluate(element => {
      const rect = element.getBoundingClientRect();
      return {
        left: rect.left,
        width: rect.width,
        viewport: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        dialogWidth: element.clientWidth,
        dialogScrollWidth: element.scrollWidth,
      };
    });
    expect(dimensions.left).toBe(0);
    expect(dimensions.width).toBe(width);
    expect(dimensions.viewport).toBe(width);
    expect(dimensions.documentWidth).toBeLessThanOrEqual(width);
    expect(dimensions.dialogScrollWidth).toBeLessThanOrEqual(dimensions.dialogWidth);
    const day = (await dialog.getByRole('radio', { name: '会员权益 1 按天', exact: true }).boundingBox())!;
    const days = (await dialog.getByLabel('会员权益 1 有效天数', { exact: true }).boundingBox())!;
    const permanent = (await dialog.getByRole('radio', { name: '会员权益 1 永久', exact: true }).boundingBox())!;
    expect(days.x).toBeGreaterThan(day.x + day.width);
    expect(days.x + days.width).toBeLessThan(permanent.x);
    expect(Math.abs(days.y + days.height / 2 - (day.y + day.height / 2))).toBeLessThanOrEqual(1);
    expect(days.height).toBe(CONTROL_HEIGHT);
    expect(days.width).toBe(80);
    for (const radio of await dialog.getByRole('radio').all()) {
      const bounds = await radio.boundingBox();
      expect(bounds?.width).toBe(16);
      expect(bounds?.height).toBe(16);
    }
  };
  await checkSize(1280);
  await checkSize(390);
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  await expect(dialog).toHaveCount(0);
});

test('private entitlement plans can use a test member tier without promoting it', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await page.getByRole('button', { name: '会员档位', exact: true }).click();
  await page.getByRole('button', { name: '新增会员档位', exact: true }).click();
  await page.getByLabel('档位标识').fill('private-test-level');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  const testLevel = page.locator('tbody tr').filter({ has: page.getByRole('cell', { name: 'private-test-level', exact: true }) });
  await expect(testLevel).toContainText('测试档位');
  await page.getByRole('button', { name: '档位额度', exact: true }).click();
  await page.getByRole('button', { name: '新增档位额度', exact: true }).click();
  await page.locator('.drawer-panel').getByRole('combobox', { name: '会员档位', exact: true }).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: 'private-test-level' }).click();
  await page.locator('.drawer-panel').getByRole('combobox', { name: 'Quota', exact: true }).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: 'visits' }).click();
  await page.locator('.drawer-panel').getByRole('spinbutton', { name: /限额/ }).fill('5');
  await page.getByRole('button', { name: '保存', exact: true }).click();

  await page.getByRole('button', { name: '权益方案', exact: true }).click();
  await expect(page.getByRole('button', { name: '新增方案', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '新增方案', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '新增权益方案', exact: true });
  await dialog.getByLabel('方案名称').fill('测试档位默认方案');
  await dialog.getByRole('combobox', { name: '添加会员权益', exact: true }).click();
  await expect(page.locator('.ant-select-dropdown:visible')).toContainText('private-test-level · 测试档位');
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: 'private-test-level · 测试档位' }).click();
  await dialog.getByRole('button', { name: '保存方案' }).click();
  const plan = page.locator('tbody tr').filter({ hasText: '测试档位默认方案' });
  await expect(plan).toContainText('private-test-level');

  await page.getByRole('button', { name: '会员档位', exact: true }).click();
  await expect(page.locator('tbody tr').filter({ has: page.getByRole('cell', { name: 'private-test-level', exact: true }) })).toContainText('测试档位');
});


test('credential issuance waits for stored identity settings and fails closed on read errors', async ({ page }) => {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号').fill('e2e');
  await page.getByLabel('密码').fill('e2e-console-password');
  await page.getByRole('button', { name: /登录管理平台/ }).click();
  await expect(page.getByText('端到端测试', {exact:true})).toBeVisible();
  let release!: () => void;
  const ready = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/v1/admin/tenants', async route => {
    await ready;
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items:[{tenant_id:'e2e-business',subject_id_definition:'synthetic_user.id'}]})});
  });
  await page.getByRole('button', { name: '客户端凭据', exact:true }).click();
  const issue=page.getByRole('button',{name:'签发会员同步凭据',exact:true});
  try {
    await expect(issue).toBeDisabled();
    await expect(page.locator('.drawer-panel')).toHaveCount(0);
  } finally { release(); }
  await expect(issue).toBeEnabled();
  await issue.click();
  await expect(page.getByLabel('用户 ID 定义',{exact:false})).toHaveValue('synthetic_user.id');
  await expect(page.getByLabel('用户 ID 定义',{exact:false})).toBeDisabled();
  await page.locator('.drawer-head button').click();
  await page.unroute('**/v1/admin/tenants');
  await page.route('**/v1/admin/tenants', route => route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({message:'测试设置读取失败'})}));
  await page.getByRole('button',{name:'会员档位',exact:true}).click();
  await page.getByRole('button',{name:'客户端凭据',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('测试设置读取失败');
  await expect(issue).toBeDisabled();
});
