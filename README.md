# 美股波動率觀察清單

全美上市股票與 ETF 的波動率排行，架在 GitHub Pages 上，由 GitHub Actions 每個交易日自動更新。

- **週期**：日／3 日／週（5 日）／月（21 日），並顯示與前一期相比的變化
- **指標**：區間振幅、歷史波動率（年化）、漲跌幅
- **功能**：點欄位排序、搜尋、股票／ETF 篩選、最低股價與成交額門檻

## 部署步驟

1. 在 GitHub 建立一個新的 repository（Public，或有 Pro 方案的 Private），把這個資料夾的內容推上去：
   ```bash
   git init -b main
   git add .
   git commit -m "init"
   git remote add origin https://github.com/<你的帳號>/<repo 名稱>.git
   git push -u origin main
   ```
2. **Settings → Pages**：Source 選 *Deploy from a branch*，Branch 選 `main`、資料夾選 `/docs`，按 Save。
3. **Settings → Actions → General → Workflow permissions**：選 *Read and write permissions*。
4. **Actions → Update volatility data → Run workflow** 手動跑第一次（約 15–30 分鐘）。
5. 完成後打開 `https://<你的帳號>.github.io/<repo 名稱>/`。

之後每週二到週六台灣時間 06:30 會自動更新（美股收盤後）。

## 本機測試

```bash
pip install -r requirements.txt
python scripts/build_data.py --limit 300   # 只抓 300 檔快速測試
python scripts/build_data.py --demo        # 不連網，產生假資料看版面
python -m http.server -d docs 8000         # 開 http://localhost:8000
```

## 資料來源與範圍

- 股票清單：Nasdaq Trader Symbol Directory（NYSE、Nasdaq、NYSE American、NYSE Arca、Cboe），排除測試代號、權證、權利、Unit 與特別股。
- 股價：Yahoo Finance 日線（經 `yfinance`，還原權息）。
- 券商篩選：見 `brokers/README.md`。

## 檔案

| 路徑 | 用途 |
| --- | --- |
| `scripts/build_data.py` | 抓清單與股價、計算指標、輸出 `docs/data.json` |
| `docs/index.html` | 觀察清單網頁 |
| `.github/workflows/update.yml` | 排程 |
| `brokers/*.txt` | 各券商可交易代號（選填） |
