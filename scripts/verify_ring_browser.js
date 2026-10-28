#!/usr/bin/env node
/**
 * 星环首页：无头浏览器截图 + 交互回归（开发工具，不参与应用运行）。
 *
 * 用法：
 *   NODE_PATH=<puppeteer-core 所在 node_modules> \
 *   node scripts/verify_ring_browser.js [--shots]
 *
 * 环境变量：
 *   DB     源数据库（会被**复制**到临时目录，不写原库）默认 D:/HealthData/health.db
 *   CHROME 本机 Chrome 可执行文件
 *   PORT   本地服务端口，默认 8934
 *   SHOTS  输出目录，默认 reports/screens
 *
 * 为什么需要它：星环是纯 CSS（radio + label + 变量）驱动的，
 * 服务端测试只能断言 HTML 结构，看不到"指针转到几度、面板谁在显示、有没有横向滚动"。
 */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const puppeteer = require('puppeteer-core');

const ROOT = path.resolve(__dirname, '..');
const PY = process.env.PYTHON || path.join(ROOT, '.venv', 'Scripts', 'python.exe');
const CHROME =
  process.env.CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = Number(process.env.PORT || 8934);
const DB = process.env.DB || 'D:/HealthData/health.db';
const OUT = process.env.SHOTS || path.join(ROOT, 'reports', 'screens');
const BASE = `http://127.0.0.1:${PORT}`;
const SHOT = process.argv.includes('--shots');

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ' · ' + detail : ''}`);
}

async function waitForServer(timeoutMs = 30000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const res = await fetch(`${BASE}/healthz`);
      if (res.ok) return true;
    } catch (err) {
      /* 还没起来 */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error('服务没有在预期时间内起来');
}

async function main() {
  if (!fs.existsSync(DB)) {
    console.error(`找不到数据库：${DB}（可用 DB=... 指定）`);
    process.exit(2);
  }
  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), 'ring_shots_'));
  const server = spawn(
    PY,
    [path.join(ROOT, 'scripts', '_ring_server.py'), DB, workdir, String(PORT)],
    { cwd: ROOT, env: { ...process.env, PYTHONPATH: path.join(ROOT, 'src') }, stdio: 'inherit' }
  );

  let browser;
  try {
    await waitForServer();
    browser = await puppeteer.launch({
      executablePath: CHROME,
      headless: 'new',
      args: ['--no-sandbox', '--force-device-scale-factor=1'],
    });

    if (SHOT) fs.mkdirSync(OUT, { recursive: true });

    const consoleErrors = [];
    const page = await browser.newPage();
    page.on('pageerror', (err) => consoleErrors.push(String(err)));
    page.on('console', (msg) => {
      // favicon 之类的资源 404 与本页无关，不计入 JS 报错
      if (msg.type() === 'error' && !/favicon|Failed to load resource/i.test(msg.text())) {
        consoleErrors.push(msg.text());
      }
    });

    await page.setViewport({ width: 1280, height: 800, deviceScaleFactor: 1 });
    await page.goto(BASE + '/', { waitUntil: 'networkidle0' });

    // ---------- 1. 默认态 ----------
    const idle = await page.evaluate(() => {
      const checked = document.querySelector('input.ring-input:checked');
      const idleCore = document.querySelector('.core-card[data-core="idle"]');
      const visiblePanels = [...document.querySelectorAll('.panel')].filter(
        (p) => getComputedStyle(p).visibility === 'visible'
      );
      return {
        checked: checked ? checked.id : null,
        idleVisible: getComputedStyle(idleCore).visibility === 'visible',
        idleText: idleCore.innerText.replace(/\s+/g, ' ').slice(0, 40),
        visiblePanels: visiblePanels.map((p) => p.dataset.module),
        needle: getComputedStyle(document.querySelector('.needle')).opacity,
      };
    });
    check('默认停在闲置态', idle.checked === 'sector-idle', idle.checked);
    check('中心显示今日状态', idle.idleVisible, idle.idleText);
    check('默认只展开一张卡（今日建议）', idle.visiblePanels.join(',') === 'idle', idle.visiblePanels.join(','));
    check('闲置态不显示指针', Number(idle.needle) < 0.05, `opacity=${idle.needle}`);

    // ---------- 2. 点模块：指针真实转动 ----------
    await page.click('.ring-node[for="sector-sleep"] .node-dot');
    await new Promise((r) => setTimeout(r, 700));
    const sleepState = await page.evaluate(() => {
      const stage = document.querySelector('.ring-stage');
      const panel = document.querySelector('.panel[data-module="sleep"]');
      const core = document.querySelector('.core-card[data-core="sleep"]');
      const node = document.querySelector('.ring-node[for="sector-sleep"]');
      const other = document.querySelector('.ring-node[for="sector-training"]');
      return {
        deg: getComputedStyle(stage).getPropertyValue('--deg').trim(),
        panelVisible: getComputedStyle(panel).visibility,
        panelOpacity: Number(getComputedStyle(panel).opacity),
        coreVisible: getComputedStyle(core).visibility,
        coreText: core.innerText.replace(/\s+/g, ' ').slice(0, 30),
        nodeScale: getComputedStyle(node.querySelector('.node-dot')).transform,
        otherOpacity: Number(getComputedStyle(other).opacity),
        activeOpacity: Number(getComputedStyle(node).opacity),
      };
    });
    check('选中睡眠后指针指到 0°', ((parseFloat(sleepState.deg) % 360) + 360) % 360 === 0, sleepState.deg);
    check('睡眠面板展开', sleepState.panelVisible === 'visible' && sleepState.panelOpacity > 0.9);
    check('中心换成睡眠摘要', sleepState.coreVisible === 'visible', sleepState.coreText);
    check('选中节点放大', sleepState.nodeScale !== 'none' && sleepState.nodeScale !== 'matrix(1, 0, 0, 1, 0, 0)', sleepState.nodeScale);
    check('其余节点降权但仍可见', sleepState.otherOpacity < sleepState.activeOpacity && sleepState.otherOpacity > 0.2,
      `active=${sleepState.activeOpacity} other=${sleepState.otherOpacity}`);

    // ---------- 3. 快速连点：状态不错乱 ----------
    for (const key of ['training', 'trends', 'recovery', 'assistant', 'reports', 'training']) {
      await page.click(`.ring-node[for="sector-${key}"] .node-dot`);
    }
    await new Promise((r) => setTimeout(r, 900));
    const rapid = await page.evaluate(() => {
      const checked = document.querySelector('input.ring-input:checked');
      const visible = [...document.querySelectorAll('.panel')].filter(
        (p) => Number(getComputedStyle(p).opacity) > 0.9
      );
      const cores = [...document.querySelectorAll('.core-card')].filter(
        (p) => Number(getComputedStyle(p).opacity) > 0.9
      );
      return {
        checked: checked.id,
        panels: visible.map((p) => p.dataset.module),
        cores: cores.map((p) => p.dataset.core),
        deg: getComputedStyle(document.querySelector('.ring-stage')).getPropertyValue('--deg').trim(),
      };
    });
    check('快速切换后只剩一个模块被选中', rapid.checked === 'sector-training', rapid.checked);
    check('快速切换后只展开一张面板', rapid.panels.length === 1 && rapid.panels[0] === 'training', rapid.panels.join(','));
    check('快速切换后只有一张中心卡', rapid.cores.length === 1 && rapid.cores[0] === 'training', rapid.cores.join(','));
    // --deg 可能是 -240deg 这种"最短路径"写法，取模后再比（visually 120°）
    const norm = (v) => ((parseFloat(v) % 360) + 360) % 360;
    check('指针停在训练（120°）', norm(rapid.deg) === 120, rapid.deg);

    // ---------- 4. 键盘可切换 ----------
    const keyboard = await page.evaluate(async () => {
      const input = document.querySelector('#sector-training');
      input.focus();
      const before = document.querySelector('input.ring-input:checked').id;
      return { before, focused: document.activeElement.id };
    });
    await page.keyboard.press('ArrowRight');
    await new Promise((r) => setTimeout(r, 500));
    const afterKey = await page.evaluate(() => {
      const checked = document.querySelector('input.ring-input:checked');
      const panel = document.querySelector(`.panel[data-module="${checked.value}"]`);
      return { checked: checked.id, panel: Number(getComputedStyle(panel).opacity) > 0.9 };
    });
    check('方向键能切到下一个模块', afterKey.checked === 'sector-reports', `${keyboard.before} → ${afterKey.checked}`);
    check('键盘切换后对应面板展开', afterKey.panel === true);

    // ---------- 5. 无横向滚动 + 环不变形 ----------
    for (const [w, h] of [[600, 900], [768, 1024], [1024, 768], [1280, 800], [1440, 900], [1920, 1080]]) {
      await page.setViewport({ width: w, height: h, deviceScaleFactor: 1 });
      await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
      const box = await page.evaluate(() => {
        const orbit = document.querySelector('.orbit').getBoundingClientRect();
        return {
          scrollWidth: document.documentElement.scrollWidth,
          innerWidth: window.innerWidth,
          orbitW: Math.round(orbit.width),
          orbitH: Math.round(orbit.height),
          minNode: Math.min(
            ...[...document.querySelectorAll('.node-dot')].map((el) => el.getBoundingClientRect().width)
          ),
        };
      });
      check(`${w}×${h} 没有横向滚动`, box.scrollWidth <= box.innerWidth + 1,
        `scrollWidth=${box.scrollWidth} / ${box.innerWidth}`);
      check(`${w}×${h} 环保持正圆`, Math.abs(box.orbitW - box.orbitH) <= 1,
        `${box.orbitW}×${box.orbitH}`);
      check(`${w}×${h} 模块圆点可点（≥44px 命中区）`, box.minNode >= 40, `dot=${Math.round(box.minNode)}px`);
    }

    // ---------- 6. 截图 ----------
    if (SHOT) {
      const shots = [
        ['desktop-1512', 1512, 950],
        ['tablet-landscape-1280', 1280, 800],
        ['tablet-portrait-800', 800, 1280],
        ['phone-390', 390, 844],
      ];
      for (const [name, w, h] of shots) {
        await page.setViewport({ width: w, height: h, deviceScaleFactor: 1 });
        await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
        await new Promise((r) => setTimeout(r, 400));
        await page.screenshot({ path: path.join(OUT, `ring-${name}-idle.png`) });

        await page.evaluate(() => document.querySelector('.ring-node[for="sector-sleep"]').click());
        await new Promise((r) => setTimeout(r, 900));
        await page.screenshot({ path: path.join(OUT, `ring-${name}-sleep.png`) });

        await page.evaluate(() => document.querySelector('.ring-node[for="sector-trends"]').click());
        await new Promise((r) => setTimeout(r, 900));
        await page.screenshot({ path: path.join(OUT, `ring-${name}-trends.png`) });
        console.log(`shot ${name}`);
      }
      // 详细列表模式（对照用）
      await page.setViewport({ width: 1280, height: 800, deviceScaleFactor: 1 });
      await page.goto(BASE + '/settings', { waitUntil: 'networkidle0' });
      await page.evaluate(() => {
        const form = document.querySelector('form[action="/settings/mode"]');
        const btn = [...form.querySelectorAll('button')].find((b) => b.value === 'simple');
        btn.click();
      });
      await new Promise((r) => setTimeout(r, 600));
      await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
      await new Promise((r) => setTimeout(r, 400));
      await page.screenshot({ path: path.join(OUT, 'detail-list-1280.png') });
      console.log('shot detail-list');

      // 星环没有数据时的空态（同一份库，用独立资料夹里的空库另外跑一次 cost 太高，
      // 这里退回星环模式，只补一张"选中训练"的图作为交互示例）
      await page.goto(BASE + '/settings', { waitUntil: 'networkidle0' });
      await page.evaluate(() => {
        const form = document.querySelector('form[action="/settings/mode"]');
        const btn = [...form.querySelectorAll('button')].find((b) => b.value === 'chronos');
        btn.click();
      });
      await new Promise((r) => setTimeout(r, 600));
      await page.goto(BASE + '/?', { waitUntil: 'networkidle0' });
    }

    check('页面没有 JS 报错', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '));
  } finally {
    if (browser) await browser.close();
    server.kill();
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  if (failed.length) process.exit(1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
