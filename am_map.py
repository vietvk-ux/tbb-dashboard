"""Ánh xạ BƯU CỤC → AM quản lý (Vùng TBB). Dùng cho xếp hạng theo AM ở báo cáo
trực tiếp + cuối ngày. Cập nhật khi có thay đổi phân công.

Cập nhật 30/09/2026: BỎ AM Đinh Văn Thu (4 BC chia lại); THÊM AM mới Lò Văn Thảnh.
- Vân Hồ/Phù Yên/Bắc Yên → Hoàng Gia Đạt · Yên Châu → Điêu Chính Luân.
- Lò Văn Thảnh nhận: Tuần Giáo/Mường Ảng/Tủa Chùa (từ Hoàng Gia Đạt) + Na Sang (từ Bùi Văn Đông).

Cập nhật 31/10/2026: THÊM AM mới Hoàng Quang Trung (5 BC: Nậm Mạ/Than Uyên/Tân Uyên/Bình Lư
từ Nguyễn Công Nam + Mộc Sơn từ Hoàng Gia Đạt). Cát Thịnh/Mù Cang Chải/Âu Lâu/Cầu Thia:
Bế Ngọc Chuyển → Nguyễn Công Nam. → 8 AM · 54 BC.
"""

AM_OF = {
    # Nguyễn Công Nam (9) — bỏ Nậm Mạ/Bình Lư/Than Uyên/Tân Uyên (→ Trung); nhận 4 BC YBA (từ Chuyển)
    "(LCH) Nậm Hàng": "Nguyễn Công Nam", "(LCH) Sìn Hồ": "Nguyễn Công Nam",
    "(LCH) Tân Phong": "Nguyễn Công Nam", "(LCH) Phong Thổ": "Nguyễn Công Nam",
    "(LCH) Bum Tở": "Nguyễn Công Nam",
    "(YBA) Cát Thịnh": "Nguyễn Công Nam", "(YBA) Mù Cang Chải": "Nguyễn Công Nam",
    "(YBA) Âu Lâu": "Nguyễn Công Nam", "(YBA) Cầu Thia": "Nguyễn Công Nam",
    # Hoàng Quang Trung (5) — AM MỚI (31/10/2026)
    "(LCH) Nậm Mạ": "Hoàng Quang Trung", "(LCH) Than Uyên": "Hoàng Quang Trung",
    "(LCH) Tân Uyên": "Hoàng Quang Trung", "(LCH) Bình Lư": "Hoàng Quang Trung",
    "(SLA) Mộc Sơn": "Hoàng Quang Trung",
    # Bùi Văn Đông (4) — Na Sang chuyển sang Lò Văn Thảnh
    "(DBI) Na Son": "Bùi Văn Đông", "(DBI) Thanh An": "Bùi Văn Đông",
    "(DBI) Mường Nhé": "Bùi Văn Đông", "(DBI) Điện Biên Phủ": "Bùi Văn Đông",
    # Hoàng Gia Đạt (6) — bỏ Mộc Sơn (→ Trung)
    "(SLA) Thảo Nguyên": "Hoàng Gia Đạt", "(SLA) Tô Hiệu": "Hoàng Gia Đạt",
    "(SLA) Quỳnh Nhai": "Hoàng Gia Đạt", "(SLA) Vân Hồ": "Hoàng Gia Đạt",
    "(SLA) Phù Yên": "Hoàng Gia Đạt", "(SLA) Bắc Yên": "Hoàng Gia Đạt",
    # Lò Văn Thảnh (4) — AM MỚI (30/09/2026)
    "(DBI) Tuần Giáo": "Lò Văn Thảnh", "(DBI) Mường Ảng": "Lò Văn Thảnh",
    "(DBI) Tủa Chùa": "Lò Văn Thảnh", "(DBI) Na Sang": "Lò Văn Thảnh",
    # Nguyễn Đức Thịnh (12)
    "(LCA) Sa Pa": "Nguyễn Đức Thịnh", "(LCA) Lào Cai": "Nguyễn Đức Thịnh",
    "(LCA) Bát Xát": "Nguyễn Đức Thịnh",
    "(LCA) Bảo Hà": "Nguyễn Đức Thịnh", "(LCA) Bảo Yên": "Nguyễn Đức Thịnh",
    "(LCA) Si Ma Cai": "Nguyễn Đức Thịnh", "(LCA) Bắc Hà": "Nguyễn Đức Thịnh",
    "(LCA) Bảo Thắng": "Nguyễn Đức Thịnh", "(LCA) Cam Đường 1": "Nguyễn Đức Thịnh",
    "(LCA) Mường Khương": "Nguyễn Đức Thịnh", "(LCA) Văn Bàn": "Nguyễn Đức Thịnh",
    "(LCA) Cam Đường 2": "Nguyễn Đức Thịnh",
    # Điêu Chính Luân (7) — nhận Yên Châu
    "(SLA) Mai Sơn": "Điêu Chính Luân", "(SLA) Sông Mã": "Điêu Chính Luân",
    "(SLA) Thuận Châu": "Điêu Chính Luân", "(SLA) Mường La": "Điêu Chính Luân",
    "(SLA) Sốp Cộp": "Điêu Chính Luân", "(SLA) Chiềng Sinh": "Điêu Chính Luân",
    "(SLA) Yên Châu": "Điêu Chính Luân",
    # Bế Ngọc Chuyển (7) — bỏ Âu Lâu/Cầu Thia/Cát Thịnh/Mù Cang Chải (→ Nam)
    "(YBA) Thác Bà": "Bế Ngọc Chuyển", "(YBA) Lục Yên": "Bế Ngọc Chuyển",
    "(YBA) Đông Cuông": "Bế Ngọc Chuyển", "(YBA) Mậu A": "Bế Ngọc Chuyển",
    "(YBA) Bảo Ái": "Bế Ngọc Chuyển", "(YBA) Trấn Yên": "Bế Ngọc Chuyển",
    "(YBA) Văn Phú": "Bế Ngọc Chuyển",
}
