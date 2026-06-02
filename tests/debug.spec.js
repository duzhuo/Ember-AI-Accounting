// @ts-check
const { test, expect } = require('@playwright/test');

test('调试登录流程', async ({ page }) => {
    // 启用控制台日志
    page.on('console', (msg) => {
        if (msg.type() === 'error') {
            console.log(`Console Error: ${msg.text()}`);
        }
    });

    page.on('pageerror', (error) => {
        console.log(`Page Error: ${error.message}`);
    });

    // 访问页面
    await page.goto('/');

    // 等待页面加载
    await page.waitForSelector('#loginOverlay', { state: 'visible' });

    // 检查表单元素
    const usernameInput = page.locator('#loginUsername');
    const passwordInput = page.locator('#loginPassword');
    const submitBtn = page.locator('button[type="submit"]');

    await expect(usernameInput).toBeVisible();
    await expect(passwordInput).toBeVisible();
    await expect(submitBtn).toBeVisible();

    // 填写表单
    await usernameInput.fill('admin');
    await passwordInput.fill('admin123');

    // 点击登录
    await submitBtn.click();

    // 等待一下看看发生了什么
    await page.waitForTimeout(2000);

    // 检查appContainer的状态
    const appContainer = page.locator('#appContainer');
    const isVisible = await appContainer.isVisible();
    console.log(`appContainer visible: ${isVisible}`);

    // 检查loginOverlay的状态
    const loginOverlay = page.locator('#loginOverlay');
    const isLoginVisible = await loginOverlay.isVisible();
    console.log(`loginOverlay visible: ${isLoginVisible}`);

    // 检查是否有错误信息
    const loginError = page.locator('#loginError');
    const errorVisible = await loginError.isVisible();
    console.log(`loginError visible: ${errorVisible}`);

    if (errorVisible) {
        const errorText = await loginError.textContent();
        console.log(`Error text: ${errorText}`);
    }
});
