"""Minimal subset of nova452/Rebalance-Pack @ 4553b14: Krea2 rebalance nodes only.
OmniNode (runtime exec + OpenRouter) and other modules intentionally not installed.

部署：把上游仓库的 krea2.py、conditioning_rebalance.py、LICENSE 复制到
/opt/ComfyUI/custom_nodes/Rebalance-Pack-Krea2/，再把本文件放进去命名为 __init__.py。
"""
from . import conditioning_rebalance, krea2

NODE_CLASS_MAPPINGS = dict(krea2.NODE_CLASS_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS = dict(getattr(krea2, "NODE_DISPLAY_NAME_MAPPINGS", {}))
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
