const $ = (s) => document.querySelector(s);

/* 统一解析接口错误。
   响应体可能是 JSON(FastAPI 的 detail), 也可能是纯文本(未捕获异常时
   Starlette 返回 "Internal Server Error")。直接 r.json() 会抛
   "Unexpected token 'I'" 把真实原因盖掉, 所以先按文本读再试解析。 */
async function errText(r) {
  let body = "";
  try { body = await r.text(); } catch (e) { /* 响应体已被消费 */ }
  try {
    const d = JSON.parse(body);
    if (d && d.detail) {
      return typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
    }
  } catch (e) { /* 不是 JSON, 按纯文本处理 */ }
  body = (body || "").trim();
  if (r.status >= 500) return `服务端错误 (HTTP ${r.status})，详情见运行日志`;
  return body.slice(0, 200) || `请求失败 (HTTP ${r.status})`;
}

const left = $("#left"), right = $("#right");
let INFO = null, syncOn = true, showSrc = false, running = false, es = null;
let polishing = false, esP = null;
let fileSerial = 0, leftObserver = null;

/* 心跳: 供桌面版浏览器模式判断页面是否还活着(全部关闭则自动退出);
   pagehide 触发退出倒计时, 刷新产生的新页面心跳会取消它 */
setInterval(() => fetch("/api/heartbeat", { method: "POST" }).catch(() => {}), 5000);
window.addEventListener("pagehide", () => {
  try { navigator.sendBeacon("/api/pagehide"); } catch (e) {}
});

