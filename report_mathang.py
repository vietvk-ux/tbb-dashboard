"""TRANG MẶT HÀNG (mathang.html) — người mua đặt gì + %GTC theo nhóm hàng, LUỸ KẾ tối đa 30 ngày.

Phân nhóm bằng từ khoá trên tên mặt hàng ĐẦU TIÊN của đơn (items[0].name, report._trip_items) → số
là ước lượng. Bắt đầu lưu từ 05/11/2026.

Lưu trữ: `mathang_data/YYYY-MM-DD.json` (1 file/ngày, prune >30) — CHỈ số đếm theo nhóm, KHÔNG lưu
tên hàng / mã đơn. Không tốn call API: dùng lại payload report.fetch_report trong report_db_sync.

Hai chế độ: `render` (mặc định) đọc mathang_data/ → docs/<slug>/mathang.html. Env: DASH_SLUG.
"""
from __future__ import annotations
import glob
import html
import json
import logging
import math
import os
import re
import sys
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mathang_data")
KEEP_DAYS = 30
UNCLASSIFIED = "Khác"
logger = logging.getLogger("mathang")

# THỨ TỰ QUAN TRỌNG: khớp nhóm đầu tiên. Mỹ phẩm có 2 dòng: dòng sau là từ khoá chung (kem, tóc…),
# phải đứng sau Phụ kiện để "kẹp tóc" không rơi vào Mỹ phẩm.
_RULES = [
    ("Đồ xe & phụ tùng", "xe máy|phụ tùng|dán xe|nhựa xe|ô tô|oto|honda|yamaha|vario|sirius|exciter|wave|dream|vision|air blade|bugi|nhông|phuộc|gương xe|lốp|yên xe|tem xe|mũ bảo hiểm|nón bảo hiểm|dầu nhớt"),
    ("Mẹ & bé", "đồ chơi|bình sữa|ti giả|xe đẩy|baby|trẻ em|em bé|cho bé|bé trai|bé gái|tập đi|bỉm|tã"),
    ("Nông nghiệp & vật nuôi", "hạt giống|phân bón|thuốc sâu|thuốc trừ|cây giống|cho chó|cho mèo|thức ăn|cá cảnh|vòi tưới|tưới cây|cuốc|cá nhanh lớn|thủy sản|cám|cưa|cắt cỏ|hạt bí|gói bí"),
    ("Sức khỏe", "thuốc|vitamin|thực phẩm chức năng|khẩu trang|men vi sinh|siro|viên uống|collagen|canxi|cao dán|miếng dán"),
    ("Chăm sóc cá nhân", "băng vệ sinh|khăn ướt|khăn giấy|dầu gội|dầu xả|sữa tắm|bàn chải|kem đánh răng|giấy vệ sinh|tăm bông|dao cạo|lăn khử mùi|nước súc miệng|sữa rửa mặt|xịt thơm"),
    ("Mỹ phẩm & làm đẹp", "son|son môi|kem chống nắng|serum|dưỡng da|dưỡng tóc|mặt nạ|nước hoa|mỹ phẩm|kem dưỡng|tẩy trang|mascara|má hồng|toner|kem nền|makeup|chì kẻ|cushion|trị mụn|tắm trắng|nhuộm tóc|sơn móng|móng tay|nail|mi giả|lông mi|kính áp tròng"),
    ("Giày dép", "giày|dép|sandal|sneaker|boot|bốt|guốc"),
    ("Thời trang", "áo|quần|váy|đầm|polo|sơ mi|khoác|jean|jeans|legging|hoodie|set bộ|đồ bộ|bộ đồ|croptop|short|bikini|đồ ngủ|đồ lót|tất|vớ|sịp|nội y|bra|cardigan|len|blazer|vest|khăn choàng|khăn len|găng tay|skirt|dress|shirt|tshirt|satin"),
    ("Phụ kiện & trang sức", "túi xách|túi đeo|túi|ví|balo|ba lô|thắt lưng|mũ|nón|kính|đồng hồ|trang sức|vòng tay|vòng cổ|dây chuyền|bông tai|khuyên tai|nhẫn|kẹp tóc|cột tóc|bờm|buộc tóc|chun|lắc tay|phụ kiện tóc"),
    ("Điện tử & phụ kiện", "điện thoại|tai nghe|sạc|cáp|ốp lưng|ốp|chuột|bàn phím|pin|loa|usb|camera|airpods|adapter|cường lực|smartwatch|ổ cắm|ổ điện|ổ chia|dây điện|công tắc|bóng đèn|đèn led|màn hình|laptop|máy tính|iphone|samsung|oppo|xiaomi|bluetooth|wifi|thẻ nhớ"),
    ("Mỹ phẩm & làm đẹp", "kem|tóc|trang điểm|xà phòng|gôm|dưỡng|mút"),
    ("Thực phẩm & đồ uống", "bánh|kẹo|trà|cà phê|cafe|hạt|snack|gia vị|mì|bim bim|nước ngọt|sữa|thực phẩm|khô gà|yến|mắm|đồ ăn|gạo|mật ong|rong biển|ô mai|mứt|ruốc|cháo|nước tương|xúc xích|tương ớt"),
    ("Sách & VPP", "sách|vở|bút|văn phòng phẩm|giấy note|tô màu|flashcard|truyện"),
    ("Thể thao & câu cá", "yoga|gym|thể thao|tạ|dây nhảy|xe đạp|câu cá|cần câu|đá bóng|cầu lông|pickleball|bóng rổ|vợt|lưỡi câu|mồi câu|mồi|nhái hơi"),
    ("Gia dụng & nội thất", "nồi|chảo|bếp|đèn|quạt|chăn|gối|ga giường|ga|rèm|kệ|hộp|móc|thảm|bình nước|bình giữ nhiệt|cốc|ly|dao|kéo|khăn tắm|khăn lau|khăn mặt|giá đỡ|tủ|bàn|ghế|chổi|cây lau|vòi|xô|chậu|rổ|màn|túi rác|thớt|thau|nước giặt|xả vải|nước rửa|nước lau|hương muỗi|nhang|tẩy|lau nhà|sàn|ấm|máy xay|bát|đĩa|đũa|thìa|muỗng|băng dính|keo|sticker|gương|lược|nến|ổ khoá|ổ khóa|tuốc nơ vít|khoan|kìm|sơn|máy|giấy|tủ lạnh|hoa giả"),
]
# Ranh giới từ: trước/sau từ khoá không được là chữ cái ("áo" không khớp "cháo").
_RX = [(c, re.compile(r"(?<![^\W\d_])(?:%s)(?![^\W\d_])" % k)) for c, k in _RULES]


