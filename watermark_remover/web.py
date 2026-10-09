from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask

from .core import RemovalOptions, remove_watermarks

app = FastAPI(title="PDF Watermark Cleaner", docs_url=None, redoc_url=None)
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>&#8203;</title>
<style>
:root{--ink:#1c2320;--muted:#6d7772;--paper:#f1f4f1;--card:rgba(255,255,253,.9);--green:#176d55;--green2:#258b6d;--line:rgba(25,72,52,.14)}
*{box-sizing:border-box}body{margin:0;min-height:100vh;color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;background:radial-gradient(circle at 15% 12%,#d8eadf 0,transparent 31rem),radial-gradient(circle at 90% 90%,#ebdfca 0,transparent 33rem),var(--paper)}
main{width:min(760px,calc(100% - 30px));margin:auto;padding:42px 0}.top{display:flex;align-items:center;justify-content:space-between;margin:0 5px 18px}.brand{display:flex;gap:10px;align-items:center;font-size:.82rem;font-weight:800;letter-spacing:.07em}.brand i{display:grid;place-items:center;width:36px;height:36px;border-radius:12px;color:#fff;font-style:normal;background:linear-gradient(145deg,var(--green2),var(--green));box-shadow:0 9px 24px rgba(23,109,85,.2)}.local{font-size:.78rem;color:var(--muted)}
form{padding:21px;background:var(--card);border:1px solid rgba(255,255,255,.92);border-radius:28px;box-shadow:0 28px 90px rgba(42,61,50,.13);backdrop-filter:blur(18px)}.drop{min-height:270px;padding:30px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;border:1.5px dashed #a4b6ac;border-radius:20px;background:linear-gradient(160deg,#fcfdfb,#eff5f0);cursor:pointer;transition:.2s}.drop:hover,.drop.drag{transform:translateY(-2px);border-color:var(--green2);background:#edf7f0}.drop input{display:none}.icon{display:grid;place-items:center;width:68px;height:68px;margin-bottom:20px;border-radius:22px;background:#fff;border:1px solid var(--line);color:var(--green);font-size:28px;box-shadow:0 12px 30px rgba(37,76,59,.1)}.pick{font-size:1.08rem;font-weight:780}.hint{margin-top:8px;color:var(--muted);font-size:.87rem}
.options{display:grid;grid-template-columns:1fr 1fr;gap:11px;margin:14px 0}.option{padding:13px 14px;border:1px solid var(--line);border-radius:14px;background:rgba(252,253,250,.72)}.option label{display:flex;justify-content:space-between;align-items:center;font-size:.86rem;color:#455149}.option output{color:var(--green);font-weight:750}.option input[type=range]{width:100%;margin:10px 0 2px;accent-color:var(--green)}.check{display:flex!important;justify-content:flex-start!important;gap:8px}.check input{accent-color:var(--green)}
button{width:100%;padding:16px;border:0;border-radius:14px;color:#fff;font-size:.96rem;font-weight:780;background:linear-gradient(135deg,var(--green2),var(--green));box-shadow:0 12px 28px rgba(23,109,85,.22);cursor:pointer}button:disabled{opacity:.55;cursor:wait}#status{min-height:21px;margin:13px 3px -2px;text-align:center;color:var(--muted);font-size:.84rem}.note{margin:16px 4px 0;text-align:center;color:#7b8680;font-size:.76rem;line-height:1.65}
@media(max-width:580px){main{padding:22px 0}.options{grid-template-columns:1fr}form{padding:14px;border-radius:22px}.drop{min-height:245px;padding:22px 14px}.local{font-size:.7rem}}
</style></head><body><main><div class="top"><div class="brand"><i>W</i><span>PDF WATERMARK CLEANER</span></div><div class="local">本机处理 · 不上传外部服务</div></div>
<form id="form"><label class="drop" id="drop"><input id="file" name="file" type="file" accept="application/pdf,.pdf" required><span class="icon">⌁</span><span class="pick" id="filename">选择 PDF，或拖放到这里</span><span class="hint">适用于浅灰色、重复、倾斜的文字水印 · 最大 200 MB</span></label>
<div class="options"><div class="option"><label>水印亮度阈值 <output id="thresholdValue">180</output></label><input id="threshold" name="threshold" type="range" min="120" max="230" value="180"></div><div class="option"><label>输出清晰度 <output id="dpiValue">180 DPI</output></label><input id="dpi" name="dpi" type="range" min="120" max="240" step="30" value="180"></div><div class="option"><label class="check"><input name="protect_tables" type="checkbox" checked>保护表格、边框和图形</label></div><div class="option"><label class="check"><input name="searchable_ocr" type="checkbox" checked>文字可选中、复制和搜索</label></div><div class="option"><label class="check"><input name="confirm_authorized" type="checkbox" required>我有权处理此文档</label></div></div>
<button id="submit" type="submit">清除水印并下载 →</button><div id="status" role="status"></div></form><div class="note">阈值越低，清除范围越大；若浅灰图表也被移除，请提高阈值。原文件不会被修改。</div></main>
<script>
const form=document.querySelector('#form'),file=document.querySelector('#file'),drop=document.querySelector('#drop'),nameEl=document.querySelector('#filename'),status=document.querySelector('#status'),button=document.querySelector('#submit');
for(const [id,out,suffix] of [['threshold','thresholdValue',''],['dpi','dpiValue',' DPI']]){const el=document.querySelector('#'+id),o=document.querySelector('#'+out);el.addEventListener('input',()=>o.value=el.value+suffix)}
file.addEventListener('change',()=>nameEl.textContent=file.files[0]?.name||'选择 PDF，或拖放到这里');['dragenter','dragover'].forEach(e=>drop.addEventListener(e,x=>{x.preventDefault();drop.classList.add('drag')}));['dragleave','drop'].forEach(e=>drop.addEventListener(e,x=>{x.preventDefault();drop.classList.remove('drag')}));drop.addEventListener('drop',e=>{if(e.dataTransfer.files.length){file.files=e.dataTransfer.files;nameEl.textContent=file.files[0].name}});
form.addEventListener('submit',async e=>{e.preventDefault();if(!file.files.length)return;button.disabled=true;const searchable=form.elements.searchable_ocr.checked;status.textContent=searchable?'正在清理水印并识别文字，大文件需要几分钟…':'正在逐页清理水印，大文件需要几分钟…';try{const response=await fetch('/clean',{method:'POST',body:new FormData(form)});if(!response.ok){let msg='处理失败';try{msg=(await response.json()).detail||msg}catch{}throw new Error(msg)}const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a'),encoded=response.headers.get('X-Output-Name');a.download=encoded?decodeURIComponent(encoded):'cleaned.pdf';a.href=url;a.click();URL.revokeObjectURL(url);const removed=response.headers.get('X-Removed-Ratio');status.textContent='处理完成'+(searchable?' · 文字可选中/复制/搜索':'')+(removed?' · 清理像素 '+removed:'')+'，下载已开始。'}catch(error){status.textContent=error.message}finally{button.disabled=false}});
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.post("/clean")
async def clean(
    file: UploadFile = File(...),
    threshold: int = Form(180),
    dpi: int = Form(180),
    protect_tables: bool = Form(False),
    searchable_ocr: bool = Form(False),
    confirm_authorized: bool = Form(False),
) -> FileResponse:
    if not confirm_authorized:
        raise HTTPException(status_code=400, detail="请确认你有权处理此文档")
    original = Path(file.filename or "document.pdf").name
    if not original.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="请选择 PDF 文件")
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="PDF 超过 200 MB 限制")
    if not payload.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="文件内容不是有效的 PDF")

    temporary = tempfile.TemporaryDirectory(prefix="watermark-cleaner-")
    workdir = Path(temporary.name)
    source = workdir / original
    output_name = f"{Path(original).stem}-cleaned.pdf"
    target = workdir / output_name
    source.write_bytes(payload)
    try:
        result = remove_watermarks(
            source,
            target,
            options=RemovalOptions(
                dpi=dpi,
                lightness_threshold=threshold,
                protect_tables=protect_tables,
                searchable_ocr=searchable_ocr,
            ),
        )
    except (OSError, ValueError) as exc:
        temporary.cleanup()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    encoded = quote(output_name)
    headers = {
        "Content-Disposition": f"attachment; filename=cleaned.pdf; filename*=UTF-8''{encoded}",
        "X-Output-Name": encoded,
        "X-Removed-Ratio": f"{result.removed_ratio:.2%}",
        "X-Searchable-Text": "enabled" if searchable_ocr and not result.warnings else "fallback",
    }
    return FileResponse(target, media_type="application/pdf", headers=headers, background=BackgroundTask(temporary.cleanup))


def main() -> None:
    import uvicorn

    uvicorn.run("watermark_remover.web:app", host="127.0.0.1", port=7001)


if __name__ == "__main__":
    main()
