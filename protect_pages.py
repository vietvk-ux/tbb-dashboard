"""Mã hoá toàn bộ trang trước khi đưa lên GitHub Pages (bước cuối của workflow).

Pages là trang tĩnh nên không kiểm tra mật khẩu phía server được. Ô nhập mật khẩu đơn thuần vẫn để lộ
số liệu trong mã nguồn trang, vì vậy file trên Pages chỉ chứa bản mã AES-GCM; trình duyệt tự giải mã
sau khi nhập đúng mật khẩu.

Mật khẩu chỉ đọc từ secret DASH_PASSWORD, không ghi vào code/doc. Thiếu secret → dừng deploy, KHÔNG
đưa trang chưa mã hoá lên.

  python protect_pages.py seal docs        mã hoá mọi .html/.json trong docs (bản đã mã hoá thì bỏ qua)
  python protect_pages.py unseal <file>    giải mã 1 file JSON đã lấy về từ Pages để pipeline đọc
"""
from __future__ import annotations
import base64
import glob
import hashlib
import json
import os
import sys
import zlib

MARK = "<!--tbb-sealed-v1-->"
JSON_KEY = "tbb_sealed_v1"
# Salt cố định để khoá không đổi giữa các lần build: nhập 1 lần, các trang và lần cập nhật sau mở được ngay.
SALT = b"tbb-dashboard-v1"
ITER = 600000


def _key(password):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), SALT, ITER, dklen=32)


def _password():
    pw = os.environ.get("DASH_PASSWORD", "").strip()
    if not pw:
        raise SystemExit("Thiếu secret DASH_PASSWORD — dừng để không đưa trang chưa mã hoá lên Pages.")
    return pw


def _seal(data, key):
    """nén → AES-GCM (nonce 12 byte đứng đầu) → base64."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, zlib.compress(data, 9), None)).decode("ascii")


def _unseal(b64, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = base64.b64decode(b64)
    return zlib.decompress(AESGCM(key).decrypt(raw[:12], raw[12:], None))


def is_sealed_json(d):
    return isinstance(d, dict) and JSON_KEY in d


def open_json(d):
    """JSON đọc từ Pages → dict gốc. Bản chưa mã hoá (từ trước khi bật mật khẩu) trả nguyên."""
    if not is_sealed_json(d):
        return d
    return json.loads(_unseal(d[JSON_KEY], _key(_password())))


_PAGE = """<!doctype html><html lang="vi"><head><meta charset="utf-8">""" + MARK + """
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Báo cáo TBB</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0d18;color:#fff;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}
form{width:100%;max-width:320px;background:#131829;border:1px solid #1f2740;border-radius:14px;padding:20px;display:grid;gap:10px}
h1{font-size:18px;margin:0}
label{color:#b9c1da;font-size:13px}
input{width:100%;font:inherit;font-size:18px;padding:10px 12px;border-radius:9px;border:1px solid #33405f;background:#0a0d18;color:#fff}
button{font:inherit;font-weight:700;padding:10px;border:0;border-radius:9px;background:#3b82f6;color:#fff;cursor:pointer}
button:disabled{opacity:.6}
input:focus-visible,button:focus-visible{outline:2px solid #9ec5f4;outline-offset:2px}
#e{min-height:20px;color:#ff8a95;font-size:13px;margin:0}
</style></head><body>
<form id="f" hidden>
<h1>Báo cáo Vùng Tây Bắc Bộ</h1>
<label for="p">Nhập mật khẩu để xem</label>
<input id="p" type="password" inputmode="numeric" autocomplete="current-password" required>
<button id="b" type="submit">Mở báo cáo</button>
<p id="e" role="alert"></p>
</form>
<noscript>Trang cần bật JavaScript để mở.</noscript>
<script type="text/plain" id="d">__DATA__</script>
<script>
(function(){
var SK="tbb_dash_key_v1",SALT="tbb-dashboard-v1",ITER=__ITER__,fails=0;
var el=function(i){return document.getElementById(i);};
function b2u(s){var b=atob(s),u=new Uint8Array(b.length);for(var i=0;i<b.length;i++)u[i]=b.charCodeAt(i);return u;}
function u2b(u){var s="";for(var i=0;i<u.length;i++)s+=String.fromCharCode(u[i]);return btoa(s);}
function load(){try{return localStorage.getItem(SK);}catch(e){return null;}}
function save(v){try{localStorage.setItem(SK,v);}catch(e){}}
function forget(){try{localStorage.removeItem(SK);}catch(e){}}
async function derive(pw){
  var k=await crypto.subtle.importKey("raw",new TextEncoder().encode(pw),"PBKDF2",false,["deriveBits"]);
  var bits=await crypto.subtle.deriveBits({name:"PBKDF2",hash:"SHA-256",salt:new TextEncoder().encode(SALT),iterations:ITER},k,256);
  return new Uint8Array(bits);
}
async function open(raw){
  var data=b2u(el("d").textContent.trim());
  var key=await crypto.subtle.importKey("raw",raw,"AES-GCM",false,["decrypt"]);
  var plain=await crypto.subtle.decrypt({name:"AES-GCM",iv:data.slice(0,12)},key,data.slice(12));
  return new Response(new Blob([plain]).stream().pipeThrough(new DecompressionStream("deflate"))).text();
}
function show(h){document.open();document.write(h);document.close();}
function ask(){el("f").hidden=false;el("p").focus();}
el("f").addEventListener("submit",async function(ev){
  ev.preventDefault();
  el("b").disabled=true;el("e").textContent="";
  try{
    var raw=await derive(el("p").value);
    var h=await open(raw);
    save(u2b(raw));show(h);
  }catch(err){
    fails++;
    el("e").textContent=(err&&err.name==="OperationError")?"Sai mật khẩu, anh/chị nhập lại.":"Không mở được trên trình duyệt này. Hãy cập nhật trình duyệt.";
    el("p").value="";
    setTimeout(function(){el("b").disabled=false;el("p").focus();},Math.min(fails,10)*1000);
  }
});
var s=load();
if(s){open(b2u(s)).then(show).catch(function(){forget();ask();});}else{ask();}
})();
</script></body></html>"""


def seal_html(text, key):
    return _PAGE.replace("__ITER__", str(ITER)).replace("__DATA__", _seal(text.encode("utf-8"), key))


def seal_dir(root, password):
    key = _key(password)
    n = skip = 0
    for path in sorted(glob.glob(os.path.join(root, "**", "*"), recursive=True)):
        low = path.lower()
        if not (low.endswith(".html") or low.endswith(".json")) or not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if low.endswith(".html"):
            if MARK in text[:200]:
                skip += 1
                continue
            out = seal_html(text, key)
        else:
            try:
                if is_sealed_json(json.loads(text)):
                    skip += 1
                    continue
            except ValueError:
                pass
            out = json.dumps({JSON_KEY: _seal(text.encode("utf-8"), key)})
        with open(path, "w", encoding="utf-8") as f:
            f.write(out)
        n += 1
    print("Đã mã hoá %d file, giữ nguyên %d file đã mã hoá." % (n, skip))
    return n


def unseal_file(path):
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if is_sealed_json(d):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(open_json(d), f, ensure_ascii=False)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "seal" and len(sys.argv) == 3:
        seal_dir(sys.argv[2], _password())
    elif mode == "unseal" and len(sys.argv) == 3:
        unseal_file(sys.argv[2])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
