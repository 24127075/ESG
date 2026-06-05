# Hướng dẫn chạy — Pipeline Thu thập & Tiền xử lý dữ liệu ESG (Phase 1 & 2)

Tài liệu này giải thích **mục đích**, **làm gì**, **vì sao làm như vậy**, **cần những gì để chạy**, và **các cách chạy** hệ thống `sda_esg_pipeline`. Phần đánh giá tính thực tế của tài liệu SDA gốc nằm ở [DANH_GIA_SDA.md](DANH_GIA_SDA.md).

---

## 1. Mục đích — vì sao có hệ thống này?

Dự án xây dựng **dữ liệu đầu vào sạch** cho một **mô hình định lượng ESG** trên ~310 cổ phiếu niêm yết tại Việt Nam, gồm hai dòng dữ liệu:

- **Định lượng (Phase 1 — Quantitative):** số liệu tài chính & giao dịch để tính các biến của **mô hình Fama-French 6 nhân tố** (RMW, CMA, SMB, $R_t$, $R_f$…).
- **Phi cấu trúc (Phase 1 crawler + Phase 2):** thu thập **báo cáo thường niên / phát triển bền vững (PDF)** và **tin tức**, rồi **trích xuất → làm sạch → cắt đoạn ngữ nghĩa → gắn nhãn ESG (E/S/G)** thành **JSONL** sẵn sàng cho mô hình NLP (PhoBERT).

> Nói ngắn gọn: **đầu vào** là mã cổ phiếu + báo cáo PDF + tin tức; **đầu ra** là (a) bảng số liệu FF6 và (b) các đoạn văn bản đã gắn nhãn ESG ở dạng JSONL.

## 2. Làm gì & vì sao làm vậy (kiến trúc rút gọn)

| Thành phần                                                        | Làm gì                                                                                          | Vì sao thiết kế vậy                                                                        |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| **Quantitative** (`phase1_ingestion/quantitative.py`)       | Lấy OHLCV + BCTC qua**vnstock FREE**; tính ME, tăng trưởng tài sản                   | Dữ liệu thật, không cần API key;**retry + fallback CSV** để pipeline không đứt |
| **Crawler 3 tầng** (`crawler_tier1/2/3`)                   | Tier1 quét DOM HOSE/HNX; Tier2 DuckDuckGo (miễn phí, không key); Tier3 RSS                 | Đa nguồn, có chặn domain giả mạo, tiết kiệm quota                                      |
| **Downloader** (`downloader.py`)                            | Tải file,**quét mã độc (ClamAV)**, băm **SHA-256**                              | Chống mã độc & trùng lặp trước khi lưu                                                |
| **Dedup** (`deduplication.py`)                              | Chuẩn hóa**NFC** rồi SHA-256                                                             | Tránh 2 kiểu gõ Unicode sinh 2 hash khác nhau                                              |
| **Storage / Metadata** (`storage.py`, `metadata_repo.py`) | Lưu Local +**S3 versioned**; **máy trạng thái** tài liệu trong PostgreSQL       | Phục hồi sau lỗi, truy vết phiên bản                                                     |
| **Extraction** (`phase2_preprocessing/extraction.py`)       | PyMuPDF (text),**Camelot/pdfplumber** (bảng), **Tesseract** (OCR scan, lọc conf>60) | Ưu tiên độ chính xác bảng; OCR chỉ khi cần                                            |
| **Cleaning & Chunking** (`cleaning.py`)                     | NFC, gộp xuống dòng, cắt đoạn**≤ 200 token** (chừa headroom PhoBERT 256)            | Đoạn vừa cỡ cho mô hình; giữ `CO2`, `kWh`                                           |
| **Taxonomy** (`taxonomy.py`)                                | **Aho-Corasick** O(n), gắn nhãn E/S/G theo trọng số (ngưỡng ≥2)                      | Nhanh, giảm dương tính giả;**khớp không phân biệt dấu**                        |
| **Orchestrator** (`orchestrator.py`)                        | Ghép toàn bộ →**JSONL**                                                                 | Một entrypoint cho mỗi tài liệu                                                            |
| **Scheduler** (`scheduler/`)                                | **Celery Beat** chạy định kỳ; worker theo hàng đợi `phase1/phase2/dlq`             | Tự động hoá; cô lập lỗi qua DLQ                                                         |
| **Monitoring/Security** (`common/`)                         | Slack alert khi tỷ lệ lỗi ≥15%; secrets qua Vault/AWS; chống path-traversal                  | Quan sát & an toàn ở production                                                             |

