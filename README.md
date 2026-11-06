# パソコン工房 PC価格一覧（GitHub Actions）

パソコン工房 公式通販のデスクトップ・ノートPCの全商品を取得し、Excelに出力します。

## 出力列
商品ID / 区分 / 型番 / 販売価格(税込) / CPU型番 / グラフィックス / メモリ容量 / メモリ規格 / 1stストレージ / ストレージ容量 / クーラー / 電源

列を変えるときは `pckoubou_list.py` の `COLUMNS` を編集します。

## 実行
- 手動：GitHubの「Actions」タブ →「パソコン工房 PC価格一覧」→「Run workflow」
- 自動：毎日 9:17 JST（`.github/workflows/pckoubou.yml` の `schedule` を消すと手動のみ）

## 結果の受け取り
- 最新版：リポジトリの `output/pckoubou_pc_latest.xlsx`
- 日付付き：実行結果ページ下部の Artifacts から（30日間保存）

## ローカル実行
```
pip install openpyxl
python pckoubou_list.py --out output
```
