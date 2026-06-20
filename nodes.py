import base64
import json
import hashlib
import os
import sys
import uuid
import socket
import subprocess
import urllib.request
import urllib.error
import threading
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
import nodes

MASTER_KEY = "darkHUB-VIP-2026-Premium"

def get_hwid() -> str:
    """Generates a secure, non-spoofable HWID based on motherboard UUID and CPU ID (Windows) or MAC."""
    components = [str(uuid.getnode())]
    
    if sys.platform == "win32":
        try:
            # Motherboard UUID
            out = subprocess.check_output("wmic csproduct get uuid", shell=True).decode().split()
            if len(out) >= 2:
                components.append(out[1].strip())
        except Exception:
            pass
        try:
            # CPU ID
            out = subprocess.check_output("wmic cpu get processorid", shell=True).decode().split()
            if len(out) >= 2:
                components.append(out[1].strip())
        except Exception:
            pass
            
    # Combine and hash
    raw_id = "|".join(components)
    return hashlib.sha256(raw_id.encode("utf-8")).hexdigest()

def get_server_url() -> str:
    """Returns the obfuscated Base64 URL of the licensing server, preventing plain-text inspection."""
    # Base64 encoded 'https://darkhub-motion-v2-server.onrender.com'
    obfuscated_url = "aHR0cHM6Ly9kYXJraHViLW1vdGlvbi12Mi1zZXJ2ZXIub25yZW5kZXIuY29t"
    return base64.b64decode(obfuscated_url).decode("utf-8")

def _wake_server_async():
    """Asynchronously pings the server at startup to wake up Render & Neon from sleep."""
    try:
        url = get_server_url()
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=15) as response:
            response.read()
    except Exception:
        pass

# Start background wake-up ping immediately when node is loaded
threading.Thread(target=_wake_server_async, daemon=True).start()

def verify_license(server_url: str, key: str, hwid: str, device_name: str) -> dict:
    """Sends a verification request to the central licensing server."""
    url = f"{server_url.rstrip('/')}/api/verify"
    data = json.dumps({
        "key": key,
        "hwid": hwid,
        "device_name": device_name
    }).encode("utf-8")
    
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            res_data = response.read().decode("utf-8")
            return json.loads(res_data)
    except urllib.error.HTTPError as e:
        try:
            res_data = e.read().decode("utf-8")
            return json.loads(res_data)
        except Exception:
            return {"status": "error", "message": f"Licensing server returned status code {e.code}"}
    except urllib.error.URLError as e:
        return {"status": "error", "message": f"Could not connect to licensing server: {str(e.reason)}"}
    except Exception as e:
        return {"status": "error", "message": f"Verification error: {str(e)}"}

def decrypt_data(encrypted_str: str, key: str) -> str:
    """Decrypts ciphertext using AES-256-CBC with SHA-256(key) as key."""
    combined = base64.b64decode(encrypted_str)
    if len(combined) < 16:
        raise ValueError("Ciphertext is too short")
    
    # Extract IV and ciphertext
    iv = combined[:16]
    ciphertext = combined[16:]
    
    # Derive key bytes from SHA-256
    key_bytes = hashlib.sha256(key.encode("utf-8")).digest()
    
    # Decrypt
    backend = default_backend()
    cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv), backend=backend)
    decryptor = cipher.decryptor()
    padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    
    # Unpad
    unpadder = padding.PKCS7(128).unpadder()
    plaintext = unpadder.update(padded_plaintext) + unpadder.finalize()
    
    return plaintext.decode("utf-8")

def get_subgraph_json(subgraph_data_str: str, key: str) -> dict:
    """Parses envelope and decrypts subgraph data based on key."""
    try:
        envelope = json.loads(subgraph_data_str)
    except Exception:
        # Fallback for old/flat format
        decrypted = decrypt_data(subgraph_data_str, key)
        return json.loads(decrypted)
    
    mode = envelope.get("mode")
    if mode == "master":
        if key != MASTER_KEY:
            raise ValueError("Access Denied: Invalid license key!")
        decrypted = decrypt_data(envelope["master_data"], MASTER_KEY)
    elif mode == "dual":
        if key == MASTER_KEY:
            decrypted = decrypt_data(envelope["master_data"], MASTER_KEY)
        else:
            try:
                decrypted = decrypt_data(envelope["user_data"], key)
            except Exception:
                raise ValueError("Access Denied: Invalid license key!")
    else:
        raise ValueError(f"Unknown encryption envelope mode: {mode}")
    
    return json.loads(decrypted)


