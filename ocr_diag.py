"""Read-only OCR diagnostic. Run INSIDE the container with one of the failing screenshots.
  docker cp 10vs1.png netbox-hub:/tmp/10vs1.png
  docker exec -i netbox-hub python /app/ocr_diag.py /tmp/10vs1.png "VMware ESXi"
It separates the three possible failures: OCR engine, platform config, extractor."""
import sys

img_path = sys.argv[1]
platform = sys.argv[2] if len(sys.argv) > 2 else None
sys.path.insert(0, "/app")

from core.ocr_engine import run_local_ocr_pipeline
from core.extractors import PROCESSING_MODULES
from config.naming_rules import load_naming_rules

with open(img_path, "rb") as f:
    data = f.read()
res = run_local_ocr_pipeline([data])
tokens = res.get("raw_tokens", [])
print("1) OCR engine : errors=%s | tokens=%d | image=%sx%s" % (res.get("errors"), len(tokens), res.get("image_width"), res.get("image_height")))
for t in tokens[:8]:
    print("     ", t.get("text"), t.get("conf"))

rules = load_naming_rules()
tpp = rules.get("topology_parsing_presets") or {}
if isinstance(tpp, list):
    tpp = {(i.get("platform") or i.get("name")): i for i in tpp if isinstance(i, dict)}
print("2) platforms  :", list(tpp.keys()))
platform = platform if platform in tpp else next(iter(tpp), None)
cfg = tpp.get(platform) if isinstance(tpp.get(platform), dict) else {}
mods = cfg.get("modules") or []
img_mods = [m for m in mods if m in PROCESSING_MODULES and PROCESSING_MODULES[m]["input_kind"] == "image"]
print("   platform=%s | modules=%s | image modules=%s | unknown=%s" % (platform, mods, img_mods, [m for m in mods if m not in PROCESSING_MODULES]))
print("   noise blacklist entries:", len(cfg.get("ui_noise_blacklist") or []), "| keys:", sorted(cfg.keys()))

if img_mods and tokens:
    handler = PROCESSING_MODULES[img_mods[0]]["handler"]
    if hasattr(handler, "extract"):
        recs = handler.extract(tokens, image_width=res.get("image_width", 1920), image_height=res.get("image_height", 1080), plat_cfg=cfg)
    else:
        recs = handler(tokens, plat_cfg=cfg)
    print("3) extractor  : records=%d" % len(recs or []))
    for r in (recs or [])[:6]:
        print("     ", r)
else:
    print("3) extractor  : skipped (no tokens or no image module)")
