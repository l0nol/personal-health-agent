#!/usr/bin/env node
/**
 * T05 时钟模式浏览器级验证（开发工具，不参与应用运行）。
 *
 * 为什么需要它：表盘是纯 CSS 的 radio + label 交互，服务端测试只能断言 HTML 结构，
 * 看不到"点击后哪个预览可见、环转到多少度、焦点环落在哪个扇区"。
 *
 * 用法：
 *   1) 安装一次性依赖（不进项目依赖）：
 *        npm install puppeteer-core --prefix <某个临时目录>
 *   2) 设置 CHROME_PATH 指向本机 Chrome（默认 Windows 安装路径）
 *   3) 在项目根执行：
 *        NODE_PATH=<临时目录>/node_modules node scripts/verify_t05_browser.js
 *
 * 环境变量：CHROME_PATH / PORT（默认 8932）/ DB（默认系统临时目录下 t05_verify.db）
 * 说明：只起本地只读服务 + 无头 Chrome，不写业务数据（示例数据由本脚本自建，可重复执行）。
 */
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const puppeteer = require('puppeteer-core');

const ROOT = path.resolve(__dirname, '..');
const PY = process.env.PYTHON || path.join(ROOT, '.venv', 'Scripts', 'python.exe');
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = Number(process.env.PORT || 8932);
const DB = process.env.DB || path.join(os.tmpdir(), 't05_verify.db');
const BASE = `http://127.0.0.1:${PORT}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForServer(tries = 60) {
  for (let i = 0; i < tries; i++) {
    try {
      const res = await fetch(`${BASE}/healthz`);
      if (res.ok) return true;
    } catch (e) { /* 还没起来 */ }
    await sleep(250);
  }
  return false;
}

(async () => {
  if (!fs.existsSync(CHROME)) {
    console.error(`找不到 Chrome：${CHROME}（用 CHROME_PATH 指定）`);
    process.exit(1);
  }
  const server = spawn(PY, ['-m', 'health_agent.cli', 'serve', '--db', DB, '--port', String(PORT)], {
    cwd: ROOT, stdio: 'ignore',
  });
  const results = [];
  const check = (name, ok, extra = '') => {
    results.push(ok);
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? '  ' + extra : ''}`);
  };

  try {
    if (!await waitForServer()) {
      console.error('服务未在 15 秒内就绪');
      process.exitCode = 1;
      return;
    }
    const browser = await puppeteer.launch({
      executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-gpu'],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 900 });
    await page.goto(`${BASE}/chronos`, { waitUntil: 'networkidle0' });

    const visible = (sel) => page.$eval(sel, (el) => getComputedStyle(el).display !== 'none');

    check('默认显示睡眠预览', await visible('.core-panel[data-sector="sleep"]'));
    check('默认隐藏趋势预览', !(await visible('.core-panel[data-sector="trends"]')));

    await page.click('label[for="sector-trends"]');
    await sleep(600);
    check('点击后显示趋势预览', await visible('.core-panel[data-sector="trends"]'));
    check('点击后隐藏睡眠预览', !(await visible('.core-panel[data-sector="sleep"]')));
    const t = await page.$eval('.dial-ring', (el) => getComputedStyle(el).transform);
    check('环旋转到 -60deg', Math.abs(parseFloat(t.split('(')[1].split(',')[1]) + Math.sin(Math.PI / 3)) < 0.02, t);

    await page.click('label[for="sector-settings"]');
    await sleep(700);
    check('点击设置显示设置预览', await visible('.core-panel[data-sector="settings"]'));

    await page.focus('#sector-settings');
    await page.keyboard.press('ArrowRight');
    await sleep(600);
    check('方向键切回睡眠', await visible('.core-panel[data-sector="sleep"]'));
    const focus = await page.evaluate(() => ({
      active: document.activeElement.id,
      sleep: getComputedStyle(document.querySelector('label[for="sector-sleep"]')).boxShadow,
      trends: getComputedStyle(document.querySelector('label[for="sector-trends"]')).boxShadow,
    }));
    check('键盘焦点在睡眠扇区', focus.active === 'sector-sleep', focus.active);
    check('焦点环只画在当前扇区', focus.sleep !== 'none' && focus.trends === 'none');

    await Promise.all([
      page.waitForNavigation({ waitUntil: 'domcontentloaded' }),
      page.click('.core-enter[data-sector="sleep"]'),
    ]);
    check('进入睡眠打开 /sleep', page.url().endsWith('/sleep'), page.url());

    const reduced = await browser.newPage();
    await reduced.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
    await reduced.goto(`${BASE}/chronos`, { waitUntil: 'networkidle0' });
    const dur = await reduced.$eval('.dial-ring', (el) => getComputedStyle(el).transitionDuration);
    check('减少动态时无过渡', dur === '0s' || dur === '', `transition-duration=${dur}`);
    await reduced.click('label[for="sector-imports"]');
    await sleep(100);
    check('减少动态时即时切换', await reduced.$eval('.core-panel[data-sector="imports"]', (el) => getComputedStyle(el).display !== 'none'));

    const widths = [1440, 1024, 900, 800, 768, 600];
    let overflow = false;
    for (const width of widths) {
      await reduced.setViewport({ width, height: 1024 });
      await sleep(250);
      const sw = await reduced.evaluate(() => document.documentElement.scrollWidth);
      if (sw > width + 1) {
        overflow = true;
        console.log(`  overflow @${width}px: scrollWidth=${sw}`);
      }
    }
    check('600–1440px 无横向滚动', !overflow);
    check('窄屏仍是 6 扇区', (await reduced.$$('label.sector')).length === 6);

    await browser.close();
    const failed = results.filter((ok) => !ok).length;
    console.log(`\nRESULT: ${results.length - failed}/${results.length} passed`);
    process.exitCode = failed ? 1 : 0;
  } finally {
    server.kill();
  }
})();
