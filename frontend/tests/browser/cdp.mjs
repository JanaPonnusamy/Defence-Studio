// Minimal Chrome DevTools Protocol driver (no dependencies; Node 24 global WebSocket/fetch).
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function launch(port = 9333) {
  const dir = mkdtempSync(join(tmpdir(), "f6-chrome-"));
  const proc = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${dir}`,
    "--no-first-run", "--disable-gpu", "--no-default-browser-check", "about:blank"], { stdio: "ignore" });
  let version;
  for (let i = 0; i < 60; i++) {
    try { version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); break; } catch { await sleep(250); }
  }
  if (!version) throw new Error("Chrome did not start");
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = targets.find((t) => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  const listeners = [];
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { res, rej } = pending.get(msg.id); pending.delete(msg.id);
      msg.error ? rej(new Error(msg.error.message)) : res(msg.result);
    } else if (msg.method) listeners.forEach((l) => l(msg));
  };
  const send = (method, params = {}) => new Promise((res, rej) => {
    const n = ++id; pending.set(n, { res, rej }); ws.send(JSON.stringify({ id: n, method, params }));
  });
  const log = { console: [], exceptions: [], failed: [], http: [] };
  listeners.push((m) => {
    if (m.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(m.params.type))
      log.console.push({ type: m.params.type, text: m.params.args.map((a) => a.value ?? a.description ?? "").join(" ").slice(0, 300) });
    if (m.method === "Runtime.exceptionThrown")
      log.exceptions.push((m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text || "").slice(0, 400));
    if (m.method === "Log.entryAdded" && m.params.entry.level === "error")
      log.console.push({ type: "log-error", text: `${m.params.entry.text} ${m.params.entry.url || ""}`.slice(0, 300) });
    if (m.method === "Network.loadingFailed") log.failed.push({ id: m.params.requestId, error: m.params.errorText });
    if (m.method === "Network.responseReceived")
      log.http.push({ url: m.params.response.url, status: m.params.response.status });
    if (m.method === "Network.requestWillBeSent")
      log.http.push({ url: m.params.request.url, method: m.params.request.method, req: true });
  });
  for (const d of ["Page", "Runtime", "Network", "Log", "DOM"]) await send(`${d}.enable`);
  const api = {
    send, log, port,
    async goto(url) { await send("Page.navigate", { url }); await sleep(600); },
    async eval(expr) {
      const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result.value;
    },
    async waitFor(expr, ms = 8000, label = expr) {
      const t0 = Date.now();
      while (Date.now() - t0 < ms) { try { if (await api.eval(expr)) return true; } catch {} await sleep(150); }
      throw new Error(`timeout waiting for: ${label}`);
    },
    async click(sel) {
      return api.eval(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});if(!e)return false;e.click();return true})()`);
    },
    async setValue(sel, value) {
      return api.eval(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});if(!e)return false;
        e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
    },
    async setFile(sel, path) {
      const { root } = await send("DOM.getDocument");
      const { nodeId } = await send("DOM.querySelector", { nodeId: root.nodeId, selector: sel });
      if (!nodeId) throw new Error("file input not found " + sel);
      await send("DOM.setFileInputFiles", { files: [path], nodeId });
    },
    async viewport(w, h) {
      await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 });
      await sleep(250);
    },
    async shot(file) {
      const r = await send("Page.captureScreenshot", { format: "png" });
      writeFileSync(file, Buffer.from(r.data, "base64"));
    },
    async metrics() {
      return api.eval(`({sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth,
        overflowX:document.documentElement.scrollWidth>document.documentElement.clientWidth+1})`);
    },
    resetLog() { log.console.length = log.exceptions.length = log.failed.length = log.http.length = 0; },
    close() { try { ws.close(); } catch {} proc.kill(); },
  };
  return api;
}
