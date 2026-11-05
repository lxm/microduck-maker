// Optional browser regression. Uses an isolated demo server; serial hardware access is blocked.
const { chromium } = require("playwright");
const { spawn } = require("node:child_process");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const root = path.resolve(__dirname, "..");
const server = spawn(
  process.env.FD_PYTHON || "python3",
  [
    "-u",
    "-c",
    `
import tempfile
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from fd_mac import Workbench, make_handler
with tempfile.TemporaryDirectory() as data, patch('feetech.serial.Serial', side_effect=AssertionError('hardware forbidden')):
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(Workbench(data), 'browser-test'))
    print('http://127.0.0.1:' + str(server.server_port), flush=True)
    server.serve_forever()
`,
  ],
  { cwd: root, stdio: ["ignore", "pipe", "pipe"] },
);
let errors = "",
  browser;
server.stderr.on("data", (d) => {
  errors += d;
});
const timeout = setTimeout(() => {
  console.error("Browser regression timed out", errors);
  server.kill();
  process.exit(1);
}, 90000);
async function main() {
  const url = await new Promise((resolve, reject) => {
    server.stdout.once("data", (d) => resolve(d.toString().trim()));
    server.once("exit", (code) =>
      reject(new Error(`Demo server exited ${code}: ${errors}`)),
    );
  });
  browser = await chromium.launch({
    headless: true,
    channel: process.env.FD_BROWSER_CHANNEL || undefined,
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1080 },
    deviceScaleFactor: 1,
  });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto(url);
  await page.waitForFunction(() =>
    document.querySelector('#port option[value="DEMO"]'),
  );
  assert(await page.locator("#search").isDisabled());
  assert(await page.locator('[data-action="move"]').first().isDisabled());
  await page.locator("#port").selectOption("DEMO");
  await page.locator("#connect").click();
  await page.waitForFunction(
    () => !document.querySelector("#demoBanner").hidden,
  );
  await page.locator("#search").click();
  await page.waitForFunction(
    () => document.querySelector("#posValue").textContent === "2300",
  );
  assert.equal(await page.locator(".servo").count(), 4);
  assert(await page.locator('[data-action="move"]').first().isDisabled());
  await page.locator('[data-action="enable"]').click();
  await page.waitForFunction(
    () => !document.querySelector('[data-action="move"]').disabled,
  );
  assert(await page.locator('[onclick="calibrate()"]').isDisabled());
  await page.getByRole("button", { name: "正一度微调", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelector("#posValue").textContent === "2311",
  );
  // Editing a target must not transmit motion.
  await page.locator("#position").fill("2400");
  await page.waitForTimeout(500);
  assert.equal(await page.locator("#goalValue").textContent(), "2311");
  await page.getByRole("button", { name: "写入目标" }).click();
  await page.waitForFunction(
    () => document.querySelector("#posValue").textContent === "2400",
  );
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 CSV" }).click();
  assert((await downloadEvent).suggestedFilename().endsWith(".csv"));
  if (process.env.FD_SCREENSHOTS) {
    fs.mkdirSync(process.env.FD_SCREENSHOTS, { recursive: true });
    await page.screenshot({
      path: path.join(process.env.FD_SCREENSHOTS, "workbench-demo.png"),
      fullPage: true,
    });
  }
  await page.getByRole("button", { name: "关闭扭矩", exact: true }).click();
  await page.waitForFunction(
    () => !document.querySelector('[onclick="calibrate()"]').disabled,
  );
  await page.getByRole("button", { name: "3 · 当前位置设为零位" }).click();
  await page.waitForFunction(
    () => document.querySelector("#posValue").textContent === "2048",
  );
  assert((await page.locator("#torqueState").textContent()).includes("关闭"));
  await page.getByRole("tab", { name: "参数与编程" }).click();
  await page.getByRole("button", { name: "读取参数", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelectorAll("#registers tr").length > 40,
  );
  await page.locator("#registerFilter").fill("位置环P");
  assert.equal(await page.locator("#registers tr").count(), 1);
  await page.locator("#registers input").fill("32");
  await page.locator("#registers button").click();
  await page.waitForFunction(() =>
    document.querySelector("#notice").textContent.includes("写入及回读通过"),
  );
  assert.equal(await page.locator("#registers input").inputValue(), "32");
  const jsonEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "保存 JSON" }).click();
  const download = await jsonEvent;
  const jsonPath = await download.path();
  await page.locator("#loadFile").setInputFiles(jsonPath);
  await page.waitForFunction(() =>
    document.querySelector("#notice").textContent.includes("已载入参数对照"),
  );
  await page.locator("#registerFilter").fill("no-such-register");
  assert((await page.locator("#registers").textContent()).includes("没有匹配"));
  await page.locator("#registerFilter").fill("");
  // Disconnect must clear previous device values from all tabs.
  await page.locator("#connect").click();
  await page.waitForFunction(() =>
    document.querySelector("#registers").textContent.includes("选择舵机后"),
  );
  assert.equal(await page.locator("#posValue").textContent(), "—");
  assert(await page.locator("#demoBanner").isHidden());
  await page.reload();
  await page.locator("#port").selectOption("DEMO");
  await page.locator("#connect").click();
  await page.locator("#search").click();
  await page.waitForFunction(
    () => document.querySelector("#posValue").textContent === "2300",
  );
  // Preserve the server session on reload, while requiring explicit servo selection.
  await page.reload();
  await page.waitForFunction(
    () => document.querySelectorAll(".servo").length === 4,
  );
  assert(await page.locator('[data-action="enable"]').isDisabled());
  await page.locator(".servo").first().click();
  await page.waitForFunction(
    () => !document.querySelector('[data-action="enable"]').disabled,
  );
  // Failed polling clears stale data and disables writes, while keeping unload available.
  await page.route("**/api", (route) =>
    route.request().postDataJSON()?.op === "state"
      ? route.fulfill({
          status: 400,
          contentType: "application/json",
          body: '{"error":"test offline"}',
        })
      : route.continue(),
  );
  await page.waitForFunction(() =>
    document.querySelector("#feedbackState").textContent.includes("通信中断"),
  );
  assert(await page.locator('[data-action="enable"]').isDisabled());
  assert(!(await page.locator("#unload").isDisabled()));
  await page.unroute("**/api");
  await page.waitForFunction(
    () => !document.querySelector('[data-action="enable"]').disabled,
  );
  // A hung request must not keep stale control buttons enabled.
  let releaseState;
  const delayed = new Promise((resolve) => {
    releaseState = resolve;
  });
  await page.route("**/api", async (route) => {
    if (route.request().postDataJSON()?.op === "state") await delayed;
    await route.continue();
  });
  await page.waitForFunction(
    () => document.querySelector("#feedbackState").textContent === "反馈已过期",
  );
  assert(await page.locator('[data-action="enable"]').isDisabled());
  releaseState();
  await page.waitForFunction(
    () => !document.querySelector('[data-action="enable"]').disabled,
  );
  await page.unroute("**/api");
  // Verify keyboard tabs and narrow layouts without horizontal document overflow.
  await page.locator("#tab-debug").focus();
  await page.keyboard.press("ArrowRight");
  assert.equal(
    await page.locator("#tab-program").getAttribute("aria-selected"),
    "true",
  );
  await page.keyboard.press("End");
  assert.equal(
    await page.locator("#tab-about").getAttribute("aria-selected"),
    "true",
  );
  for (const width of [1024, 768, 390, 360]) {
    await page.setViewportSize({ width, height: 900 });
    for (const name of ["运动调试", "参数与编程", "使用说明"]) {
      await page.getByRole("tab", { name, exact: true }).click();
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        `${width}px overflow: ${name}`,
      );
    }
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: "运动调试" }).click();
  if (process.env.FD_SCREENSHOTS)
    await page.screenshot({
      path: path.join(process.env.FD_SCREENSHOTS, "workbench-mobile.png"),
      fullPage: true,
    });
  assert.deepEqual(pageErrors, []);
  console.log(
    "PASS: demo motion, calibration, parameter write/export/import, reconnect, polling failure, keyboard tabs, and 4 responsive widths",
  );
}
main()
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(async () => {
    clearTimeout(timeout);
    if (browser) await browser.close();
    server.kill();
  });
