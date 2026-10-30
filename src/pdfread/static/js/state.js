const $ = (s) => document.querySelector(s);
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

