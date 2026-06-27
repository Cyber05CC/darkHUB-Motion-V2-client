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

def _apply_fp8_compatibility_patch():
    try:
        import torch
        import comfy.ops
        
        # Check if Linear class exists in comfy.ops
        if hasattr(comfy.ops, "disable_weight_init") and hasattr(comfy.ops.disable_weight_init, "Linear"):
            LinearClass = comfy.ops.disable_weight_init.Linear
            if hasattr(LinearClass, "forward_comfy_cast_weights"):
                orig_forward = LinearClass.forward_comfy_cast_weights
                
                # Fetch float8 types safely
                f8_e4m3fn = getattr(torch, "float8_e4m3fn", None)
                f8_e5m2 = getattr(torch, "float8_e5m2", None)
                
                def patched_forward(self, input, *args, **kwargs):
                    if hasattr(input, "dtype") and (
                        (f8_e4m3fn and input.dtype == f8_e4m3fn) or 
                        (f8_e5m2 and input.dtype == f8_e5m2)
                    ):
                        # Cast input from FP8 to float16 to prevent CUDA addmm_cuda NotImplementedError
                        input = input.to(torch.float16)
                    return orig_forward(self, input, *args, **kwargs)
                
                LinearClass.forward_comfy_cast_weights = patched_forward
                print("[darkHUB] Applied PyTorch FP8 compatibility patch for Linear layers.")
    except Exception as e:
        print(f"[darkHUB] Failed to apply FP8 compatibility patch: {e}")

# Run FP8 compatibility patch
_apply_fp8_compatibility_patch()

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./web"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
