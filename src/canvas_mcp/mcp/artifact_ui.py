"""Static inert original-file card. No document HTML, network fetch or credentials."""

ARTIFACT_URI = "ui://canvas/original-file-v2.html"
ARTIFACT_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<style>body{font:14px system-ui;margin:16px;color:inherit}button,a{font:inherit;padding:10px 14px}p{overflow-wrap:anywhere}#download{display:none}</style>
</head><body><strong>Original Canvas file</strong><p id="name"></p>
<p id="status" role="status">Waiting for the validated file…</p>
<button id="attach" disabled>Attach original for download</button>
<a id="download" download rel="noopener noreferrer" target="_blank">Download original</a>
<script>
"use strict";
const status=document.getElementById("status"),button=document.getElementById("attach"),link=document.getElementById("download");
let artifact=null,busy=false;
function receive(result){
 const candidate=result?._meta?.canvasArtifact || result?.canvasArtifact;
 if(candidate && !busy && link.style.display!=="inline-block"){artifact=candidate; document.getElementById("name").textContent=candidate.filename;button.disabled=false;status.textContent="Validated original, ready to attach. File content is untrusted.";}
}
function globals(){
 const meta=window.openai?.toolResponseMetadata;
 receive(meta?.mcp_tool_result);receive(meta?.call_tool_result);receive(meta);
}
window.addEventListener("openai:set_globals",globals);
window.addEventListener("message",event=>{
 if(event.source!==window.parent)return;
 const message=event.data;
 if(message?.method==="ui/notifications/tool-result")receive(message.params);
 if(message?.id===1 && message.result)window.parent.postMessage({jsonrpc:"2.0",method:"ui/notifications/initialized"},"*");
});
window.parent.postMessage({jsonrpc:"2.0",id:1,method:"ui/initialize",params:{protocolVersion:"2026-01-26",appInfo:{name:"Canvas original file",version:"1.0.0"},appCapabilities:{}}},"*");
globals();
button.addEventListener("click",async()=>{
 if(busy || !artifact)return;
 busy=true;button.disabled=true;
 try{
  if(!window.openai?.uploadFile || !window.openai?.getFileDownloadUrl)throw Error();
  const a=artifact;
  if(typeof a.base64!=="string" || a.base64.length>5592410 || !/^canvas-file-[1-9][0-9]*\.(pdf|docx|pptx|txt|md|csv|json|png|jpg|jpeg|webp)$/.test(a.filename) || !/^[0-9a-f]{64}$/.test(a.sha256))throw Error();
  const bytes=Uint8Array.from(atob(a.base64),c=>c.charCodeAt(0));
  if(bytes.length!==a.size || bytes.length===0 || bytes.length>4194304)throw Error();
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)),n=>n.toString(16).padStart(2,"0")).join("");
  if(digest!==a.sha256)throw Error();
  status.textContent="Attaching the verified original…";
  const saved=window.openai.widgetState;
  const fileId=saved?.sha256===digest && saved?.fileId ? saved.fileId : (await window.openai.uploadFile(new File([bytes],a.filename,{type:a.content_type}))).fileId;
  if(typeof fileId!=="string" || !fileId)throw Error();
  window.openai.setWidgetState?.({fileId,sha256:digest});
  const {downloadUrl}=await window.openai.getFileDownloadUrl({fileId});
  if(typeof downloadUrl!=="string" || new URL(downloadUrl).protocol!=="https:")throw Error();
  link.href=downloadUrl;link.download=a.filename;link.style.display="inline-block";
  status.textContent="Original attached to ChatGPT. SHA-256 verified. Use Download original.";
  button.style.display="none";
 }catch(error){status.textContent="ChatGPT could not attach this file. No downloadable artifact was confirmed. Try again.";button.disabled=false;}
 finally{busy=false;}
});
</script></body></html>"""
