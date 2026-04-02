## 根目录“全进文件夹”方案（不失效版）

你要求：
- 根目录不要放这些模块/入口文件
- 但功能不要失效

现实约束：
- 把 `app.py`/`run_daily_digest.py` 等入口移走后，**原命令**（如 `streamlit run app.py`、`python run_daily_digest.py ...`）会失效，因为文件路径变了。
- 但功能不会失效：只要改为从新路径启动即可（例如 `streamlit run project/app.py`）。

### 一键搬家脚本

仓库已提供脚本：`scripts/restructure_move_all_into_folder.py`

先预演（不会真的移动）：

```bash
python3 scripts/restructure_move_all_into_folder.py --dest project --dry-run
```

确认计划无误后执行移动：

```bash
python3 scripts/restructure_move_all_into_folder.py --dest project
```

### 搬家后的启动方式

- Streamlit：

```bash
streamlit run project/app.py
```

- CLI：

```bash
python3 project/run_daily_digest.py --help
```

