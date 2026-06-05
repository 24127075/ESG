# Báo cáo đánh giá tính thực tế của tài liệu SDA & các bản vá đã áp dụng

**Tài liệu nguồn:** `SDA-Data-Ingestion-Preprocessing-v3.pdf` (Phase 1 & 2 — Hệ thống Mô hình Định lượng ESG)
**Phạm vi rà soát:** toàn bộ code trong `sda_esg_pipeline/` so với đặc tả; có kiểm chứng bằng **dữ liệu thật** (vnstock FREE + báo cáo PDF tải về).
**Ngày:** 2026-06-06

---

## 1. Kết luận tổng quan

SDA là một thiết kế **nghiêm túc và có chiều sâu kỹ thuật**: kiến trúc tách trách nhiệm (SoC) hợp lý, có máy trạng thái tài liệu, circuit breaker, retry/backoff, dedup bằng SHA-256 sau chuẩn hóa NFC, chống path-traversal, sandbox `nobody`+AppArmor, quản lý bí mật qua Secrets Manager/Vault. Những điểm này **đáng giữ nguyên** và đã được hiện thực hóa đầy đủ trong repo.

Tuy nhiên, **một số đoạn code minh hoạ không chạy được nguyên trạng** và **một vài SLA/giả định chưa khớp thực tế**. Mức độ:

| # | Vấn đề | Mức độ | Trạng thái |
|---|--------|--------|-----------|
| 1 | API vnstock trong §2 không khả dụng/không nhất quán (Sponsor Tier) | 🔴 Cao | ✅ Đã vá |
| 2 | Bộ lọc domain lừa đảo so khớp chuỗi con `'doc'` + parse URL thô | 🔴 Cao | ✅ Đã vá |
| 3 | Output ví dụ §10 không thể tái tạo từ code | 🟠 Trung bình | ✅ Đã vá |
| 4 | SLA "Throughput > 500 pages/sec" gây hiểu lầm | 🟠 Trung bình | ✅ Đã vá |
| 5 | Taxonomy không khớp tiếng Việt **có dấu** | 🔴 Cao | ✅ Đã vá |
| 6 | §8 cleaning giả định có `\n\n`; header/footer & heading thật không khớp | 🟠 Trung bình | ◐ Vá một phần |
| 7 | Tài liệu scan mất hoàn toàn bảng số liệu | 🟠 Trung bình | ⚠ Ghi nhận + đề xuất |
| 8 | HTTP 403 do thiếu User-Agent khi tải báo cáo | 🟠 Trung bình | ✅ Đã vá |
| 9 | Camelot `pages='all'` treo worker với báo cáo lớn | 🟡 Thấp | ✅ Đã vá |
| 10 | Các bẫy nhỏ trong code SDA gốc | 🟡 Thấp | ✅ Đã vá |
| 11 | Quota Google CSE 100/ngày + cần API key trả phí cho 310 mã | 🟠 Trung bình | ✅ Đã vá (đổi sang DuckDuckGo) |
| 12 | `_load_universe()` chỉ 5 mã + thiếu công cụ cào hàng loạt 311 mã | 🟠 Trung bình | ✅ Thêm runner / ◐ caveat chất lượng |

**Sau khi vá: toàn bộ 21 test xanh, demo §10 tái tạo chính xác, và pipeline chạy được trên dữ liệu thật** (chi tiết bằng chứng ở cuối).

---

## 2. Chi tiết từng vấn đề

### 🔴 #1 — API vnstock trong §2 không khả dụng / mâu thuẫn nội bộ

- **Bảng §2.2** ánh xạ trường dữ liệu qua `Fundamental().equity.income_statement(symbol, period='Y')`, `Market().equity(symbol).ohlcv()`, `Reference().company(symbol).info()`, `Market().interest_rate()`.
- **Code §2.3** lại gọi `from vnstock_data import Insights; Insights().get_financial_report(ticker, year)`.
- Hai chỗ **không khớp nhau**, và `vnstock_data` là gói **Sponsor Tier trả phí** — không cài được công khai (`pip` không có). Người dùng không có gói này ⇒ pipeline định lượng **không chạy được**.
- API **FREE** thực tế của `vnstock` 3.x là:
  `Vnstock().stock(symbol, source='VCI').quote.history(...)`, `.finance.income_statement(period='year')`, `.finance.balance_sheet(period='year')`, `.company.overview()`.

