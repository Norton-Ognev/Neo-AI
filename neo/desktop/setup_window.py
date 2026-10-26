"""Окно первичной настройки с прогресс-баром (pywebview). Запускается как `NeoAI.exe --setup`."""

from __future__ import annotations

import json
import logging
import threading

from .setup_runtime import run_setup

logger = logging.getLogger(__name__)

HTML = """<!doctype html><html lang="ru"><meta charset="utf-8">
<style>
 body{margin:0;font-family:Segoe UI,sans-serif;background:#14161a;color:#e8eaed;display:flex;
      flex-direction:column;justify-content:center;height:100vh;padding:0 36px;box-sizing:border-box}
 h1{font-size:20px;margin:0 0 6px} p{margin:0 0 18px;color:#9aa0a6;font-size:13px}
 .bar{height:10px;background:#2a2d33;border-radius:5px;overflow:hidden}
 .fill{height:100%;width:0;background:#4c8dff;transition:width .3s}
 .indet .fill{width:35%;animation:slide 1.2s infinite ease-in-out}
 @keyframes slide{0%{margin-left:-35%}100%{margin-left:100%}}
 #msg{margin-top:12px;font-size:13px;min-height:34px} #err{color:#ff7b7b}
 button{display:none;margin-top:14px;padding:8px 18px;border:0;border-radius:6px;background:#4c8dff;color:#fff;cursor:pointer}
</style>
<h1>Первая настройка Neo</h1>
<p>Скачиваем модели. Это делается один раз, потом Neo работает офлайн.</p>
<div class="bar" id="bar"><div class="fill" id="fill"></div></div>
<div id="msg">Подготовка…</div><div id="err"></div>
<button id="retry" onclick="pywebview.api.retry();document.getElementById('err').textContent='';this.style.display='none'">Повторить</button>
<script>
 function update(msg,pct){document.getElementById('msg').textContent=msg;
   const bar=document.getElementById('bar');
   if(pct===null){bar.classList.add('indet')}else{bar.classList.remove('indet');document.getElementById('fill').style.width=pct+'%'}}
 function fail(text){document.getElementById('err').textContent='Ошибка: '+text;document.getElementById('retry').style.display='inline-block'}
</script></html>"""


def run_setup_window() -> bool:
    import webview

    retry_event = threading.Event()
    result = {"ok": False}

    class Api:
        def retry(self) -> None:
            retry_event.set()

    window = webview.create_window(
        "Neo — первая настройка", html=HTML, width=560, height=320, resizable=False, js_api=Api()
    )

    def report(message: str, pct: float | None) -> None:
        window.evaluate_js(f"update({json.dumps(message)}, {json.dumps(pct)})")

    def worker() -> None:
        while True:
            try:
                run_setup(report)
                result["ok"] = True
                window.destroy()
                return
            except Exception as exc:  # показываем пользователю и даём повторить
                logger.exception("Первичная настройка не удалась")
                window.evaluate_js(f"fail({json.dumps(str(exc))})")
                retry_event.wait()
                retry_event.clear()

    webview.start(worker)
    return result["ok"]
