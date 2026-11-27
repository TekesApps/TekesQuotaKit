import { expect, test, type Page } from '@playwright/test';

async function credentials(page: Page, username: string, password: string) {
  await page.goto('/user-quota/admin');
  await page.getByLabel('账号', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill(password);
  await page.getByRole('button', { name: /登录管理平台/ }).click();
}

test('security operator sees identities and can revoke a named session', async ({ page }) => {
  await credentials(page, 'security', 'Console-Test43!');
  await expect(page.getByRole('heading', { name: '安全管理员' })).toBeVisible();
  const ownAccount = page.getByRole('row').filter({ has: page.getByRole('cell', {name: 'security', exact: true}) });
  await ownAccount.getByRole('button', {name: '注销会话'}).click();
  await expect(page.getByRole('status')).toContainText('已注销 security');
  await page.reload();
  await expect(page.getByRole('heading', { name: '登录工作台' })).toBeVisible();
});

test('expired auditor rotates their password before accessing the read-only audit view', async ({page}) => {
  await credentials(page, 'expired-fixture', 'Fixture-Rotate45!');
  await expect(page.getByRole('alert')).toContainText('密码已到期');
  await page.getByLabel(/^新密码/).fill('Fixture-Renew46!');
  await page.getByRole('button', {name:'更改密码并登录'}).click();
  await expect(page.getByRole('heading', {name:'管理操作'})).toBeVisible();
  await expect(page.getByRole('cell', {name:'rotate password',exact:true})).toBeVisible();
  await expect(page.getByRole('heading', {name:'登录记录'})).toBeVisible();
  await expect(page.getByRole('button', {name:'注销会话'})).toHaveCount(0);
});