**Bản vá** (`phase1_ingestion/quantitative.py`):
- Viết lại toàn bộ engine dùng **API FREE**, không cần API key.
- `FF6_FIELD_MAPPING` nay ghi **cả hai**: `sdad_call` (theo SDA) và `real_call` (thực dùng) để truy vết.
- Thêm `fetch_ohlcv / fetch_income_statement / fetch_balance_sheet / fetch_company_overview` (đều bọc retry + exponential backoff như §2.3) và `fetch_ff6_inputs()` lắp ráp đầu vào FF6 thật.
- Giữ `fetch_financial_data(ticker, year)` (chữ ký cũ, scheduler đang gọi) nhưng đấu lại vào API thật + giữ cơ chế **fallback CSV tĩnh**.

**Lưu ý kinh tế lượng còn lại** (chưa vá, thuộc phạm vi mô hình):
- `interest_rate` (lãi suất TPCP 10 năm — $R_f$) **không có** trong vnstock free ⇒ cần nguồn ngoài (bảng trái phiếu HNX / SBV / nguồn dữ liệu vĩ mô).
- Bản community giới hạn **tối đa 8 kỳ** báo cáo tài chính.
- ME lịch sử (§2.2 hàng 5) nên dùng **số cổ phiếu theo thời điểm**; `company.overview().issue_share` chỉ là số **hiện tại** ⇒ tính ME cho chuỗi giá quá khứ sẽ sai số. Cần lịch sử cổ phiếu lưu hành (sự kiện phát hành/chia tách).

### 🔴 #2 — Bộ lọc domain lừa đảo so khớp chuỗi con `'doc'`

- `SCAM_KEYWORDS = ('tailieu','doc','scribd','123doc')` + `any(k in domain ...)`:
  - `'doc'` là **chuỗi con** ⇒ chặn nhầm **mọi** host chứa "doc".
  - `'123doc'` thừa (đã chứa `'doc'`).
  - `domain = url.split('/')[2]` **vỡ** khi URL không có scheme, có port, hoặc userinfo.
  - `official_domain in domain` (substring) dễ bị lách: `vinamilk.com.vn.evil.com`.

**Bản vá** (`phase1_ingestion/crawler_tier2.py`):
- Dùng `urllib.parse.urlparse(...).hostname`.
- `SCAM_DOMAINS` là danh sách **host** (tailieu.vn, 123doc.vn/.net, scribd.com, slideshare.net, academia.edu, coursehero.com, studocu.com…), khớp **chính xác host hoặc subdomain**, và **chặn trước** cả khi xét TLD `.vn` (để loại `123doc.vn`).
- So khớp domain chính thức theo `host == official` hoặc `host.endswith("."+official)`.

### 🟠 #3 — Output ví dụ §10 không thể tái tạo từ code

Ví dụ §10:
- Input thô: `"Cng ty huong toi N3t Zer0. ... Tong luong phat thai CO2 ..."`
- Output: `"Cong ty huong toi Net Zero. ..."`, `heading_context: "Bao cao Moi truong"`, field tên `filtered_chunk_text`, `token_count: 18`.
- **Không có bước nào** trong SDA sửa `N3t Zer0`→`Net Zero` hay `Cng`→`Cong`; `heading_context` không có trong input; `chunk_document` tạo field tên `text` chứ không phải `filtered_chunk_text`; `token_count: 18` chỉ là ước lượng (PhoBERT subword thật cho **22**).

**Bản vá** (`phase2_preprocessing/cleaning.py`, `orchestrator.py`, demo):
- Thêm `normalize_ocr_artifacts()`: gỡ leetspeak `N3t Zer0`→`Net Zero`, **bảo vệ** công thức hoá học/đơn vị (`CO2`, `kWh`) và số (`2023`, `1500`); thêm map confusable nhỏ `cng→cong` (production nên thay bằng spell-corrector tiếng Việt).
- `heading_context` truyền qua metadata (`process_single_document`) hoặc tham số (`process_raw_text`).
- Orchestrator xuất đúng tên field `filtered_chunk_text`.
- Demo (`scripts/run_demo.py`, `esg-pipeline demo`) nay dùng **input thô §10** và tái tạo **chính xác** câu output; `token_count` để giá trị PhoBERT thật.

### 🟠 #4 — SLA "Throughput > 500 pages/sec" gây hiểu lầm

Đo trên máy tham chiếu (1 core):
- Aho-Corasick taxonomy scan: **~47.700 trang/s**.
- clean + chunk (gồm tokenize PhoBERT): **~600 trang/s**.