def classify(name):
    import unicodedata
    s = unicodedata.normalize("NFC", name or "").lower()
    for cat, rx in _RX:
        if rx.search(s):
            return cat
    return UNCLASSIFIED


# ══════════════════ COLLECT ══════════════════
def build_day_payload(report_payload):
    """Từ payload report.fetch_report → số đếm 1 ngày. Gộp đơn theo (bưu cục, mã đơn) như
    dedup_orders: đơn gán nhiều chuyến chỉ tính 1 lần, thành công ở chuyến nào cũng tính GTC."""
    od = {}
    for t in report_payload["trips"]:
        if "error" in t:
            continue
        for r in t.get("items", []):
            if r.get("type") != "DELIVER" or not r.get("code"):
                continue
            key = (t.get("bc"), r["code"])
            cur = od.setdefault(key, {"name": "", "succ": False})
            cur["name"] = cur["name"] or r.get("item") or ""
            cur["succ"] = cur["succ"] or bool(r.get("succ"))
    nhom = {}
    for o in od.values():
        c = nhom.setdefault(classify(o["name"]), [0, 0])
        c[0] += 1
        c[1] += 1 if o["succ"] else 0
    d = report_payload["date"]
    return {"ngay": d.isoformat() if hasattr(d, "isoformat") else str(d),
            "tot": sum(v[0] for v in nhom.values()), "gtc": sum(v[1] for v in nhom.values()),
            "nhom": nhom}


