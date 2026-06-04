# Financial-Health

Repo chứa **`sda_esg_pipeline/`** — pipeline Thu thập & Tiền xử lý dữ liệu cho hệ
thống Mô hình Định lượng ESG (Phase 1 & 2), hiện thực hoá tài liệu
`SDA-Data-Ingestion-Preprocessing-v3`.

- ▶️ **Hướng dẫn chạy (tiếng Việt):** [sda_esg_pipeline/docs/HUONG_DAN_CHAY.md](sda_esg_pipeline/docs/HUONG_DAN_CHAY.md)
- 🔎 **Đánh giá tính thực tế của SDA + bản vá:** [sda_esg_pipeline/docs/DANH_GIA_SDA.md](sda_esg_pipeline/docs/DANH_GIA_SDA.md)
- 🧩 **Chi tiết kỹ thuật (English):** [sda_esg_pipeline/README.md](sda_esg_pipeline/README.md)

Chạy nhanh:
```bash
cd sda_esg_pipeline
pip install -r requirements.txt
python scripts/run_demo.py            # demo Phase 2 (offline, tái tạo ví dụ §10)
python scripts/fetch_quant_demo.py    # lấy dữ liệu định lượng THẬT qua vnstock FREE
pytest -q                             # 21 passed
```
