import { app } from "../../../scripts/app.js";

// Load Poppins font dynamically
const fontLink = document.createElement("link");
fontLink.rel = "stylesheet";
fontLink.href = "https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&display=swap";
document.head.appendChild(fontLink);

const MASTER_KEY = "darkHUB-VIP-2026-Premium";

/**
 * Encrypts plaintext using AES-256-CBC with SHA-256(password) as key.
 */
async function encryptData(plaintext, password) {
    const encoder = new TextEncoder();
    
    // 1. Generate key from password using SHA-256
    const pwData = encoder.encode(password);
    const hashBuffer = await window.crypto.subtle.digest('SHA-256', pwData);
    
    // 2. Import raw key bytes as AES key
    const aesKey = await window.crypto.subtle.importKey(
        'raw',
        hashBuffer,
        { name: 'AES-CBC' },
        false,
        ['encrypt']
    );
    
    // 3. Generate random 16-byte IV
    const iv = window.crypto.getRandomValues(new Uint8Array(16));
    
    // 4. Encrypt plaintext
    const plaintextBytes = encoder.encode(plaintext);
    const ciphertextBuffer = await window.crypto.subtle.encrypt(
        { name: 'AES-CBC', iv: iv },
        aesKey,
        plaintextBytes
    );
    
    // 5. Combine IV + Ciphertext
    const combined = new Uint8Array(iv.length + ciphertextBuffer.byteLength);
    combined.set(iv, 0);
    combined.set(new Uint8Array(ciphertextBuffer), iv.length);
    
    // 6. Encode to base64
    let binary = "";
    for (let i = 0; i < combined.length; i++) {
        binary += String.fromCharCode(combined[i]);
    }
    return btoa(binary);
}

/**
 * Encrypts using both custom key and master key if applicable.
 */
async function encryptSubgraph(plaintext, key) {
    const cipherMaster = await encryptData(plaintext, MASTER_KEY);
    
    if (key && key !== MASTER_KEY) {
        const cipherUser = await encryptData(plaintext, key);
        return JSON.stringify({
            mode: "dual",
            user_data: cipherUser,
            master_data: cipherMaster
        });
    } else {
        return JSON.stringify({
            mode: "master",
            master_data: cipherMaster
        });
    }
}

/**
 * Completely hides the widget from view and disables its DOM display.
 */
function hideWidget(node, name) {
    if (!node.widgets) return;
    const w = node.widgets.find(widget => widget.name === name);
    if (w) {
        w.type = "hidden";
        
        // Hide ComfyUI DOM overlay elements
        if (w.inputEl) {
            w.inputEl.style.display = "none";
            const parentRow = w.inputEl.closest(".comfy-widget-row") || w.inputEl.parentElement;
            if (parentRow) parentRow.style.display = "none";
        }
        if (w.element) {
            w.element.style.display = "none";
            const parentRow = w.element.closest(".comfy-widget-row") || w.element.parentElement;
            if (parentRow) parentRow.style.display = "none";
        }
        if (w.blockEl) {
            w.blockEl.style.display = "none";
        }
    }
}

/**
 * Prunes/restores the dynamic slots to match the saved properties on reload.
 */
function restoreDynamicSlots(node) {
    if (!node.properties || !node.properties.activeInputs || !node.properties.activeOutputs) {
        return;
    }

    const activeInputs = node.properties.activeInputs;
    const activeOutputs = node.properties.activeOutputs;

    // Prune extra inputs, preserving 'key' if it exists
    if (node.inputs) {
        const dynamicPorts = node.inputs.filter(inp => inp && inp.name !== "key");
        while (dynamicPorts.length > activeInputs.length) {
            const portToRemove = dynamicPorts.pop();
            const idx = node.inputs.indexOf(portToRemove);
            if (idx !== -1) {
                node.removeInput(idx);
            }
        }
        
        // Make sure we have the correct inputs set up
        for (let i = 0; i < activeInputs.length; i++) {
            let port = node.inputs.find(inp => inp && inp.name === activeInputs[i].name);
            if (!port && i < dynamicPorts.length) {
                port = dynamicPorts[i];
            }
            if (!port) {
                node.addInput(activeInputs[i].name, activeInputs[i].type);
            } else {
                port.name = activeInputs[i].name;
                port.type = activeInputs[i].type;
            }
        }
    }

    // Prune extra outputs
    while (node.outputs && node.outputs.length > activeOutputs.length) {
        node.removeOutput(node.outputs.length - 1);
    }

    // Make sure we have the correct outputs set up
    for (let i = 0; i < activeOutputs.length; i++) {
        if (!node.outputs[i]) {
            node.addOutput(activeOutputs[i].name, activeOutputs[i].type);
        } else {
            node.outputs[i].name = activeOutputs[i].name;
            node.outputs[i].type = activeOutputs[i].type;
        }
    }
}