def write_day(day_payload):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "%s.json" % day_payload["ngay"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(day_payload, f, ensure_ascii=False, separators=(",", ":"))
    for old in sorted(glob.glob(os.path.join(DATA_DIR, "*.json")))[:-KEEP_DAYS]:
        try:
            os.remove(old)
        except OSError:
            pass
    return path


def _load_days(n=KEEP_DAYS):
    out = []
    for f in sorted(glob.glob(os.path.join(DATA_DIR, "*.json")))[-n:]:
        try:
            with open(f, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except Exception:
            pass
    return out


# ══════════════════ RENDER ══════════════════
# 7 nhóm có lát riêng trên biểu đồ tròn. Màu đi theo nhóm, KHÔNG đổi theo thứ hạng giữa các ngày.
_NAMED = [("Thời trang", "#3987e5", "#fff"), ("Gia dụng & nội thất", "#d95926", "#fff"),
          ("Mỹ phẩm & làm đẹp", "#199e70", "#0b0b0b"), ("Mẹ & bé", "#c98500", "#0b0b0b"),
          ("Phụ kiện & trang sức", "#d55181", "#0b0b0b"), ("Điện tử & phụ kiện", "#008300", "#fff"),
          ("Thực phẩm & đồ uống", "#9085e9", "#0b0b0b")]
_FOLD = ["Giày dép", "Nông nghiệp & vật nuôi", "Đồ xe & phụ tùng", "Chăm sóc cá nhân", "Sức khỏe",
         "Thể thao & câu cá", "Sách & VPP"]
_FOLD_C, _NONE_C = "#8f8d86", "#55544f"
_LBL_NONE = "Chưa phân loại"


def _esc(s):
    return html.escape(str(s))


def _n(x):
    return f"{int(x):,}".replace(",", ".")


def _p1(a, b):
    return ("%.1f%%" % (a * 100.0 / b)).replace(".", ",") if b else "—"


def _fmt(ds):
    return datetime.strptime(ds, "%Y-%m-%d").strftime("%d/%m")


def _pie(slices, tot):
    """slices = [(tên, số đơn, màu lát, màu chữ)] → SVG. Lát ≥7% in % ngay trên lát."""
    a, paths, labels = -math.pi / 2, [], []
    for nm, v, col, ink in slices:
        if v <= 0:
            continue
        f = v / tot
        b = a + f * 2 * math.pi
        tip = "<title>%s: %s đơn (%s)</title>" % (_esc(nm), _n(v), _p1(v, tot))
        if f >= 0.9999:      # cả vòng: cung SVG không vẽ được điểm đầu trùng điểm cuối
            paths.append("<circle cx='100' cy='100' r='100' fill='%s'>%s</circle>" % (col, tip))
        else:
            paths.append("<path d='M100 100L%.2f %.2fA100 100 0 %d 1 %.2f %.2fZ' fill='%s' stroke='#131829' stroke-width='2'>%s</path>"
                         % (100 + 100 * math.cos(a), 100 + 100 * math.sin(a), 1 if f > 0.5 else 0,
                            100 + 100 * math.cos(b), 100 + 100 * math.sin(b), col, tip))
        if f >= 0.07:
            m = (a + b) / 2
            labels.append("<text x='%.1f' y='%.1f' text-anchor='middle' dominant-baseline='central' fill='%s'>%d%%</text>"
                          % (100 + 68 * math.cos(m), 100 + 68 * math.sin(m), ink, round(f * 100)))
        a = b
    return ("<svg viewBox='-2 -2 204 204' role='img' aria-label='Biểu đồ tròn cơ cấu nhóm hàng toàn vùng'>%s%s</svg>"
            % ("".join(paths), "".join(labels)))


def build_html(days, back=True):
    P = ["<!doctype html><html lang='vi'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1'>",
         "<meta name='robots' content='noindex,nofollow'>",
         "<title>Mặt hàng đơn giao TBB</title>",
         "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700;800&display=swap'>",
         _CSS, "</head><body><main class='wrap'>"]
    if back:
        P.append("<a class='back' href='index.html'>‹ Trang trực tiếp</a>")
    if not days:
        P.append("<h1>Người mua Tây Bắc Bộ đặt hàng gì?</h1><section class='card'>"
                 "<p>Dữ liệu bắt đầu lưu từ ngày 05/11. Số đầu tiên có sau lần chốt 23:35 của ngày này.</p>"
                 "</section></main></body></html>")
        return "".join(P)

    agg = {}
    for d in days:
        for k, v in d.get("nhom", {}).items():
            a = agg.setdefault(k, [0, 0])
            a[0] += v[0]
            a[1] += v[1]
    get = lambda k: agg.get(k, [0, 0])
    tot = sum(v[0] for v in agg.values())
    gtc = sum(v[1] for v in agg.values())
    fold_n = sum(get(k)[0] for k in _FOLD)

    slices = [(k, get(k)[0], c, ink) for k, c, ink in _NAMED]
    slices += [("Nhóm nhỏ", fold_n, _FOLD_C, "#0b0b0b"), (_LBL_NONE, get(UNCLASSIFIED)[0], _NONE_C, "#fff")]

    # Hàng bảng: 7 nhóm chính, nhóm nhỏ, chưa phân loại; trong mỗi khối xếp số đơn giảm dần.
    named_rows = sorted([(k, c) for k, c, _ in _NAMED], key=lambda x: -get(x[0])[0])
    fold_rows = sorted([(k, _FOLD_C) for k in _FOLD], key=lambda x: -get(x[0])[0])
    rows = named_rows + fold_rows + [(UNCLASSIFIED, _NONE_C)]
    avg = gtc * 100.0 / tot if tot else 0
    T = []
    for k, col in rows:
        n, g = get(k)
        if not n:
            continue
        r = g * 100.0 / n
        arrow = ("<i class='up'>▲</i>" if r >= avg else "<i class='dn'>▼</i>")
        T.append("<tr><td><span class='sw' style='background:%s'></span>%s</td><td>%s</td><td>%s</td>"
                 "<td class='g'>%s%s</td></tr>"
                 % (col, _esc(_LBL_NONE if k == UNCLASSIFIED else k), _n(n), _p1(n, tot), _p1(g, n), arrow))

    # Câu tóm tắt tính từ dữ liệu: bỏ nhóm chưa phân loại và nhóm dưới 200 đơn (dễ dao động).
    ranked = sorted([k for k in agg if k != UNCLASSIFIED], key=lambda k: -agg[k][0])
    lead = ""
    if len(ranked) >= 2:
        k1, k2 = ranked[0], ranked[1]
        lead = "%s và %s chiếm %s số đơn." % (k1, k2, _p1(agg[k1][0] + agg[k2][0], tot))
    solid = [k for k in ranked if agg[k][0] >= 200]
    if len(solid) >= 2:
        hi = max(solid, key=lambda k: agg[k][1] / agg[k][0])
        lo = min(solid, key=lambda k: agg[k][1] / agg[k][0])
        lead += " %%GTC cao nhất là %s (%s), thấp nhất là %s (%s)." % (
            hi, _p1(agg[hi][1], agg[hi][0]), lo, _p1(agg[lo][1], agg[lo][0]))

    span = _fmt(days[0]["ngay"]) if len(days) == 1 else "%s đến %s" % (_fmt(days[0]["ngay"]), _fmt(days[-1]["ngay"]))
    P.append("<h1>Người mua Tây Bắc Bộ đặt hàng gì?</h1>")
    P.append("<p class='span'>Luỹ kế %d ngày · %s</p>" % (len(days), span))
    P.append("<p class='lead'>%s</p>" % _esc(lead))
    P.append("<section class='card'><div class='kpis'>"
             "<div><b>%s</b><span>đơn giao</span></div><div><b>%s</b><span>giao thành công</span></div>"
             "<div><b>%s</b><span>%%GTC toàn vùng</span></div></div>"
             "<div class='grid'><div class='pie'>%s</div><div class='tw'><table><thead><tr><th>Nhóm hàng</th>"
             "<th>Số đơn</th><th>Tỷ trọng</th><th>%%GTC</th></tr></thead><tbody>%s</tbody></table>"
             "<p class='sub'>Lát Nhóm nhỏ trên biểu đồ gộp các dòng có ô màu xám. ▲▼ là cao hơn hoặc thấp hơn %%GTC toàn vùng.</p>"
             "</div></div></section>" % (_n(tot), _n(gtc), _p1(gtc, tot), _pie(slices, tot), "".join(T)))
    P.append("<section class='notes'><h2>Cách đọc số liệu</h2><ul>"
             "<li>Tính từng ngày đã chốt, gồm chuyến kết thúc từ 10h, cùng cách tính với báo cáo cuối ngày. "
             "Trang cộng dồn tối đa 30 ngày gần nhất; từ ngày thứ 31, ngày cũ nhất được bỏ ra.</li>"
             "<li>Nhóm hàng được phân bằng từ khoá trên tên hàng do shop nhập, nên số liệu là ước lượng. "
             "Đọc thử 140 tên hàng lấy mẫu, khoảng 117 tên xếp đúng nhóm. Sức khỏe và Điện tử kém chính xác nhất.</li>"
             "<li>Mỗi đơn tính một lần theo mặt hàng đầu tiên, các mặt hàng sau của đơn không được tính.</li>"
             "<li>%GTC chỉ cho thấy nhóm nào cao hơn hoặc thấp hơn, chưa cho biết nguyên nhân.</li>"
             "</ul></section>")
    P.append("</main></body></html>")
    return "".join(P)


_CSS = """<style>
/* Một theme tối, đồng bộ các trang dashboard. Bố cục: 3 số tổng + biểu đồ tròn và bảng trong 1 ô. */
:root{color-scheme:dark;--bg:#0a0d18;--card:#131829;--line:#1f2740;--txt:#fff;--mut:#b9c1da;--good:#25dd90;--bad:#ff5668;
--font:'Geist',system-ui,-apple-system,'Segoe UI',sans-serif}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 var(--font)}
.wrap{max-width:960px;margin:0 auto;padding:18px 14px 40px;display:grid;grid-template-columns:minmax(0,1fr);gap:10px}
.back{color:var(--mut);text-decoration:none;font-size:13px}
h1{font-size:23px;line-height:1.2;margin:0;text-wrap:balance}
h2{font-size:15px;margin:0 0 6px}
p{margin:0}
.span{color:var(--mut);font-size:13px}
.lead{max-width:68ch}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}
.kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-bottom:12px}
.kpis b{display:block;font-size:20px;font-variant-numeric:tabular-nums}
.kpis span{color:var(--mut);font-size:12px}
.grid{display:grid;grid-template-columns:minmax(220px,320px) minmax(0,1fr);gap:18px;align-items:center}
svg{display:block;width:100%;height:auto}
svg text{font:700 11px var(--font)}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 6px;text-align:right;border-top:1px solid var(--line);white-space:nowrap}
th{border-top:0;color:var(--mut);font-size:12px;font-weight:600}
th:first-child,td:first-child{text-align:left;white-space:normal;padding-left:0}
td:last-child,th:last-child{padding-right:0}
td:first-child{position:relative;padding-left:17px}
.sw{position:absolute;left:0;top:11px;width:10px;height:10px;border-radius:3px}
.g{font-weight:700}
.g i{font-style:normal;font-size:10px;margin-left:3px}
.up{color:var(--good)}.dn{color:var(--bad)}
.sub{color:var(--mut);font-size:12px;margin-top:8px}
.notes{color:var(--mut);font-size:13px;padding:4px 2px}
.notes h2{color:var(--txt)}
.notes ul{margin:0;padding-left:18px;display:grid;gap:4px;max-width:76ch}
@media (max-width:640px){
 .grid{grid-template-columns:minmax(0,1fr)}
 .pie{max-width:280px;margin:0 auto;width:100%}
 td,th{font-size:13px}
}
</style>"""


def render(outdir):
    days = _load_days()
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "mathang.html"), "w", encoding="utf-8") as f:
        f.write(build_html(days))
    logger.info("render mathang.html ← %d ngày", len(days))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    render(os.path.join("docs", slug))


if __name__ == "__main__":
    main()
