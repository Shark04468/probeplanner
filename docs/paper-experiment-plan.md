# ProbePlanner Native 投稿前实验策划

目标期刊：Frontiers in Neuroinformatics
建议文章类型：Technology and Code
版本：v0.1 draft
核心原则：没有动物实验和术后组织学验证，因此论文只主张软件系统、计算正确性、约束规划和可复现性能，不主张真实植入命中率。

## 1. 论文定位

### 1.1 推荐主线

ProbePlanner Native 是一个面向 mouse brain atlas / MRI-derived artifact 的快速、可复现、约束感知 multi-shank probe trajectory planning 软件系统。系统将原 Python 计算热点迁移到 Rust native engine，并通过固定 replay benchmark、SDF 风险层 adversarial cases、MRI registration QC 和 3D performance gate 验证软件可用性。

### 1.2 可以诚实主张的内容

- 系统架构：React UI + Flask compatibility layer + Rust planner-engine + mmap atlas/SDF + bitset solver。
- 性能：native engine 相比 legacy Python backend 显著降低后端求解耗时。
- 约束规划：支持 atlas-backed target coverage、entry spacing、clearance、brain bounds、continuous SDF risk distance。
- 多 shank：支持 multi-shank footprint、selected payload 和 conflict checking。
- 可复现性：固定 replay cases、固定 seed、版本化 reports、CI/self-hosted gate。
- MRI workflow：在示例 MRI/atlas profile 上给出 registration QC、artifact contract 和 replay validation。

### 1.3 不能主张的内容

- 不能主张真实动物植入命中率。
- 不能主张术后组织学验证过的 target accuracy。
- 不能主张比人工规划或其他工具在真实实验中更准确。
- 不能主张 skull/brain safety 经过手术验证。
- 不能主张 native DRCC 完整等价 legacy MISOCP/CVX。
- 不能把单个 MRI profile 的 QC 写成跨动物泛化结论。

## 2. 投稿前必须补齐的实验

### E0. 代码、数据和运行环境冻结

目的：保证后续所有结果可复现。

需要冻结：

- Git commit hash。
- Rust / Node / Python 版本。
- OS 和硬件信息。
- `PROBEPLANNER_ARTIFACT_ROOT` artifact manifest。
- replay case manifest。
- baseline summary。
- 所有 benchmark command。

输出：

- `docs/evidence/paper-evidence-v1.md`
- `docs/evidence/paper-evidence-v1-summary.json`
- `reports/paper-evidence-v1/`

通过标准：

- 所有报告能从一个 README 中重跑。
- 所有 case 和 artifact 路径不依赖个人绝对路径。
- GitHub/self-hosted CI 至少跑通一次，并保留 artifact。

## 3. 主要实验设计

### E1. Fixed Replay Benchmark

目的：证明 native engine 在固定真实规划请求上稳定、快速、可审计。

当前状态：

- 已有 10-case replay gate。
- 最新结果：backend p95 约 586 ms，scoring p95 83 ms，solver p95 3 ms，0/30 failures。
- 问题：10 case 太少，且部分复杂 case 仍有 atlas-backed uncovered。

计划：

- 扩展到 50 case，理想 100 case。
- 每个 case 固定 seed。
- 分层采样：
  - single target：10-20 case。
  - 2-3 targets：10-20 case。
  - 4-6 targets：10-20 case。
  - hippocampus / thalamus / cortex / basal ganglia / mixed-depth：每类至少 5 case。
  - 4-shank：至少 20 case。
  - known infeasible：至少 10 case。

记录指标：

- candidate count。
- selected count。
- target count。
- uncovered count。
- atlas-backed uncovered count。
- excluded non-atlas targets。
- generation time。
- scoring time。
- solver time。
- backend total time。
- min skull / brain_surface / ventricle / vessel / sulcus distance。
- entry spacing。
- clearance。
- failure / no-solution reason。

通过标准：

- Backend p95 <= 1000 ms。
- Scoring p95 <= 250 ms。
- Solver p95 <= 50 ms。
- Backend failures = 0 for feasible cases。
- Hard constraint violation = 0。
- Unknown / non-atlas targets must be excluded and clearly reported, not silently planned.
- Feasible atlas-backed single-target cases should have atlas-backed uncovered = 0。
- Multi-target partial coverage must be reported as partial coverage, not success.

