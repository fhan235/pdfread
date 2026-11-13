/* ---------------- 启动序列 ----------------
   所有脚本加载完之后才执行: 恢复偏好 -> 应用到界面 -> 绑定分段控件
   -> 拉起文档恢复(init)。

   独立成文件而不是挂在 prefs.js 尾部, 是因为它并不属于"阅读偏好",
   而且前端测试按 `/* 启动:` 截断脚本(不想在 vm 里真的跑启动流程),
   放在最后一个文件里才能保证前面所有模块都被完整加载。
   新增 js 文件时, 本文件必须保持在 index.html 的最后一个。 */

/* 启动: 先恢复阅读偏好, 再加载 */
buildThemeGrid();
loadPrefs();
applyPrefs();
segInit("segTheme", "theme");
segInit("segFont", "font");
segInit("segLayout", "layout");
segInit("segExpFmt", "expFmt");
segInit("segExpSrc", "expSrc");

/* 静默检查新版本(失败不打扰, 见 update.js) */
setTimeout(() => checkUpdate(false), 2000);

init().catch((e) => setBusy(String(e), true));
