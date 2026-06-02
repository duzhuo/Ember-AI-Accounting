# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: app.spec.js >> 聊天功能 >> 应该开始新对话
- Location: tests/app.spec.js:163:5

# Error details

```
Test timeout of 30000ms exceeded while running "beforeEach" hook.
```

```
Error: page.goto: Test timeout of 30000ms exceeded.
Call log:
  - navigating to "http://localhost:8000/", waiting until "load"

```

# Page snapshot

```yaml
- generic [ref=e3]:
  - generic [ref=e4]:
    - img [ref=e6]
    - generic [ref=e9]: Ember AI
  - heading "登录系统" [level=2] [ref=e10]
  - generic [ref=e11]:
    - generic [ref=e12]:
      - generic [ref=e13]: 用户名
      - textbox "用户名" [ref=e14]:
        - /placeholder: 请输入用户名
    - generic [ref=e15]:
      - generic [ref=e16]: 密码
      - textbox "密码" [ref=e17]:
        - /placeholder: 请输入密码
    - button "登录" [ref=e18] [cursor=pointer]
  - paragraph [ref=e19]: 默认管理员账号：admin / admin123
```

# Test source

```ts
  1   | // @ts-check
  2   | const { test, expect } = require('@playwright/test');
  3   | 
  4   | // ── Test Data ────────────────────────────────────────────────────────────────
  5   | 
  6   | const TEST_USERS = {
  7   |     admin: { username: 'admin', password: 'admin123' },
  8   |     zhangsan: { username: 'zhangsan', password: '123456' },
  9   |     duzhuo: { username: 'duzhuo', password: '123456' },
  10  | };
  11  | 
  12  | // ── Helper Functions ─────────────────────────────────────────────────────────
  13  | 
  14  | /**
  15  |  * Login with given credentials
  16  |  */
  17  | async function login(page, username, password) {
> 18  |     await page.goto('/');
      |                ^ Error: page.goto: Test timeout of 30000ms exceeded.
  19  |     await page.waitForSelector('#loginOverlay', { state: 'visible' });
  20  | 
  21  |     await page.fill('#loginUsername', username);
  22  |     await page.fill('#loginPassword', password);
  23  |     await page.click('button[type="submit"]');
  24  | 
  25  |     // Wait for login to complete
  26  |     await page.waitForSelector('#appContainer', { state: 'visible', timeout: 5000 });
  27  | }
  28  | 
  29  | /**
  30  |  * Wait for chat to be ready
  31  |  */
  32  | async function waitForChatReady(page) {
  33  |     await page.waitForSelector('#chatHistory', { state: 'visible' });
  34  |     await page.waitForSelector('#userInput', { state: 'visible' });
  35  |     await page.waitForSelector('#sendBtn', { state: 'visible' });
  36  | }
  37  | 
  38  | /**
  39  |  * Send a chat message and wait for response
  40  |  */
  41  | async function sendChatMessage(page, message) {
  42  |     await page.fill('#userInput', message);
  43  |     await page.click('#sendBtn');
  44  | 
  45  |     // Wait for AI response
  46  |     await page.waitForSelector('.ai-message:last-child', { timeout: 10000 });
  47  | }
  48  | 
  49  | /**
  50  |  * Logout
  51  |  */
  52  | async function logout(page) {
  53  |     await page.click('#logoutBtn');
  54  |     await page.waitForSelector('#loginOverlay', { state: 'visible' });
  55  | }
  56  | 
  57  | // ── Login/Logout Tests ───────────────────────────────────────────────────────
  58  | 
  59  | test.describe('登录/登出功能', () => {
  60  |     test('应该显示登录页面', async ({ page }) => {
  61  |         await page.goto('/');
  62  | 
  63  |         // 检查登录表单存在
  64  |         await expect(page.locator('#loginOverlay')).toBeVisible();
  65  |         await expect(page.locator('#loginUsername')).toBeVisible();
  66  |         await expect(page.locator('#loginPassword')).toBeVisible();
  67  |         await expect(page.locator('button[type="submit"]')).toBeVisible();
  68  |     });
  69  | 
  70  |     test('应该成功登录管理员账号', async ({ page }) => {
  71  |         await login(page, TEST_USERS.admin.username, TEST_USERS.admin.password);
  72  | 
  73  |         // 验证登录成功
  74  |         await expect(page.locator('#appContainer')).toBeVisible();
  75  |         await expect(page.locator('#userDisplayName')).toContainText('系统管理员');
  76  |     });
  77  | 
  78  |     test('应该显示错误信息当密码错误时', async ({ page }) => {
  79  |         await page.goto('/');
  80  |         await page.fill('#loginUsername', 'admin');
  81  |         await page.fill('#loginPassword', 'wrongpassword');
  82  |         await page.click('button[type="submit"]');
  83  | 
  84  |         // 验证错误信息显示
  85  |         await expect(page.locator('#loginError')).toBeVisible();
  86  |         await expect(page.locator('#loginError')).toContainText('用户名或密码错误');
  87  |     });
  88  | 
  89  |     test('应该成功登出', async ({ page }) => {
  90  |         await login(page, TEST_USERS.admin.username, TEST_USERS.admin.password);
  91  | 
  92  |         // 点击登出按钮
  93  |         await page.click('#logoutBtn');
  94  | 
  95  |         // 验证返回登录页面
  96  |         await expect(page.locator('#loginOverlay')).toBeVisible();
  97  |         await expect(page.locator('#appContainer')).toBeHidden();
  98  |     });
  99  | });
  100 | 
  101 | // ── Chat Tests ───────────────────────────────────────────────────────────────
  102 | 
  103 | test.describe('聊天功能', () => {
  104 |     test.beforeEach(async ({ page }) => {
  105 |         await login(page, TEST_USERS.zhangsan.username, TEST_USERS.zhangsan.password);
  106 |         await waitForChatReady(page);
  107 |     });
  108 | 
  109 |     test('应该显示欢迎消息', async ({ page }) => {
  110 |         // 检查聊天历史中有AI消息
  111 |         const messages = page.locator('.ai-message');
  112 |         await expect(messages.first()).toBeVisible();
  113 |     });
  114 | 
  115 |     test('应该发送消息并收到回复', async ({ page }) => {
  116 |         const message = '你好';
  117 | 
  118 |         // 记录初始消息数量
```