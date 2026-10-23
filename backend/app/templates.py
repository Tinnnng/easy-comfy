"""模板引擎：把新手填的白話欄位，轉成 ComfyUI 的 API workflow JSON。"""

from __future__ import annotations

import copy
import json
import random
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

MAX_SEED = 2**32 - 1
# 上傳到 ComfyUI input 資料夾的檔名（由 /api/uploads 產生），不允許路徑
IMAGE_NAME = re.compile(r"^[\w\-. ()]+\.(png|jpe?g|webp)$", re.IGNORECASE)


class TemplateError(ValueError):
    """模板定義本身有問題（給模板作者看的錯誤）。"""


class ParamError(ValueError):
    """使用者填的欄位不合法（給使用者看的錯誤）。"""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


class ChoiceOption(BaseModel):
    value: str
    label: str
    set: dict[str, Any] = Field(default_factory=dict)
    # 接在文字輸入後面，例如風格預設把 ", watercolor painting" 加到提示詞尾端
    append: dict[str, str] = Field(default_factory=dict)


class TemplateField(BaseModel):
    key: str
    label: str
    type: Literal["text", "number", "choice", "seed", "image"]
    help: str | None = None
    required: bool = False
    advanced: bool = False
    # 含中文時先翻成英文再送給模型
    translate: bool = False
    default: Any = None
    targets: list[str] = Field(default_factory=list)
    options: list[ChoiceOption] = Field(default_factory=list)
    min: float | None = None
    max: float | None = None
    max_length: int | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> "TemplateField":
        if self.type == "choice":
            if not self.options:
                raise ValueError(f"choice 欄位 {self.key} 需要 options")
            if self.default is not None and self.default not in {o.value for o in self.options}:
                raise ValueError(f"欄位 {self.key} 的 default 不在 options 裡")
        elif not self.targets:
            raise ValueError(f"欄位 {self.key} 需要 targets")
        return self


class Template(BaseModel):
    id: str
    name: str
    description: str = ""
    # 表單上方的大標題
    heading: str = "你想看到什麼畫面？"
    # 送出按鈕上的字
    action: str = "開始生成"
    # 前端分頁的排列順序，數字小的在前面
    order: int = 100
    fields: list[TemplateField]
    workflow: dict[str, Any] = Field(exclude=True)

    def public(self) -> dict[str, Any]:
        """給前端的版本：不含 workflow 和節點對應細節。"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "heading": self.heading,
            "action": self.action,
            "fields": [
                f.model_dump(include={"key", "label", "type", "help", "required", "advanced",
                                      "default", "min", "max", "max_length", "translate"})
                | {"options": [{"value": o.value, "label": o.label} for o in f.options]}
                for f in self.fields
            ],
        }

    def resolve_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """驗證並補齊欄位值。seed 為 -1 時會換成實際隨機值，方便之後重現。"""
        known = {f.key for f in self.fields}
        unknown = set(params) - known
        if unknown:
            raise ParamError(sorted(unknown)[0], "不是這個模板的欄位")

        resolved: dict[str, Any] = {}
        for f in self.fields:
            value = params.get(f.key, f.default)
            if value is None or value == "":
                if f.required:
                    raise ParamError(f.key, f"「{f.label}」必填")
                continue

            if f.type == "text":
                value = str(value).strip()
                if f.required and not value:
                    raise ParamError(f.key, f"「{f.label}」必填")
                if f.max_length and len(value) > f.max_length:
                    raise ParamError(f.key, f"最多 {f.max_length} 個字")
            elif f.type == "number":
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    raise ParamError(f.key, "必須是數字") from None
                if value.is_integer():
                    value = int(value)
                if f.min is not None and value < f.min:
                    raise ParamError(f.key, f"不能小於 {f.min}")
                if f.max is not None and value > f.max:
                    raise ParamError(f.key, f"不能大於 {f.max}")
            elif f.type == "choice":
                if value not in {o.value for o in f.options}:
                    raise ParamError(f.key, "不是可用的選項")
            elif f.type == "image":
                value = str(value)
                if not IMAGE_NAME.match(value):
                    raise ParamError(f.key, "請重新上傳圖片")
            elif f.type == "seed":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise ParamError(f.key, "必須是整數") from None
                if value == -1:
                    value = random.randint(0, MAX_SEED)
                elif not 0 <= value <= MAX_SEED:
                    raise ParamError(f.key, f"必須介於 0 到 {MAX_SEED}，或填 -1")
            resolved[f.key] = value
        return resolved

    def build_workflow(self, resolved: dict[str, Any]) -> dict[str, Any]:
        """把已驗證的欄位值寫進 workflow 的對應節點。"""
        workflow = copy.deepcopy(self.workflow)
        appends: list[tuple[str, str]] = []
        for f in self.fields:
            if f.key not in resolved:
                continue
            value = resolved[f.key]
            if f.type == "choice":
                option = next(o for o in f.options if o.value == value)
                for target, v in option.set.items():
                    _set_input(workflow, target, v)
                appends += option.append.items()
            else:
                for target in f.targets:
                    _set_input(workflow, target, value)
        # 最後才接，才不會被後面的欄位覆蓋掉
        for target, suffix in appends:
            node, name = _split_target(target)
            _set_input(workflow, target, str(workflow[node]["inputs"][name]) + suffix)
        return workflow


def _split_target(target: str) -> tuple[str, str]:
    node, sep, name = target.partition(".")
    if not sep or not node or not name:
        raise TemplateError(f"target 格式應為 '節點ID.輸入名稱'，收到 {target!r}")
    return node, name


def _set_input(workflow: dict[str, Any], target: str, value: Any) -> None:
    node, name = _split_target(target)
    workflow[node]["inputs"][name] = value


def _check_targets(template: Template) -> None:
    for f in template.fields:
        targets = list(f.targets) + [t for o in f.options for t in [*o.set, *o.append]]
        for target in targets:
            node, name = _split_target(target)
            if node not in template.workflow:
                raise TemplateError(f"模板 {template.id} 欄位 {f.key}: 找不到節點 {node}")
            if name not in template.workflow[node].get("inputs", {}):
                raise TemplateError(f"模板 {template.id} 欄位 {f.key}: 節點 {node} 沒有輸入 {name}")


def load_template(folder: Path) -> Template:
    meta = yaml.safe_load((folder / "template.yaml").read_text(encoding="utf-8"))
    workflow_file = folder / meta.pop("workflow", "workflow_api.json")
    workflow = json.loads(workflow_file.read_text(encoding="utf-8"))
    try:
        template = Template(**meta, workflow=workflow)
    except ValueError as e:
        raise TemplateError(f"模板 {folder.name} 格式錯誤：{e}") from e
    _check_targets(template)
    return template


def load_templates(root: Path) -> dict[str, Template]:
    templates: dict[str, Template] = {}
    for folder in sorted(p for p in root.iterdir() if (p / "template.yaml").exists()):
        t = load_template(folder)
        if t.id in templates:
            raise TemplateError(f"模板 id 重複：{t.id}")
        templates[t.id] = t
    return dict(sorted(templates.items(), key=lambda kv: (kv[1].order, kv[0])))
