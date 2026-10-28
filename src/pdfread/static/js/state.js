const $ = (s) => document.querySelector(s);
const left = $("#left"), right = $("#right");
let INFO = null, syncOn = true, showSrc = false, running = false, es = null;
let polishing = false, esP = null;
let fileSerial = 0, leftObserver = null;

