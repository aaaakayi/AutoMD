#!/usr/bin/env bash
# ============================================================================
# AutoMD 一键安装脚本
# ----------------------------------------------------------------------------
# 目标: 在 WSL (Ubuntu) 或原生 Linux 上, 一键装好 AutoMD + mgltools
#       两个 conda 环境, 并准备好 .env。
#
# 用法:
#   bash setup.sh
#
# 预计耗时: 5-10 分钟 (主要在 conda 装包)
# 预计磁盘: 3-4 GB (两个 env 加起来)
# ============================================================================
set -e

# ── 颜色 / 格式辅助 ─────────────────────────────────────────────────────
BOLD="\033[1m"; GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"; NC="\033[0m"
info()  { printf "${BOLD}${GREEN}[✓]${NC} %s\n" "$*"; }
warn()  { printf "${BOLD}${YELLOW}[!]${NC} %s\n" "$*"; }
err()   { printf "${BOLD}${RED}[✗]${NC} %s\n" "$*" >&2; }
hr()    { printf -- "─%.0s" $(seq 1 70); printf "\n"; }

# ── 0. 检查前提 ─────────────────────────────────────────────────────────
hr
echo -e "${BOLD}AutoMD 安装程序${NC}"
hr

# 必须在项目根跑 (能找到 environment.yml)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f "environment.yml" ] || [ ! -f "mgltools-environment.yml" ]; then
    err "找不到 environment.yml / mgltools-environment.yml"
    err "请在项目根目录运行: bash setup.sh"
    exit 1
fi

# 检测 conda / mamba / micromamba
CONDA_CMD=""
if command -v mamba >/dev/null 2>&1; then
    CONDA_CMD="mamba"
    info "检测到 mamba: $(mamba --version | head -1)"
elif command -v conda >/dev/null 2>&1; then
    CONDA_CMD="conda"
    info "检测到 conda: $(conda --version)"
else
    err "未找到 conda / mamba"
    err "请先装 Miniconda: https://docs.conda.io/projects/miniconda/en/latest/"
    err "  或 Miniforge (推荐, 自带 mamba): https://github.com/conda-forge/miniforge"
    exit 1
fi

# 检测 WSL
if grep -q Microsoft /proc/version 2>/dev/null; then
    info "运行在 WSL: $(uname -r)"
fi

# ── 1. 创建主环境 (AutoMD) ───────────────────────────────────────────
hr
echo -e "${BOLD}[1/4] 创建 AutoMD 环境...${NC}"
hr

if $CONDA_CMD env list | grep -qE '^AutoMD\s'; then
    warn "AutoMD 环境已存在, 跳过创建 (如需重建: conda env remove -n AutoMD)"
else
    $CONDA_CMD env create -f environment.yml -y
    info "AutoMD 环境创建完成"
fi

# ── 2. 创建 mgltools 环境 (Python 2.7) ───────────────────────────────────
hr
echo -e "${BOLD}[2/4] 创建 mgltools 环境 (Python 2.7)...${NC}"
hr

if $CONDA_CMD env list | grep -qE '^mgltools\s'; then
    warn "mgltools 环境已存在, 跳过创建 (如需重建: conda env remove -n mgltools)"
else
    $CONDA_CMD env create -f mgltools-environment.yml -y
    info "mgltools 环境创建完成"
fi

# ── 3. 验证主依赖 ───────────────────────────────────────────────────────
hr
echo -e "${BOLD}[3/4] 验证依赖...${NC}"
hr

DEPS=(
    "openmm:openmm"
    "rdkit:rdkit"
    "mdtraj:mdtraj"
    "fastapi:fastapi"
    "langgraph:langgraph"
    "langchain_core:langchain-core"
    "pandas:pandas"
    "numpy:numpy"
)

MISSING=()
for entry in "${DEPS[@]}"; do
    label="${entry%%:*}"
    mod="${entry##*:}"
    if $CONDA_CMD run -n AutoMD python -c "import $mod" >/dev/null 2>&1; then
        info "$label  ✓"
    else
        err "$label  ✗ (import 失败)"
        MISSING+=("$label")
    fi
done

# tleap / antechamber (AmberTools) — 用 which 检查 (在 PATH 里)
if $CONDA_CMD run -n AutoMD which tleap >/dev/null 2>&1; then
    info "AmberTools (tleap)  ✓  $($CONDA_CMD run -n AutoMD which tleap)"
else
    err "AmberTools (tleap)  ✗  未找到"
    MISSING+=("AmberTools")
fi

# AutoDock Vina — 提供 /bin/vina 二进制, 对接必用。
# 必须在 environment.yml 里装好, 不要依赖运行时 set_env 自动修复
# (set_env 走的 conda libmamba solver 在某些状态下会误判 "已装",
#  returncode=0 但实际没装, 触发死循环)。
if $CONDA_CMD run -n AutoMD which vina >/dev/null 2>&1; then
    info "AutoDock Vina (vina)  ✓  $($CONDA_CMD run -n AutoMD which vina)"
else
    err "AutoDock Vina (vina)  ✗  未找到"
    err "  修复: ${CONDA_CMD} install -n AutoMD -c bioconda -c conda-forge autodock-vina"
    MISSING+=("AutoDock Vina")
fi

# obabel (openbabel CLI) — 配体准备必用
if $CONDA_CMD run -n AutoMD which obabel >/dev/null 2>&1; then
    info "openbabel (obabel)  ✓  $($CONDA_CMD run -n AutoMD which obabel)"
else
    err "openbabel (obabel)  ✗  未找到"
    MISSING+=("openbabel")
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    err ""
    err "以下依赖缺失, 安装可能不完整: ${MISSING[*]}"
    err "试试手动重装: ${CONDA_CMD} install -n AutoMD -c conda-forge <包名>"
    err "或重新跑: ${CONDA_CMD} env remove -n AutoMD && bash setup.sh"
fi

# ── 4. 配置 .env ────────────────────────────────────────────────────────
hr
echo -e "${BOLD}[4/4] 配置 .env...${NC}"
hr

if [ -f ".env" ]; then
    warn ".env 已存在, 跳过 (如需重置: rm .env && bash setup.sh)"
else
    if [ ! -f ".env.example" ]; then
        err "找不到 .env.example"
        exit 1
    fi
    cp .env.example .env
    info "已生成 .env"
    echo ""
    warn "请编辑 .env 填入你的 LLM_API_KEY 等:"
    warn "  nano .env       # 或 vim .env / code .env"
    echo ""
fi

# ── 完成 ────────────────────────────────────────────────────────────────
hr
echo -e "${BOLD}${GREEN}✅ 安装完成!${NC}"
hr
echo ""
echo "下一步:"
echo ""
echo -e "  ${BOLD}1.${NC} 编辑 .env, 填入 LLM_API_KEY (DeepSeek 注册就有: https://platform.deepseek.com/)"
echo -e "  ${BOLD}2.${NC} 启动 AutoMD:"
echo -e "     ${BOLD}conda activate AutoMD${NC}"
echo -e "     ${BOLD}python app.py${NC}"
echo -e "  ${BOLD}3.${NC} 浏览器打开 http://localhost:8765"
echo ""
echo "常用命令:"
echo "  conda activate AutoMD         # 进主环境"
echo "  conda deactivate              # 退出"
echo "  mamba env list                 # 看所有环境"
echo "  mamba env remove -n AutoMD     # 删环境"
echo ""
echo "问题排查: https://github.com/<your-repo>/AutoMD#troubleshooting"
echo ""