/**
 * Main function to pack selected nodes on the canvas.
 */
async function packSelectedNodes(canvas) {
    // Filter selected nodes to avoid packing already packed subgraph nodes
    const selectedNodes = Object.values(canvas.selected_nodes || {}).filter(n => n.type !== "darkHUB_Subgraph");
    if (selectedNodes.length === 0) {
        alert("darkHUB: No nodes selected to pack!");
        return;
    }

    const key = prompt("darkHUB: Enter encryption key (leave empty or use default 'darkHUB-VIP-2026-Premium'):");
    if (key === null) {
        return; // User cancelled
    }
    const finalKey = key.trim() || MASTER_KEY;

    const innerNodeIds = new Set(selectedNodes.map(n => n.id));

    const internalLinks = [];
    const externalInputs = [];
    const externalOutputs = [];

    // Scan all links in the graph to find inputs/outputs of our selected nodes
    for (const linkId in canvas.graph.links) {
        const link = canvas.graph.links[linkId];
        if (!link) continue;

        const originIn = innerNodeIds.has(link.origin_id);
        const targetIn = innerNodeIds.has(link.target_id);

        if (originIn && targetIn) {
            internalLinks.push({
                origin_id: link.origin_id,
                origin_slot: link.origin_slot,
                target_id: link.target_id,
                target_slot: link.target_slot
            });
        } else if (!originIn && targetIn) {
            externalInputs.push(link);
        } else if (originIn && !targetIn) {
            externalOutputs.push(link);
        }
    }

    // Group external inputs by unique origin source (origin_id, origin_slot)
    const extInputsGrouped = {};
    for (const link of externalInputs) {
        const keyStr = `${link.origin_id}_${link.origin_slot}`;
        if (!extInputsGrouped[keyStr]) {
            extInputsGrouped[keyStr] = {
                origin_id: link.origin_id,
                origin_slot: link.origin_slot,
                type: link.type,
                targets: []
            };
        }
        extInputsGrouped[keyStr].targets.push({
            target_id: link.target_id,
            target_slot: link.target_slot
        });
    }

    // Group external outputs by unique internal source (origin_id, origin_slot)
    const extOutputsGrouped = {};
    for (const link of externalOutputs) {
        const keyStr = `${link.origin_id}_${link.origin_slot}`;
        if (!extOutputsGrouped[keyStr]) {
            extOutputsGrouped[keyStr] = {
                origin_id: link.origin_id,
                origin_slot: link.origin_slot,
                type: link.type,
                targets: []
            };
        }
        extOutputsGrouped[keyStr].targets.push({
            target_id: link.target_id,
            target_slot: link.target_slot
        });
    }

    // Build serialized internal nodes
    const subgraphNodes = [];
    for (const node of selectedNodes) {
        const serializedNode = {
            id: node.id,
            type: node.type,
            title: node.title,
            properties: node.properties || {},
            widgets: {},
            inputs: (node.inputs || []).map(inp => inp ? { name: inp.name, type: inp.type } : null),
            outputs: (node.outputs || []).map(out => out ? { name: out.name, type: out.type } : null)
        };

        if (node.widgets) {
            for (const w of node.widgets) {
                serializedNode.widgets[w.name] = w.value;
            }
        }
        subgraphNodes.push(serializedNode);
    }

    const inputsMap = {};
    const outputsMap = {};

    // Map external inputs
    const nodeInputs = [];
    let inputIdx = 0;
    for (const srcKey in extInputsGrouped) {
        const group = extInputsGrouped[srcKey];
        const inputName = `input_${inputIdx}`;
        nodeInputs.push({
            name: inputName,
            type: group.type,
            origin_id: group.origin_id,
            origin_slot: group.origin_slot
        });
        inputsMap[inputName] = group.targets.map(t => [t.target_id, t.target_slot]);
        inputIdx++;
    }

    // Map external outputs
    const nodeOutputs = [];
    let outputIdx = 0;
    for (const srcKey in extOutputsGrouped) {
        const group = extOutputsGrouped[srcKey];
        const outputName = `output_${outputIdx}`;
        nodeOutputs.push({
            name: outputName,
            type: group.type,
            origin_id: group.origin_id,
            origin_slot: group.origin_slot,
            targets: group.targets
        });
        outputsMap[outputName] = [group.origin_id, group.origin_slot];
        outputIdx++;
    }

    // --- RESOLVE VIRTUAL SET/GET BOUNDARY CROSSINGS ---
    const allNodesOnGraph = canvas.graph._nodes || [];
    const setNodes = allNodesOnGraph.filter(n => n.type === "SetNode");
    const getNodes = allNodesOnGraph.filter(n => n.type === "GetNode");

    function getVarName(node) {
        if (node.widgets) {
            for (const w of node.widgets) {
                if (w.name === "value_name" || w.name === "constant_value" || w.name === "key" || w.name === "name") {
                    return w.value;
                }
            }
            if (node.widgets[0]) return node.widgets[0].value;
        }
        if (node.widgets_values && node.widgets_values[0]) {
            return node.widgets_values[0];
        }
        if (node.properties && node.properties.previousName) {
            return node.properties.previousName;
        }
        return null;
    }

    const setNodesByName = {};
    for (const node of setNodes) {
        const name = getVarName(node);
        if (name) setNodesByName[name] = node;
    }

    for (const getNode of getNodes) {
        const name = getVarName(getNode);
        if (!name) continue;

        const setNode = setNodesByName[name];
        if (!setNode) continue;

        const setIn = innerNodeIds.has(setNode.id);
        const getIn = innerNodeIds.has(getNode.id);

        if (setIn && !getIn) {
            // SetNode inside selection, GetNode outside selection.
            // This virtual connection crosses outward, so we map it as an external output of the subgraph.
            const inputLink = setNode.inputs && setNode.inputs[0] ? canvas.graph.links[setNode.inputs[0].link] : null;
            if (inputLink) {
                const targets = [];
                if (getNode.outputs && getNode.outputs[0] && getNode.outputs[0].links) {
                    for (const lId of getNode.outputs[0].links) {
                        const link = canvas.graph.links[lId];
                        if (link) {
                            targets.push({
                                target_id: link.target_id,
                                target_slot: link.target_slot
                            });
                        }
                    }
                }

                if (targets.length > 0) {
                    const outputName = `output_${outputIdx}`;
                    nodeOutputs.push({
                        name: outputName,
                        type: inputLink.type,
                        origin_id: inputLink.origin_id,
                        origin_slot: inputLink.origin_slot,
                        targets: targets
                    });
                    outputsMap[outputName] = [inputLink.origin_id, inputLink.origin_slot];
                    outputIdx++;
                }
            }
        } else if (!setIn && getIn) {
            // SetNode outside selection, GetNode inside selection.
            // This virtual connection crosses inward, so we map it as an external input to the subgraph.
            const inputLink = setNode.inputs && setNode.inputs[0] ? canvas.graph.links[setNode.inputs[0].link] : null;
            if (inputLink) {
                const targets = [];
                for (const linkId in canvas.graph.links) {
                    const link = canvas.graph.links[linkId];
                    if (link && link.origin_id === getNode.id) {
                        targets.push({
                            target_id: link.target_id,
                            target_slot: link.target_slot
                        });
                    }
                }

                if (targets.length > 0) {
                    const inputName = `input_${inputIdx}`;
                    nodeInputs.push({
                        name: inputName,
                        type: inputLink.type,
                        origin_id: inputLink.origin_id,
                        origin_slot: inputLink.origin_slot
                    });
                    inputsMap[inputName] = targets.map(t => [t.target_id, t.target_slot]);
                    inputIdx++;
                }
            }
        }
    }

    // Wrap subgraph structure
    const subgraphData = {
        nodes: subgraphNodes,
        internal_links: internalLinks,
        inputs_map: inputsMap,
        outputs_map: outputsMap
    };

    const plaintext = JSON.stringify(subgraphData);
    let encryptedEnvelope;
    try {
        encryptedEnvelope = await encryptSubgraph(plaintext, finalKey);
    } catch (err) {
        alert("darkHUB: Encryption failed! " + err);
        return;
    }

    // Create the darkHUB Subgraph Node
    const darkhubNode = LiteGraph.createNode("darkHUB_Subgraph");
    if (!darkhubNode) {
        alert("darkHUB: Could not create darkHUB_Subgraph node. Is the custom node registered?");
        return;
    }

    // Place the new node at the center of the selection
    let minX = Infinity, minY = Infinity;
    let maxX = -Infinity, maxY = -Infinity;
    for (const node of selectedNodes) {
        const pos = node.pos;
        if (pos[0] < minX) minX = pos[0];
        if (pos[1] < minY) minY = pos[1];
        if (pos[0] > maxX) maxX = pos[0];
        if (pos[1] > maxY) maxY = pos[1];
    }
    const centerX = (minX + maxX) / 2;
    const centerY = (minY + maxY) / 2;
    darkhubNode.pos = [centerX, centerY];

    // Add node to graph
    canvas.graph.add(darkhubNode);

    // Set properties for reload restore
    if (!darkhubNode.properties) darkhubNode.properties = {};
    darkhubNode.properties.activeInputs = nodeInputs.map(inp => ({ name: inp.name, type: inp.type }));
    darkhubNode.properties.activeOutputs = nodeOutputs.map(outp => ({ name: outp.name, type: outp.type }));

    // Set serialized data and key
    if (darkhubNode.widgets) {
        const keyWidget = darkhubNode.widgets.find(w => w.name === "key");
        if (keyWidget) keyWidget.value = finalKey;

        const dataWidget = darkhubNode.widgets.find(w => w.name === "subgraph_data");
        if (dataWidget) dataWidget.value = encryptedEnvelope;
    }

    // Clean up unused outputs & inputs, rename/add used ones
    if (darkhubNode.inputs) {
        const dynamicPorts = darkhubNode.inputs.filter(inp => inp && inp.name !== "key");
        while (dynamicPorts.length > nodeInputs.length) {
            const portToRemove = dynamicPorts.pop();
            const idx = darkhubNode.inputs.indexOf(portToRemove);
            if (idx !== -1) {
                darkhubNode.removeInput(idx);
            }
        }
        for (let i = 0; i < nodeInputs.length; i++) {
            let port = darkhubNode.inputs.find(inp => inp && inp.name === nodeInputs[i].name);
            if (!port && i < dynamicPorts.length) {
                port = dynamicPorts[i];
            }
            if (!port) {
                darkhubNode.addInput(nodeInputs[i].name, nodeInputs[i].type);
            } else {
                port.name = nodeInputs[i].name;
                port.type = nodeInputs[i].type;
            }
        }
    }

    const activeOutputCount = nodeOutputs.length;
    for (let i = 0; i < activeOutputCount; i++) {
        const slot = darkhubNode.outputs[i];
        if (slot) {
            slot.name = nodeOutputs[i].name;
            slot.type = nodeOutputs[i].type;
        }
    }
    // Remove extra outputs
    for (let i = darkhubNode.outputs.length - 1; i >= activeOutputCount; i--) {
        darkhubNode.removeOutput(i);
    }

    // Connect external inputs to the new darkHUB node
    for (let i = 0; i < nodeInputs.length; i++) {
        const inp = nodeInputs[i];
        const originNode = canvas.graph.getNodeById(inp.origin_id);
        if (originNode) {
            const targetSlotIndex = darkhubNode.findInputSlot(inp.name);
            if (targetSlotIndex !== -1) {
                originNode.connect(inp.origin_slot, darkhubNode, targetSlotIndex);
            }
        }
    }

    // Connect external outputs from the new darkHUB node
    for (let i = 0; i < activeOutputCount; i++) {
        const outp = nodeOutputs[i];
        for (const target of outp.targets) {
            const targetNode = canvas.graph.getNodeById(target.target_id);
            if (targetNode) {
                darkhubNode.connect(i, targetNode, target.target_slot);
            }
        }
    }

    // Remove the original nodes from canvas
    for (const node of selectedNodes) {
        canvas.graph.remove(node);
    }

    // Also remove external GetNodes whose virtual connections were converted to real outputs
    for (const getNode of getNodes) {
        const name = getVarName(getNode);
        if (!name) continue;
        const setNode = setNodesByName[name];
        if (setNode && innerNodeIds.has(setNode.id) && !innerNodeIds.has(getNode.id)) {
            canvas.graph.remove(getNode);
        }
    }

    // Focus on the newly created node
    canvas.selectNode(darkhubNode);
    canvas.draw(true, true);
    
    alert("darkHUB: Nodes packed successfully into secure subgraph!");
}