def get_var_name(node_data):
    widgets = node_data.get("widgets", {})
    for k in ["value_name", "constant_value", "key", "name"]:
        if k in widgets and isinstance(widgets[k], str):
            return widgets[k]
    for k, v in widgets.items():
        if isinstance(v, str):
            return v
    widgets_values = node_data.get("widgets_values", [])
    if widgets_values and isinstance(widgets_values[0], str):
        return widgets_values[0]
    properties = node_data.get("properties", {})
    if "previousName" in properties:
        return properties["previousName"]
    return None


def resolve_virtual_nodes(subgraph):
    nodes_list = subgraph.get("nodes", [])
    internal_links = subgraph.get("internal_links", [])
    inputs_map = subgraph.get("inputs_map", {})
    outputs_map = subgraph.get("outputs_map", {})

    # 1. Identify virtual nodes and normal nodes
    virtual_nodes = {}
    normal_nodes = []
    for node in nodes_list:
        if node.get("type") in ("SetNode", "GetNode", "Reroute"):
            virtual_nodes[node["id"]] = node
        else:
            normal_nodes.append(node)

    if not virtual_nodes:
        return subgraph

    # 2. Build SetNode mappings by name
    set_nodes_by_name = {}
    for node_id, node in virtual_nodes.items():
        if node["type"] == "SetNode":
            var_name = get_var_name(node)
            if var_name:
                set_nodes_by_name[var_name] = node_id

    nodes_map = {n["id"]: n for n in nodes_list}

    # Recursive helper to trace the actual origin of a connection
    def trace_slot(nid, slot_idx, visited=None):
        if visited is None:
            visited = set()
        state = (nid, slot_idx)
        if state in visited:
            return None  # Cycle detected
        visited.add(state)

        node = nodes_map.get(nid)
        if not node:
            return None

        ntype = node["type"]
        if ntype == "Reroute":
            # Reroute has one input slot 0, trace whatever is connected to it
            return trace_link_to(nid, 0, visited)
        elif ntype == "GetNode":
            # GetNode fetches from SetNode, trace SetNode input slot 0
            var_name = get_var_name(node)
            if var_name:
                set_id = set_nodes_by_name.get(var_name)
                if set_id:
                    return trace_link_to(set_id, 0, visited)
            return None
        elif ntype == "SetNode":
            # SetNode has input slot 0, trace its origin
            return trace_link_to(nid, 0, visited)
        else:
            # Normal node: this is the source!
            return (nid, slot_idx)

    # Helper to find what is connected to a target node slot
    def trace_link_to(target_id, target_slot, visited):
        # Check inputs_map (external inputs)
        for ext_key, targets in inputs_map.items():
            for t in targets:
                if t[0] == target_id and t[1] == target_slot:
                    return ("external", ext_key)

        # Check internal links
        for link in internal_links:
            if link["target_id"] == target_id and link["target_slot"] == target_slot:
                return trace_slot(link["origin_id"], link["origin_slot"], visited)

        # Fallback to widget/default
        return ("widget", target_id, target_slot)

    # 3. Rebuild all connections and widgets for normal nodes
    new_internal_links = []
    new_inputs_map = {k: [] for k in inputs_map}

    for node in normal_nodes:
        node_id = node["id"]

        # Map slot indices to slot names
        slot_to_name = {}
        if "inputs" in node and node["inputs"]:
            for idx, inp in enumerate(node["inputs"]):
                if inp:
                    slot_to_name[idx] = inp["name"]

        # Trace and map each input slot
        for slot_idx, slot_name in slot_to_name.items():
            source = trace_slot(node_id, slot_idx)
            if not source:
                continue

            if source[0] == "external":
                ext_key = source[1]
                if ext_key not in new_inputs_map:
                    new_inputs_map[ext_key] = []
                new_inputs_map[ext_key].append([node_id, slot_idx])
            elif source[0] == "widget":
                # Copy widget value from source node if available
                src_node_id = source[1]
                src_slot_idx = source[2]
                src_node = nodes_map.get(src_node_id)
                if src_node and "widgets" in src_node:
                    src_slot_to_name = {}
                    if "inputs" in src_node and src_node["inputs"]:
                        for idx, inp in enumerate(src_node["inputs"]):
                            if inp:
                                src_slot_to_name[idx] = inp["name"]
                    src_name = src_slot_to_name.get(src_slot_idx)
                    if src_name and src_name in src_node["widgets"]:
                        if "widgets" not in node:
                            node["widgets"] = {}
                        node["widgets"][slot_name] = src_node["widgets"][src_name]
            else:
                src_id, src_slot = source
                new_internal_links.append({
                    "origin_id": src_id,
                    "origin_slot": src_slot,
                    "target_id": node_id,
                    "target_slot": slot_idx
                })

    # 4. Rebuild outputs map for external outputs
    new_outputs_map = {}
    for ext_key, origin in outputs_map.items():
        source = trace_slot(origin[0], origin[1])
        if source and isinstance(source, tuple) and source[0] != "external" and source[0] != "widget":
            new_outputs_map[ext_key] = [source[0], source[1]]

    subgraph["nodes"] = normal_nodes
    subgraph["internal_links"] = new_internal_links
    subgraph["inputs_map"] = new_inputs_map
    subgraph["outputs_map"] = new_outputs_map
    return subgraph


