from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse

from .converter import ConversionOptions, convert_pdf

app = FastAPI(title="PDF 转 Markdown", docs_url=None, redoc_url=None)
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>&#8203;</title>
  <style>
    :root { color-scheme: light; --ink:#18231d; --muted:#69766f; --paper:#f3f5f0; --card:rgba(255,255,252,.86); --accent:#11684f; --accent2:#1e8a68; --line:rgba(29,69,52,.13); }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; color:var(--ink); overflow-x:hidden; background:
      linear-gradient(rgba(20,60,44,.025) 1px, transparent 1px),
      linear-gradient(90deg, rgba(20,60,44,.025) 1px, transparent 1px), var(--paper);
      background-size:32px 32px; font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }
    body::before, body::after { content:""; position:fixed; z-index:-1; border-radius:50%; filter:blur(2px); }
    body::before { width:520px; height:520px; left:-190px; top:-220px; background:radial-gradient(circle,#cae4d5 0,rgba(202,228,213,0) 70%); }
    body::after { width:600px; height:600px; right:-250px; bottom:-310px; background:radial-gradient(circle,#eadfc9 0,rgba(234,223,201,0) 70%); }
    main { width:min(720px, calc(100% - 32px)); min-height:100vh; margin:0 auto; padding:46px 0; display:grid; place-items:center; }
    .shell { width:100%; }
    .topline { display:flex; align-items:center; justify-content:space-between; margin:0 4px 18px; }
    .mark { display:flex; align-items:center; gap:10px; color:#29463a; font-weight:760; font-size:.82rem; letter-spacing:.08em; }
    .mark-icon { width:34px; height:34px; display:grid; place-items:center; color:white; border-radius:11px; background:linear-gradient(145deg,var(--accent2),var(--accent)); box-shadow:0 8px 22px rgba(17,104,79,.2); }
    .private { display:flex; align-items:center; gap:7px; color:var(--muted); font-size:.79rem; }
    .private::before { content:""; width:7px; height:7px; border-radius:50%; background:#46a67f; box-shadow:0 0 0 4px rgba(70,166,127,.12); }
    form { position:relative; background:var(--card); backdrop-filter:blur(18px); border:1px solid rgba(255,255,255,.9); border-radius:28px; padding:22px; box-shadow:0 28px 90px rgba(42,61,50,.12),0 2px 8px rgba(42,61,50,.05); }
    .drop { display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:310px; border:1.5px dashed #a8b8ae; border-radius:20px; text-align:center; padding:34px; cursor:pointer; background:linear-gradient(160deg,rgba(249,252,248,.85),rgba(241,247,242,.62)); transition:border-color .2s ease,background .2s ease,transform .2s ease,box-shadow .2s ease; }
    .drop.drag, .drop:hover { border-color:var(--accent2); background:linear-gradient(160deg,#f6fcf8,#eaf5ed); transform:translateY(-2px); box-shadow:inset 0 0 0 3px rgba(30,138,104,.04); }
    .drop input { display:none; }
    .icon { width:70px; height:70px; display:grid; place-items:center; border-radius:22px; color:var(--accent); margin-bottom:22px; background:white; border:1px solid var(--line); box-shadow:0 12px 30px rgba(37,76,59,.1); }
    .icon svg { width:29px; height:29px; }
    .pick { font-weight:760; font-size:1.08rem; letter-spacing:-.01em; } .hint { color:var(--muted); margin-top:9px; font-size:.88rem; }
    .formats { display:flex; gap:7px; margin-top:22px; }
    .formats span { padding:5px 8px; border:1px solid var(--line); border-radius:7px; color:#77847d; background:rgba(255,255,255,.65); font-size:.68rem; font-weight:750; letter-spacing:.06em; }
    .options { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:14px 0; }
    label.option { display:flex; align-items:center; border:1px solid var(--line); background:rgba(252,253,250,.68); border-radius:13px; padding:13px 14px; color:#415048; font-size:.88rem; cursor:pointer; }
    label.option input { accent-color:var(--accent); margin-right:8px; }
    button { width:100%; display:flex; align-items:center; justify-content:center; gap:9px; border:0; border-radius:14px; padding:16px 20px; background:linear-gradient(135deg,var(--accent2),var(--accent)); color:white; font-weight:760; font-size:.96rem; cursor:pointer; box-shadow:0 12px 25px rgba(17,104,79,.2); transition:transform .2s ease,box-shadow .2s ease; }
    button:hover { transform:translateY(-1px); box-shadow:0 15px 30px rgba(17,104,79,.27); } button:disabled { opacity:.55; cursor:wait; transform:none; }
    #status { min-height:20px; color:var(--muted); margin:13px 3px -3px; text-align:center; font-size:.84rem; }
    footer { margin-top:17px; text-align:center; color:#7d8882; font-size:.76rem; line-height:1.6; }
    @media (max-width:560px) { main{padding:22px 0;align-items:start}.topline{margin-bottom:14px}.private{font-size:.72rem}form{padding:14px;border-radius:22px}.drop{min-height:270px;padding:24px 16px}.options{grid-template-columns:1fr}.icon{width:62px;height:62px} }
  </style>
</head>
<body>
<main>
  <section class="shell" aria-label="PDF 转 Markdown">
    <div class="topline">
      <div class="mark"><span class="mark-icon">M</span><span>PDF → MD</span></div>
      <div class="private">仅在本机处理</div>
    </div>
    <form id="form">
      <label class="drop" id="drop">
        <input id="file" name="file" type="file" accept="application/pdf,.pdf" required>
        <span class="icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5"/><path d="M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4"/></svg></span>
        <span class="pick" id="filename">选择文件，或拖放到这里</span>
        <span class="hint">自动提取文字、图片与基础排版</span>
        <span class="formats"><span>PDF</span><span>≤ 100 MB</span><span>ZIP 输出</span></span>
      </label>
      <div class="options">
        <label class="option"><input type="checkbox" name="remove_margins" checked>移除重复页眉页脚</label>
        <label class="option"><input type="checkbox" name="page_markers" checked>保留页面位置注释</label>
      </div>
      <button id="submit" type="submit"><span>开始转换</span><span aria-hidden="true">→</span></button>
      <div id="status" role="status"></div>
    </form>
    <footer>扫描文档可保留页面图片 · 文件不会上传到外部服务器</footer>
  </section>
</main>
<script>
const form=document.querySelector('#form'), file=document.querySelector('#file'), drop=document.querySelector('#drop');
const filename=document.querySelector('#filename'), status=document.querySelector('#status'), button=document.querySelector('#submit');
function showFile(){ filename.textContent=file.files[0]?.name || '点击选择或拖入 PDF'; }
file.addEventListener('change', showFile);
['dragenter','dragover'].forEach(e=>drop.addEventListener(e,x=>{x.preventDefault();drop.classList.add('drag')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,x=>{x.preventDefault();drop.classList.remove('drag')}));
drop.addEventListener('drop',e=>{ if(e.dataTransfer.files.length){file.files=e.dataTransfer.files;showFile()} });
form.addEventListener('submit',async e=>{
  e.preventDefault(); if(!file.files.length)return;
  button.disabled=true; status.textContent='正在解析页面、提取图片…';
  try{
    const data=new FormData(form), response=await fetch('/convert',{method:'POST',body:data});
    if(!response.ok){let msg='转换失败';try{msg=(await response.json()).detail||msg}catch{}throw new Error(msg)}
    const blob=await response.blob(), url=URL.createObjectURL(blob), a=document.createElement('a');
    const encodedName=response.headers.get('X-Archive-Name');
    a.download=encodedName?decodeURIComponent(encodedName):'markdown.zip';
    a.href=url;a.click();URL.revokeObjectURL(url); status.textContent='转换完成，下载已开始。';
  }catch(error){status.textContent=error.message}finally{button.disabled=false}
});
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.post("/convert")
async def convert(
    file: UploadFile = File(...),
    remove_margins: bool = Form(False),
    page_markers: bool = Form(False),
) -> StreamingResponse:
    original_name = Path(file.filename or "document.pdf").name
    if not original_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="请选择 PDF 文件")

    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="PDF 超过 100 MB 限制")
    if not payload.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="文件内容不是有效的 PDF")

    with tempfile.TemporaryDirectory(prefix="pdf2md-") as temporary:
        workdir = Path(temporary)
        source = workdir / original_name
        source.write_bytes(payload)
        markdown_name = f"{source.stem}.md"
        try:
            result = convert_pdf(
                source,
                workdir / markdown_name,
                options=ConversionOptions(
                    keep_page_markers=page_markers,
                    remove_repeated_margins=remove_margins,
                ),
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(result.markdown_path, markdown_name)
            for asset in sorted(result.assets_dir.iterdir()):
                archive.write(asset, f"{result.assets_dir.name}/{asset.name}")
            if result.warnings:
                archive.writestr("转换提示.txt", "\n".join(result.warnings))
        archive_buffer.seek(0)

    archive_name = f"{Path(original_name).stem}-markdown.zip"
    encoded_name = quote(archive_name)
    headers = {
        "Content-Disposition": f"attachment; filename=markdown.zip; filename*=UTF-8''{encoded_name}",
        "X-Archive-Name": encoded_name,
    }
    return StreamingResponse(archive_buffer, media_type="application/zip", headers=headers)


def main() -> None:
    import uvicorn

    uvicorn.run("pdf2md.web:app", host="127.0.0.1", port=7000)


if __name__ == "__main__":
    main()