⇒ Với phạm vi đúng như SDA ghi ("Aho-Corasick + Chunking"), ngưỡng 500 **đạt được**. **Nhưng** nó **bỏ qua nút thắt thật** của Phase 2: extraction Camelot ~1–5 trang/s, **OCR ~0,1–1 trang/s**. Hiểu "throughput pipeline > 500 trang/s" là **sai bản chất** — thực tế end-to-end thấp hơn hàng trăm lần.

**Bản vá** (`phase2_preprocessing/metrics.py`):
- Giữ `throughput_pps` (benchmark vi mô AC+chunking, > 500).
- Thêm `throughput_e2e_pps` (end-to-end, ngưỡng thực tế ≥ **3 trang/s/core**, bị chặn bởi extraction/OCR) + `measure_taxonomy_throughput()`; ghi chú rõ trong docstring.

### 🔴 #5 — Taxonomy không khớp tiếng Việt **có dấu** (phát hiện khi chạy báo cáo thật)

- Từ điển `config/esg_taxonomy.json` viết **không dấu** (`an toan lao dong`, `hoi dong quan tri`).
- Chuẩn hóa của code chỉ `NFC + lower`, **không bỏ dấu**. Báo cáo thật dùng **dấu đầy đủ** (`an toàn lao động`) ⇒ `"an toàn lao động" ≠ "an toan lao dong"` ⇒ **trượt**.
- Hệ quả thực đo trên **BCTN Vinamilk 2024**: chỉ match `E_Emissions` (vì `net zero`, `co2` vốn không dấu). Toàn bộ S/G **trượt**.

**Bản vá** (`phase2_preprocessing/taxonomy.py`):
- `_norm()` nay **fold dấu** (NFD → bỏ combining marks → `đ→d`). Diacritic-insensitive.
- **Kết quả thật trên cùng báo cáo:** từ **12 chunk (chỉ E)** → **51 chunk** đủ E/S/G (G=41, S=7, E=16).

### 🟠 #6 — §8 cleaning giả định có `\n\n`; header/footer & heading thật không khớp

- `clean_text_advanced` gộp **mọi** `\n` đơn thành space, chỉ giữ ranh giới đoạn ở `\n\n`. Nhiều trình trích xuất (kể cả PyMuPDF nối trang) **chỉ phát `\n`** ⇒ toàn văn gộp thành **một đoạn**. Nếu đoạn đó bắt đầu bằng "1." ⇒ bị `is_heading` nhận nhầm là heading ⇒ **0 chunk** (đã gặp khi sinh PDF mẫu).
- Header/footer thật (`"Báo cáo thường niên Vinamilk 2024 8 9 Luôn cầu tiến"`) **không khớp** regex `^(trang|page)\s*\d+` ⇒ noise lẫn vào chunk.
- Heading thật của báo cáo **không** theo dạng "1./2.3" ⇒ `heading_context` về mặc định "Thong tin chung".

**Bản vá một phần** (`phase2_preprocessing/extraction.py`):
- `_extract_text_based` nối các trang bằng `\n\n` (ranh giới trang = ranh giới đoạn) ⇒ tránh gộp toàn văn.

**Đề xuất bổ sung (chưa làm):**
- Dùng `page.get_text("blocks"/"dict")` của PyMuPDF để giữ cấu trúc đoạn/heading theo layout (font-size, vị trí) thay vì regex số thứ tự.
- Lọc header/footer bằng **tần suất lặp** dòng qua các trang (dòng xuất hiện ở >X% số trang → coi là chrome).

### 🟠 #7 — Tài liệu scan mất hoàn toàn bảng số liệu

- `extract_tables_robust(is_scanned=True)` trả `[]`; `_extract_scan_based` cũng `tables: []`.
- Nhiều báo cáo ESG/BCTN ở VN là **bản scan**, và **số liệu phát thải/ESG nằm trong bảng** ⇒ mất dữ liệu định lượng quan trọng. Mâu thuẫn tiềm tàng với SLA "Table Ext. > 85%" nếu tỷ lệ scan cao.

**Đề xuất:** dùng table-structure recognition cho ảnh (PaddleOCR PP-Structure, Microsoft Table Transformer, hoặc vision-LLM) ở nhánh scan; hoặc khai báo rõ SLA chỉ áp dụng cho PDF số hoá.

### 🟠 #8 — HTTP 403 do thiếu User-Agent (phát hiện khi tải báo cáo thật)

- Cổng IR (vinamilk.com.vn) trả **403** với UA mặc định `python-requests`.