可支撑 claim：

- Native engine provides interactive backend latency for fixed planning requests.
- The benchmark reports target coverage and constraint failures explicitly.

不能支撑 claim：

- Real surgical targeting accuracy。

### E2. Synthetic Analytic Phantom Correctness

目的：在没有动物实验的情况下，给出计算正确性验证。

设计：

- 构造小型 synthetic atlas volume。
- 几何体：
  - spherical brain mask。
  - cuboid / spherical target regions。
  - spherical ventricle。
  - cylindrical vessel。
  - planar skull / brain surface。
- 构造已知 entry / tip / trajectory。
- 对每条轨迹预先计算 expected outcome。

测试内容：

- trajectory voxel sampling。
- region coverage。
- SDF trilinear sampling。
- entry spacing。
- capsule clearance。
- multi-shank footprint。
- conflict matrix。
- greedy / exact solver expected optimum。

建议规模：

- 100 synthetic cases minimum。
- 500 synthetic cases preferred。

指标：

- voxel sampling exact match rate。
- SDF value absolute error。
- coverage exact match rate。
- conflict matrix precision / recall。
- solver optimality gap for small exact-solvable cases。

通过标准：

- Synthetic expected pass/fail classification accuracy = 100% for deterministic cases。
- SDF sampling max error <= one voxel interpolation tolerance。
- Conflict matrix false negative = 0。
- For small exact-solvable cases, exact solver matches known optimum。

可支撑 claim：

- Core computational kernels behave as expected on analytically controlled geometry.

不能支撑 claim：

- Biological targeting accuracy。

### E3. SDF Boundary Adversarial Validation

目的：证明 continuous risk distance 的边界行为稳定，尤其是 skull、brain surface、ventricle、vessel、sulcus。

当前状态：

- 已有 SDF adversarial reject/pass-control gate。
- 最新 10 class-paired cases PASS。
- 问题：还需要更密集的 near-threshold geometry cases。

计划：

- 每个 risk class 构造：
  - epsilon-inside。
  - on-boundary。
  - epsilon-outside。
  - grazing trajectory。
  - crossing trajectory。
  - legal entry corridor。
- epsilon：0.025 mm、0.05 mm、0.10 mm、0.20 mm。
- 每类至少 20 cases，5 类共至少 100 cases。

指标：

- unsafe false accept。
- safe false reject。
- minDistanceMm。
- rejection reason。
- selected count。
- noCoverage reason。

通过标准：

- continuousRiskDistance=enforce 时 unsafe false accept = 0。
- Pass-control cases 必须至少产生一个 selectable plan，除非被其他硬约束合理拒绝。
- risk layer diagnostics 必须包含被测 class。

可支撑 claim：

- The engine fails closed on specified SDF risk-layer violations.

不能支撑 claim：

- Surgical safety has been validated in vivo。

### E4. Randomized Feasibility Stress Test

目的：证明系统在更宽 target 组合下稳定，不只对少量固定 replay 有效。

计划：

- 从 atlas-backed region pool 中随机抽取 target sets。
- 分组：
  - 1 target：200 cases。
  - 2-3 targets：200 cases。
  - 4-6 targets：200 cases。
  - deep-only：100 cases。
  - mixed cortical/deep：100 cases。
  - multi-shank constrained：200 cases。
- 总计 500-1000 cases。
- 固定 seed 并保存 generated manifest。

记录：

- feasible success。
- partial coverage。
- no solution。
- noCoverage reason distribution。
- risk rejection distribution。
- runtime distribution。

通过标准：

- Process crash = 0。
- Invalid JSON / schema violation = 0。
- Hard constraint violation = 0。
- No-solution cases must report explainable failure reason。
- Runtime p95 <= 1500 ms for stress suite，或按 case complexity 分层报告。

可支撑 claim：

- The planner remains stable under randomized atlas-backed target combinations.

不能支撑 claim：

- All real user planning requests will be feasible。

### E5. Legacy Python vs Native Rust Comparison

目的：证明架构升级的性能收益，并检查结果语义差异。

计划：

- 只比较 legacy Python 能成功跑完的 subset。
- 至少 30 shared cases。
- 每个 case 运行 3 次。
- 同一 artifact、同一 seed、同一 request。

指标：

