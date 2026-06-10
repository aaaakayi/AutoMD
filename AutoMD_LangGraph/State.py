# 记录图中的状态
from typing import TypedDict

# 项目中的图状态
class AutoMDState(TypedDict, total=False):
    # 项目相关
    project_id : str
    run_id: str                      # session 隔离 ID, 用于 output/ 子目录
    raw_task : str                  # 原始需求
    plan : str                      # 规划结果
    normalized_task : str           # 规范化后的任务
    route : str                     # 路由选择
    route_reason : str              # 路由理由

    # 需求相关
    need_protein : bool             # 是否需要蛋白处理
    need_ligand : bool              # 是否需要配体处理
    need_docking : bool             # 是否需要对接
    need_md : bool                  # 是否需要MD模拟
    need_analysis : bool            # 是否需要分析

    # 环境相关
    package_needs : list[str]       # 依赖包列表
    calling_node : str              # 上一个调用的节点
    env_setup_attempts : dict       # set_env 熔断计数：{ "pkg1,pkg2": int }，同一组包连续失败次数
    env_setup_status : str          # "success" | "failed" | ""（最后一次 set_env 结果）

    # 错误与兜底
    last_error : str                # 最近一次工具错误文本
    last_failed_node : str          # 最近失败节点名
    last_error_analysis : str       # classify_tool_error 初步分析
    fallback_count : int            # fallback_agent 尝试次数
    fallback_result : str           # fallback_agent 结果
    _fallback_attempts : list[str]  # 已尝试的修复方案描述（内部）
    _fallback_messages : list[dict]  # 序列化的 LangChain messages（内部）
    _fallback_phase : str            # fallback 阶段: "diagnose" | "confirm"（内部）
    _fallback_action_raw : str       # 序列化的 FallbackAction（内部）
    _fallback_history : list[dict]   # 每次尝试的完整记录（技术报告+脚本+全量输出+缺失文件）
    _original_error : str            # 首次触发 fallback 时的原始错误（retry 不变）
    _last_tech_report : str          # 最新诊断技术报告（跨 retry 持久化）
    _tmux_session : str              # tmux 会话名（如 "fb_md_run"）
    _waiting_next_node : str         # 等待完成后跳转的目标节点
    _waiting_final_outputs : list[str]  # 后台任务完成后期望的最终文件列表
    _waiting_state_updates : dict    # 等待成功后的 state 更新

    # 集群提交（集群配置从 .env 读取）
    submit_to_cluster : bool        # 是否提交到集群（否则本地运行）
    submit_input_files : list[str]  # 要上传的文件路径列表
    submit_result : str             # 提交结果描述
    submit_job_id : str             # 集群作业 ID

    # 蛋白质相关
    protein_pdb_id : str            # pdb id
    protein_raw_pdb : str           # 下载得到的原始PDB
    protein_clean_pdb : str         # 清洗后的PDB
    protein_filtered_pdb : str      # 过滤后的PDB
    protein_receptor_pdbqt : str    # 受体PDBQT
    protein_prmtop : str            # 蛋白MD拓扑
    protein_inpcrd : str            # 蛋白MD坐标
    protein_result : str            # 结果文件路径
    protein_is_success : bool       # 是否成功
    protein_summary : str           # 蛋白节点摘要

    # 配体相关
    ligand_smiles : str             # 配体的SMILES字符串
    ligand_input_file : str         # 配体输入文件
    ligand_mol2 : str               # 配体mol2
    ligand_frcmod : str             # 配体frcmod
    ligand_prmtop : str             # 配体prmtop
    ligand_inpcrd : str             # 配体inpcrd
    ligand_pdbqt : str              # 配体pdbqt
    ligand_result : str             # 结果文件路径
    ligand_is_success : bool        # 是否成功
    ligand_summary : str            # 配体节点摘要

    # 对接相关
    docking_mode: str                # 对接模式: "blind"(默认P2Rank) | "visual_box"(PyMOL可视)
    docking_box : dict              # 对接盒参数
    docked_ligand_pdb : str         # 对接后配体最佳构象 PDB 路径
    docked_ligand_pdbqt : str       # 对接后配体 PDBQT 路径
    docking_interactions : dict     # 相互作用分析
    docking_result : str            # 结果文件路径
    docking_is_success : bool       # 是否成功
    docking_summary : str           # 对接节点摘要
    docking_exhaustiveness: int     # Vina 精度，默认 8
    docking_num_modes: int          # 生成对接模式数，默认 9
    docking_energy_range: float     # 能量范围(kcal/mol)，默认 3.0

    # 复合物准备
    complex_is_success : bool       # 复合物准备是否完成

    # MD相关
    md_prmtop : str                 # MD拓扑
    md_inpcrd : str                 # MD坐标
    md_duration_ns : float           # MD 模拟时长（ns），默认10.0
    md_temperature_k: float          # 温度(K)，默认 300.0
    md_ensemble: str                 # 系综: "npt" | "nvt" | "nve"，默认 "npt"
    md_solvent: str                  # 溶剂模型: "explicit" | "tip3p" | "opc"，默认 "explicit"
    md_pressure_atm: float           # 压强(atm)，默认 1.0
    md_timestep_fs: float            # 积分步长(fs)，默认 2.0
    md_nvt_equil_ps: float           # NVT 平衡时长(ps)，默认 100.0
    md_npt_equil_ps: float           # NPT 平衡时长(ps)，默认 100.0
    md_save_interval_ps: float       # 轨迹保存间隔(ps)，默认 100.0
    md_force_field: str              # 蛋白力场，默认 "ff14SB"
    md_water_model: str              # 水模型: "tip3p" | "opc" | "tip4pew" | "tip4p"，默认 "tip3p"
    md_ligand_ff: str                # 配体力场: "gaff" | "gaff2"，默认 "gaff2"
    md_trajectory : str             # MD轨迹
    md_result : str                 # 结果文件路径
    md_is_success : bool            # 是否成功
    md_summary : str                # MD节点摘要

    # 轨迹分析
    analysis_result : str           # 结果文件路径
    analysis_is_success : bool      # 是否成功
    analysis_summary : str          # 分析节点摘要
    analysis_mmpbsa: bool           # 是否尝试 MMPBSA 结合自由能，默认 False
    degradation_instructions: str   # 用户指定的降级/容错策略（自然语言）

    # 提取确认（内部）
    _extraction_feedback: str       # 用户对参数提取的修改意见，空表示无反馈

    # 报告
    final_report : str              # 最终报告