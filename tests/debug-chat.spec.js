// @ts-check
const { test, expect } = require('@playwright/test');

test('调试聊天功能', async ({ page }) => {
    test.setTimeout(60000);
    // 启用控制台日志
    page.on('console', (msg) => {
        console.log(`Console [${msg.type()}]: ${msg.text()}`);
    });

    page.on('pageerror', (error) => {
        console.log(`Page Error: ${error.message}`);
    });

    // 登录
    await page.goto('/');
    await page.waitForSelector('#loginOverlay', { state: 'visible' });
    await page.fill('#loginUsername', 'zhangsan');
    await page.fill('#loginPassword', '123456');
    await page.click('button[type="submit"]');
    await page.waitForSelector('#appContainer', { state: 'visible', timeout: 10000 });

    // 等待聊天准备就绪
    await page.waitForSelector('#chatHistory', { state: 'visible' });
    await page.waitForSelector('#userInput', { state: 'visible' });
    await page.waitForSelector('#sendBtn', { state: 'visible' });

    // 检查初始消息数量
    const initialMessages = await page.locator('.message').count();
    console.log(`Initial messages: ${initialMessages}`);

    // 发送消息
    const message = '你好';
    await page.fill('#userInput', message);
    console.log('Filled input with message');

    // 检查输入框的值
    const inputValue = await page.inputValue('#userInput');
    console.log(`Input value: ${inputValue}`);

    // 点击发送按钮
    await page.click('#sendBtn');
    console.log('Clicked send button');

    // 等待一下
    await page.waitForTimeout(1000);

    // 检查消息数量
    const currentMessages = await page.locator('.message').count();
    console.log(`Current messages: ${currentMessages}`);

    // 检查用户消息
    const userMessages = await page.locator('.user-message').count();
    console.log(`User messages: ${userMessages}`);

    // 检查AI消息
    const aiMessages = await page.locator('.ai-message').count();
    console.log(`AI messages: ${aiMessages}`);

    // 等待AI回复
    try {
        await page.waitForSelector('.streaming-message', { state: 'detached', timeout: 15000 });
        console.log('Streaming message completed');
    } catch (e) {
        console.log('Streaming message timeout or error:', e.message);
    }

    // 最终消息数量
    const finalMessages = await page.locator('.message').count();
    console.log(`Final messages: ${finalMessages}`);
});
