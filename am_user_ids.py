"""GTalk userId của 7 AM Vùng TBB — dùng để tag @AM trong tin cảnh báo.
Fill userId thật khi có. Nếu chưa có (""), tin vẫn gửi bình thường (chỉ hiển thị text @Tên,
không trigger notification).
"""

AM_USER_ID = {
    "Nguyễn Công Nam": "",
    "Bùi Văn Đông": "",
    "Hoàng Gia Đạt": "",
    "Đinh Văn Thu": "",
    "Nguyễn Đức Thịnh": "",
    "Lê Minh Khiết": "",
    "Nguyễn Tuấn Việt": "",
    # thêm nếu còn AM khác
}


def get_user_id(am_name: str) -> str:
    """Trả về userId của AM (nếu có) hoặc chuỗi rỗng."""
    return AM_USER_ID.get(am_name, "").strip()


def collect_ids(am_names) -> list:
    """Nhận iterable tên AM, trả list unique userId non-empty."""
    seen = set()
    ids = []
    for name in am_names:
        uid = get_user_id(name)
        if uid and uid not in seen:
            seen.add(uid)
            ids.append(uid)
    return ids


def extract_ids_from_msg(msg_text: str) -> list:
    """Quét msg text, tìm các AM name xuất hiện → trả list userId để tag.
    Chỉ trả userId non-empty (nếu chưa fill userId thì bỏ qua)."""
    ids = []
    seen = set()
    for name, uid in AM_USER_ID.items():
        if uid and name in msg_text and uid not in seen:
            seen.add(uid)
            ids.append(uid)
    return ids
