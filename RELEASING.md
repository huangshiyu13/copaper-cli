# 版本与交付

`main` 是唯一交付分支。每次交付同步版本、更新日志、Git 提交和注解标签，并及时推送 GitHub。

使用语义化版本：不兼容变化增加主版本，新增兼容功能增加次版本，修复、文档和发布流程调整增加修订版本。CLI 与服务端独立发布，HTTP API 的版本另行管理。

## 发布步骤

1. `git fetch origin --tags`，检查工作区并保留协作者修改；在最新 `origin/main` 上继续工作。
2. 只在 `src/copaper_cli/__init__.py` 修改 `__version__`。Setuptools 动态读取该值，命令行和 User-Agent 也使用它。同步 README 和 `CHANGELOG.md` 的最新版本、日期、改动说明、实际验证及比较链接。
3. 在隔离环境重新安装并执行检查：

   ```bash
   python -m pip install -e '.[dev]'
   python tools/check_release.py
   python -m ruff check src tests tools
   python -m pytest --cov=copaper_cli --cov-branch --cov-report=term-missing --cov-report=json --cov-report=xml
   python tools/check_coverage.py
   python tools/check_public_tree.py
   python -m build
   python tools/check_public_tree.py --artifacts dist
   ```

4. 将新 wheel 安装到干净的临时环境，确认安装包元数据与 `cp-cli --version` 一致，且源码包包含对应更新日志。不要提交虚拟环境、安装包、日志或覆盖率报告。
5. 检查待发布标签名，例如 `python tools/check_release.py --tag vX.Y.Z`；在 `main` 提交并创建同名注解标签，原子推送：

   ```bash
   git add <本次变更的文件>
   git diff --cached --check
   git commit -m "Release Copaper CLI vX.Y.Z: describe changes"
   git tag -a vX.Y.Z -m "Copaper CLI vX.Y.Z: describe changes"
   git push --atomic origin main refs/tags/vX.Y.Z
   git ls-remote origin refs/heads/main 'refs/tags/vX.Y.Z*'
   ```

6. 确认远端分支和标签指向预期提交，并查看 GitHub Actions。CI 会校验标签、已安装包、源码和日志版本；语句和分支覆盖率都必须达到 95%。无法读取远端 CI 状态时应明确区分本地通过与远端未验证。

已经发布的标签不可移动，修复后递增新版本。不自动发布到包注册表，不自动改变仓库可见性。`v0.1.0` 是对首个已验证实现的历史基线标签，从 `v0.1.1` 起版本提交包含本更新流程和日志。