- backend total time。
- generation time。
- scoring time。
- solver time。
- selected count。
- uncovered count。
- atlas-backed uncovered。
- hard constraint violation。

统计：

- median speedup。
- p95 speedup。
- paired ratio per case。
- Wilcoxon signed-rank test 可选。
- effect size 可选。

通过标准：

- Native p95 runtime 显著低于 legacy。
- Coverage 不应在 shared feasible cases 上系统性变差。
- 若语义不等价，必须标记原因。

注意：

- MISOCP/CVX 不进入 native equivalence claim。
- Native MISOCP fail-closed 是边界，不是缺陷掩盖。

可支撑 claim：

- Native implementation improves backend performance while preserving audited coverage semantics for supported solver modes.

### E6. Solver and Constraint Ablation

目的：说明每个主要设计选择的作用。

实验组：

- risk constraints off / warn / enforce。
- greedy_bitset。
- exact_pool。
- hybrid。
- heuristic。
- cvar_relax。
- single-shank vs 4-shank。
- entry spacing on/off。
- clearance on/off。

指标：

- coverage。
- risk violation count。
- selected count。
- runtime。
- no-solution rate。

通过标准：

- enforce 模式必须消除 hard risk violation。
- constraint off 可以作为对照，但不能用于主系统 claim。
- solver mode 必须在报告中显示 requested/effective。

可支撑 claim：

- Safety constraints trade off coverage and feasibility, and the system reports this tradeoff explicitly.

### E7. MRI / Atlas Artifact Workflow Validation

目的：证明系统可以接入 MRI-derived artifact，但不夸大为跨动物 MRI validation。

当前状态：

- `ambmc_true_outer_full` profile 最新 QC：
  - Dice = 0.841848。
  - HD95 = 0.365718 mm。
  - TRE = 0.267084 mm。
  - Jacobian anomaly ratio = 0。
  - Gate = PASS。

计划：

- 固定默认 acceptance profile：`ambmc_true_outer_full`。
- 输出 artifact manifest。
- 输出 QC endpoint JSON。
- 保存 registration overlay screenshots。
- 跑 native MRI-profile replay 20-50 cases。
- 若可能，补 3-5 个公开或内部 MRI profiles；如果没有，不强行扩展结论。

通过标准：

- artifactReady = true。
- missingArtifacts = []。
- Dice >= 0.8。
- HD95 <= 0.45 mm。
- TRE <= 0.3 mm。
- Jacobian anomaly ratio <= 0.03。

可支撑 claim：

- The software supports an MRI/atlas artifact workflow and exposes registration QC.

不能支撑 claim：

- MRI registration generalizes across subjects。

### E8. 3D Visualization and UI Performance Gate

目的：证明系统交互体验稳定，不把 3D 性能和后端性能混在一起。

当前状态：

- 最新 3D gate：
  - FPS median = 238.1。
  - frame p95 = 4.4 ms。
  - interactive median = 9.6 s。
  - total load median = 19.7 s。

计划：

- Production build。
- Headed browser 3-run minimum。
- 记录：
  - interactive ready。
  - total load。
  - FPS。
  - frame p95。
  - Unity resource size。
  - JS heap。
  - WASM heap。
- 截图保存。

通过标准：

- 3D failures = 0。
- FPS median >= 45。
- frame p95 <= 45 ms。
- interactive load <= 30 s。
- UI probe trajectory payload must match native selected entry/tip when applicable。

可支撑 claim：

- The user interface supports interactive 3D inspection under the tested hardware/software environment.

### E9. Feature Comparison With Existing Tools

目的：明确贡献位置，避免不公平准确度比较。

比较对象建议：

- Pinpoint。
- HERBS。
- Neuropixels Trajectory Explorer / related atlas tools。
- BrainGlobe ecosystem。
- Legacy ProbePlanner Python backend。

比较维度：

- atlas visualization。
- trajectory planning。
- automated candidate generation。
- SDF/risk hard constraints。
- multi-shank geometry。
- batch replay benchmark。
- local native engine。
- MRI artifact workflow。
- open-source reproducibility。

注意：

- 只做 feature / workflow comparison。
- 不写真实命中率比较。
- 不写 superiority claim，除非有同一 benchmark 的定量证据。

## 4. 统计和报告策略

### 4.1 主要 endpoint

