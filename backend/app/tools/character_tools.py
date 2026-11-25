"""
角色管理工具 - 多角色一致性的角色注册表

通过 storage/characters.json 维护「角色 ID → 参考图路径 / 外观锚点」的结构化映射，
供长视频分镜生图阶段按镜头标注的角色 ID 路由到正确的参考图。
Agent 无状态、每次请求重建，因此角色状态必须落文件而非内存。
"""
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 优先加载 backend/.env（避免直接运行工具脚本时环境未加载）
BASE_DIR = Path(__file__).parent.parent.parent
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

# 角色注册表路径（storage/ 已在 .gitignore 中，属运行时数据）
STORAGE_DIR = BASE_DIR / "storage"
CHARACTERS_FILE = STORAGE_DIR / "characters.json"


# ─── 注册表读写 ────────────────────────────────────────────────────────
def _load_registry() -> dict:
    """读取角色注册表；文件不存在或损坏时返回空结构。"""
    if not CHARACTERS_FILE.exists():
        return {"project_id": "default", "characters": []}
    try:
        data = json.loads(CHARACTERS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("characters"), list):
            logger.warning("characters.json 结构异常，按空注册表处理")
            return {"project_id": "default", "characters": []}
        return data
    except Exception as e:
        logger.warning(f"读取 characters.json 失败，按空注册表处理: {e}")
        return {"project_id": "default", "characters": []}


def _save_registry(data: dict) -> None:
    """原子性写入角色注册表。"""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = CHARACTERS_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(CHARACTERS_FILE)


def _validate_reference_image(reference_image: str) -> str:
    """校验参考图必须是已存在的本地 /storage/ 路径。"""
    if not reference_image or not isinstance(reference_image, str):
        raise ValueError("reference_image 不能为空")
    if not reference_image.startswith("/storage/"):
        raise ValueError(
            f"参考图必须是本地 /storage/ 路径（如 /storage/images/xxx.jpg），收到: {reference_image}"
        )
    file_path = BASE_DIR / reference_image.lstrip("/")
    if not file_path.exists():
        raise FileNotFoundError(f"参考图文件不存在: {file_path}")
    return reference_image


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _find_by_id(registry: dict, character_id: str) -> Optional[dict]:
    return next(
        (c for c in registry["characters"] if c.get("character_id") == character_id),
        None,
    )


def _find_by_name(registry: dict, name: str) -> Optional[dict]:
    for c in registry["characters"]:
        if c.get("name") == name or name in (c.get("aliases") or []):
            return c
    return None


# ─── Pydantic 入参 ─────────────────────────────────────────────────────
class RegisterCharacterInput(BaseModel):
    """角色注册输入参数"""
    name: str = Field(description="角色名称，如「小雨」，同一注册表内不可重名")
    reference_image: str = Field(
        description="角色参考图本地路径（/storage/images/xxx.jpg），必须是已生成并经用户确认的图片"
    )
    appearance: str = Field(
        description="稳定外观锚点描述：仅保留不易触发重绘的身份特征，如发型类别、服装类别、职业身份、标志性道具；"
                    "避免身高、体型、胖瘦、肤色深浅等易引发外观漂移的描述"
    )
    aliases: Optional[List[str]] = Field(
        default=None,
        description="角色别名列表（用户口语称呼），用于称呼归一化，可选"
    )


class UpdateCharacterInput(BaseModel):
    """角色信息更新输入参数"""
    character_id: str = Field(description="register_character 返回的角色 ID，如 char_a1b2c3d4")
    reference_image: Optional[str] = Field(
        default=None, description="新的参考图本地路径（/storage/images/xxx.jpg），可选"
    )
    appearance: Optional[str] = Field(default=None, description="新的外观锚点描述，可选")
    aliases: Optional[List[str]] = Field(default=None, description="新的别名列表（整体覆盖），可选")


# ─── 工具定义 ──────────────────────────────────────────────────────────
@tool("register_character", args_schema=RegisterCharacterInput)
def register_character_tool(
    name: str,
    reference_image: str,
    appearance: str,
    aliases: Optional[List[str]] = None,
) -> str:
    """
    注册一个角色到角色注册表（storage/characters.json），建立「角色 ID → 参考图/外观锚点」映射。
    多角色长视频中，每个角色在分镜生图前必须先注册；分镜与镜头 prompt 只引用返回的 character_id。

    Returns:
        注册结果 JSON（含 character_id）或错误信息
    """
    try:
        _validate_reference_image(reference_image)

        registry = _load_registry()
        if _find_by_name(registry, name):
            return json.dumps(
                {
                    "error": f"角色「{name}」已存在，不能重复注册。"
                    f"如需更换参考图或外观，请使用 update_character_reference 工具。"
                },
                ensure_ascii=False,
            )

        character_id = f"char_{uuid.uuid4().hex[:8]}"
        record = {
            "character_id": character_id,
            "name": name,
            "aliases": aliases or [],
            "reference_image": reference_image,
            "appearance": appearance,
            "status": "confirmed",
            "created_at": _now(),
            "updated_at": _now(),
        }
        registry["characters"].append(record)
        _save_registry(registry)

        logger.info(f"✅ 角色注册成功: {character_id} ({name}) -> {reference_image}")
        return json.dumps(
            {
                "message": f"角色「{name}」注册成功，后续分镜请引用 character_id={character_id}",
                "character": record,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        logger.error(f"❌ 角色注册失败: {e}")
        return json.dumps({"error": f"角色注册失败: {str(e)}"}, ensure_ascii=False)


@tool("list_characters")
def list_characters_tool() -> str:
    """
    列出角色注册表中的全部角色（ID、名称、别名、参考图路径、外观锚点、状态）。
    生图前用本工具确认分镜中标注的 character_id 均已注册且参考图可用。

    Returns:
        角色列表 JSON
    """
    registry = _load_registry()
    characters = registry.get("characters", [])
    return json.dumps(
        {
            "count": len(characters),
            "characters": characters,
        },
        ensure_ascii=False,
    )


@tool("update_character_reference", args_schema=UpdateCharacterInput)
def update_character_reference_tool(
    character_id: str,
    reference_image: Optional[str] = None,
    appearance: Optional[str] = None,
    aliases: Optional[List[str]] = None,
) -> str:
    """
    按 character_id 更新角色信息：更换参考图、更新外观锚点或别名。
    至少传入一个更新字段；reference_image 必须是已存在的本地 /storage/ 路径。

    Returns:
        更新后的角色记录 JSON 或错误信息
    """
    if reference_image is None and appearance is None and aliases is None:
        return json.dumps(
            {"error": "至少需要传入 reference_image / appearance / aliases 中的一个字段"},
            ensure_ascii=False,
        )

    try:
        if reference_image is not None:
            _validate_reference_image(reference_image)

        registry = _load_registry()
        record = _find_by_id(registry, character_id)
        if record is None:
            return json.dumps(
                {"error": f"未找到 character_id={character_id}，可先用 list_characters 查看已注册角色"},
                ensure_ascii=False,
            )

        if reference_image is not None:
            record["reference_image"] = reference_image
        if appearance is not None:
            record["appearance"] = appearance
        if aliases is not None:
            record["aliases"] = aliases
        record["updated_at"] = _now()

        _save_registry(registry)
        logger.info(f"✅ 角色更新成功: {character_id}")
        return json.dumps(
            {"message": f"角色 {character_id} 更新成功", "character": record},
            ensure_ascii=False,
        )
    except Exception as e:
        logger.error(f"❌ 角色更新失败: {e}")
        return json.dumps({"error": f"角色更新失败: {str(e)}"}, ensure_ascii=False)
