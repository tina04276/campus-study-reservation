# 校園自習空間預約系統 V2

黃色介面的校園自習空間管理專題。支援一般座位、VIP、雙人／四人研究室；容量與設備、座位配置圖、入口走道、當日時段、預約篩選與明細、點數錢包、模擬儲值付款、取消退點、報到離場及管理者建立學生帳號。

## 本機啟動

Python 3.11以上。Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

開啟 http://127.0.0.1:8000 。初始帳號：

| 身分 | Email | 密碼 |
|---|---|---|
| 學生 | student@example.edu | student123 |
| 管理者 | admin@example.edu | admin123 |

管理者可建立新的學生帳號。示範帳號僅供課程展示，正式環境需移除或更換。

## 點數、付款與報到

- 所有新錢包從0點開始；1點=NT$1。
- 錢包建立儲值訂單後，按「確認模擬付款」才入帳；不收取真實款項。可取消待付款訂單。
- 預約按分鐘計費、點數進位；扣點成功與建立預約使用同一交易。
- 研究室費率按整間計算；使用人數不得超過容量。
- 開始前且未報到可取消、全額退原扣點；報到後不可取消。提前離場不退點，不提前釋出座位。
- 開始前15分鐘至預約結束前可網頁報到，非實體門禁驗證。
- PAYMENT_MODE預設demo；設為disabled（或其他值）會停用模擬付款與儲值。
- 20／35／60／100點費率與空間配置是展示資料，非參考店家的實際價格或平面圖。

## Render 與既有資料庫升級

沿用既有GitHub repo、Render Web Service與PostgreSQL，不必新增服務。推送main後由既有Render設定部署。

- build: `pip install -r requirements.txt`
- start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- DATABASE_URL：保留原PostgreSQL內部連線字串；JWT_SECRET保留現有值。
- 先備份資料庫。啟動時以ADD COLUMN補新欄位、建立錢包等新表，保留舊資料。
- 舊空間為免費一般座位、舊預約amount=0，不追補扣點。
- 自動初始化一次四類示範空間；可在管理介面停用，重啟不重複新增。
- 長座位代碼可能換行；管理者可縮短代碼，依現場調整座標及入口走道。

## 文件與驗證

- docs/SRS_校園自習空間預約系統.md：SRS v2.0，包含13項FR與資料字典。
- docs/ERD.mmd、docs/ERD.svg：E-R關係及可檢視圖形。
- docs/VALIDATION.md：已測項目與尚未驗收的效能、PostgreSQL並行及瀏覽器相容性項目。

```powershell
python -m pytest -q
```

字型Noto Sans TC以SIL Open Font License授權，授權檔在app/static/fonts/LICENSE。
