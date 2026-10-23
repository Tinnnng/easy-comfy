from pathlib import Path

import pytest

from app.comfy_client import ComfyClient
from app.templates import MAX_SEED, ParamError, TemplateError, load_template, load_templates

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@pytest.fixture
def flux():
    return load_templates(TEMPLATES_DIR)["flux_schnell_t2i"]


def test_all_templates_load():
    assert load_templates(TEMPLATES_DIR)


def test_defaults_and_random_seed(flux):
    resolved = flux.resolve_params({"prompt": "  a cat  "})
    assert resolved["prompt"] == "a cat"
    assert resolved["size"] == "square"
    assert 0 <= resolved["seed"] <= MAX_SEED


def test_build_workflow_sets_nodes(flux):
    resolved = flux.resolve_params({"prompt": "a cat", "size": "portrait", "quality": "fine", "seed": 42})
    wf = flux.build_workflow(resolved)
    assert wf["4"]["inputs"]["text"] == "a cat"
    assert (wf["6"]["inputs"]["width"], wf["6"]["inputs"]["height"]) == (832, 1216)
    assert wf["7"]["inputs"]["steps"] == 8
    assert wf["7"]["inputs"]["seed"] == 42


def test_build_workflow_does_not_mutate_template(flux):
    flux.build_workflow(flux.resolve_params({"prompt": "a cat", "seed": 1}))
    assert flux.workflow["4"]["inputs"]["text"] == ""


def test_same_params_reproduce_same_workflow(flux):
    resolved = flux.resolve_params({"prompt": "a cat"})
    assert flux.build_workflow(resolved) == flux.build_workflow(dict(resolved))


@pytest.mark.parametrize("params, field", [
    ({}, "prompt"),
    ({"prompt": "   "}, "prompt"),
    ({"prompt": "x", "size": "huge"}, "size"),
    ({"prompt": "x", "seed": "abc"}, "seed"),
    ({"prompt": "x", "seed": -5}, "seed"),
    ({"prompt": "x", "steps": 10}, "steps"),
])
def test_invalid_params(flux, params, field):
    with pytest.raises(ParamError) as e:
        flux.resolve_params(params)
    assert e.value.field == field


def test_target_pointing_to_missing_node_is_rejected(tmp_path):
    (tmp_path / "workflow_api.json").write_text('{"1": {"class_type": "X", "inputs": {"a": 1}}}')
    (tmp_path / "template.yaml").write_text(
        "id: bad\nname: bad\nfields:\n  - {key: a, label: a, type: text, targets: ['2.a']}\n")
    with pytest.raises(TemplateError):
        load_template(tmp_path)


def test_validate_reports_missing_model(flux):
    wf = flux.build_workflow(flux.resolve_params({"prompt": "a cat", "seed": 1}))
    info = {cls: {"input": {"required": {}}} for cls in {n["class_type"] for n in wf.values()}}
    info["UnetLoaderGGUF"]["input"]["required"]["unet_name"] = [["other.gguf"]]
    problems = ComfyClient._find_problems(wf, info)
    assert len(problems) == 1 and "flux1-schnell-Q4_0.gguf" in problems[0]


def test_parse_preview_formats():
    import json

    from app.comfy_client import parse_preview

    legacy = (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"JPEGDATA"
    assert parse_preview(legacy)["data"] == {"mime": "image/jpeg", "image": b"JPEGDATA"}

    meta = json.dumps({"prompt_id": "p1", "image_type": "image/png"}).encode()
    modern = (4).to_bytes(4, "big") + len(meta).to_bytes(4, "big") + meta + b"PNGDATA"
    assert parse_preview(modern)["data"] == {"mime": "image/png", "image": b"PNGDATA", "prompt_id": "p1"}

    assert parse_preview(b"\x00\x00\x00\x09junk") is None


def test_style_appends_after_prompt(flux):
    resolved = flux.resolve_params({"prompt": "a cat", "style": "watercolor", "seed": 1})
    text = flux.build_workflow(resolved)["4"]["inputs"]["text"]
    assert text.startswith("a cat, watercolor painting")


def test_no_style_leaves_prompt_untouched(flux):
    resolved = flux.resolve_params({"prompt": "a cat", "seed": 1})
    assert resolved["style"] == "none"
    assert flux.build_workflow(resolved)["4"]["inputs"]["text"] == "a cat"


def test_templates_are_ordered_for_beginners():
    assert list(load_templates(TEMPLATES_DIR))[0] == "flux_schnell_t2i"


def test_img2img_strengths_hit_distinct_noise_levels():
    # ComfyUI 以 int(steps / denoise) 決定起始雜訊量，三檔必須落在不同的值
    i2i = load_templates(TEMPLATES_DIR)["flux_schnell_i2i"]
    strength = next(f for f in i2i.fields if f.key == "strength")
    levels = []
    for o in strength.options:
        steps, denoise = o.set["9.steps"], o.set["9.denoise"]
        levels.append(steps / int(steps / denoise))
    assert levels == sorted(levels) and len(set(levels)) == 3
    assert all(0.85 < level < 0.95 for level in levels)
