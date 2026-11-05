"use strict";
const $ = (id) => document.getElementById(id);
let connected = false,
  selected = null,
  ids = [],
  busy = false,
  polling = false,
  state = null,
  samples = [],
  dump = null,
  reference = null,
  session = 0,
  lastFeedback = 0,
  modelId = null;
const num = (id) => Number($(id).value);
function log(text) {
  const el = $("log");
  el.textContent += new Date().toLocaleTimeString() + "  " + text + "\n";
  if (el.textContent.length > 24000)
    el.textContent = el.textContent.slice(-16000);
  el.scrollTop = el.scrollHeight;
}
function notice(text, error = false) {
  $("notice").hidden = false;
  $("notice").className = error ? "error" : "";
  $("notice").textContent = text;
  log(text);
}
async function api(op, args = {}) {
  const response = await fetch("/api", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-FD-Token": token },
    body: JSON.stringify({ op, ...args }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}
function controls() {
  const ready = connected && selected !== null && !busy;
  const fresh = ready && state && Date.now() - lastFeedback < 2000;
  const supported = modelId === 7946,
    positionMode = state && [0, 4].includes(state.mode);
  document.querySelectorAll("[data-servo]").forEach((b) => {
    let enabled = ready;
    if (b.dataset.action === "move")
      enabled = fresh && supported && positionMode && state.torque === 1;
    if (b.dataset.action === "enable")
      enabled = fresh && supported && positionMode && state.torque === 0;
    if (b.dataset.action === "off")
      enabled = fresh && supported && state.torque === 0;
    if (b.dataset.action === "stop")
      enabled = fresh && supported && positionMode;
    if (b.id === "useCurrent") enabled = !!fresh;
    b.disabled = !enabled;
  });
  for (const id of ["search", "start", "end"])
    $(id).disabled = !connected || busy;
  $("connect").disabled = busy;
  $("refresh").disabled = connected || busy;
  $("port").disabled = connected || busy;
  $("baud").disabled = connected || busy;
  $("unload").disabled = !connected || !ids.length || busy;
  document.querySelectorAll(".servo").forEach((b) => (b.disabled = busy));
  const demo = connected && $("port").value === "DEMO";
  $("demoBanner").hidden = !demo;
  $("connection").className =
    "connection" + (connected ? (demo ? " demo" : " online") : "");
  $("stepConnect").className = connected ? "done" : "current";
  $("stepScan").className = ids.length ? "done" : connected ? "current" : "";
  $("stepDebug").className =
    selected !== null ? "done" : ids.length ? "current" : "";
  $("workflowTip").textContent = busy
    ? "正在处理，请稍候…"
    : !connected
      ? "从左侧连接设备开始"
      : !ids.length
        ? "搜索总线上的舵机"
        : selected === null
          ? "在左侧选择一颗舵机"
          : "已选择 ID " + selected;
  $("motionHint").textContent =
    selected === null
      ? "选择舵机后，先读取位置，再在当前位置使能。"
      : !fresh
        ? "等待有效反馈，写入操作暂不可用。"
        : !supported
          ? "当前型号仅支持读取与卸力。"
          : !positionMode
            ? "当前不是位置模式 0/4，请先核对运行模式。"
            : state.torque === 1
              ? "扭矩已开启，可写入目标或用 ±1° 微调。"
              : "扭矩已关闭。运动前请先「在当前位置使能」。";
}
function resetSelection() {
  session++;
  selected = null;
  state = null;
  dump = null;
  modelId = null;
  lastFeedback = 0;
  clearData();
  clearReadout();
  renderRegisters();
  $("selected").textContent = "尚未选择舵机";
  $("model").textContent = "等待选择";
}
async function run(fn) {
  if (busy) return;
  session++;
  busy = true;
  controls();
  try {
    return await fn();
  } catch (e) {
    notice(e.message, true);
  } finally {
    busy = false;
    controls();
  }
}
async function refreshPorts() {
  try {
    const p = await api("ports");
    const old = $("port").value;
    $("port").replaceChildren();
    for (const v of p.ports) {
      const opt = new Option(v.device + " · " + v.description, v.device);
      $("port").add(opt);
    }
    $("port").add(new Option("演示设备（不连接硬件）", "DEMO"));
    if ([...$("port").options].some((o) => o.value === old))
      $("port").value = old;
    if (p.connected) {
      connected = true;
      ids = p.ids;
      selected = null;
      state = null;
      if (![...$("port").options].some((o) => o.value === p.port))
        $("port").add(new Option(p.port, p.port));
      $("port").value = p.port;
      $("connect").textContent = "断开连接";
      $("connection").textContent = p.demo
        ? "● 演示设备 · 未连接硬件"
        : "● " + p.port;
      renderIds();
      controls();
      notice("已恢复当前串口连接，请选择舵机或重新搜索。");
    }
  } catch (e) {
    notice(e.message, true);
  }
}
async function connectPort() {
  await run(async () => {
    if (connected) {
      session++;
      await api("disconnect");
      connected = false;
      ids = [];
      resetSelection();
      renderIds();
      $("selected").textContent = "尚未选择舵机";
      $("model").textContent = "等待搜索";
      $("connection").textContent = "● 串口未连接";
      $("connect").textContent = "连接设备";
      clearReadout();
      notice("已关闭连接；舵机扭矩状态不会因断开而改变。");
    } else {
      const d = await api("connect", {
        port: $("port").value,
        baud: num("baud"),
      });
      connected = true;
      session++;
      $("connection").textContent = d.demo
        ? "● 演示设备 · 未连接硬件"
        : "● " + d.port;
      $("connect").textContent = "断开连接";
      notice(
        d.demo
          ? "已打开演示设备。点击搜索查看模拟舵机。"
          : "串口已打开，请搜索舵机。",
      );
    }
  });
}
function renderIds() {
  $("count").textContent = ids.length + " 台";
  $("servos").replaceChildren();
  if (!ids.length) {
    const el = document.createElement("div");
    el.className = "empty";
    el.textContent = connected
      ? "暂无设备，请搜索或检查连接。"
      : "连接后搜索，再选择一颗舵机。";
    $("servos").append(el);
  }
  for (const id of ids) {
    const b = document.createElement("button");
    b.className = "servo" + (id === selected ? " active" : "");
    b.innerHTML =
      "<strong>ID " +
      id.toString().padStart(3, "0") +
      '</strong><span class="dot">● 在线</span>';
    b.setAttribute("aria-pressed", String(id === selected));
    b.onclick = () => selectServo(id);
    $("servos").append(b);
  }
}
async function scan() {
  let scanned = false;
  await run(async () => {
    resetSelection();
    renderIds();
    controls();
    notice("正在逐个搜索，请稍候…");
    const d = await api("scan", { start: num("start"), end: num("end") });
    ids = d.ids;
    scanned = true;
    renderIds();
    $("selected").textContent = "请选择舵机";
    notice(
      "搜索完成：" +
        ids.length +
        " 台" +
        (ids.length
          ? "，ID " + ids.join("、")
          : "。请检查波特率、供电与连接。"),
    );
  });
  if (scanned && ids.length) await selectServo(ids[0]);
}
async function selectServo(id) {
  await run(async () => {
    resetSelection();
    selected = id;
    renderIds();
    $("selected").textContent = "舵机 ID " + id;
    $("model").textContent = "读取中";
    $("registers").innerHTML =
      '<tr><td colspan="7" class="empty">点击「读取参数」查看当前舵机</td></tr>';
    const d = await api("dump", { id });
    dump = d;
    const r = d.raw;
    modelId = r[3] + 256 * r[4];
    $("model").textContent =
      "型号 " + (r[3] + 256 * r[4]) + " · 固件 " + r[0] + "." + r[1];
    const feedback = await api("state", { id });
    updateState(feedback);
    setGoal(feedback.state.pos);
    renderRegisters();
    log("已选择 ID " + id + "，只读连接。");
  });
}
function clearReadout() {
  lastFeedback = 0;
  $("positionAngle").textContent = "等待回读";
  $("feedbackState").textContent = "等待反馈";
  $("feedbackState").className = "feedback-state";
  $("torqueState").className = "torque-state";
  for (const id of [
    "posValue",
    "goalValue",
    "voltValue",
    "tempValue",
    "speedValue",
    "loadValue",
  ])
    $(id).textContent = "—";
  $("torqueState").textContent = "扭矩 —";
  $("moving").textContent = "移动：—";
  $("status").textContent = "状态：—";
  $("current").textContent = "电流换算值：—";
}
function updateState(d) {
  state = d.state;
  lastFeedback = Date.now();
  $("positionAngle").textContent =
    (((state.pos - 2048) * 360) / 4096).toFixed(2) + "°";
  $("feedbackState").textContent = "● 实时回读";
  $("feedbackState").className = "feedback-state live";
  $("torqueState").className = "torque-state" + (state.torque ? " on" : "");
  for (const [id, v] of Object.entries({
    posValue: state.pos,
    goalValue: state.goal,
    voltValue: state.volt.toFixed(1),
    tempValue: state.temp,
    speedValue: state.speed,
    loadValue: state.load,
  }))
    $(id).textContent = v;
  $("torqueState").textContent =
    "扭矩 " + (state.torque ? "开启" : "关闭") + " · 模式 " + state.mode;
  $("moving").textContent = "移动：" + (state.moving ? "是" : "否");
  $("status").textContent =
    "状态：" + (state.status_text || state.err || "正常");
  $("current").textContent =
    "电流换算值：" + state.current_ma.toFixed(0) + " mA";
  $("stats").textContent =
    "TX " +
    d.stats.tx +
    " / RX " +
    d.stats.rx_ok +
    " / 超时 " +
    d.stats.timeout;
  samples.push({ time: Date.now(), id: selected, ...state });
  if (samples.length > 6000) samples.shift();
  $("samples").textContent = samples.length;
  draw();
  controls();
}
async function poll() {
  if (connected && selected !== null && !busy && !polling) {
    polling = true;
    const s = session,
      id = selected;
    try {
      const d = await api("state", { id });
      if (s === session && !busy) updateState(d);
    } catch (e) {
      if (s === session && !busy) {
        clearReadout();
        state = null;
        $("status").textContent = "通信中断";
        $("feedbackState").textContent = "● 通信中断";
        controls();
      }
    } finally {
      polling = false;
    }
  }
  if (state && Date.now() - lastFeedback >= 2000) {
    clearReadout();
    state = null;
    $("feedbackState").textContent = "反馈已过期";
  }
  controls();
  $("clock").textContent = new Date().toLocaleTimeString();
  setTimeout(poll, 200);
}
function setGoal(v) {
  $("position").value = Math.round(v);
  $("slider").value = v;
  $("angle").textContent = (((v - 2048) * 360) / 4096).toFixed(2) + "°";
}
$("position").addEventListener("input", () => {
  $("angle").textContent =
    (((num("position") - 2048) * 360) / 4096).toFixed(2) + "°";
});
async function command(op, args = {}) {
  await run(async () => {
    const d = await api(op, { id: selected, ...args });
    if (d.goal !== undefined) setGoal(d.goal);
    notice(
      op === "stop" ? "已将目标设为当前位置；扭矩保持原状态。" : "操作完成。",
    );
  });
}
async function torque(on) {
  await run(async () => {
    await api("torque", { id: selected, on });
    state = null;
    lastFeedback = 0;
    notice(
      on ? "已以当前位置为目标使能。" : "已关闭当前舵机扭矩，请继续托住关节。",
    );
  });
}
async function move() {
  await run(async () => {
    const fresh = await api("state", { id: selected });
    const large = Math.abs(num("position") - fresh.state.pos) > 114;
    if (
      large &&
      !confirm("目标距离当前位置超过 10°。确认关节行程内无干涉，执行此次运动？")
    )
      return;
    await api("move", {
      id: selected,
      position: num("position"),
      speed: num("speed"),
      acc: num("acc"),
      confirm_large: large,
    });
    notice("已写入目标 " + num("position") + "。");
  });
}
async function jog(direction) {
  await run(async () => {
    const fresh = await api("state", { id: selected });
    const position = fresh.state.pos + direction * 11;
    if (position < 0 || position > 4095) throw new Error("已到单圈边界");
    await api("move", {
      id: selected,
      position,
      speed: num("speed"),
      acc: num("acc"),
    });
    setGoal(position);
    log("微调目标 " + position);
  });
}
async function unload() {
  if (!confirm("已托住机器人？将关闭全部已搜索舵机的扭矩。")) return;
  await run(async () => {
    const d = await api("unload");
    notice(
      "已卸力：" +
        d.unloaded.join("、") +
        (d.failures.length ? "；失败：" + d.failures.join("；") : ""),
      !!d.failures.length,
    );
  });
}
async function calibrate() {
  if (
    !confirm(
      "已关闭扭矩、托住关节并摆到模型 0°？将备份参数并把当前位置设为 2048，完成后保持断扭。",
    )
  )
    return;
  await run(async () => {
    const d = await api("calibrate", { id: selected });
    state = null;
    lastFeedback = 0;
    setGoal(d.after.position);
    dump = null;
    renderRegisters();
    notice(
      "ID " +
        selected +
        " 校零回读通过：" +
        d.before.position +
        " → " +
        d.after.position +
        "。保持断扭；请断电重启验证保存，再检查行程。备份：" +
        d.backup,
    );
  });
}
function tab(name) {
  document.querySelectorAll(".page").forEach((el) => {
    el.classList.toggle("active", el.id === name);
    el.hidden = el.id !== name;
  });
  document.querySelectorAll(".tab").forEach((el) => {
    const active = el.dataset.page === name;
    el.classList.toggle("active", active);
    el.setAttribute("aria-selected", String(active));
    el.tabIndex = active ? 0 : -1;
  });
  draw();
}
$("tab-debug").parentElement.addEventListener("keydown", (event) => {
  const tabs = [...document.querySelectorAll(".tab")],
    i = tabs.indexOf(document.activeElement);
  if (i < 0 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
    return;
  event.preventDefault();
  const index =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (i + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
          tabs.length;
  tab(tabs[index].dataset.page);
  tabs[index].focus();
});
async function readRegisters() {
  await run(async () => {
    dump = await api("dump", { id: selected });
    renderRegisters();
    notice("参数读取完成：" + dump.rows.length + " 项。");
  });
}
const blocked = [5, 6, 7, 8, 31, 40, 42, 44, 55];
function renderRegisters() {
  const tbody = $("registers");
  tbody.replaceChildren();
  if (!dump) {
    tbody.innerHTML =
      '<tr><td colspan="7" class="empty">选择舵机后，点击「读取参数」</td></tr>';
    return;
  }
  const query = $("registerFilter").value.trim().toLowerCase();
  const rows = dump.rows.filter((r) =>
    [r.addr, r.name, r.area].join(" ").toLowerCase().includes(query),
  );
  if (!rows.length)
    tbody.innerHTML =
      '<tr><td colspan="7" class="empty">没有匹配的参数</td></tr>';
  for (const r of rows) {
    const tr = document.createElement("tr");
    const editable =
      ["EEPROM", "SRAM"].includes(r.area) && !blocked.includes(r.addr);
    for (const text of [r.addr, r.name, r.area, r.size]) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.append(td);
    }
    const cell = document.createElement("td");
    const input = document.createElement("input");
    input.type = "number";
    input.min = 0;
    input.max = 2 ** (8 * r.size) - 1;
    input.value = r.value;
    input.disabled = !editable;
    input.setAttribute("aria-label", r.name + " 地址 " + r.addr);
    cell.append(input);
    tr.append(cell);
    const ref = document.createElement("td");
    const refRow = reference?.rows?.find((x) => x.addr === r.addr);
    ref.textContent = refRow ? refRow.value : "—";
    if (refRow && refRow.value !== r.value) ref.className = "pending";
    tr.append(ref);
    const action = document.createElement("td");
    if (editable) {
      const button = document.createElement("button");
      button.textContent = "写入";
      button.dataset.servo = "";
      button.dataset.action = "off";
      button.onclick = () => writeRegister(r, Number(input.value));
      action.append(button);
    } else action.textContent = "只读 / 专用";
    tr.append(action);
    tbody.append(tr);
  }
  controls();
}
async function writeRegister(r, value) {
  if (
    !confirm(
      "将 ID " +
        selected +
        " 的 " +
        r.name +
        "（地址 " +
        r.addr +
        "）设为 " +
        value +
        "？须先关闭扭矩。" +
        (r.area === "EEPROM" ? "此值掉电保存。" : ""),
    )
  )
    return;
  await run(async () => {
    const d = await api("write", {
      id: selected,
      addr: r.addr,
      value,
      confirm: true,
    });
    dump = await api("dump", { id: selected });
    renderRegisters();
    notice(
      "地址 " +
        r.addr +
        " 写入及回读通过。" +
        (d.backup ? "原参数备份：" + d.backup : ""),
    );
  });
}
function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function saveRegisters() {
  await run(async () => {
    dump = await api("dump", { id: selected });
    download(
      "HD1910-id" + selected + "-" + Date.now() + ".json",
      JSON.stringify(dump, null, 2),
      "application/json",
    );
    renderRegisters();
    log("已导出当前寄存器 JSON。");
  });
}
async function loadReference(el) {
  try {
    const file = el.files[0];
    if (!file) return;
    if (file.size > 1000000) throw new Error("文件过大");
    const d = JSON.parse(await file.text());
    if (
      d.format !== "microduck-fd-v1" ||
      !Array.isArray(d.rows) ||
      d.rows.length > 100 ||
      d.rows.some(
        (r) => !Number.isInteger(r.addr) || !Number.isInteger(r.value),
      )
    )
      throw new Error("请选择本工具导出的 JSON 文件");
    reference = d;
    renderRegisters();
    notice("已载入参数对照，文件 ID " + d.id + "；尚未写入任何参数。");
  } catch (e) {
    notice(e.message, true);
  } finally {
    el.value = "";
  }
}
async function changeId() {
  if (!confirm("请物理断开其他舵机，仅连接这一颗，关闭扭矩。确认修改 ID？"))
    return;
  let newId = null;
  await run(async () => {
    const d = await api("set_id", { id: selected, new: num("newId") });
    newId = d.id;
    ids = [newId];
    notice("新 ID " + d.id + " 已验证。备份：" + d.backup);
  });
  if (newId !== null) await selectServo(newId);
}
async function reboot() {
  if (!confirm("重启当前舵机？请先关闭扭矩并托住关节。")) return;
  await command("reboot");
}
function clearData() {
  samples = [];
  $("samples").textContent = 0;
  draw();
}
function exportCsv() {
  if (!samples.length) {
    notice("暂无反馈数据，请先选择在线舵机。", true);
    return;
  }
  const keys = [
    "time",
    "id",
    "pos",
    "goal",
    "speed",
    "load",
    "volt",
    "temp",
    "current_ma",
    "status",
    "torque",
  ];
  download(
    "servo-feedback-" + Date.now() + ".csv",
    keys.join(",") +
      "\n" +
      samples.map((row) => keys.map((k) => row[k]).join(",")).join("\n"),
    "text/csv",
  );
  log("已导出 " + samples.length + " 条反馈。");
}
function draw() {
  const c = $("chart");
  const w = c.clientWidth,
    h = c.clientHeight;
  if (!w) return;
  const dpr = window.devicePixelRatio || 1;
  c.width = w * dpr;
  c.height = h * dpr;
  const ctx = c.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  ctx.font = "10px -apple-system";
  const left = 47,
    right = w - 16,
    top = 16,
    bottom = h - 28;
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = top + ((bottom - top) * i) / 4;
    ctx.strokeStyle = "#e6ebdf";
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
    ctx.fillStyle = "#8d9b80";
    ctx.fillText(Math.round(4095 * (1 - i / 4)), 7, y + 3);
  }
  for (let i = 0; i <= 6; i++) {
    const x = left + ((right - left) * i) / 6;
    ctx.strokeStyle = "#eff2e8";
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    ctx.fillStyle = "#8d9b80";
    ctx.fillText(i * 5 - 30 + "s", x - 8, h - 9);
  }
  const now = Date.now();
  const rows = samples.filter((s) => now - s.time <= 30000);
  for (const [key, color] of [
    ["goal", "#c18a40"],
    ["pos", "#1f625c"],
  ]) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.6;
    ctx.setLineDash(key === "goal" ? [5, 3] : []);
    ctx.beginPath();
    rows.forEach((r, i) => {
      const x = left + (1 - (now - r.time) / 30000) * (right - left);
      const y =
        bottom - (Math.max(0, Math.min(4095, r[key])) / 4095) * (bottom - top);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
  ctx.setLineDash([]);
  if (!rows.length) {
    ctx.fillStyle = "#98a3af";
    ctx.textAlign = "center";
    ctx.fillText("选择舵机后显示实时位置反馈", w / 2, h / 2);
    ctx.textAlign = "left";
  }
}
// This watchdog must run independently of a pending feedback request.
setInterval(() => {
  if (state && Date.now() - lastFeedback >= 2000) {
    clearReadout();
    state = null;
    $("feedbackState").textContent = "反馈已过期";
  }
  controls();
}, 250);
window.addEventListener("resize", draw);
refreshPorts();
poll();
draw();
