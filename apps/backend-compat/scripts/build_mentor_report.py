from __future__ import annotations

from datetime import datetime
from pathlib import Path
from path_defaults import ants_bin, backend_root, mri_path, workspace_root
import json
import math

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml.ns import qn

ROOT = backend_root()
OUT_DIR = ROOT / "docs" / "reports"
ASSET_DIR = OUT_DIR / "assets"
OUT_DIR.mkdir(parents=True, exist_ok=True)
ASSET_DIR.mkdir(parents=True, exist_ok=True)

TODAY = datetime.now().strftime("%Y-%m-%d")

def draw_architecture(path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 7), dpi=180)
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 7)
    ax.axis("off")

    def box(x, y, w, h, text, color):
        rect = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.03,rounding_size=0.08", linewidth=1.5, edgecolor=color, facecolor="#0b1a2b")
        ax.add_patch(rect)
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color="#dbeafe", fontsize=10)

    box(0.5, 4.9, 3.2, 1.5, "React GUI\n(Region/3D/Results/Benchmark)", "#22d3ee")
    box(4.3, 4.9, 3.2, 1.5, "Optimizer Backend\nFlask + Planning Core", "#34d399")
    box(8.1, 4.9, 3.2, 1.5, "Unity WebGL\nNative picking + camera pose", "#f59e0b")

    box(0.5, 2.8, 3.2, 1.4, "State Hooks\nuseBackend/useOptimizer", "#60a5fa")
    box(4.3, 2.8, 3.2, 1.4, "Risk + Contract Layer\nSDF + failure cards", "#a78bfa")
    box(8.1, 2.8, 3.2, 1.4, "Atlas / Mesh Endpoints\n/api/allen_mesh*", "#f472b6")

    box(0.5, 0.7, 3.2, 1.4, "Replay + History\nImport/Export", "#38bdf8")
    box(4.3, 0.7, 3.2, 1.4, "Benchmark Pipeline\nreliability/external/paper", "#4ade80")
    box(8.1, 0.7, 3.2, 1.4, "Outputs\nCI, hash, figures, reports", "#fbbf24")

    arrows = [
        ((3.7, 5.65), (4.3, 5.65)),
        ((7.5, 5.65), (8.1, 5.65)),
        ((2.1, 4.9), (2.1, 4.2)),
        ((5.9, 4.9), (5.9, 4.2)),
        ((9.7, 4.9), (9.7, 4.2)),
        ((2.1, 2.8), (2.1, 2.1)),
        ((5.9, 2.8), (5.9, 2.1)),
        ((9.7, 2.8), (9.7, 2.1)),
        ((3.7, 1.4), (4.3, 1.4)),
        ((7.5, 1.4), (8.1, 1.4)),
        ((7.5, 3.5), (8.1, 3.5)),
        ((4.3, 3.5), (3.7, 3.5)),
    ]
    for (x1, y1), (x2, y2) in arrows:
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=12, linewidth=1.3, color="#93c5fd"))

    ax.set_title("System Architecture (Current Implementation)", color="#e2e8f0", fontsize=14, pad=12)
    fig.patch.set_facecolor("#020617")
    ax.set_facecolor("#020617")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def draw_pipeline(path: Path) -> None:
    stages = [
        "Request\nValidation",
        "Candidate\nGeneration",
        "Objective\nScoring",
        "Pareto\nRanking",
        "Core\nSolver",
        "Coverage\nBoost",
        "Uncertainty\n+ CI",
        "Safety\nContract",
        "Result\n+ Replay",
    ]
    fig, ax = plt.subplots(figsize=(14, 3.8), dpi=180)
    ax.axis("off")
    fig.patch.set_facecolor("#020617")
    ax.set_facecolor("#020617")

    x = 0.4
    y = 1.3
    w = 1.35
    h = 1.15
    gap = 0.3
    colors = ["#22d3ee", "#60a5fa", "#38bdf8", "#4ade80", "#f59e0b", "#fbbf24", "#a78bfa", "#f472b6", "#34d399"]
    for i, st in enumerate(stages):
        rect = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.03,rounding_size=0.08", linewidth=1.4, edgecolor=colors[i], facecolor="#0b1a2b")
        ax.add_patch(rect)
        ax.text(x + w / 2, y + h / 2, st, ha="center", va="center", fontsize=9, color="#e2e8f0")
        if i < len(stages) - 1:
            ax.add_patch(FancyArrowPatch((x + w, y + h / 2), (x + w + gap - 0.05, y + h / 2), arrowstyle="-|>", mutation_scale=10, color="#93c5fd", linewidth=1.2))
        x += w + gap

    ax.set_xlim(0, x + 0.2)
    ax.set_ylim(0.6, 3.0)
    ax.set_title("Probe Planning Execution Pipeline", color="#e2e8f0", fontsize=13, pad=8)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def draw_drcc_profiles(path: Path) -> None:
    profiles = ["balanced", "coverage_first", "safety_first", "angle_first"]
    cov = [0.50, 0.72, 0.25, 0.30]
    safety = [0.30, 0.18, 0.60, 0.25]
    angle = [0.20, 0.10, 0.15, 0.45]
    drcc = [0.75, 0.45, 1.00, 0.55]

    x = range(len(profiles))
    width = 0.2

    fig, ax = plt.subplots(figsize=(9.4, 4.8), dpi=180)
    fig.patch.set_facecolor("white")
    ax.bar([i - 1.5 * width for i in x], cov, width, label="coverage", color="#22d3ee")
    ax.bar([i - 0.5 * width for i in x], safety, width, label="safety", color="#34d399")
    ax.bar([i + 0.5 * width for i in x], angle, width, label="angle", color="#f59e0b")
    ax.bar([i + 1.5 * width for i in x], drcc, width, label="drcc", color="#a78bfa")

    ax.set_xticks(list(x), profiles)
    ax.set_ylim(0, 1.1)
    ax.set_ylabel("Weight")
    ax.set_title("Preference Profiles Used by DRCC Planner")
    ax.grid(axis="y", linestyle="--", alpha=0.25)
    ax.legend(ncol=4, frameon=False)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def draw_safety_loop(path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10.5, 4.8), dpi=180)
    fig.patch.set_facecolor("white")

    depths = [i * 0.25 for i in range(60)]
    threshold = 0.35
    distances = [0.62 - 0.32 * math.exp(-((d - 6.5) ** 2) / 2.8) - 0.12 * math.exp(-((d - 10.2) ** 2) / 0.9) for d in depths]

    ax.plot(depths, distances, color="#0ea5e9", linewidth=2.2, label="continuous min-distance profile")
    ax.axhline(threshold, color="#ef4444", linestyle="--", linewidth=1.6, label="threshold")

    below = [d < threshold for d in distances]
    for i in range(len(depths) - 1):
        if below[i]:
            ax.fill_between([depths[i], depths[i + 1]], [distances[i], distances[i + 1]], threshold, color="#fca5a5", alpha=0.4)

    ax.text(8.7, 0.13, "failure segment -> failure card", color="#b91c1c", fontsize=9)
    ax.set_xlabel("Depth along trajectory (mm)")
    ax.set_ylabel("Distance to risk structure (mm)")
    ax.set_title("Continuous Risk-Distance Contract (Trajectory-Wise)")
    ax.set_ylim(0.0, 0.8)
    ax.grid(alpha=0.2)
    ax.legend(frameon=False)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def draw_reproducibility(path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10.5, 4.6), dpi=180)
    ax.axis("off")
    fig.patch.set_facecolor("#020617")
    ax.set_facecolor("#020617")

    items = [
        "Atlas signature lock",
        "Param template freeze",
        "Deterministic seed + sort",
        "Request/response hash",
        "Golden replay CI gate",
        "Benchmark figure export",
    ]
    y = 3.7
    for i, txt in enumerate(items, start=1):
        x = 0.6 + (i - 1) * 1.55
        rect = FancyBboxPatch((x, y), 1.35, 0.72, boxstyle="round,pad=0.03,rounding_size=0.08", linewidth=1.2, edgecolor="#22d3ee", facecolor="#0b1a2b")
        ax.add_patch(rect)
        ax.text(x + 0.675, y + 0.36, f"{i}. {txt}", ha="center", va="center", color="#e2e8f0", fontsize=8)
        if i < len(items):
            ax.add_patch(FancyArrowPatch((x + 1.35, y + 0.36), (x + 1.52, y + 0.36), arrowstyle="-|>", mutation_scale=10, linewidth=1.2, color="#93c5fd"))

    ax.text(0.6, 2.45, "Goal: same input + same version + same atlas -> same ranked output", color="#cbd5e1", fontsize=10)
    ax.text(0.6, 1.95, "Evidence: benchmark distributions + reliability curves + failure mode Sankey", color="#cbd5e1", fontsize=10)
    ax.set_xlim(0.3, 10.4)
    ax.set_ylim(1.6, 4.7)
    ax.set_title("Reproducibility and Paper Evidence Chain", color="#e2e8f0", fontsize=13, pad=8)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def add_caption(doc: Document, text: str) -> None:
    p = doc.add_paragraph(text)
    p.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    for run in p.runs:
        run.font.size = Pt(10)


