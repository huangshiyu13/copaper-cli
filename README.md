# Copaper CLI

当前版本：**0.1.1**。代码统一维护在 `main`，每个版本的改动见 [CHANGELOG.md](CHANGELOG.md)，发布步骤见 [RELEASING.md](RELEASING.md)。

用 Python 命令行直接访问 Copaper：管理项目、同步论文源文件、编译并下载 PDF。使用网站创建的 API Key 登录，无需提取浏览器 Cookie。

命令名为 **`cp-cli`**，需要 **Python 3.10 或更新版本**。运行 CLI 不需要 Node.js 或本机 LaTeX。

## 安装

推荐用 [uv](https://docs.astral.sh/uv/guides/tools/) 安装独立的命令行工具：

```bash
uv tool install --python 3.12 git+https://github.com/huangshiyu13/copaper-cli.git
cp-cli --version
```

也可以使用 pipx（先准备 Python 3.10+）：

```bash
pipx install git+https://github.com/huangshiyu13/copaper-cli.git
```

从源码安装：

```bash
git clone https://github.com/huangshiyu13/copaper-cli.git
cd copaper-cli
python3 -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install .
cp-cli --help
```

安装完成后可在任意目录运行 `cp-cli`。命令不存在时，检查 uv/pipx 的可执行文件目录是否已加入 PATH。

升级：

```bash
uv tool upgrade copaper-cli
# 或：pipx upgrade copaper-cli
```

## 创建 Key 和登录

1. 登录 [Copaper](https://paper.mm-lab.cn/)，打开导航中的 [API Keys](https://paper.mm-lab.cn/user/api-keys)。
2. 点击“创建 API Key”，填写设备名称、有效期和权限。一般写作选择读取、编辑和编译三项权限。
3. 可将 Key 限定到所选项目；不限定时，只能访问当前账号本身有权限的项目。
4. 在列表中点击“显示”或“复制”。以后重新打开页面仍可查看，不必重新创建。

```bash
# 将 sk-xxxxx 替换成网站复制的完整 Key
cp-cli login --key sk-xxxxx
cp-cli whoami
cp-cli list
```

为避免 Key 进入 shell 历史，推荐隐藏输入：

```bash
cp-cli login --key
```

也支持从标准输入读取：

```bash
printf '%s\n' "$COPAPER_API_KEY" | cp-cli login --key -
```

命令行参数可能被 shell 历史或进程列表记录；`--key` 隐藏输入、标准输入和 CI 密钥环境变量可避免把 Key 写进命令文本。CLI 不打印 Key，也不需要保存账号密码。

默认站点为 `https://paper.mm-lab.cn`。其他兼容站点可这样设置：

```bash
cp-cli config set-url https://paper.example.org
cp-cli login --key
cp-cli config get-url
cp-cli auth status
cp-cli logout
```

已保存的凭据与站点地址绑定；改地址后必须显式重新登录。请求重定向不会转发 Key。

## 第一次使用

```bash
cp-cli list
cp-cli pull "My Paper" paper
cd paper
# 用你习惯的编辑器修改 main.tex
cp-cli diff
cp-cli push --dry-run
cp-cli push
cp-cli pdf -o draft.pdf
```

`pull` 建立本地项目绑定。之后在该目录可省略项目参数；进入其他目录时，可使用项目名称、唯一名称前缀、ID 或同一站点的项目链接。

```bash
cp-cli info "My Paper"
cp-cli project create "New Paper"
cp-cli project create "Example Paper" --template example
cp-cli project rename "Renamed Paper" "My Paper"
```

## 同步和冲突

```bash
cp-cli pull "My Paper" paper
cd paper
cp-cli diff --name-only
cp-cli sync --dry-run
cp-cli sync
```

- `pull` 下载远端变更，保留本地未提交的修改；双方修改冲突时停止并列出路径。
- `push` 上传本地新增或修改的文件，默认不删除远端文件。需要传播本地删除时用 `push --delete`。
- `sync` 同步双方修改，默认传播本地和远端删除；`sync --no-delete` 保留远端被本地删除的文件。
- 同一文件在两端都改过时返回退出码 `3`，不会自动选择一方。检查 `diff` 后再决定如何处理。
- `pull --force` 明确采用远端文件，可能覆盖本地修改；`push --force` 明确采用本地修改。使用前请检查差异。
- 远端在上传期间再次变化时，服务会拒绝旧版本请求。重新运行 `diff` / `sync`，不要盲目重复强制覆盖。
- 同步过程如果中断，本地绑定不会被当作完整成功更新；重新 `diff` 检查双方状态后再重试。

包含删除或文件类型替换的同步，可能要求先关闭该项目在浏览器中的编辑窗口。按错误提示关闭窗口后重试即可。

目标目录非空且尚未绑定项目时，`pull` 会停止。建议给每个项目使用独立的空目录。

## 文件操作

```bash
cp-cli upload figure.pdf --to figures/figure.pdf
cp-cli download figures/figure.pdf -o downloaded-figure.pdf
cp-cli rename figures/figure.pdf figures/renamed.pdf
cp-cli delete figures/renamed.pdf --dry-run
cp-cli delete figures/renamed.pdf
cp-cli zip -o source.zip
```

`rename` / `mv` 和 `delete` / `rm` 同时支持文件与文件夹。操作使用最新项目版本作为前提；并发变更会中止操作。

下载不会默认覆盖已有本地文件；确认要覆盖时添加 `--force`。路径不能越出项目目录，也不能通过符号链接读取或覆盖其他文件。

## 编译和导出

```bash
cp-cli compile
cp-cli compile -r appendix.tex --compiler xelatex
cp-cli pdf -o paper.pdf
cp-cli pdf -r appendix.tex --compiler xelatex -o appendix.pdf
cp-cli output --list
cp-cli output log -o compile.log
cp-cli output bbl -o bibliography.bbl
```

编译在 Copaper 上完成，无需本机安装 TeX。`output` 获取该账号最近一次 CLI 编译的产物；可用 `--project` 指定目录绑定以外的项目。

编译失败会返回退出码 `2`，随后可运行 `cp-cli output log` 查看日志。

## 忽略规则

默认忽略 LaTeX 临时文件、日志、缓存、`node_modules`、虚拟环境和 `output.pdf`，但会保留作为论文插图的其他 PDF。

- `.cpignore`：可共享的 Gitignore 风格规则。
- `.cpignore.local`：只对本机生效，永不上传。
- `.copaper/`：项目绑定和同步基线，永不上传；建议加入论文仓库的 `.gitignore`。

```gitignore
# .cpignore 示例
scratch/
*.tmp
```

```bash
cp-cli ignored
```

凭据文件、`.env`、私密证书/私钥和 Git 元数据属于保护范围，不能用忽略规则的 `!` 取消保护。配置目录即使位于工作目录内，也不会被同步。上传内容如果包含当前 API Key，会被阻止。

项目上限为 1500 个文件、128 MiB，单文件上限为 32 MiB。

## 命令参考

| 命令 | 用途 |
| --- | --- |
| `login --key` / `login --key <key>` | 隐藏输入或显式指定 Key 登录 |
| `login --key -` | 从标准输入读取 Key |
| `auth status` / `whoami` | 查看当前登录账号和权限 |
| `logout` | 清除本机保存的凭据 |
| `list` / `list --json` / `list -n` | 项目列表、JSON、仅名称 |
| `info [project]` | 项目详情和文件清单 |
| `project create <name>` | 新建项目；支持 `--template blank/example` |
| `project rename <new> [project]` | 重命名项目 |
| `pull [project] [directory]` | 下载并绑定项目；支持 `--dry-run`、`--force` |
| `push [directory]` | 上传修改；支持 `--dry-run`、`--delete`、`--force` |
| `sync [directory]` | 双向同步；支持 `--dry-run`、`--no-delete` |
| `diff [project] [directory]` | 比较本地与远端；支持 `--name-only`、`--exit-code` |
| `upload <file> [project] --to <path>` | 上传文件 |
| `download <file> [project] -o <path>` | 下载文件 |
| `delete <path> [project]` | 删除文件/文件夹；别名 `rm` |
| `rename <old> <new> [project]` | 移动或重命名文件/文件夹；别名 `mv` |
| `zip [project] -o <path>` | 导出源码 ZIP |
| `compile [project]` | 编译；支持 `-r` 和 `--compiler` |
| `pdf [project] -o <path>` | 编译并下载 PDF |
| `output [type]` | 下载编译输出；支持 `--list`、`--project` |
| `ignored [directory]` | 查看忽略规则 |
| `config set-url/get-url` | 配置站点地址 |
| `config set-timeout/get-timeout` | 配置请求超时秒数 |

全局参数可放在命令前或后：`--base-url`、`--timeout`（1–600 秒）、`--json`、`--verbose`。

退出码：`0` 成功；`1` 一般错误，或 `diff --exit-code` 检测到差异；`2` 参数错误或编译失败；`3` 同步冲突/版本变化；`130` 用户取消。

## 配置与自动化

| 环境变量 | 用途 |
| --- | --- |
| `COPAPER_API_KEY` | 提供 Key，不写入本机配置 |
| `COPAPER_BASE_URL` | 环境变量 Key 对应的站点 |
| `COPAPER_CONFIG_DIR` | 指定本机配置目录 |
| `XDG_CONFIG_HOME` | macOS/Linux 用户配置目录的上级目录 |

凭据默认存放在用户配置目录中的 `copaper/credentials.json`：macOS/Linux 通常为 `~/.config/copaper/credentials.json`，Windows 使用 `LOCALAPPDATA`。文件放在项目目录之外；POSIX 下目录权限为 `700`、凭据文件为 `600`。不要把该文件复制进代码仓库。Windows 下请使用当前用户的私有配置目录，并保留系统的访问控制。

`COPAPER_API_KEY` 优先于保存的凭据。环境变量 Key 也绑定于 `COPAPER_BASE_URL`（未设置时使用已配置的站点）；单独用 `--base-url` 切换到其他地址不会转发 Key。

`logout` 只清除本机保存的 Key，不会删除环境变量或撤销网站上的 Key。网站撤销后，所有使用该 Key 的客户端都会失去访问权限。

HTTP(S) 代理使用 Python 的标准代理环境变量。生产站点必须使用 HTTPS；HTTP 仅允许本机回环地址。

## 开发和测试

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m ruff check src tests tools
python -m pytest --cov=copaper_cli --cov-branch \
  --cov-report=term-missing --cov-report=json --cov-report=xml
python tools/check_coverage.py
python tools/check_public_tree.py
python -m build
python tools/check_public_tree.py --artifacts dist
```

覆盖率包括 `src/copaper_cli` 下全部运行代码及模块入口，启用分支覆盖。CI 同时要求 **语句覆盖率 ≥95%** 和 **分支覆盖率 ≥95%**，不通过降低门槛或排除生产文件来满足指标。

测试使用临时目录和本机 HTTP 测试服务，不需要真实 Key，不连接生产站点，不修改真实论文。覆盖登录、凭据权限、站点绑定、重定向防护、密钥脱敏、符号链接、路径穿越、文本与二进制文件、并发冲突、删除传播、编译及文件导出。

测试日志、覆盖率报告、虚拟环境和构建产物均由 `.gitignore` 排除。发布前会检查源码及安装包，拒绝真实 Key、私钥、凭据文件和机器私有路径。

问题反馈请查看 [CONTRIBUTING.md](CONTRIBUTING.md)，安全说明见 [SECURITY.md](SECURITY.md)，公开接口约定见 [docs/API.md](docs/API.md)。

## License

MIT，见 [LICENSE](LICENSE)。命令设计参考了 [olcli](https://github.com/aloth/olcli)，本项目通过 Copaper 的公开 API 独立实现。