**Bản vá** (`phase1_ingestion/downloader.py`): gửi UA trình duyệt + `Accept` + `Accept-Language`, `allow_redirects=True`, timeout 30→60s.
**Lưu ý:** nhiều URL "…pdf" thực ra trả **HTML** (WAF/viewer SPA) — đã có sẵn kiểm tra **magic `%PDF-`** chặn; production nên kiểm `Content-Type` và xử lý interstitial.

### 🟡 #9 — Camelot `pages='all'` treo worker với báo cáo lớn

- Lattice ~1–5 trang/s; chạy trên báo cáo 100+ trang có thể mất nhiều phút / treo.

**Bản vá** (`extraction.py`): biến môi trường `TABLE_EXTRACT_MAX_PAGES` giới hạn số trang cho **cả** Camelot và pdfplumber (0 = tất cả).

### 🟡 #10 — Các bẫy nhỏ trong code SDA gốc (repo đã/được vá)

- **OCR confidence:** `int(data['conf'][i]) > 60` lỗi `ValueError` khi conf là chuỗi float (`'95.5'`); repo dùng `_safe_conf` (float→int, mặc định -1). *(SDA gốc sẽ crash.)*
- **`requirements.txt`** thiếu `celery/boto3/psycopg2/clamd` dù README ghi "full stack" → **đã bổ sung** (kèm `hvac`).
- **Windows/cp1252:** banner tiếng Việt của vnstock làm `UnicodeEncodeError` → **đã thêm** `ensure_utf8_io()` (reconfigure stdout/stderr UTF-8) trong `logging_config`.
- **Regex nhỏ:** `LINK_PATTERN` (`download.*id=`) chưa escape dấu chấm; heading regex không bắt `XI/XII`. (Thấp, chưa đụng.)

### 🟠 #11 — Quota Google CSE 100/ngày + cần API key trả phí

- Tier 2 của SDA dùng **Google Custom Search Engine**: cần `GOOGLE_API_KEY` + `GOOGLE_CX_ID`, **chỉ 100 truy vấn/ngày** miễn phí ⇒ với 310 mã (≥1 query/mã) phải **>3 ngày/1 key** hoặc trả phí ($5/1.000 query). Phụ thuộc khóa + chi phí + quota là rào cản vận hành thật.

**Bản vá** (`phase1_ingestion/crawler_tier2.py`): chuyển Tier 2 sang **DuckDuckGo** (gói `ddgs`) — **miễn phí, không cần API key, không quota cứng**. Giữ nguyên: lọc domain piracy, cache Redis (tùy chọn), circuit breaker khi bị rate-limit → DLQ. Thêm `scripts/run_phase2_pdf.py --search` để tự tìm + tải + xử lý báo cáo không cần hạ tầng.
- ✅ Kiểm chứng: tìm & tải được báo cáo thật (vd FPT trên `fpt.vn`, ESG FPT 2023 trên `fpt.com`) không cần key.
- Lưu ý: nhiều báo cáo VN là **bản scan ảnh** (không có lớp text) ⇒ cần `--scanned` (OCR) — liên quan #6/#7.

### 🟠 #12 — `_load_universe()` chỉ có 5 mã & thiếu công cụ cào cả vũ trụ

- `scheduler/tasks.py::_load_universe()` trả **cứng 5 mã** (`VNM, FPT, HPG, VCB, MWG`) với chú thích "stub". Vũ trụ thật của mô hình FF6 là **311 mã** trong `ff_universe.csv` ⇒ scheduler/crawler chạy nguyên trạng chỉ phủ **<2%** danh mục.
- Chưa có entrypoint nào cào **hàng loạt** báo cáo ESG cho cả vũ trụ — `scripts/run_phase2_pdf.py` chỉ xử lý **một** mã/lần.

**Bản vá** (`scripts/crawl_esg_universe.py` — mới):
- Đọc `ff_universe.csv`, lặp **mỗi mã × mỗi năm**, tái dùng đúng chuỗi `search_esg_report → download_document → process_single_document` (không thêm phụ thuộc mới).
- **Resume** qua `out/phase2/_manifest.csv`; **back-off lũy thừa** khi DuckDuckGo rate-limit (60s → tối đa 15 phút); chịu lỗi từng mã (một mã hỏng không vỡ cả mẻ); cờ `--no-keep-pdf` cho ổ đĩa hạn chế (Kaggle).
- ✅ Kiểm chứng trên **Kaggle** (3 mã test 2023): 1 `processed` (16 chunk) + 2 `no_chunks` — pipeline chạy đúng end-to-end, manifest + resume hoạt động.