class darkHUB_Subgraph:
    @classmethod
    def INPUT_TYPES(s):
        inputs = {
            "required": {
                "key": ("STRING", {"default": ""}),
                "subgraph_data": ("STRING", {"default": "", "multiline": True}),
            },
            "optional": {},
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
                "unique_id": "UNIQUE_ID",
            }
        }
        for i in range(30):
            inputs["optional"][f"input_{i}"] = ("*",)
        return inputs

    RETURN_TYPES = ("*",) * 30
    RETURN_NAMES = tuple(f"output_{i}" for i in range(30))
    FUNCTION = "execute"
    CATEGORY = "darkHUB"

    @classmethod
    def IS_CHANGED(s, key, subgraph_data, **kwargs):
        try:
            subgraph = get_subgraph_json(subgraph_data, key)
            subgraph = resolve_virtual_nodes(subgraph)
        except Exception:
            return float("nan")

        changed_values = []
        nodes_list = subgraph.get("nodes", [])
        
        for node_data in nodes_list:
            node_type = node_data["type"]
            if node_type in nodes.NODE_CLASS_MAPPINGS:
                node_class = nodes.NODE_CLASS_MAPPINGS[node_type]
                if hasattr(node_class, "IS_CHANGED"):
                    try:
                        args = {}
                        serialized_widgets = node_data.get("widgets", {})
                        input_types = node_class.INPUT_TYPES()
                        all_inputs = {**input_types.get("required", {}), **input_types.get("optional", {})}
                        
                        for k in all_inputs:
                            if k in serialized_widgets:
                                args[k] = serialized_widgets[k]
                        
                        is_changed_val = node_class.IS_CHANGED(**args)
                        changed_values.append(str(is_changed_val))
                    except Exception:
                        return float("nan")
        
        if changed_values:
            return hashlib.sha256(f"{subgraph_data};{';'.join(changed_values)}".encode()).hexdigest()
        return subgraph_data

    def execute(self, key, subgraph_data, **kwargs):
        # 1. Verify License and Hardware ID (HWID) on the central server
        server_url = get_server_url()
        hwid = get_hwid()
        device_name = socket.gethostname()

        # Send activation check to central server
        verification = verify_license(server_url, key, hwid, device_name)
        if verification.get("status") != "success":
            raise ValueError(f"darkHUB Licensing Error: {verification.get('message', 'Access Denied')}")

        # 2. Decrypt the subgraph data
        subgraph = get_subgraph_json(subgraph_data, key)
        subgraph = resolve_virtual_nodes(subgraph)

        nodes_list = subgraph.get("nodes", [])
        internal_links = subgraph.get("internal_links", [])
        inputs_map = subgraph.get("inputs_map", {})
        outputs_map = subgraph.get("outputs_map", {})

        nodes_map = {n["id"]: n for n in nodes_list}

        # 3. Topological Sort
        dependencies = {n["id"]: set() for n in nodes_list}
        for link in internal_links:
            origin_id = link["origin_id"]
            target_id = link["target_id"]
            if target_id in dependencies:
                dependencies[target_id].add(origin_id)

        sorted_node_ids = []
        visited = set()
        temp_visited = set()

        def visit(node_id):
            if node_id in temp_visited:
                raise ValueError("darkHUB: Cycle detected inside the packed subgraph!")
            if node_id not in visited:
                temp_visited.add(node_id)
                for dep in dependencies.get(node_id, []):
                    visit(dep)
                temp_visited.remove(node_id)
                visited.add(node_id)
                sorted_node_ids.append(node_id)

        for n_id in dependencies:
            if n_id not in visited:
                visit(n_id)

        # 4. Execution Cache & UI Outputs merging
        cache = {}
        merged_ui = {}

        for node_id in sorted_node_ids:
            node_data = nodes_map[node_id]
            node_type = node_data["type"]

            if node_type not in nodes.NODE_CLASS_MAPPINGS:
                raise ValueError(f"darkHUB: Required node class '{node_type}' is not installed/registered!")

            node_class = nodes.NODE_CLASS_MAPPINGS[node_type]
            node_instance = node_class()
            func_name = getattr(node_instance, "FUNCTION", "execute")
            func = getattr(node_instance, func_name)

            # Map slot indices to slot names based on serialized node
            slot_to_input_name = {}
            if "inputs" in node_data and node_data["inputs"]:
                for idx, inp in enumerate(node_data["inputs"]):
                    if inp:
                        slot_to_input_name[idx] = inp["name"]

            input_types = node_class.INPUT_TYPES()
            required_inputs = input_types.get("required", {})
            optional_inputs = input_types.get("optional", {})

            # Resolve arguments
            args = {}
            # Combine static inputs with actual slots present on the node (e.g., dynamic inputs like a, b in Math nodes)
            all_input_names = set(required_inputs.keys()) | set(optional_inputs.keys())
            for slot_name in slot_to_input_name.values():
                if "." not in slot_name:
                    all_input_names.add(slot_name)

            for input_name in all_input_names:
                input_def = required_inputs.get(input_name) or optional_inputs.get(input_name)
                # Check if this input name is an Autogrow plural input (e.g. images, latents, masks)
                is_autogrow = False
                prefix_prefix = f"{input_name}."
                for slot_name in slot_to_input_name.values():
                    if slot_name.startswith(prefix_prefix):
                        is_autogrow = True
                        break

                if is_autogrow:
                    autogrow_dict = {}
                    for slot_idx, slot_name in slot_to_input_name.items():
                        if slot_name.startswith(prefix_prefix):
                            val = None
                            found = False

                            # 1. External inputs mapping
                            for ext_input_key, targets in inputs_map.items():
                                for target in targets:
                                    if target[0] == node_id and target[1] == slot_idx:
                                        val = kwargs.get(ext_input_key)
                                        found = True
                                        break
                                if found:
                                    break

                            # 2. Internal link connections
                            if not found:
                                for link in internal_links:
                                    if link["target_id"] == node_id and link["target_slot"] == slot_idx:
                                        origin_id = link["origin_id"]
                                        o_slot = link["origin_slot"]
                                        val = cache.get((origin_id, o_slot))
                                        found = True
                                        break

                            # 3. Serialized widgets
                            if not found:
                                serialized_widgets = node_data.get("widgets", {})
                                if slot_name in serialized_widgets:
                                    val = serialized_widgets[slot_name]
                                    found = True

                            if found:
                                autogrow_dict[slot_name] = val
                    args[input_name] = autogrow_dict
                    continue

                val = None
                found = False

                # 1. External inputs mapping
                for ext_input_key, targets in inputs_map.items():
                    for target in targets:
                        if target[0] == node_id:
                            slot_idx = target[1]
                            if slot_to_input_name.get(slot_idx) == input_name:
                                val = kwargs.get(ext_input_key)
                                found = True
                                break
                    if found:
                        break

                # 2. Internal link connections
                if not found:
                    for link in internal_links:
                        if link["target_id"] == node_id:
                            t_slot = link["target_slot"]
                            if slot_to_input_name.get(t_slot) == input_name:
                                origin_id = link["origin_id"]
                                o_slot = link["origin_slot"]
                                val = cache.get((origin_id, o_slot))
                                found = True
                                break

                # 3. Serialized widgets
                if not found:
                    serialized_widgets = node_data.get("widgets", {})
                    if input_name in serialized_widgets:
                        val = serialized_widgets[input_name]
                        found = True

                # Populate argument
                if found:
                    args[input_name] = val
                else:
                    if isinstance(input_def, tuple) and len(input_def) > 1 and isinstance(input_def[1], dict):
                        if "default" in input_def[1]:
                            args[input_name] = input_def[1]["default"]

            # Special handling for ColorTransfer node's DynamicCombo input "source_stats"
            if node_type == "ColorTransfer":
                if "source_stats" not in args or args["source_stats"] is None:
                    args["source_stats"] = {}
                
                if isinstance(args["source_stats"], str):
                    val_str = args["source_stats"]
                    args["source_stats"] = {
                        "source_stats": val_str,
                        "target_index": 0
                    }
                
                if isinstance(args["source_stats"], dict):
                    if "source_stats" not in args["source_stats"]:
                        serialized_widgets = node_data.get("widgets", {})
                        mode = serialized_widgets.get("source_stats", "per_frame")
                        if isinstance(mode, dict):
                            mode = mode.get("source_stats", "per_frame")
                        args["source_stats"]["source_stats"] = mode
                    
                    if "target_index" not in args["source_stats"]:
                        target_index = 0
                        serialized_widgets = node_data.get("widgets", {})
                        if "target_index" in serialized_widgets:
                            try:
                                target_index = int(serialized_widgets["target_index"])
                            except Exception:
                                pass
                        args["source_stats"]["target_index"] = target_index

            # Run node execution
            try:
                import inspect
                sig = inspect.signature(func)
                for param_name in sig.parameters:
                    if param_name not in args:
                        if param_name == "unique_id":
                            args["unique_id"] = str(node_id)
                        elif param_name == "prompt":
                            args["prompt"] = kwargs.get("prompt", {})
                        elif param_name == "extra_pnginfo":
                            args["extra_pnginfo"] = kwargs.get("extra_pnginfo", {})
                        elif param_name == "prompt_id":
                            args["prompt_id"] = kwargs.get("prompt_id")
            except Exception:
                pass

            # Mock and inject V3 API `hidden` context
            orig_class_hidden = getattr(node_class, "hidden", None)
            orig_instance_hidden = getattr(node_instance, "hidden", None)
            
            class MockHidden:
                def __init__(self, unique_id, prompt=None, extra_pnginfo=None):
                    self.unique_id = unique_id
                    self.prompt = prompt if prompt is not None else {}
                    self.extra_pnginfo = extra_pnginfo if extra_pnginfo is not None else {}
                def __getattr__(self, name):
                    return None

            mock_hidden = MockHidden(
                str(node_id),
                kwargs.get("prompt"),
                kwargs.get("extra_pnginfo")
            )
            try:
                node_class.hidden = mock_hidden
            except AttributeError:
                pass
            try:
                node_instance.hidden = mock_hidden
            except AttributeError:
                pass

            # Run node execution with logs suppressed to protect IP
            import logging
            import contextlib
            logging.disable(logging.CRITICAL)
            try:
                with open(os.devnull, "w") as devnull:
                    with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
                        res = func(**args)
            finally:
                logging.disable(logging.NOTSET)
                # Restore original hidden attributes
                try:
                    if orig_class_hidden is not None:
                        node_class.hidden = orig_class_hidden
                    else:
                        try:
                            delattr(node_class, "hidden")
                        except AttributeError:
                            pass
                except AttributeError:
                    pass
                try:
                    if orig_instance_hidden is not None:
                        node_instance.hidden = orig_instance_hidden
                    else:
                        try:
                            delattr(node_instance, "hidden")
                        except AttributeError:
                            pass
                except AttributeError:
                    pass

            # Unwrap ComfyUI latest API wrapped outputs
            if hasattr(res, "args") and isinstance(res.args, tuple):
                res = res.args

            # Handle UI results
            if isinstance(res, dict) and "result" in res:
                if "ui" in res:
                    for ui_key, ui_val in res["ui"].items():
                        if ui_key not in merged_ui:
                            merged_ui[ui_key] = []
                        if isinstance(ui_val, list):
                            merged_ui[ui_key].extend(ui_val)
                        else:
                            merged_ui[ui_key].append(ui_val)
                node_outputs_val = res["result"]
            else:
                node_outputs_val = res

            # Cache the outputs
            if isinstance(node_outputs_val, tuple):
                for slot_idx, val in enumerate(node_outputs_val):
                    cache[(node_id, slot_idx)] = val
            else:
                cache[(node_id, 0)] = node_outputs_val

        # 5. Map final return values
        return_values = []
        for j in range(30):
            ext_output_key = f"output_{j}"
            if ext_output_key in outputs_map:
                origin_info = outputs_map[ext_output_key]
                origin_id = origin_info[0]
                origin_slot = origin_info[1]
                val = cache.get((origin_id, origin_slot))
                return_values.append(val)
            else:
                return_values.append(None)

        return {
            "ui": merged_ui,
            "result": tuple(return_values)
        }


NODE_CLASS_MAPPINGS = {
    "darkHUB_Subgraph": darkHUB_Subgraph
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "darkHUB_Subgraph": "darkHUB Subgraph"
}
