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
        import comfy.utils
        
        # Patch 1: Linear layers FP8 inputs (cast to float16)
        if hasattr(comfy.ops, "disable_weight_init") and hasattr(comfy.ops.disable_weight_init, "Linear"):
            LinearClass = comfy.ops.disable_weight_init.Linear
            if hasattr(LinearClass, "forward_comfy_cast_weights"):
                orig_forward = LinearClass.forward_comfy_cast_weights
                
                f8_e4m3fn = getattr(torch, "float8_e4m3fn", None)
                f8_e5m2 = getattr(torch, "float8_e5m2", None)
                
                def patched_forward(self, input, *args, **kwargs):
                    if hasattr(input, "dtype") and (
                        (f8_e4m3fn and input.dtype == f8_e4m3fn) or 
                        (f8_e5m2 and input.dtype == f8_e5m2)
                    ):
                        input = input.to(torch.float16)
                    return orig_forward(self, input, *args, **kwargs)
                
                LinearClass.forward_comfy_cast_weights = patched_forward
                print("[darkHUB] Patched Linear layers for FP8 compatibility.")

        # Patch 2: common_upscale (interpolate) FP8 inputs (cast to float16)
        if hasattr(comfy.utils, "common_upscale"):
            orig_upscale = comfy.utils.common_upscale
            
            f8_e4m3fn = getattr(torch, "float8_e4m3fn", None)
            f8_e5m2 = getattr(torch, "float8_e5m2", None)
            
            def patched_upscale(samples, width, height, upscale_method, crop):
                orig_dtype = None
                if hasattr(samples, "dtype") and (
                    (f8_e4m3fn and samples.dtype == f8_e4m3fn) or 
                    (f8_e5m2 and samples.dtype == f8_e5m2)
                ):
                    orig_dtype = samples.dtype
                    samples = samples.to(torch.float16)
                
                out = orig_upscale(samples, width, height, upscale_method, crop)
                
                if orig_dtype is not None:
                    out = out.to(orig_dtype)
                return out
                
            comfy.utils.common_upscale = patched_upscale
            print("[darkHUB] Patched common_upscale for FP8 compatibility.")

        # Patch 3: SAM3 model get_dtype (force float16 to prevent bicubic and conv2d failures)
        try:
            import comfy_extras.nodes_sam3
            classes_to_patch = ["SAM3_VideoTrack", "SAM3_Detect"]
            for class_name in classes_to_patch:
                cls = getattr(comfy_extras.nodes_sam3, class_name, None)
                if cls and hasattr(cls, "execute"):
                    orig_execute = cls.execute
                    
                    def make_patched_execute(original):
                        def patched(self, *args, **kwargs):
                            model = args[0] if len(args) > 0 else (kwargs.get("model") or kwargs.get("sam3_model"))
                            if model and hasattr(model, "model") and hasattr(model.model, "get_dtype"):
                                orig_get_dtype = model.model.get_dtype
                                
                                f8_e4m3fn = getattr(torch, "float8_e4m3fn", None)
                                f8_e5m2 = getattr(torch, "float8_e5m2", None)
                                
                                def safe_get_dtype():
                                    dtype = orig_get_dtype()
                                    if (f8_e4m3fn and dtype == f8_e4m3fn) or (f8_e5m2 and dtype == f8_e5m2):
                                        return torch.float16
                                    return dtype
                                model.model.get_dtype = safe_get_dtype
                            return original(self, *args, **kwargs)
                        return patched
                    
                    cls.execute = make_patched_execute(orig_execute)
                    print(f"[darkHUB] Patched {class_name} get_dtype for FP8 mode compatibility.")
        except Exception as e:
            print(f"[darkHUB] SAM3 nodes not patched (might not be loaded): {e}")
            
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