def add_bullets(doc: Document, items: list[str]) -> None:
    for t in items:
        doc.add_paragraph(t, style="List Bullet")


def build_doc(output_path: Path, figures: dict[str, Path]) -> None:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    style.font.size = Pt(11)

    title = doc.add_heading("脑机接口探针规划系统导师汇报（功能全景与核心原理）", 0)
    title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    doc.add_paragraph(f"项目：Probe Planner 2.0（鼠脑、automatic_decision）\n日期：{TODAY}\n作者：自动化技术汇总文档")

    doc.add_heading("一、汇报目标与范围", level=1)
    doc.add_paragraph(
        "本报告面向导师汇报，目标是把当前软件“能做什么、怎么做、为什么这样做”讲清楚，重点展开核心功能“探针轨迹规划”"
        "的实现路径与算法原理，并给出工程可复现、临床可行与论文产出的证据链。"
    )
    add_bullets(doc, [
        "范围锁定：鼠脑（mouse）场景，不改物种域。",
        "系统定位：自动决策（automatic_decision），非仅推荐模式。",
        "内容层次：功能模块 -> 数据流 -> 优化算法 -> 安全闭环 -> 统计可信 -> 复现与论文产出。",
    ])

    doc.add_heading("二、系统总体架构", level=1)
    doc.add_paragraph("系统由 React GUI、Flask 优化后端、Unity 3D 渲染与 Benchmark 证据层组成。")
    doc.add_picture(str(figures["arch"]), width=Inches(6.8))
    add_caption(doc, "图1 系统总体架构（前端-后端-Unity-复现证据链）")

    doc.add_heading("三、软件功能全景（按用户工作流）", level=1)
    doc.add_heading("3.1 目标与脑区管理", level=2)
    add_bullets(doc, [
        "Regions 面板：支持脑区选择、仅看未覆盖、显示全部脑区、侧别过滤。",
        "支持后端可用脑区集校验，避免请求发出后才报“atlas/backend 不支持”错误。",
        "支持单侧与双侧对称规划语义（targetingMode）。",
    ])

    doc.add_heading("3.2 参数与约束管理", level=2)
    add_bullets(doc, [
        "算法可切换：greedy / exact / milp-lp松弛接口 / drcc（第四算法）。",
        "关键参数：角度、深度、覆盖半径、clearance、entry spacing、risk threshold。",
        "约束策略三级：off / warn / enforce，支持安全契约阻断。",
        "brainBounds 与 hemisphere 已纳入后端统一校验与阻断逻辑。",
    ])

    doc.add_heading("3.3 三维可视化与交互", level=2)
    add_bullets(doc, [
        "Unity 原生拾取：点击 3D 脑区直接触发 regionClicked，替代前端投影猜测。",
        "相机姿态回传：cameraPose(theta/phi/distance)，锚点与模型同步。",
        "稳态通信：stateVersion + ACK + heartbeat + 超时重同步 + 全量 snapshot。",
        "支持全屏、预设视角（等轴/顶视/侧视/前视）、半球过滤、仅显示已选。",
    ])

    doc.add_heading("3.4 结果解释与临床可读输出", level=2)
    add_bullets(doc, [
        "每条探针输出覆盖脑区、侧别覆盖、连续最小安全距离。",
        "输出统计可信指标：minDistance CI95、P(safety>threshold)、概率CI95。",
        "失败原因卡片：危险区间(mm)、命中结构类别、阈值、可替代候选及最小改变量。",
        "Pareto 可视化 + 偏好模板切换（偏安全/偏覆盖/平衡）。",
    ])

    doc.add_heading("3.5 Benchmark 自动产出", level=2)
    add_bullets(doc, [
        "支持初始化、路径配置、运行选项、文件回看。",
        "自动导出 coverage/safety/pass-rate 分布、failure mode、Sankey 边表与图。",
        "支持 reliability、external validation、paper package、golden replay、version signature。",
    ])

    screenshot = ROOT / "屏幕截图 2026-03-08 135725.png"
    if screenshot.exists():
        doc.add_paragraph("当前系统界面示意如下：")
        doc.add_picture(str(screenshot), width=Inches(6.8))
        add_caption(doc, "图2 系统界面示意（3D脑区与多探针渲染）")

    doc.add_heading("四、核心功能：探针规划实现路径", level=1)
    doc.add_paragraph(
        "核心执行入口为后端优化流水线，处理路径是“输入契约校验 -> 候选生成 -> 多目标评分 -> Pareto 排名 -> 求解器选择 -> 安全契约阻断 -> 结果解释输出”。"
    )
    doc.add_picture(str(figures["pipeline"]), width=Inches(6.8))
    add_caption(doc, "图3 探针规划核心执行流水线")

    doc.add_heading("4.1 输入与契约层（Contract）", level=2)
    add_bullets(doc, [
        "请求体 schema 校验：targetKeys/params/probeParams/constraintModes/correction 等字段。",
        "参数归一化与边界约束：算法枚举、阈值范围、不确定性参数合法性。",
        "临床契约锁定：intended_use=automatic_decision，species_scope=mouse。",
    ])

    doc.add_heading("4.2 候选轨迹生成层（Candidate Generation）", level=2)
    add_bullets(doc, [
        "体素网格采样：围绕目标脑区入口进行网格化候选。",
        "PCA 引导候选：沿目标解剖主轴生成倾角方案。",
        "多目标联通候选：为多脑区覆盖构建 pairwise 轨迹。",
        "深度有序体素去重：修复 entry/exit depth 失真问题。",
    ])

    doc.add_heading("4.3 评分层（Objective + Robustness）", level=2)
    doc.add_paragraph(
        "每个候选会计算 coverage/safety/angle/robust 等目标，并缓存 objective 结果。"
        "安全项来自连续风险距离评估（优先 SDF，降级 KDTree）。"
    )
    add_bullets(doc, [
        "Coverage：覆盖目标数 + overlap voxel 质量。",
        "Safety：边界裕度 + 连续风险短缺惩罚（integral + tail/CVaR）。",
        "Angle：入射角代价。",
        "DRCC 扩展：最坏违约概率 worstCaseViolationProb。",
    ])

    doc.add_heading("4.4 Pareto 与求解层", level=2)
    doc.add_paragraph(
        "在候选池上先做 Pareto 前沿分层，再进入求解器。"
        "当前系统提供四种方法："
    )
    add_bullets(doc, [
        "Greedy：集合覆盖启发式，速度快。",
        "Exact：候选池上 B&B 精确求解（含冲突约束，超时回退 greedy）。",
        "MILP/LP 松弛接口：前端命名保留，后端兼容 milpStatus 字段并走精确求解路径。",
        "DRCC：分布鲁棒机会约束 + 偏好感知多目标 + exact_pool/hybrid。",
    ])

    doc.add_heading("4.5 DRCC 第四算法原理（重点）", level=2)
    doc.add_paragraph(
        "目标是把“安全”从经验阈值提升为可解释概率约束。核心形式：\n"
        "sup_{Q in B_W(P_hat, rho)} P_Q(d_min < tau) <= epsilon\n"
        "其中 tau 为最小安全距离阈值，epsilon 为风险预算，rho 为 Wasserstein 半径。"
    )
    doc.add_paragraph(
        "工程实现上，系统先估计每条候选的 violationProb，再加 rho 得到 worstCaseViolationProb，"
        "并结合 SDF 全程风险 shortfall（积分项+尾部项）作为代价。"
    )
    doc.add_picture(str(figures["drcc"]), width=Inches(6.5))
    add_caption(doc, "图4 DRCC 偏好模板权重（coverage/safety/angle/drcc）")

    doc.add_heading("4.6 覆盖补偿与侧别规划", level=2)
    add_bullets(doc, [
        "coverageBoost：在主求解后对残余未覆盖区域做二次补覆盖。",
        "targetingMode=single_hemisphere/bilateral_symmetric：支持单侧或双侧对称任务。",
        "sideCoverage 输出用于直接解释左右半球覆盖充分性。",
    ])

    doc.add_heading("4.7 不确定性、校准与选择后可信", level=2)
    add_bullets(doc, [
        "鲁棒采样参数：entrySigma/angleSigma/depthSigma/riskDistanceSigma。",
        "每条探针输出：minSafetyDistanceMm、minSafetyDistanceCi95、P(safety>τ)、概率CI95。",
        "Selective Conformal：输出 accept/review/reject 决策及概率下界。",
        "分层校准：global + 脑区 + 半球 + 医院 + 设备 + 批次（ECE/Brier）。",
    ])

    doc.add_heading("4.8 连续安全闭环与失败卡片", level=2)
    doc.add_paragraph("系统按轨迹连续采样风险距离，并对每条失败轨迹生成结构化解释卡。")
    doc.add_picture(str(figures["safety"]), width=Inches(6.6))
    add_caption(doc, "图5 连续风险距离与失败区段示意（触发 safety contract）")

    add_bullets(doc, [
        "失败卡片包含：危险起止深度、命中结构类别、最小距离、阈值来源。",
        "附带反事实建议：最近可行候选、角度/入点/深度最小改变量。",
        "当约束模式为 enforce 且低于阈值时，后端阻断并返回可解释原因。",
    ])

    doc.add_heading("五、3D 交互机制原理（为什么现在更稳）", level=1)
    add_bullets(doc, [
        "消息机制：SetStateSnapshot + 细粒度 SetXxx 命令并行下发。",
        "一致性机制：每条消息携带 stateVersion，Unity ACK 回传，前端按方法维度确认版本。",
        "活性机制：heartbeat 周期探活；超时触发 resync，推送全量 snapshot。",
        "相机一致性：Unity 回传 cameraPose，前端锚点使用真姿态更新，消除“锚点转模型不转”。",
    ])

    doc.add_heading("六、关键 API 与数据输出（导师可快速查）", level=1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light List Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text = "接口"
    hdr[1].text = "用途"
    hdr[2].text = "关键返回"

    api_rows = [
        ("GET /api/status", "后端与图谱状态", "atlasLoad/progress/clinicalContract"),
        ("POST /api/optimize", "执行规划", "selected/uncovered/failureCards/summary"),
        ("GET /api/task/status/<id>", "实时进度", "stage/progress/status/message"),
        ("POST /api/task/cancel", "取消任务", "cancelled"),
        ("GET /api/benchmark/status", "benchmark 状态", "outDir/files/pipeline"),
        ("POST /api/benchmark/run", "一键证据产出", "createdFiles + pipeline results"),
    ]
    for a, b, c in api_rows:
        row = table.add_row().cells
        row[0].text = a
        row[1].text = b
        row[2].text = c

    doc.add_heading("七、Benchmark 与论文证据链", level=1)
    doc.add_picture(str(figures["repro"]), width=Inches(6.7))
    add_caption(doc, "图6 可复现与论文产出证据链")
    add_bullets(doc, [
        "分布导出：coverage_distribution.csv / safety_distance_distribution.csv / pass_rate_distribution.csv。",
        "失败模式：failure_mode_counts.csv + failure_sankey_edges.csv + 图形输出。",
        "可靠性评估：reliability_curve、ECE、Brier、分层校准明细。",
        "金标准回放：同输入同版本同 atlas，hash 一致性可做 CI 门禁。",
    ])

    doc.add_heading("八、你当前版本的完成度评估（客观）", level=1)
    doc.add_paragraph(
        "从工程实现看，你的软件已经不是“功能演示”级，而是进入“可重复实验 + 可解释自动决策”阶段。"
        "当前优势在于：全链路可追踪、第四算法DRCC、连续安全闭环、3D交互稳态、Benchmark自动化。"
        "短板在于：外部多中心验证样本规模与前瞻性研究证据仍需补齐。"
    )

    doc.add_heading("九、建议你汇报时重点强调的 10 句话", level=1)
    add_bullets(doc, [
        "1) 我们已将系统定位锁定为鼠脑 automatic_decision，而非仅建议工具。",
        "2) 核心规划是候选生成 + Pareto + 求解 + 安全契约阻断的闭环系统。",
        "3) 安全不再是离散点判断，而是沿轨迹连续风险距离评估。",
        "4) 新增 DRCC 把安全约束升级为最坏概率约束，具备统计解释。",
        "5) 每条探针都输出 minDistance、95%CI、P(safety>threshold)。",
        "6) 对失败轨迹给出结构化 failure card，而不是黑箱拒绝。",
        "7) 3D 端实现原生选区与相机姿态同步，显著降低误选和错位。",
        "8) benchmark 支持一键导出图表与统计，直接服务论文主图。",
        "9) 通过 atlas/hash/seed/replay 把可复现性落到工程门禁。",
        "10) 下一步重点是外部验证和前瞻研究，形成 1 区投稿证据闭环。",
    ])

    doc.add_heading("十、附录：代码定位索引", level=1)
    idx_table = doc.add_table(rows=1, cols=2)
    idx_table.style = "Light List Accent 2"
    idx_table.rows[0].cells[0].text = "模块"
    idx_table.rows[0].cells[1].text = "主要文件"
    entries = [
        ("主界面状态编排", "src/ProbeplannerGUI.jsx"),
        ("3D交互与Unity通信", "src/components/Brain3DView.jsx"),
        ("优化请求与动态进度", "src/hooks/useOptimizer.js"),
        ("后端连接/图谱加载", "src/hooks/useBackend.js"),
        ("核心优化后端", "src/optimizer_server.py"),
        ("任务状态与取消注册", "src/backend/api.py"),
        ("契约校验", "src/backend/contract.py"),
        ("风险距离模型辅助", "src/backend/risk_model.py"),
        ("Benchmark总控", "scripts/benchmark/run_benchmark.py"),
        ("可靠性评估", "scripts/benchmark/evaluate_reliability.py"),
        ("论文图包导出", "scripts/benchmark/export_paper_package.py"),
    ]
    for k, v in entries:
        row = idx_table.add_row().cells
        row[0].text = k
        row[1].text = v

    doc.add_paragraph("\n说明：本报告为基于当前代码库自动生成的技术汇报文档，可直接作为导师汇报底稿。")
    doc.save(output_path)


def main() -> None:
    figs = {
        "arch": ASSET_DIR / "fig_architecture.png",
        "pipeline": ASSET_DIR / "fig_pipeline.png",
        "drcc": ASSET_DIR / "fig_drcc_profiles.png",
        "safety": ASSET_DIR / "fig_safety_loop.png",
        "repro": ASSET_DIR / "fig_repro_chain.png",
    }
    draw_architecture(figs["arch"])
    draw_pipeline(figs["pipeline"])
    draw_drcc_profiles(figs["drcc"])
    draw_safety_loop(figs["safety"])
    draw_reproducibility(figs["repro"])

    output = OUT_DIR / f"mentor_report_probe_planner_{TODAY}.docx"
    build_doc(output, figs)

    manifest = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "docx": str(output),
        "assets": {k: str(v) for k, v in figs.items()},
    }
    (OUT_DIR / "mentor_report_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output))


if __name__ == "__main__":
    main()
