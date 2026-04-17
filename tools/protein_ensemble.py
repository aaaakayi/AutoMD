from typing import List
import requests
from rcsbapi.search import SeqSimilarityQuery
from tools.protein import fetch_pdb

RCSB_DATA_API_BASE = "https://data.rcsb.org/rest/v1/core"

def _is_pdb_id(identifier: str) -> bool:
    """判断是否为 PDB ID（4 字符，首字符数字）"""
    return len(identifier) == 4 and identifier[0].isdigit() and identifier.isalnum()

def _fetch_uniprot_from_pdb(pdb_id: str, timeout: float = 15.0) -> str:
    """通过 RCSB Data API 将 PDB ID 解析为 UniProt ID"""
    entry_url = f"{RCSB_DATA_API_BASE}/entry/{pdb_id}"
    entry_resp = requests.get(entry_url, timeout=timeout)
    entry_resp.raise_for_status()
    entry_data = entry_resp.json()

    entity_ids = (
        entry_data.get("rcsb_entry_container_identifiers", {})
        .get("polymer_entity_ids", [])
    )
    if not entity_ids:
        raise ValueError(f"PDB {pdb_id} 不包含任何 polymer entity")

    for entity_id in entity_ids:
        entity_url = f"{RCSB_DATA_API_BASE}/polymer_entity/{pdb_id}/{entity_id}"
        entity_resp = requests.get(entity_url, timeout=timeout)
        if entity_resp.status_code != 200:
            continue
        entity_data = entity_resp.json()

        refs = (
            entity_data.get("rcsb_polymer_entity_container_identifiers", {})
            .get("reference_sequence_identifiers", [])
        )
        for ref in refs:
            if ref.get("database_name") == "UniProt" and ref.get("database_accession"):
                return str(ref["database_accession"]).upper()

    raise ValueError(f"未能在 PDB {pdb_id} 中找到 UniProt 映射")

def _fetch_uniprot_sequence(uniprot_id: str) -> str:
    """从 UniProt API 获取蛋白质序列"""
    url = f"https://rest.uniprot.org/uniprotkb/{uniprot_id}.fasta"
    response = requests.get(url)
    response.raise_for_status()
    lines = response.text.splitlines()
    # 跳过第一行（> 开头的注释）
    seq_lines = [line.strip() for line in lines if not line.startswith('>')]
    return ''.join(seq_lines)

def get_pdb_ensemble(
    identifier: str,
    sequence_identity_threshold: float = 100.0,
    max_structures: int = 10
) -> List[str]:
    """
    根据目标蛋白的 UniProt ID 或 PDB ID，通过序列相似性搜索返回相关的 PDB 结构列表。

    Args:
        identifier: UniProt ID (例如 'P00519') 或 PDB ID (例如 '1IEP')
        sequence_identity_threshold: 序列同一性阈值（百分比），默认 90.0
        max_structures: 最多返回多少个结构

    Returns:
        PDB ID 列表
    """
    identifier = identifier.strip().upper()
    if not identifier:
        raise ValueError("请输入有效的 UniProt ID 或 PDB ID")

    # 解析 UniProt ID
    if _is_pdb_id(identifier):
        try:
            uniprot_id = _fetch_uniprot_from_pdb(identifier)
        except Exception as e:
            raise RuntimeError(f"从 PDB {identifier} 获取 UniProt ID 失败: {e}")
    else:
        uniprot_id = identifier

    # 获取参考序列
    try:
        ref_sequence = _fetch_uniprot_sequence(uniprot_id)
        if not ref_sequence:
            raise ValueError(f"UniProt {uniprot_id} 序列为空")
    except Exception as e:
        raise RuntimeError(f"获取 UniProt {uniprot_id} 序列失败: {e}")

    # SeqSimilarityQuery 要求 identity_cutoff 在 [0, 1]。
    # 兼容用户常用的百分数输入（如 90 或 100）。
    identity_cutoff = float(sequence_identity_threshold)
    if identity_cutoff > 1:
        identity_cutoff /= 100.0
    if not (0 <= identity_cutoff <= 1):
        raise ValueError(
            f"sequence_identity_threshold={sequence_identity_threshold} 无效，"
            "请传 0~1 小数或 0~100 百分数。"
        )

    # 使用 SeqSimilarityQuery 搜索相似结构
    try:
        query = SeqSimilarityQuery(
            value=ref_sequence,
            identity_cutoff=identity_cutoff,
            evalue_cutoff=1  # 可选的 E 值阈值，默认 10
        )
        results = list(query())
        if not results:
            raise ValueError(
                f"未找到与 {uniprot_id} 序列相似度 ≥ {identity_cutoff * 100:.1f}% 的 PDB 结构"
            )

        # 去重并统一大写
        deduped = list(dict.fromkeys(str(r).upper() for r in results if r))
        return deduped[:max_structures]
    except Exception as e:
        raise RuntimeError(f"序列相似性搜索失败: {e}")

def fetch_pdb_list(pdb_ids: List[str], output_dir: str = "./data/pdb") -> List[str]:
    """批量下载 PDB 文件，返回本地路径列表"""
    paths = []
    for pdb_id in pdb_ids:
        try:
            path = fetch_pdb(pdb_id, output_dir=output_dir)
            paths.append(path)
        except Exception as e:
            print(f"下载 PDB {pdb_id} 失败: {e}")
    return paths

def get_protein_ensemble(
    identifier: str,
    output_dir: str = "./data/protein_ensembles",
    sequence_identity_threshold: float = 100.0,
    max_structures: int = 10
) -> List[str]:
    """获取蛋白构象系综并下载到本地"""
    pdb_ids = get_pdb_ensemble(identifier, sequence_identity_threshold, max_structures)
    print(f"找到 {len(pdb_ids)} 个相关 PDB 结构: {pdb_ids}")
    local_paths = fetch_pdb_list(pdb_ids, output_dir=output_dir)
    print(f"成功下载 {len(local_paths)} 个 PDB 文件到 {output_dir}")
    return local_paths

if __name__ == "__main__":
    try:
        ensemble = get_protein_ensemble("1IEP", sequence_identity_threshold=100.0, max_structures=5)
        print(f"找到 {len(ensemble)} 个 PDB 结构: {ensemble}")
        print("下载完成。")
    except Exception as e:
        print(f"错误: {e}")