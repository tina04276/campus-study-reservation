# 校園自習空間預約系統

依據 SRS 製作的可操作原型，包含學生預約流程和管理者空間維護功能。

## 本機啟動

需要 Python 3.11 或更新版本。

```bash
python -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

開啟 <http://127.0.0.1:8000>。第一次啟動會建立 SQLite 資料庫及示範資料。

| 身分 | 帳號 | 密碼 |
| --- | --- | --- |
| 學生 | `student@example.edu` | `student123` |
| 管理者 | `admin@example.edu` | `admin123` |

登入後可查詢空間和時段、預約可用座位、查看或取消自己的預約。管理者可新增空間和座位、設定每週開放時間、啟用或停用空間與座位，並檢視預約清單。

## Render 部署

專案已附 `render.yaml` Blueprint。把此資料夾推送至 GitHub/GitLab 後，在 Render 建立 Blueprint 並選擇該儲存庫。部署前先建立一個 Render PostgreSQL 資料庫，將其內部連線字串設為服務的 `DATABASE_URL`；Blueprint 會為服務產生 `JWT_SECRET`。Web Service 使用免費方案，資料庫方案由你在 Render 建立時選擇。

資料庫連線字串可使用 `postgres://` 或 `postgresql://` 格式；應用程式會轉成 psycopg 3 連線方式。部署成功後，可在服務頁面使用 `/api/health` 確認健康狀態。

## 設定

- `DATABASE_URL`：省略時使用專案目錄下的 SQLite；部署時請設定為 PostgreSQL 連線字串。
- `JWT_SECRET`：簽署 2 小時有效的登入權杖；部署環境應使用長且隨機的值。

首次啟動會建立示範帳號和範例自習空間。上線前請移除或更換示範帳號密碼，並檢查初始資料是否符合正式環境需要。

## 執行測試

```bash
python -m pytest -q
```