**Caveat còn lại — hướng "cào rộng, lọc sau" (đang chọn):**
- Search `filetype:pdf` có thể trả **nhầm công ty/năm** hoặc **bản scan** (`no_chunks`). Hiện **lọc hậu kỳ** bằng cột `url` trong manifest (đối chiếu link chứa đúng mã CK/năm). Bản **siết độ chính xác** (bắt buộc URL chứa đúng mã CK) được giữ lại như **lựa chọn**, chưa bật theo yêu cầu vận hành.
- `_load_universe()` vẫn nên đấu vào `ff_universe.csv`/DB ở production để scheduler phủ đủ 311 mã.

---

## 3. Những điểm SDA làm **đúng** (giữ nguyên)

- Chuẩn hóa **NFC trước SHA-256** cho dedup (tránh 2 kiểu gõ Unicode) — chuẩn xác.
- `secure_filename` + `safe_join` chống path-traversal (2 lớp).
- Bí mật qua AWS Secrets Manager / Vault, không `.env` ở production.
- Sandbox PDF: container `nobody` + AppArmor.
- Máy trạng thái tài liệu (PostgreSQL) để phục hồi.
- Retry + exponential backoff; circuit breaker + DLQ khi 429.
- Cap chunk 200 token chừa headroom cho PhoBERT (256) — hợp lý.
- **Không** lọc cứng ký tự để giữ `CO2`, `kWh` — đúng tinh thần.

---

## 4. Bằng chứng kiểm chứng (chạy thật)

| Hạng mục | Lệnh | Kết quả |
|----------|------|---------|
| Test suite | `pytest -q` | **21 passed** |
| Demo §10 (tái tạo) | `python scripts/run_demo.py` | `"Cong ty huong toi Net Zero. ..."`, `heading_context="Bao cao Moi truong"`, `E_Emissions` ✔ khớp §10 |
| Quant **thật** | `python scripts/fetch_quant_demo.py --tickers VNM,FPT,HPG` | FPT op-profit 2023 ≈ 9.111 tỷ; VNM/FPT/HPG market-cap hợp lý; ghi CSV |
| Phase 2 trên **PDF thật** | `... run_phase2_pdf.py --url <BCTN VNM 2024> ...` | tải 6.5MB OK → **51 chunk ESG** (E/S/G) sau vá bỏ dấu |
| Cào **hàng loạt** (Kaggle) | `python scripts/crawl_esg_universe.py --years 2023 --limit 3` | 3 job: 1 `processed` (16 chunk) + 2 `no_chunks`; manifest + resume OK |
| Throughput | đo nội bộ | taxonomy ~47.700 tr/s; clean+chunk ~600 tr/s |

> Nguồn báo cáo thật dùng để kiểm chứng: [BCTN Vinamilk 2024 (Vietstock mirror)](https://static2.vietstock.vn/data/HOSE/2024/BCTN/VN/VNM_Baocaothuongnien_2024.pdf) và trang [Báo cáo PTBV Vinamilk 2023](https://www.vinamilk.com.vn/phat-trien-ben-vung/bao-cao/2023/pdf/RGB-VIE-Vinamilk-SR-2023.pdf) (URL này trả HTML/WAF — minh hoạ vấn đề #8).

---

## 5. Việc còn nên làm (roadmap)

1. **Nguồn $R_f$** (lãi suất TPCP 10 năm) + lịch sử cổ phiếu lưu hành để ME chính xác (#1).
2. **Layout-aware extraction** (PyMuPDF blocks) + lọc header/footer theo tần suất + nhận diện heading theo font (#6).
3. **Table OCR cho bản scan** (PP-Structure/Table Transformer) (#7).
4. **Taxonomy:** duy trì song song bản có dấu + mở rộng từ khoá; cân nhắc gắn nhãn bằng PhoBERT (mô hình) thay vì chỉ từ điển, để giảm FP/tăng recall.
5. **Khám phá báo cáo:** đã đổi Tier 2 sang DuckDuckGo (miễn phí); có thể bổ sung đọc trực tiếp cổng HOSE/HNX (Tier 1) và **auto-OCR** khi PDF là bản scan (#7, #11).
6. **Cào toàn bộ vũ trụ:** đã có `scripts/crawl_esg_universe.py` (resume + manifest) cho 311 mã; cần (a) đấu `_load_universe()` vào `ff_universe.csv`/DB, (b) tùy chọn **siết độ chính xác** URL (bắt buộc chứa đúng mã CK) để giảm nhầm công ty, (c) auto `--scanned` cho mã `no_chunks` (#7, #12).
