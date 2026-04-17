import os
import requests
from typing import Optional

def web_search(query: str, num_results: int = 5) -> str:
    """
    使用 SERPAPI 进行网络搜索，返回搜索结果的格式化摘要。

    Args:
        query: 搜索关键词
        num_results: 返回的结果数量（默认5）

    Returns:
        成功时返回格式化字符串，包含标题、链接、摘要；失败时返回错误信息。
    """
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        return "错误：未设置 SERPAPI_API_KEY 环境变量。"

    url = "https://serpapi.com/search"
    params = {
        "q": query,
        "api_key": api_key,
        "num": num_results,
        "engine": "google",      # 可选：google, bing, baidu 等
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "organic_results" not in data:
            return f"搜索失败：未找到结果。API响应：{data.get('error', '未知错误')}"

        results = []
        for idx, res in enumerate(data["organic_results"][:num_results], 1):
            title = res.get("title", "无标题")
            link = res.get("link", "无链接")
            snippet = res.get("snippet", "无摘要")
            results.append(f"{idx}. {title}\n   链接: {link}\n   摘要: {snippet}")

        return "\n\n".join(results)

    except requests.exceptions.RequestException as e:
        return f"网络请求失败: {str(e)}"
    except Exception as e:
        return f"搜索失败: {str(e)}"