- Backend total p95。
- Solver p95。
- Scoring p95。
- Feasible case success rate。
- Hard constraint violation rate。
- Atlas-backed uncovered distribution。
- Unsafe false accept rate in SDF adversarial cases。

### 4.2 次要 endpoint

- Candidate count。
- Selected count。
- Multi-shank selected payload validity。
- UI 3D FPS。
- MRI QC metrics。
- No-solution reason distribution。

### 4.3 统计呈现

- 对 runtime 用 median、IQR、p95。
- 对 pass/fail 用 count 和 percentage。
- 对 legacy/native paired comparison 用 paired ratio。
- 不做过度 p-value 叙事，软件论文更重要的是 effect size 和 reproducibility。

## 5. Figure 和 Table 计划

### Figure 1. System architecture

内容：

- React UI。
- Flask compatibility layer。
- Rust planner-engine。
- mmap atlas。
- layered SDF。
- region index。
- candidate/scoring/solver kernels。

### Figure 2. Planning workflow example

内容：

- target selection。
- candidate trajectory。
- selected multi-shank probe。
- risk diagnostics。
- 3D visualization。

### Figure 3. Runtime benchmark

内容：

- Legacy Python vs native Rust。
- Backend total / generation / scoring / solver。
- Box plot or violin plot。

### Figure 4. SDF boundary validation

内容：

- skull、brain surface、ventricle、vessel、sulcus。
- reject/pass-control。
- minDistance vs threshold。

### Figure 5. Stress test outcome

内容：

- success。
- partial coverage。
- no solution。
- no-solution reasons。

### Table 1. Feature comparison

内容：

- ProbePlanner Native vs related tools。

### Table 2. Replay benchmark summary

内容：

- 50/100 cases by group。

### Table 3. MRI artifact QC

内容：

- Dice、HD95、TRE、Jacobian anomaly。

## 6. 论文结论模板

推荐结论写法：

> ProbePlanner Native demonstrates that a local native-engine architecture can support interactive, reproducible, constraint-aware probe trajectory planning in atlas/MRI-derived coordinate spaces. Across fixed replay, synthetic correctness, SDF adversarial, and stress-test benchmarks, the system reports coverage, infeasibility, and hard-constraint violations explicitly while substantially reducing backend latency relative to the legacy Python implementation.

必须加的限制：

> This study does not include in vivo implantations or post hoc histological reconstruction. Therefore, the reported validation concerns computational correctness, software performance, and constraint satisfaction, not biological targeting accuracy or surgical outcome.

## 7. 建议执行顺序

### Phase 1. 最小可投证据

- E0 freeze。
- E1 replay50。
- E2 synthetic100。
- E3 SDF boundary100。
- E5 legacy/native comparison。
- E7 one MRI QC profile。
- E8 3D gate。

完成后可以准备初稿。

### Phase 2. 更稳投稿证据

- replay100。
- synthetic500。
- stress500-1000。
- solver ablation。
- 3-5 MRI/public profiles，如数据可得。
- self-hosted GitHub Actions artifact。

完成后投稿把握更高。

## 8. 当前项目已有证据和缺口

### 已有

- 10-case native backend gate PASS。
- SDF adversarial gate PASS。
- 3D gate PASS。
- MRI QC profile PASS。
- Rust/backend/frontend tests PASS。
- Native engine 已替代主要计算核。

### 缺口

- replay 数量不足。
- 缺 synthetic analytic phantom。
- 缺大规模 stress test。
- 缺完整 ablation。
- 缺真实 GitHub/self-hosted CI 通过记录。
- 缺公开 release artifact / Zenodo DOI。
- 缺 manuscript-ready figures。
- 缺 public tool comparison table。

## 9. 最终投稿 gate

投稿前必须全部满足：

- `paper-evidence-v1` report Overall PASS。
- replay >= 50，最好 >= 100。
- synthetic correctness cases >= 100。
- SDF boundary adversarial cases >= 100。
- hard constraint violation = 0。
- feasible backend failures = 0。
- native p95 backend <= 1 s。
- MRI QC default profile PASS。
- 3D gate PASS。
- GitHub repo 可公开。
- 数据和 artifact 有下载说明。
- limitations 明确写无动物实验、无 histology validation。

若上述条件不满足，应推迟投稿或降低论文 claim。
