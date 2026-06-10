# 将运行日志通过LLM提取,并且将提取结果作为RAG节点的输入, 以便后续节点使用

import os
from dotenv import load_dotenv

from langchain_deepseek import ChatDeepSeek
from langgraph.types import Command,interrupt

from pydantic import BaseModel, Field
from .common import _llm

class RAG_llm_state(BaseModel):
    # RAG_LLM 的结构化输出内容
    PDB_ID: str # 蛋白质PDB ID
    SMILES: str # 配体SMILES

    failed_node : str # 任务失败的节点
    failed_reason : str # 任务失败的原因
    diagnosis : str # 任务失败的诊断结果
    fix_script : str # 最后成功的修复脚本，如果无，说明修复失败了

    sammary: str # 任务总结, 如果成功了，总结成功的经验，失败了就总结失败的原因

prompt = f"""
你是一个经验丰富的分子动力学模拟专家，你需要根据提供的任务日志，提取以下的内容：
1. 蛋白质的PDB ID
2. 配体的SMILES
3. 任务失败的节点
4. 任务失败的原因
5. 任务失败的诊断结果
6. 最后成功的修复脚本，如果无，说明修复失败了
7. 任务总结, 如果成功了，总结成功的经验，失败了就总结失败的原因

并且进行结构化的输出，输出格式如下：
PDB_ID: <蛋白质PDB ID>
SMILES: <配体的SMILES>
failed_node: <任务失败的节点>
failed_reason: <任务失败的原因>
diagnosis: <任务失败的诊断结果>
fix_script: <最后成功的修复脚本，如果无，说明修复失败了>
summary: <任务总结, 如果成功了，总结成功的经验，失败了就总结失败的原因>

以下是任务日志：
"""

load_dotenv()

def creat_llm():
    model_name = os.getenv("LLM_MODEL_ID", "deepseek-chat")
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    llm = ChatDeepSeek(
        model=model_name,
        temperature=0.5,
        max_tokens=4096,
        api_key=api_key,
        base_url=base_url,
        model_kwargs={"extra_body": {"thinking": {"type": "disabled"}}},
    )

# 将log_path下的内容结果LLM总结成RAG_llm_state，再提取成json，供RAG使用
def creat_rag(log_path : str):
    with open(log_path, "r") as f:
        log_content = f.read()
    
    llm = creat_llm()
    strctured_llm = llm.with_structured_output(RAG_llm_state)
    response = strctured_llm(prompt + log_content)
    print("LLM response:", response)

    # 解析LLM的响应，提取结构化数据
    rag_state = response.content
    return rag_state

if __name__ == "__main__":
    log_path = "../log/run_03efa06d_20260603_160019.log"
    rag_state = creat_rag(log_path)
    print("Extracted RAG state:", rag_state)