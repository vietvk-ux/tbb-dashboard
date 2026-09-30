"""Ánh xạ BƯU CỤC → AM quản lý (Vùng TBB). Dùng cho xếp hạng theo AM ở báo cáo
trực tiếp + cuối ngày. Cập nhật khi có thay đổi phân công.

Cập nhật 30/09/2026: BỎ AM Đinh Văn Thu (4 BC chia lại); THÊM AM mới Lò Văn Thảnh.
- Vân Hồ/Phù Yên/Bắc Yên → Hoàng Gia Đạt · Yên Châu → Điêu Chính Luân.
- Lò Văn Thảnh nhận: Tuần Giáo/Mường Ảng/Tủa Chùa (từ Hoàng Gia Đạt) + Na Sang (từ Bùi Văn Đông).
"""

AM_OF = {
    # Nguyễn Công Nam (12)
    "(LCH) Nậm Hàng": "Nguyễn Công Nam", "(LCA) Sa Pa": "Nguyễn Công Nam",
    "(LCH) Sìn Hồ": "Nguyễn Công Nam", "(LCH) Nậm Mạ": "Nguyễn Công Nam",
    "(LCH) Tân Phong": "Nguyễn Công Nam",
    "(LCH) Phong Thổ": "Nguyễn Công Nam", "(LCA) Lào Cai": "Nguyễn Công Nam",
    "(LCH) Bum Tở": "Nguyễn Công Nam", "(LCA) Bát Xát": "Nguyễn Công Nam",
    "(LCH) Bình Lư": "Nguyễn Công Nam", "(LCH) Than Uyên": "Nguyễn Công Nam",
    "(LCH) Tân Uyên": "Nguyễn Công Nam",
    # Bùi Văn Đông (4) — Na Sang chuyển sang Lò Văn Thảnh
    "(DBI) Na Son": "Bùi Văn Đông", "(DBI) Thanh An": "Bùi Văn Đông",
    "(DBI) Mường Nhé": "Bùi Văn Đông", "(DBI) Điện Biên Phủ": "Bùi Văn Đông",
    # Hoàng Gia Đạt (7) — nhận Vân Hồ/Phù Yên/Bắc Yên; bỏ Tuần Giáo/Mường Ảng/Tủa Chùa
    "(SLA) Thảo Nguyên": "Hoàng Gia Đạt", "(SLA) Tô Hiệu": "Hoàng Gia Đạt",
    "(SLA) Mộc Sơn": "Hoàng Gia Đạt", "(SLA) Quỳnh Nhai": "Hoàng Gia Đạt",
    "(SLA) Vân Hồ": "Hoàng Gia Đạt", "(SLA) Phù Yên": "Hoàng Gia Đạt",
    "(SLA) Bắc Yên": "Hoàng Gia Đạt",
    # Lò Văn Thảnh (4) — AM MỚI (30/09/2026)
    "(DBI) Tuần Giáo": "Lò Văn Thảnh", "(DBI) Mường Ảng": "Lò Văn Thảnh",
    "(DBI) Tủa Chùa": "Lò Văn Thảnh", "(DBI) Na Sang": "Lò Văn Thảnh",
    # Nguyễn Đức Thịnh (9)
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
    # Bế Ngọc Chuyển (11)
    "(YBA) Âu Lâu": "Bế Ngọc Chuyển", "(YBA) Cầu Thia": "Bế Ngọc Chuyển",
    "(YBA) Cát Thịnh": "Bế Ngọc Chuyển", "(YBA) Thác Bà": "Bế Ngọc Chuyển",
    "(YBA) Lục Yên": "Bế Ngọc Chuyển", "(YBA) Đông Cuông": "Bế Ngọc Chuyển",
    "(YBA) Mậu A": "Bế Ngọc Chuyển", "(YBA) Bảo Ái": "Bế Ngọc Chuyển",
    "(YBA) Trấn Yên": "Bế Ngọc Chuyển", "(YBA) Mù Cang Chải": "Bế Ngọc Chuyển",
    "(YBA) Văn Phú": "Bế Ngọc Chuyển",
}
