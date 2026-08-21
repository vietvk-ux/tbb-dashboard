"""Ánh xạ BƯU CỤC → AM quản lý (Vùng TBB). Dùng cho xếp hạng theo AM ở báo cáo
trực tiếp + cuối ngày. Cập nhật khi có thay đổi phân công."""

AM_OF = {
    # Nguyễn Công Nam
    "(LCH) Nậm Hàng": "Nguyễn Công Nam", "(LCA) Sa Pa": "Nguyễn Công Nam",
    "(LCH) Sìn Hồ": "Nguyễn Công Nam", "(LCH) Nậm Mạ": "Nguyễn Công Nam",
    "(LCH) Tân Phong": "Nguyễn Công Nam", "(YBA) Văn Phú": "Nguyễn Công Nam",
    "(LCH) Phong Thổ": "Nguyễn Công Nam", "(LCA) Lào Cai": "Nguyễn Công Nam",
    "(LCH) Bum Tở": "Nguyễn Công Nam", "(LCA) Bát Xát": "Nguyễn Công Nam",
    "(LCH) Bình Lư": "Nguyễn Công Nam", "(LCH) Than Uyên": "Nguyễn Công Nam",
    "(LCH) Tân Uyên": "Nguyễn Công Nam",
    # Bùi Văn Đông
    "(DBI) Na Son": "Bùi Văn Đông", "(DBI) Thanh An": "Bùi Văn Đông",
    "(DBI) Mường Nhé": "Bùi Văn Đông", "(DBI) Na Sang": "Bùi Văn Đông",
    "(DBI) Điện Biên Phủ": "Bùi Văn Đông",
    # Hoàng Gia Đạt
    "(DBI) Mường Ảng": "Hoàng Gia Đạt", "(SLA) Thảo Nguyên": "Hoàng Gia Đạt",
    "(DBI) Tuần Giáo": "Hoàng Gia Đạt", "(SLA) Tô Hiệu": "Hoàng Gia Đạt",
    "(SLA) Mộc Sơn": "Hoàng Gia Đạt", "(DBI) Tủa Chùa": "Hoàng Gia Đạt",
    "(SLA) Quỳnh Nhai": "Hoàng Gia Đạt",
    # Đinh Văn Thu
    "(SLA) Vân Hồ": "Đinh Văn Thu", "(SLA) Phù Yên": "Đinh Văn Thu",
    "(SLA) Bắc Yên": "Đinh Văn Thu", "(SLA) Yên Châu": "Đinh Văn Thu",
    # Nguyễn Đức Thịnh
    "(LCA) Bảo Hà": "Nguyễn Đức Thịnh", "(LCA) Bảo Yên": "Nguyễn Đức Thịnh",
    "(LCA) Si Ma Cai": "Nguyễn Đức Thịnh", "(LCA) Bắc Hà": "Nguyễn Đức Thịnh",
    "(LCA) Bảo Thắng": "Nguyễn Đức Thịnh", "(LCA) Cam Đường 1": "Nguyễn Đức Thịnh",
    "(LCA) Mường Khương": "Nguyễn Đức Thịnh", "(LCA) Văn Bàn": "Nguyễn Đức Thịnh",
    "(LCA) Cam Đường 2": "Nguyễn Đức Thịnh",
    # Điêu Chính Luân
    "(SLA) Mai Sơn": "Điêu Chính Luân", "(SLA) Sông Mã": "Điêu Chính Luân",
    "(SLA) Thuận Châu": "Điêu Chính Luân", "(SLA) Mường La": "Điêu Chính Luân",
    "(SLA) Sốp Cộp": "Điêu Chính Luân", "(SLA) Chiềng Sinh": "Điêu Chính Luân",
    # Bế Ngọc Chuyển
    "(YBA) Âu Lâu": "Bế Ngọc Chuyển", "(YBA) Cầu Thia": "Bế Ngọc Chuyển",
    "(YBA) Cát Thịnh": "Bế Ngọc Chuyển", "(YBA) Thác Bà": "Bế Ngọc Chuyển",
    "(YBA) Lục Yên": "Bế Ngọc Chuyển", "(YBA) Đông Cuông": "Bế Ngọc Chuyển",
    "(YBA) Mậu A": "Bế Ngọc Chuyển", "(YBA) Bảo Ái": "Bế Ngọc Chuyển",
    "(YBA) Trấn Yên": "Bế Ngọc Chuyển", "(YBA) Mù Cang Chải": "Bế Ngọc Chuyển",
}