Sơ đồ luồng đầy đủ xem §1 và §6 của SDA gốc.

---

## 3. Yêu cầu để chạy

### 3.1 Bắt buộc (mức tối thiểu — demo & test)

- **Python 3.10+** (repo đã test trên 3.13).
- Các gói Python lõi: `pandas`, `tenacity`, `requests` (xem `requirements.txt`).

> Demo §10 và toàn bộ test **chạy offline**, không cần mạng/Redis/DB nhờ cơ chế fallback (PhoBERT→đếm từ, Aho-Corasick→bộ quét thuần Python).

### 3.2 Để lấy **dữ liệu thật**

- **Mạng Internet**.
- `vnstock` (đã có trong `requirements.txt`) — **bản FREE, KHÔNG cần API key**.
- Để chạy Phase 2 trên PDF thật: `PyMuPDF`, `pdfplumber`, `camelot-py[cv]`, `pytesseract`, `Pillow`, `transformers`, `torch`, `pyahocorasick`.

### 3.3 Phụ thuộc cấp hệ điều hành (chỉ khi dùng tính năng tương ứng)

| Tính năng                         | Cần cài                                                                  |
| ----------------------------------- | -------------------------------------------------------------------------- |
| OCR tài liệu scan                 | **Tesseract** + gói ngôn ngữ **`vie`**                    |
| Trích bảng (Camelot lattice)      | **Ghostscript** (+ `poppler-utils`, OpenCV qua `camelot-py[cv]`) |
| Hàng đợi / lịch                 | **Redis**                                                            |
| Máy trạng thái tài liệu (prod) | **PostgreSQL** (mặc định dev dùng SQLite)                        |
| Quét mã độc                     | **ClamAV** daemon (`clamd`)                                        |
| Bí mật (prod)                     | **AWS Secrets Manager** hoặc **HashiCorp Vault**              |
| Chạy cả stack                     | **Docker + Docker Compose**                                          |