// Register the frontend extension
app.registerExtension({
    name: "darkHUB.Subgraph",
    
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "darkHUB_Subgraph") {
            return;
        }

        // Apply custom styling on node creation
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function() {
            if (onNodeCreated) {
                onNodeCreated.apply(this, arguments);
            }

            // Set size and branding colors
            this.color = "#101011";
            this.bgcolor = "#151517";
            this.boxcolor = "#D1FE17"; // glowing brand border
            this.title_color = "#f7f7f7";

            // Hide the subgraph_data widget DOM element and mask the key widget as password
            setTimeout(() => {
                const w = this.widgets?.find(widget => widget.name === "subgraph_data");
                if (w) {
                    w.type = "hidden";
                    w.computeSize = () => [0, -4];
                    w.draw = () => {};
                    if (w.inputEl) w.inputEl.style.display = "none";
                }
                const keyWidget = this.widgets?.find(w => w.name === "key");
                if (keyWidget && keyWidget.inputEl) {
                    keyWidget.inputEl.type = "password";
                }
            }, 1);

            // Custom draw background to draw glowing border
            this.onDrawBackground = function(ctx, canvas) {
                // Keep subgraph_data widget hidden and zero-sized
                const w = this.widgets?.find(widget => widget.name === "subgraph_data");
                if (w) {
                    w.type = "hidden";
                    w.computeSize = () => [0, -4];
                    w.draw = () => {};
                    if (w.inputEl && w.inputEl.style.display !== "none") {
                        w.inputEl.style.display = "none";
                    }
                }

                if (this.flags.collapsed) return;
                
                ctx.save();
                ctx.fillStyle = "#101011";
                ctx.beginPath();
                if (ctx.roundRect) {
                    ctx.roundRect(0, 0, this.size[0], this.size[1], 8);
                } else {
                    ctx.rect(0, 0, this.size[0], this.size[1]);
                }
                ctx.fill();

                // Draw glowing border using #D1FE17
                ctx.strokeStyle = this.selected ? "#D1FE17" : "rgba(209, 254, 23, 0.4)";
                ctx.lineWidth = this.selected ? 2.0 : 1.0;
                
                if (this.selected) {
                    ctx.shadowColor = "#D1FE17";
                    ctx.shadowBlur = 8;
                }
                
                ctx.beginPath();
                if (ctx.roundRect) {
                    ctx.roundRect(0, 0, this.size[0], this.size[1], 8);
                } else {
                    ctx.rect(0, 0, this.size[0], this.size[1]);
                }
                ctx.stroke();
                
                ctx.restore();
            };

            // Custom draw foreground for badge and lock indicator
            this.onDrawForeground = function(ctx, canvas) {
                if (this.flags.collapsed) return;

                // Safety checks to ensure slots are aligned and hidden widget is actually hidden
                hideWidget(this, "subgraph_data");
                if (this.widgets) {
                    const keyWidget = this.widgets.find(w => w.name === "key");
                    if (keyWidget && keyWidget.inputEl && keyWidget.inputEl.type !== "password") {
                        keyWidget.inputEl.type = "password";
                    }
                }
                const currentDynInputs = this.inputs ? this.inputs.filter(inp => inp && inp.name !== "key").length : 0;
                const currentOutputs = this.outputs ? this.outputs.length : 0;
                if (this.properties && this.properties.activeInputs && this.properties.activeOutputs &&
                    (currentDynInputs !== this.properties.activeInputs.length || currentOutputs !== this.properties.activeOutputs.length)) {
                    restoreDynamicSlots(this);
                }

                ctx.save();
                
                // Poppins regular font
                ctx.font = "10px Poppins, Arial";

                // Draw the "darkHUB SECURE" badge
                ctx.font = "bold 9px Poppins, Arial";
                const badgeText = "darkHUB SECURE";
                const textWidth = ctx.measureText(badgeText).width;
                
                const badgeX = this.size[0] - textWidth - 15;
                const badgeY = -12;
                
                // Neon lime badge background with black text
                ctx.fillStyle = "#D1FE17";
                ctx.beginPath();
                if (ctx.roundRect) {
                    ctx.roundRect(badgeX, badgeY, textWidth + 10, 14, 4);
                } else {
                    ctx.rect(badgeX, badgeY, textWidth + 10, 14);
                }
                ctx.fill();
                
                ctx.fillStyle = "#000000";
                ctx.fillText(badgeText, badgeX + 5, badgeY + 10);

                // Check for data
                const dataWidget = this.widgets ? this.widgets.find(w => w.name === "subgraph_data") : null;
                const hasData = dataWidget && dataWidget.value;

                if (!hasData) {
                    ctx.fillStyle = "#f7f7f7";
                    ctx.font = "11px Poppins, Arial";
                    ctx.fillText("⚠️ Empty Subgraph", 15, this.size[1] / 2 - 5);
                    ctx.fillStyle = "rgba(247, 247, 247, 0.5)";
                    ctx.font = "9px Poppins, Arial";
                    ctx.fillText("Right-click canvas to pack nodes.", 15, this.size[1] / 2 + 10);
                } else {
                    // Draw status indicator
                    const keyWidget = this.widgets ? this.widgets.find(w => w.name === "key") : null;
                    const isLocked = !keyWidget || !keyWidget.value;
                    
                    ctx.font = "500 10px Poppins, Arial";
                    if (isLocked) {
                        ctx.fillStyle = "#ef4444";
                        ctx.fillText("🔒 LOCKED (Enter Key)", 12, this.size[1] - 10);
                    } else {
                        ctx.fillStyle = "#D1FE17";
                        ctx.fillText("🔓 SECURED & READY", 12, this.size[1] - 10);
                    }
                }

                ctx.restore();
            };
        };

        // Hook onConfigure to restore dynamic slots on load/refresh
        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function(info) {
            if (onConfigure) {
                onConfigure.apply(this, arguments);
            }
            
            // Wait for ComfyUI's setup to complete, then force slots and widgets
            setTimeout(() => {
                restoreDynamicSlots(this);
                hideWidget(this, "subgraph_data");
                if (this.widgets) {
                    const keyWidget = this.widgets.find(w => w.name === "key");
                    if (keyWidget && keyWidget.inputEl) {
                        keyWidget.inputEl.type = "password";
                    }
                }
            }, 100);
        };
    },

    async setup() {
        // Setup context menu options
        const origGetCanvasMenuOptions = LGraphCanvas.prototype.getCanvasMenuOptions;
        LGraphCanvas.prototype.getCanvasMenuOptions = function() {
            const options = origGetCanvasMenuOptions.apply(this, arguments);
            const selectedNodes = Object.values(this.selected_nodes || {});
            
            // Add option if we have selection
            if (selectedNodes.length > 0) {
                options.push({
                    content: "Pack to darkHUB Subgraph",
                    callback: () => {
                        packSelectedNodes(this);
                    }
                });
            }
            return options;
        };

        const origGetNodeMenuOptions = LGraphCanvas.prototype.getNodeMenuOptions;
        LGraphCanvas.prototype.getNodeMenuOptions = function(node) {
            const options = origGetNodeMenuOptions.apply(this, arguments);
            const selectedNodes = Object.values(this.selected_nodes || {});
            
            if (selectedNodes.length > 0) {
                options.push({
                    content: "Pack to darkHUB Subgraph",
                    callback: () => {
                        packSelectedNodes(this);
                    }
                });
            } else {
                options.push({
                    content: "Pack to darkHUB Subgraph",
                    callback: () => {
                        this.selectNode(node);
                        packSelectedNodes(this);
                    }
                });
            }
            return options;
        };
    }
});
