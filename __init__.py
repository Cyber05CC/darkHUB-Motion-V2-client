"""
darkHUB Secure Subgraph Node
A secure subgraph packer for ComfyUI.
"""

import os
import shutil

def _copy_default_workflow():
    try:
        current_dir = os.path.dirname(__file__)
        src_file = os.path.join(current_dir, "darkHUB-Motion-V2.json")
        if os.path.exists(src_file):
            comfy_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
            dest_dir = os.path.join(comfy_root, "user", "default", "workflows")
            os.makedirs(dest_dir, exist_ok=True)
            dest_file = os.path.join(dest_dir, "darkHUB-Motion-V2.json")
            shutil.copy2(src_file, dest_file)
    except Exception as e:
        print(f"[darkHUB] Failed to copy default workflow: {e}")

# Run startup copy
_copy_default_workflow()

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./web"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