> Windows: xem [§7 Lưu ý Windows](#7-lưu-ý-windows-utf-8).

---

## 4. Cài đặt

```powershell
# Từ thư mục sda_esg_pipeline/
python -m venv .venv
.\.venv\Scripts\Activate.ps1            # PowerShell  (Linux/macOS: source .venv/bin/activate)

# (A) Tối thiểu — đủ để chạy demo + test:
pip install pandas tenacity requests pytest

# (B) Đầy đủ (khuyến nghị — dữ liệu thật + Phase 2 + hạ tầng):
pip install -r requirements.txt

# (C) Cài như package để có lệnh `esg-pipeline`:
pip install -e .
```

Cài theo nhóm extras (tuỳ chọn): `pip install -e ".[crawler,extract,nlp,infra]"`.

Tạo file môi trường nếu chạy crawler/hạ tầng:

```powershell
Copy-Item .env.example .env      # Tier 2 (DuckDuckGo) KHÔNG cần key; chỉ điền REDIS_*/SLACK/S3 khi dùng
```

---

## 5. Các cách chạy

### 5.1 Demo Phase 2 (tái tạo đúng ví dụ §10) — offline

```powershell
python scripts/run_demo.py
# hoặc, sau pip install -e .:
esg-pipeline demo
```

Đầu ra khớp §10: `"Cong ty huong toi Net Zero. Tong luong phat thai CO2 ..."`, `heading_context="Bao cao Moi truong"`, `matched_tags=["E_Emissions"]`.

### 5.2 Chạy test (offline)

```powershell
pytest -q          # 21 passed
```

### 5.3 Lấy **dữ liệu định lượng thật** (vnstock FREE)

```powershell
python scripts/fetch_quant_demo.py --tickers VNM,FPT,HPG --start 2023-01-01 --end 2024-12-31
# Ghi out/quant/<MÃ>_ff6_yearly.csv, <MÃ>_ohlcv.csv + bảng tóm tắt market-cap
```

### 5.4 Chạy **Phase 2 trên PDF thật** → JSONL

```powershell
# (a) Tự sinh PDF ESG mẫu rồi chạy (hoàn toàn offline):
python scripts/run_phase2_pdf.py --ticker DEMO --year 2023

# (b) PDF có sẵn trên máy:
python scripts/run_phase2_pdf.py --pdf duong_dan/bao_cao.pdf --ticker VNM --year 2023

# (c) Tải báo cáo thật theo URL rồi chạy (giới hạn trang bảng để không treo):
$env:TABLE_EXTRACT_MAX_PAGES=8
python scripts/run_phase2_pdf.py --url "https://static2.vietstock.vn/data/HOSE/2024/BCTN/VN/VNM_Baocaothuongnien_2024.pdf" --ticker VNM --year 2024

# (d) TỰ TÌM báo cáo bằng DuckDuckGo (MIỄN PHÍ, không cần API key) rồi chạy:
python scripts/run_phase2_pdf.py --search --ticker FPT --year 2023

# Tài liệu scan (bật nhánh OCR Tesseract):
python scripts/run_phase2_pdf.py --pdf scan.pdf --ticker ABC --year 2023 --scanned
```

> Tier 2 (tìm báo cáo) dùng **DuckDuckGo** qua gói `ddgs` — **không cần API key**,
> thay cho Google CSE. Một số báo cáo là **bản scan ảnh** (không có lớp text) →
> chạy lại với `--scanned` để bật OCR (script sẽ tự gợi ý khi gặp 0 chunk).

Kết quả: `out/phase2/<MÃ>_<NĂM>_chunks.jsonl` (mỗi dòng 1 chunk đã gắn nhãn E/S/G).

### 5.5 Cào báo cáo ESG cho **TOÀN BỘ vũ trụ 311 mã** (`crawl_esg_universe.py`)

Chạy hàng loạt đúng luồng §5.4 (tìm → tải → Phase 2 → JSONL) cho **mọi mã × mỗi năm**, đọc danh mục từ `ff_universe.csv` (cột `ticker`, 311 mã). Đây là entrypoint để dựng **dataset đầu vào ở quy mô vũ trụ**.

```powershell
# Cả vũ trụ, 2020–2024 (≈ 311 × 5 = 1.555 lượt):
python scripts/crawl_esg_universe.py --years 2020-2024

# Test trước 5 mã, một năm:
python scripts/crawl_esg_universe.py --years 2023 --limit 5

# Một năm, xoá PDF sau khi trích để tiết kiệm ổ (vd Kaggle):
python scripts/crawl_esg_universe.py --years 2020 --no-keep-pdf --delay 6
```

**Các cờ:**

| Cờ | Ý nghĩa | Mặc định |
| --- | --- | --- |
| `--universe` | Đường dẫn `ff_universe.csv` | file ở gốc repo |
| `--years` | Khoảng `2020-2024` hoặc danh sách `2020,2022` | `2020-2024` |
| `--limit N` | Chỉ N mã đầu (test) | `0` = tất cả |
| `--delay` | Giây nghỉ giữa các lần search (chống rate-limit DuckDuckGo) | `5` |
| `--max-results` | Số kết quả DuckDuckGo mỗi lần | `10` |
| `--no-keep-pdf` | Xoá PDF sau khi trích, chỉ giữ JSONL | giữ PDF |
| `--scanned` | Ép nhánh OCR (Tesseract) cho mọi báo cáo | tắt |
| `--retry-failed` | Khi resume, thử lại các mã-năm **chưa** thành công | tắt |
| `--no-resume` | Bỏ qua manifest, chạy lại từ đầu | tắt |

**Resume & manifest.** Mỗi mã-năm ghi một dòng vào `out/phase2/_manifest.csv` (`ticker,year,status,url,n_chunks,error`). Chạy lại sẽ **tự bỏ qua** mã-năm đã xong; có thể **Ctrl-C an toàn** bất cứ lúc nào, manifest được giữ. Các trạng thái:

- `processed` — có ≥1 chunk ESG (thành công).
- `no_chunks` — tải được PDF nhưng 0 chunk (thường là **bản scan ảnh** → chạy lại `--scanned`; hoặc PDF không liên quan).
- `not_found` — search không ra PDF nào.
- `download_failed` / `search_error` — lỗi tải / lỗi Phase 2 (xem cột `error`).

> **⚠ Chất lượng nguồn (quan trọng).** Search `filetype:pdf` của DuckDuckGo đôi khi trả **nhầm công ty/năm** (mã A lại ra báo cáo công ty B). Hướng vận hành hiện tại là **"cào rộng, lọc sau"**: pipeline vẫn cào hết, rồi **lọc hậu kỳ** bằng cột `url` trong `_manifest.csv` (đối chiếu link có chứa đúng mã CK / đúng năm). Chi tiết ở [DANH_GIA_SDA.md](DANH_GIA_SDA.md) #12.

**Trên Kaggle** (Linux — extraction tốt hơn Windows). Bật **Internet** ở *Settings*, rồi:

```python
!git clone -b ESG https://github.com/24127075/ESG.git /kaggle/working/ESG
!pip install -q -r /kaggle/working/ESG/sda_esg_pipeline/requirements.txt
!cd /kaggle/working/ESG/sda_esg_pipeline && python scripts/crawl_esg_universe.py --years 2020 --no-keep-pdf --delay 6
```

Phiên Kaggle tối đa ~12h và `/kaggle/working` **chỉ giữ lại khi *Save Version*** ⇒ nên chạy **từng năm một** (≈311 job/lần) và lưu/tải `out/phase2/` sau mỗi lần. Lỗi `vnstock` *"Guest 20 req/phút"* (nếu thấy) thuộc luồng **định lượng**, không ảnh hưởng luồng báo cáo.

### 5.6 Chạy **toàn bộ stack** (Celery + Redis + Postgres)

```bash
# Cách 1 — Docker Compose (kèm sandbox AppArmor, §4.1):
docker compose up --build

# Cách 2 — chạy thủ công từng tiến trình:
celery -A esg_pipeline.scheduler.celery_app:app worker -Q phase2,phase1 --loglevel=INFO
celery -A esg_pipeline.scheduler.celery_app:app beat   --loglevel=INFO
```

Lịch Beat: `scan_quantitative` (02:00), `crawl_reports` (03:00), `poll_rss` (mỗi 30 phút). Lỗi 429 (quota) → circuit breaker → đẩy payload sang hàng đợi `dlq`.

### 5.7 Poll RSS một lần

```powershell
esg-pipeline rss
```

---

## 6. Đầu vào / Đầu ra

**Metadata cho 1 tài liệu** (`process_single_document`):

```json
{
  "absolute_storage_path": "C:/.../VNM_2024.pdf",
  "ticker": "VNM",
  "fiscal_year": 2024,
  "pdf_type_flag": "TEXT_BASED",        // hoặc "SCAN_BASED"
  "heading_context": "Bao cao Moi truong",   // (tuỳ chọn)
  "normalize_ocr": false                      // (tuỳ chọn; mặc định = true nếu scan)
}
```

**Một dòng JSONL đầu ra:**

```json
{"ticker":"VNM","fiscal_year":2024,"heading_context":"...","filtered_chunk_text":"...","token_count":120,"matched_tags":["E_Emissions","G_Governance"],"chunk_source":"TEXT"}
```

**Cào hàng loạt (§5.5):** đầu vào danh mục là `ff_universe.csv` (311 mã); ngoài JSONL mỗi mã-năm, còn ghi `out/phase2/_manifest.csv` (`ticker,year,status,url,n_chunks,error`) để theo dõi tiến độ, **resume**, và **lọc hậu kỳ** các kết quả nhầm nguồn theo cột `url`.

---

## 7. Lưu ý Windows (UTF-8)

Console Windows mặc định dùng cp1252; thư viện `vnstock` in banner tiếng Việt sẽ gây `UnicodeEncodeError`. Hệ thống đã tự `reconfigure` stdout/stderr sang UTF-8 (`common.logging_config.ensure_utf8_io`, gọi trong CLI/scripts). Nếu vẫn gặp lỗi mã hoá khi chạy lệnh tuỳ biến, đặt:

```powershell
$env:PYTHONUTF8 = "1"
```

---

## 8. Biến môi trường hữu ích

| Biến                                | Ý nghĩa                                                       | Mặc định                |
| ------------------------------------ | --------------------------------------------------------------- | -------------------------- |
| `VNSTOCK_SOURCE`                   | Nguồn dữ liệu vnstock (`VCI`/`TCBS`/`MSN`)             | `VCI`                    |
| `TABLE_EXTRACT_MAX_PAGES`          | Giới hạn số trang trích bảng (0 = tất cả)                | `0`                      |
| `STATIC_FALLBACK_ROOT`             | Thư mục CSV fallback định lượng                           | `./data/static_fallback` |
| `DATABASE_URL`                     | DB máy trạng thái (`postgresql://...` / `sqlite:///...`) | SQLite local               |
| `REDIS_HOST/PORT/DB`               | Redis cho rate-limit/cache/broker                               | `localhost:6379`         |
| `DDG_MAX_RESULTS`                  | Số kết quả DuckDuckGo (Tier 2, không cần key)             | `10`                     |
| `SLACK_ALERT_WEBHOOK`              | Cảnh báo Slack khi lỗi ≥15%                                 | rỗng                      |
| `SECRET_BACKEND`                   | `env`/`aws`/`vault`                                       | `env`                    |
| `PYTHONUTF8`                       | Ép UTF-8 (Windows)                                             | —                         |

---

## 9. Khắc phục sự cố

- **`UnicodeEncodeError` khi gọi vnstock** → đặt `PYTHONUTF8=1` (xem §7).
- **`vnstock_data` / Sponsor Tier** → **không dùng**; engine đã chuyển sang vnstock FREE.
- **Tải PDF báo 403** → đã thêm User-Agent trình duyệt; nếu URL trả HTML (WAF/viewer) thì đó không phải PDF trực tiếp (kiểm tra `Content-Type`).
- **Camelot chạy rất lâu** trên báo cáo lớn → đặt `TABLE_EXTRACT_MAX_PAGES` (vd 8–30).
- **`clamd not installed`** → cảnh báo bình thường ở dev (bỏ qua quét mã độc); production chạy ClamAV trong container.
- **Chỉ ra nhãn E, thiếu S/G** trên báo cáo thật → đã vá bằng so khớp **không phân biệt dấu** (xem [DANH_GIA_SDA.md](DANH_GIA_SDA.md) #5).
- **0 chunk** với PDF tự tạo → cần `\n\n` ngăn đoạn; `_extract_text_based` nay nối trang bằng `\n\n` (xem #6).

---

## 10. Bản đồ Module ↔ Mục SDA

`quantitative.py`→§2 · `crawler_tier1/2/3`→§3.1/3.2/3.3 · `deduplication`→§3.4 · `security`→§4.1 · `state`/`metadata_repo`→§4.2 · `monitoring`→§5 · `extraction`→§7 · `cleaning`→§8 · `taxonomy`→§9 · `orchestrator`→§10 · `metrics`→§11.
