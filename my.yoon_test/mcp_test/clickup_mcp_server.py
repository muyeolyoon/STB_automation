import asyncio
import os
from typing import List, Dict, Any
import httpx
from mcp.server.fastmcp import FastMCP

# ClickUp API 설정
CLICKUP_API_BASE = "https://api.clickup.com/api/v2"
CLICKUP_TOKEN = os.getenv("CLICKUP_API_TOKEN")  # 환경 변수에서 API 토큰 가져오기

# MCP 서버 초기화
mcp = FastMCP("ClickUpServer")

# ClickUp API 호출 헬퍼 함수
async def clickup_request(method: str, endpoint: str, data: Dict = None) -> Dict:
    headers = {"Authorization": CLICKUP_TOKEN}
    async with httpx.AsyncClient() as client:
        if method.upper() == "GET":
            response = await client.get(f"{CLICKUP_API_BASE}{endpoint}", headers=headers)
        elif method.upper() == "POST":
            response = await client.post(f"{CLICKUP_API_BASE}{endpoint}", headers=headers, json=data)
        response.raise_for_status()
        return response.json()

# 작업 목록 조회 도구
@mcp.tool()
async def list_tasks(team_id: str) -> List[Dict[str, Any]]:
    """ClickUp에서 특정 팀의 작업 목록을 조회합니다."""
    endpoint = f"/team/{team_id}/task"
    data = await clickup_request("GET", endpoint)
    return data.get("tasks", [])

# 새 작업 생성 도구
@mcp.tool()
async def create_task(list_id: str, name: str, description: str = "") -> Dict[str, Any]:
    """ClickUp에서 새 작업을 생성합니다."""
    endpoint = f"/list/{list_id}/task"
    data = {"name": name, "description": description}
    return await clickup_request("POST", endpoint)

# MCP 서버 실행
async def main():
    if not CLICKUP_TOKEN:
        raise ValueError("환경 변수 CLICKUP_API_TOKEN을 설정하세요.")
    await mcp.start()

if __name__ == "__main__":
    asyncio.run(main())