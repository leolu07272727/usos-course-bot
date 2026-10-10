# USOS Course Bot — 使用說明

華沙大學 (UW) 自動選課機器人。在選課開放瞬間自動登入 USOSweb、找到你指定的課程與群組，並完成選課。

---

## 目錄

1. [快速開始](#快速開始)
2. [環境變數設定](#環境變數設定)
3. [config.yaml 設定](#configyaml-設定)
4. [兩種執行模式](#兩種執行模式)
5. [群組選擇策略](#群組選擇策略)
6. [查看目前狀態](#查看目前狀態)
7. [VPS 持續監控](#vps-持續監控)
8. [常見問題](#常見問題)

---

## 快速開始

```bash
# 1. 進入專案目錄
cd /path/to/usos-watcher

# 2. 安裝依賴
pip3 install -r requirements.txt

# 3. 設定帳號密碼（見下方）
export USOS_USERNAME=你的學號
export USOS_PASSWORD=你的密碼

# 4. 編輯 config.yaml，填入你的課程資訊

# 5. 搶課當天執行（等到 21:00 Warsaw 自動開搶）
python3 shoot.py
```

---

## 環境變數設定

以下變數**必填**，建議存成 `.env` 檔（不要 commit 進 git）：

```bash
# .env 範例
USOS_USERNAME=s123456          # 你的 UW 帳號（學號）
USOS_PASSWORD=yourpassword     # 你的 UW 密碼

# Telegram 通知（選填，有填才會送通知）
TELEGRAM_BOT_TOKEN=123456:ABC-xxx
TELEGRAM_CHAT_ID=987654321
```

使用方式：
```bash
source .env   # 或 set -a && source .env && set +a
```

其他可選變數：

| 變數 | 預設 | 說明 |
|------|------|------|
| `WARSAW_TZ_OFFSET` | `2` | 華沙時區偏移（夏令 CEST=2，冬令 CET=1） |
| `SHOOT_HOUR` | `21` | 目標時刻（小時，24hr） |
| `SHOOT_MIN` | `0` | 目標時刻（分鐘） |
| `USOS_WATCHER_CONFIG` | `config.yaml` | 設定檔路徑 |
| `USOS_WATCHER_STATE` | `state.json` | 狀態檔路徑 |

---

## config.yaml 設定

```yaml
targets:
  - registration_code: "2600-IBP-MS2026Z"   # 在 USOSweb 選課頁面上顯示的課程代碼
    course_name_match: "Master Seminar - IBP" # 課程名稱關鍵字（用來在頁面上找到正確那行）
    calendar_url: "https://usosweb.uw.edu.pl/kontroler.php?_action=dla_stud/rejestracja/kalendarz&lang=en"
    group_preference:
      strategy: keyword                       # 群組選擇策略（見下方）
      tier1_keywords: ["Hensel"]              # 優先匹配這些關鍵字（老師名字等）
      tier2_keywords: ["AI"]                  # tier1 找不到才用 tier2
      on_no_match: "email_and_exit"           # 找不到偏好群組時的行為

# 可以同時監控多個課程：
#  - registration_code: "2600-XYZ-2026Z"
#    course_name_match: "Statistics"
#    group_preference:
#      strategy: any_available
#      on_no_match: "notify_and_exit"

behavior:
  dry_run: false     # true = 只解析、不真的送出 POST（測試用）
  refill_mode: false # true = 選上後繼續監控，看有沒有更好的群組

email:
  daily_heartbeat_time: "09:00"  # 每天幾點送一次「還活著」通知（伺服器本地時間）
  failure_threshold_login: 3     # 登入失敗幾次才送通知
  failure_threshold_http: 5      # HTTP 失敗幾次才送通知
```

### 如何找 registration_code 和 course_name_match

1. 登入 [USOSweb](https://usosweb.uw.edu.pl)
2. 進入 **Dla studentów → Rejestracja → Kalendarz rejestracji**
3. 找到你要選的課，`registration_code` 是那個 `XXXX-XXX-XXXXZ` 格式的代碼
4. `course_name_match` 填課程名稱的一部分（能唯一識別即可）

---

## 兩種執行模式

### 模式一：`shoot.py`（搶課當天用，本機執行）

選課當天在自己電腦上跑，會等到指定時間才動作。**不需要 VPS**。

```bash
# 等到今天 21:00 Warsaw 時間才開始（選課當天用）
python3 shoot.py

# 立刻執行，不等（已開放 / 測試用）
python3 shoot.py --now

# 測試模式：解析 + 挑群組，但不送出 POST
# 先把 config.yaml 的 dry_run: true，再：
python3 shoot.py --now

# 如果 config.yaml 有多個 targets，用 --target 選哪一個
python3 shoot.py --now --target 1   # 第二個 target（從 0 開始）
```

執行過程中會在 terminal 顯示 log，如果有設 Telegram 也會收到通知。

---

### 模式二：`python -m usos_watcher`（持續監控，VPS 用）

每分鐘跑一次，自動偵測選課何時開放，開放就立刻搶。**適合放在 VPS + systemd timer**。

```bash
# 跑一輪（手動測試）
python3 -m usos_watcher

# 查看狀態
python3 -m usos_watcher status
```

---

## 群組選擇策略

在 `config.yaml` 的 `group_preference.strategy` 設定：

### `keyword`（預設）

優先選匹配 `tier1_keywords` 的群組，找不到才試 `tier2_keywords`。適合想選特定老師。

```yaml
strategy: keyword
tier1_keywords: ["Hensel", "Przemysław Hensel"]
tier2_keywords: ["AI", "Artificial Intelligence"]
```

### `group_number`

直接選特定群組編號（編號在 USOSweb 群組頁面表格第一欄）。

```yaml
strategy: group_number
prefer_group: 3    # 搶第 3 組
```

### `any_available`

不挑，選當下空位最多的群組。

```yaml
strategy: any_available
```

---

### `on_no_match` 行為

| 值 | 說明 |
|----|------|
| `email_and_exit` | 找不到偏好群組時送通知並停止（不亂選） |
| `notify_and_exit` | 同上（別名） |

---

## 查看目前狀態

```bash
python3 -m usos_watcher status
```

輸出範例：
```
Course                          Status               Registered   Group
------------------------------------------------------------------------------------------
2600-IBP-MS2026Z                FCFS_OPEN            ✅ yes       Hensel
```

狀態說明：

| 狀態 | 意思 |
|------|------|
| `FCFS_OPEN` | 選課已開放（先到先得） |
| `PENDING` | 還沒到開放時間 |
| `CLOSED` | 選課已關閉 |
| `COORDINATOR_ONLY` | 只有協調員可以換組 |
| `ROW_NOT_FOUND` | 在頁面上找不到這門課（代碼可能有誤） |
| `UNKNOWN` | 頁面格式未識別（會送通知附原始 HTML） |

---

## VPS 持續監控

如果想讓機器人在 VPS 上 24/7 監控（選課開放時自動搶）：

```bash
# 啟用
ssh root@your-vps "systemctl enable --now usos-watcher.timer"

# 停用
ssh root@your-vps "systemctl disable --now usos-watcher.timer"

# 查看最近 log
ssh root@your-vps "journalctl -u usos-watcher.service -n 50"
```

VPS 上設定檔和 .env 位置：
- 程式：`/opt/usos-watcher/app/`
- 環境變數：`/etc/usos-watcher/.env`
- 狀態檔：`/var/lib/usos-watcher/state.json`

---

## 常見問題

**Q：選課開放了但機器人說找不到群組**
- 可能所有群組都滿了，或 `tier1_keywords` / `tier2_keywords` 拼錯老師名字
- 改用 `strategy: any_available` 或 `strategy: group_number` 試試

**Q：`ROW_NOT_FOUND`**
- 確認 `registration_code` 完全正確（大小寫、連字號）
- 確認 `calendar_url` 是英文版（`&lang=en`）

**Q：本機跑 shoot.py 要讓電腦不睡眠**
- macOS：系統設定 → 省電 → 關閉「電腦空閒時睡眠」
- 或執行前跑 `caffeinate -i python3 shoot.py`

**Q：已經選上了但機器人還在跑**
- 機器人會偵測到 USOS 回傳的 `odczyt=1`（已選狀態），自動標記完成並停止
- 也可以手動更新 `state.json` 或直接停用 timer

**Q：想換帳號/換課程**
- 編輯 `config.yaml` 的 `targets`，換 `.env` 的帳密，刪掉 `state.json` 重跑即可
