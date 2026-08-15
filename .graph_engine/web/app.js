const TYPE_STYLE = {
  Repository: { color: "#d83c2e", shape: "hexagon", order: 0 },
  CodeFile: { color: "#315f89", shape: "fold", order: 1 },
  Class: { color: "#4c725d", shape: "triangle", order: 2 },
  Function: { color: "#1a1a18", shape: "circle", order: 3 },
  Page: { color: "#996a48", shape: "square", order: 4 },
  APIEndpoint: { color: "#d83c2e", shape: "diamond", order: 5 },
  BackendRoute: { color: "#b07d14", shape: "route", order: 6 },
  WorkflowProcess: { color: "#087f8c", shape: "hexagon", order: 7 },
  WorkflowStep: { color: "#ef8354", shape: "square", order: 8 },
  UIAction: { color: "#cf4f24", shape: "diamond", order: 9 },
  ExternalSystem: { color: "#5f6b75", shape: "hexagon", order: 10 },
  MessageChannel: { color: "#286f6c", shape: "route", order: 11 },
  Decision: { color: "#8b4f78", shape: "decision", order: 12 },
  Session: { color: "#777168", shape: "circle", order: 13 },
  SessionEvent: { color: "#9b958c", shape: "square", order: 14 },
};

const RELATION_COLORS = {
  CONTAINS: "#8d877e",
  DEFINES: "#315f89",
  IMPORTS: "#315f89",
  MAKES_REQUEST: "#d83c2e",
  TARGETS_ROUTE: "#b07d14",
  HANDLED_BY: "#4c725d",
  HAS_STEP: "#087f8c",
  NEXT: "#ef8354",
  INVOKES: "#4c725d",
  CALLS: "#087f8c",
  HAS_ACTION: "#cf4f24",
  DECLARED_IN: "#996a48",
  STARTS_PROCESS: "#087f8c",
  TARGETS_SYSTEM: "#5f6b75",
  PUBLISHES_TO: "#286f6c",
  CONSUMED_BY: "#286f6c",
  ROUTES_TO: "#b07d14",
  SAME_CHANNEL: "#8d877e",
  HAS_SESSION: "#777168",
  MADE_DECISION: "#8b4f78",
  AFFECTS: "#d83c2e",
  SUPERSEDES: "#8b4f78",
};

const dom = Object.fromEntries([
  "repository", "search", "label-filters", "relationship-filters", "node-limit",
  "node-limit-value", "graph-canvas", "paper-sheet", "graph-loading", "graph-empty",
  "clear-filters", "summary-repo", "summary-count", "legend", "unfold", "fit-graph",
  "refresh-graph", "zoom-readout", "inspector", "inspector-empty", "inspector-content",
  "node-number", "node-kinds", "node-title", "node-properties", "neighbor-count",
  "neighbor-list", "fold-node", "fold-history", "toast", "scope-rail", "open-controls",
  "close-controls", "close-inspector", "keyboard-help", "key-dialog", "toggle-labels",
  "toggle-relations", "node-access-list",
  "open-trace", "close-trace", "trace-sheet", "trace-form", "trace-view", "trace-state",
  "trace-result", "trace-title", "trace-stats", "trace-warnings", "trace-board",
  "trace-lanes", "trace-links", "copy-mermaid", "trace-lane-nav", "trace-access-list",
].map((id) => [id, document.getElementById(id)]));

const state = {
  meta: null,
  graph: { nodes: [], links: [] },
  repository: "",
  labels: new Set(),
  relationships: new Set(),
  search: "",
  limit: 240,
  selected: null,
  selectedDetail: null,
  history: [{ id: "", label: "Full sheet" }],
  historyIndex: 0,
  layout: "fold",
  loadingToken: 0,
  mode: "atlas",
  trace: null,
};

function primaryType(node) {
  return [...(node.labels || [])].sort((a, b) => (TYPE_STYLE[a]?.order ?? 99) - (TYPE_STYLE[b]?.order ?? 99))[0] || "Node";
}

function safeText(value) {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function shortLabel(value, length = 38) {
  const text = safeText(value);
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

function escapeSelector(value) {
  return window.CSS?.escape ? CSS.escape(value) : value.replace(/["\\]/g, "\\$&");
}

function showToast(message) {
  dom.toast.textContent = message;
  dom.toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => dom.toast.classList.remove("show"), 2800);
}

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

class GraphCanvas {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.nodes = [];
    this.links = [];
    this.nodeMap = new Map();
    this.positions = new Map();
    this.transform = { x: 0, y: 0, scale: 1 };
    this.pointer = null;
    this.hovered = null;
    this.dragged = null;
    this.dragStart = null;
    this.panStart = null;
    this.running = false;
    this.alpha = 0;
    this.frame = 0;
    this.layout = "fold";
    this.selectedId = null;
    this.keyboardIndex = -1;
    this.reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas.parentElement);
    this.bindEvents();
  }

  setData(graph, layout = this.layout) {
    this.layout = layout;
    const bounds = this.canvas.getBoundingClientRect();
    const centerX = Math.max(bounds.width, 500) / 2;
    const centerY = Math.max(bounds.height, 400) / 2;
    const oldPositions = this.positions;
    const degrees = new Map(graph.nodes.map((node) => [node.id, 0]));
    for (const link of graph.links) {
      degrees.set(link.source, (degrees.get(link.source) || 0) + 1);
      degrees.set(link.target, (degrees.get(link.target) || 0) + 1);
    }
    const rankedDegrees = [...degrees.values()].sort((a, b) => b - a);
    const highDegreeCutoff = Math.max(3, rankedDegrees[Math.min(11, rankedDegrees.length - 1)] || 3);
    this.nodes = graph.nodes.map((raw, index) => {
      const saved = oldPositions.get(raw.id);
      const angle = index * 2.39996;
      const spread = 35 + Math.sqrt(index) * 25;
      return {
        ...raw,
        type: primaryType(raw),
        x: saved?.x ?? centerX + Math.cos(angle) * spread,
        y: saved?.y ?? centerY + Math.sin(angle) * spread,
        vx: 0,
        vy: 0,
        radius: raw.labels?.includes("Repository") ? 12 : 7,
        degree: degrees.get(raw.id) || 0,
        highDegree: (degrees.get(raw.id) || 0) >= highDegreeCutoff,
        fixed: false,
      };
    });
    this.nodeMap = new Map(this.nodes.map((node) => [node.id, node]));
    this.links = graph.links.map((link) => ({ ...link, sourceNode: this.nodeMap.get(link.source), targetNode: this.nodeMap.get(link.target) })).filter((link) => link.sourceNode && link.targetNode);
    this.positions = new Map(this.nodes.map((node) => [node.id, node]));
    this.alpha = this.reducedMotion ? .08 : 1;
    this.keyboardIndex = this.nodes.findIndex((node) => node.id === this.selectedId);
    this.start();
  }

  setLayout(layout) {
    this.layout = layout;
    this.alpha = .75;
    this.start();
  }

  setSelected(id) {
    this.selectedId = id;
    this.keyboardIndex = this.nodes.findIndex((node) => node.id === id);
    this.draw();
  }

  start() {
    if (this.running) return;
    this.running = true;
    requestAnimationFrame(() => this.tick());
  }

  tick() {
    if (this.alpha > .003) {
      const iterations = this.reducedMotion ? 5 : 1;
      for (let i = 0; i < iterations; i += 1) this.simulate();
      this.alpha *= this.reducedMotion ? .68 : .985;
      this.draw();
      requestAnimationFrame(() => this.tick());
    } else {
      this.running = false;
      this.draw();
    }
  }

  simulate() {
    const width = this.canvas.clientWidth;
    const height = this.canvas.clientHeight;
    const alpha = this.alpha;
    for (const link of this.links) {
      const dx = link.targetNode.x - link.sourceNode.x;
      const dy = link.targetNode.y - link.sourceNode.y;
      const distance = Math.max(Math.hypot(dx, dy), 1);
      const desired = link.type === "TARGETS_ROUTE" ? 70 : 52;
      const force = (distance - desired) * .0035 * alpha;
      const fx = (dx / distance) * force;
      const fy = (dy / distance) * force;
      if (!link.sourceNode.fixed) { link.sourceNode.vx += fx; link.sourceNode.vy += fy; }
      if (!link.targetNode.fixed) { link.targetNode.vx -= fx; link.targetNode.vy -= fy; }
    }

    const maxPairs = this.nodes.length > 350 ? 2 : 5;
    for (let i = 0; i < this.nodes.length; i += 1) {
      const a = this.nodes[i];
      for (let step = 1; step <= maxPairs; step += 1) {
        const b = this.nodes[(i + step * 37) % this.nodes.length];
        if (!b || a === b) continue;
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const distance2 = dx * dx + dy * dy + .1;
        const force = Math.min(55 / distance2, .38) * alpha;
        a.vx += dx * force;
        a.vy += dy * force;
        b.vx -= dx * force;
        b.vy -= dy * force;
      }
    }

    const typeOrder = Object.keys(TYPE_STYLE);
    const repos = [...new Set(this.nodes.map((node) => node.repository).filter(Boolean))];
    for (const node of this.nodes) {
      if (node.fixed) continue;
      if (this.layout === "fold") {
        const lane = Math.max(0, typeOrder.indexOf(node.type));
        const targetY = 45 + (lane / Math.max(typeOrder.length - 1, 1)) * Math.max(height - 90, 100);
        const repoIndex = Math.max(0, repos.indexOf(node.repository));
        const targetX = repos.length > 1 ? 70 + (repoIndex / Math.max(repos.length - 1, 1)) * Math.max(width - 140, 100) : width / 2;
        node.vy += (targetY - node.y) * .0018 * alpha;
        node.vx += (targetX - node.x) * .00055 * alpha;
      } else {
        node.vx += (width / 2 - node.x) * .00045 * alpha;
        node.vy += (height / 2 - node.y) * .00045 * alpha;
      }
      node.vx *= .88;
      node.vy *= .88;
      node.x += node.vx;
      node.y += node.vy;
    }
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const ratio = Math.min(devicePixelRatio || 1, 2);
    this.canvas.width = Math.max(1, Math.floor(rect.width * ratio));
    this.canvas.height = Math.max(1, Math.floor(rect.height * ratio));
    this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.ratio = ratio;
    this.draw();
  }

  worldToScreen(node) {
    return { x: node.x * this.transform.scale + this.transform.x, y: node.y * this.transform.scale + this.transform.y };
  }

  screenToWorld(x, y) {
    return { x: (x - this.transform.x) / this.transform.scale, y: (y - this.transform.y) / this.transform.scale };
  }

  draw() {
    const ctx = this.ctx;
    const width = this.canvas.clientWidth;
    const height = this.canvas.clientHeight;
    ctx.clearRect(0, 0, width, height);
    ctx.save();
    ctx.translate(this.transform.x, this.transform.y);
    ctx.scale(this.transform.scale, this.transform.scale);

    ctx.lineWidth = 1 / this.transform.scale;
    for (const link of this.links) this.drawLink(ctx, link);
    for (const node of this.nodes) this.drawNode(ctx, node);
    ctx.restore();

    this.drawPersistentLabels(ctx, width, height);
    if (this.hovered && this.hovered.id !== this.selectedId) this.drawTooltip(ctx, this.hovered, width, height);
  }

  drawLink(ctx, link) {
    const selected = link.source === this.selectedId || link.target === this.selectedId;
    const dx = link.targetNode.x - link.sourceNode.x;
    const dy = link.targetNode.y - link.sourceNode.y;
    const distance = Math.hypot(dx, dy) || 1;
    const curve = Math.min(distance * .13, 22);
    const mx = (link.sourceNode.x + link.targetNode.x) / 2 - (dy / distance) * curve;
    const my = (link.sourceNode.y + link.targetNode.y) / 2 + (dx / distance) * curve;
    const endDx = link.targetNode.x - mx;
    const endDy = link.targetNode.y - my;
    const endDistance = Math.hypot(endDx, endDy) || 1;
    const endPadding = link.targetNode.radius + 3 / this.transform.scale;
    const endX = link.targetNode.x - (endDx / endDistance) * endPadding;
    const endY = link.targetNode.y - (endDy / endDistance) * endPadding;
    const color = selected ? (RELATION_COLORS[link.type] || "#d83c2e") : "rgba(75,68,61,.32)";
    ctx.beginPath();
    ctx.moveTo(link.sourceNode.x, link.sourceNode.y);
    ctx.quadraticCurveTo(mx, my, endX, endY);
    ctx.strokeStyle = color;
    ctx.globalAlpha = selected ? .95 : .8;
    ctx.lineWidth = (selected ? 1.7 : .7) / this.transform.scale;
    if (link.type === "SUPERSEDES") ctx.setLineDash([5 / this.transform.scale, 3 / this.transform.scale]);
    else ctx.setLineDash([]);
    ctx.stroke();
    const arrowSize = (selected ? 5.5 : 4) / this.transform.scale;
    const angle = Math.atan2(endDy, endDx);
    ctx.beginPath();
    ctx.moveTo(endX, endY);
    ctx.lineTo(endX - Math.cos(angle - .48) * arrowSize, endY - Math.sin(angle - .48) * arrowSize);
    ctx.lineTo(endX - Math.cos(angle + .48) * arrowSize, endY - Math.sin(angle + .48) * arrowSize);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.setLineDash([]);
    if (selected) this.drawRelationshipLabel(ctx, link, mx, my);
  }

  drawRelationshipLabel(ctx, link, x, y) {
    const scale = this.transform.scale;
    const text = link.type.replaceAll("_", " ");
    ctx.save();
    ctx.font = `${9 / scale}px Commissioner, sans-serif`;
    const width = ctx.measureText(text).width + 10 / scale;
    const height = 17 / scale;
    ctx.fillStyle = "rgba(247,243,238,.94)";
    ctx.fillRect(x - width / 2, y - height / 2, width, height);
    ctx.fillStyle = RELATION_COLORS[link.type] || "#5f5a54";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, x, y + .4 / scale);
    ctx.restore();
  }

  drawNode(ctx, node) {
    const style = TYPE_STYLE[node.type] || { color: "#777168", shape: "circle" };
    const selected = node.id === this.selectedId;
    const hovered = node === this.hovered;
    const radius = node.radius + (selected ? 2.5 : hovered ? 1.4 : 0);
    ctx.save();
    ctx.translate(node.x, node.y);
    ctx.beginPath();
    this.nodePath(ctx, style.shape, radius);
    ctx.fillStyle = selected ? "#d4af37" : style.color;
    ctx.fill();
    ctx.lineWidth = (selected ? 3 : 1.2) / this.transform.scale;
    ctx.strokeStyle = selected ? "#1a1a18" : "#f7f3ee";
    ctx.stroke();
    if (style.shape === "fold") {
      ctx.beginPath();
      ctx.moveTo(radius * .15, -radius);
      ctx.lineTo(radius, -radius * .15);
      ctx.lineTo(radius * .15, -radius * .15);
      ctx.closePath();
      ctx.fillStyle = "rgba(247,243,238,.68)";
      ctx.fill();
    }
    ctx.restore();
  }

  nodePath(ctx, shape, r) {
    if (shape === "circle") { ctx.arc(0, 0, r, 0, Math.PI * 2); return; }
    const points = shape === "triangle" ? 3 : shape === "hexagon" ? 6 : shape === "diamond" ? 4 : shape === "decision" ? 8 : 4;
    const rotation = shape === "square" || shape === "fold" ? Math.PI / 4 : shape === "route" ? 0 : -Math.PI / 2;
    const sx = shape === "route" ? 1.45 : 1;
    for (let i = 0; i < points; i += 1) {
      const angle = rotation + (i / points) * Math.PI * 2;
      const pointRadius = shape === "decision" && i % 2 ? r * .55 : r;
      const x = Math.cos(angle) * pointRadius * sx;
      const y = Math.sin(angle) * pointRadius;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.closePath();
  }

  drawPersistentLabels(ctx, width, height) {
    const query = state.search.toLowerCase();
    const candidates = this.nodes.filter((node) =>
      node.id === this.selectedId ||
      node.type === "Repository" ||
      node.highDegree ||
      (query && `${node.label} ${node.id}`.toLowerCase().includes(query))
    ).sort((a, b) => {
      const selectedDelta = Number(b.id === this.selectedId) - Number(a.id === this.selectedId);
      return selectedDelta || b.degree - a.degree;
    }).slice(0, 30);
    const occupied = [];
    ctx.save();
    ctx.font = "600 10px Commissioner, sans-serif";
    ctx.textBaseline = "middle";
    for (const node of candidates) {
      const screen = this.worldToScreen(node);
      if (screen.x < -30 || screen.y < -20 || screen.x > width + 30 || screen.y > height + 20) continue;
      const text = shortLabel(node.label, 30);
      const textWidth = ctx.measureText(text).width;
      const box = { x: screen.x + 10, y: screen.y - 8, width: textWidth + 8, height: 17 };
      if (node.id !== this.selectedId && occupied.some((item) => box.x < item.x + item.width && box.x + box.width > item.x && box.y < item.y + item.height && box.y + box.height > item.y)) continue;
      occupied.push(box);
      ctx.fillStyle = node.id === this.selectedId ? "rgba(212,175,55,.94)" : "rgba(247,243,238,.88)";
      ctx.fillRect(box.x, box.y, box.width, box.height);
      ctx.fillStyle = "#1a1a18";
      ctx.fillText(text, box.x + 4, box.y + box.height / 2 + .5);
    }
    ctx.restore();
  }

  drawTooltip(ctx, node, width, height) {
    const screen = this.worldToScreen(node);
    const text = shortLabel(node.label, 52);
    ctx.save();
    ctx.font = "500 11px Commissioner, sans-serif";
    const textWidth = Math.min(ctx.measureText(text).width, 290);
    const boxWidth = textWidth + 20;
    let x = screen.x + 13;
    let y = screen.y - 35;
    if (x + boxWidth > width - 8) x = screen.x - boxWidth - 13;
    if (y < 8) y = screen.y + 16;
    ctx.fillStyle = "rgba(26,26,24,.94)";
    ctx.fillRect(x, y, boxWidth, 26);
    ctx.fillStyle = "#f7f3ee";
    ctx.fillText(text, x + 10, y + 17, boxWidth - 20);
    ctx.restore();
  }

  fit() {
    if (!this.nodes.length) return;
    const xs = this.nodes.map((node) => node.x);
    const ys = this.nodes.map((node) => node.y);
    const minX = Math.min(...xs) - 25;
    const maxX = Math.max(...xs) + 25;
    const minY = Math.min(...ys) - 25;
    const maxY = Math.max(...ys) + 25;
    const width = this.canvas.clientWidth;
    const height = this.canvas.clientHeight;
    const scale = Math.min(width / Math.max(maxX - minX, 1), height / Math.max(maxY - minY, 1), 1.7) * .9;
    this.transform.scale = Math.max(.12, scale);
    this.transform.x = width / 2 - ((minX + maxX) / 2) * this.transform.scale;
    this.transform.y = height / 2 - ((minY + maxY) / 2) * this.transform.scale;
    updateZoom(this.transform.scale);
    this.draw();
  }

  hitTest(screenX, screenY) {
    const point = this.screenToWorld(screenX, screenY);
    let closest = null;
    let best = 18 / this.transform.scale;
    for (const node of this.nodes) {
      const distance = Math.hypot(node.x - point.x, node.y - point.y);
      if (distance < best) { best = distance; closest = node; }
    }
    return closest;
  }

  bindEvents() {
    this.canvas.addEventListener("pointerdown", (event) => {
      this.canvas.setPointerCapture(event.pointerId);
      const rect = this.canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const node = this.hitTest(x, y);
      this.dragStart = { x, y, moved: false };
      if (node) {
        this.dragged = node;
        node.fixed = true;
      } else {
        this.panStart = { x, y, tx: this.transform.x, ty: this.transform.y };
      }
      this.canvas.classList.add("dragging");
    });
    this.canvas.addEventListener("pointermove", (event) => {
      const rect = this.canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      if (this.dragStart && Math.hypot(x - this.dragStart.x, y - this.dragStart.y) > 3) this.dragStart.moved = true;
      if (this.dragged) {
        const point = this.screenToWorld(x, y);
        this.dragged.x = point.x;
        this.dragged.y = point.y;
        this.dragged.vx = 0;
        this.dragged.vy = 0;
        this.draw();
      } else if (this.panStart) {
        this.transform.x = this.panStart.tx + x - this.panStart.x;
        this.transform.y = this.panStart.ty + y - this.panStart.y;
        this.draw();
      } else {
        const hovered = this.hitTest(x, y);
        if (hovered !== this.hovered) { this.hovered = hovered; this.canvas.style.cursor = hovered ? "pointer" : "grab"; this.draw(); }
      }
    });
    const release = () => {
      if (this.dragged) this.dragged.fixed = false;
      this.dragged = null;
      this.panStart = null;
      this.canvas.classList.remove("dragging");
      this.alpha = Math.max(this.alpha, .08);
      this.start();
    };
    this.canvas.addEventListener("pointerup", (event) => {
      const rect = this.canvas.getBoundingClientRect();
      const node = this.hitTest(event.clientX - rect.left, event.clientY - rect.top);
      if (node && !this.dragStart?.moved) selectNode(node.id);
      release();
    });
    this.canvas.addEventListener("pointercancel", release);
    this.canvas.addEventListener("dblclick", (event) => {
      const rect = this.canvas.getBoundingClientRect();
      const node = this.hitTest(event.clientX - rect.left, event.clientY - rect.top);
      if (node) foldTo(node.id, node.label);
    });
    this.canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      const rect = this.canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const before = this.screenToWorld(x, y);
      this.transform.scale = Math.max(.12, Math.min(4, this.transform.scale * Math.exp(-event.deltaY * .0012)));
      this.transform.x = x - before.x * this.transform.scale;
      this.transform.y = y - before.y * this.transform.scale;
      updateZoom(this.transform.scale);
      this.draw();
    }, { passive: false });
    this.canvas.addEventListener("keydown", (event) => this.onKey(event));
  }

  onKey(event) {
    if (!this.nodes.length) return;
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      event.preventDefault();
      const current = this.nodes[Math.max(this.keyboardIndex, 0)];
      const direction = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
      const candidates = this.nodes.filter((node) => node !== current).map((node) => {
        const dx = node.x - current.x;
        const dy = node.y - current.y;
        const forward = dx * direction[0] + dy * direction[1];
        return { node, score: forward > 0 ? forward + Math.abs(dx * direction[1] - dy * direction[0]) * 1.7 : Infinity };
      }).sort((a, b) => a.score - b.score);
      const next = candidates[0]?.node || this.nodes[(this.keyboardIndex + 1) % this.nodes.length];
      this.keyboardIndex = this.nodes.indexOf(next);
      selectNode(next.id);
    } else if (event.key === "Enter") {
      const node = this.nodes[Math.max(this.keyboardIndex, 0)];
      if (node) selectNode(node.id);
    } else if (event.key.toLowerCase() === "f") {
      const node = this.nodes[Math.max(this.keyboardIndex, 0)];
      if (node) foldTo(node.id, node.label);
    } else if (event.key === "0") {
      event.preventDefault();
      this.fit();
    }
  }
}

const graphCanvas = new GraphCanvas(dom["graph-canvas"]);

function updateZoom(scale) {
  dom["zoom-readout"].textContent = `${Math.round(scale * 100)}%`;
}

function filterRow(kind, item, checked = true) {
  const label = document.createElement("label");
  label.className = "filter-row";
  label.innerHTML = `<input type="checkbox" value="${item[kind]}" ${checked ? "checked" : ""}><span class="check-shape"></span><span class="filter-name"></span><span class="filter-count">${item.count}</span>`;
  label.querySelector(".filter-name").textContent = item[kind].replaceAll("_", " ");
  return label;
}

function populateMeta(meta) {
  state.meta = meta;
  for (const repo of meta.repositories) {
    const option = document.createElement("option");
    option.value = repo.name;
    option.textContent = `${repo.name} · ${repo.nodes}`;
    dom.repository.append(option);
  }
  if (meta.repositories.some((repo) => repo.name === meta.default_repository)) {
    state.repository = meta.default_repository;
    dom.repository.value = state.repository;
  }
  state.labels = new Set(meta.labels.map((item) => item.label));
  state.relationships = new Set(meta.relationships.map((item) => item.relationship));
  for (const item of meta.labels) dom["label-filters"].append(filterRow("label", item));
  for (const item of meta.relationships) dom["relationship-filters"].append(filterRow("relationship", item));
  renderLegend(meta.labels.map((item) => item.label));
}

function renderLegend(labels) {
  dom.legend.replaceChildren();
  for (const type of labels.filter((label) => TYPE_STYLE[label]).slice(0, 10)) {
    const item = document.createElement("span");
    item.className = "legend-item";
    item.innerHTML = `<i class="legend-shape" style="background:${TYPE_STYLE[type].color}"></i><span>${type}</span>`;
    dom.legend.append(item);
  }
}

function graphQuery() {
  const params = new URLSearchParams();
  if (state.repository) params.set("repository", state.repository);
  params.set("labels", state.labels.size ? [...state.labels].join(",") : "__none__");
  params.set("relationships", state.relationships.size ? [...state.relationships].join(",") : "__none__");
  if (state.search) params.set("search", state.search);
  const focus = state.history[state.historyIndex]?.id;
  if (focus) { params.set("focus", focus); params.set("depth", "2"); }
  params.set("limit", String(state.limit));
  return `/api/graph?${params}`;
}

function renderAccessibleNodes() {
  dom["node-access-list"].replaceChildren();
  const fragment = document.createDocumentFragment();
  for (const node of state.graph.nodes) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.nodeId = node.id;
    button.textContent = `${node.label}, ${(node.labels || []).join(", ")}`;
    if (node.id === state.selected) button.setAttribute("aria-current", "true");
    button.addEventListener("click", () => selectNode(node.id));
    item.append(button);
    fragment.append(item);
  }
  dom["node-access-list"].append(fragment);
}

function swapGraphData(graph, animate) {
  clearTimeout(swapGraphData.dataTimer);
  clearTimeout(swapGraphData.timer);
  if (!animate || graphCanvas.reducedMotion) {
    dom["paper-sheet"].classList.remove("is-folding");
    graphCanvas.setData(graph, state.layout);
    return;
  }
  dom["paper-sheet"].classList.remove("is-folding");
  void dom["paper-sheet"].offsetWidth;
  dom["paper-sheet"].classList.add("is-folding");
  swapGraphData.dataTimer = setTimeout(() => graphCanvas.setData(graph, state.layout), 190);
  swapGraphData.timer = setTimeout(() => dom["paper-sheet"].classList.remove("is-folding"), 560);
}

async function loadGraph({ fit = true, animate = false } = {}) {
  const token = ++state.loadingToken;
  dom["graph-loading"].hidden = false;
  dom["graph-empty"].hidden = true;
  try {
    const graph = await api(graphQuery());
    if (token !== state.loadingToken) return;
    state.graph = graph;
    swapGraphData(graph, animate);
    renderAccessibleNodes();
    if (fit) setTimeout(() => graphCanvas.fit(), matchMedia("(prefers-reduced-motion: reduce)").matches ? 20 : 650);
    dom["graph-empty"].hidden = graph.nodes.length !== 0;
    dom["summary-repo"].textContent = state.repository || "All repositories";
    dom["summary-count"].textContent = `${graph.nodes.length} forms · ${graph.links.length} creases${graph.truncated ? " · limited" : ""}`;
    dom.unfold.disabled = state.historyIndex === 0;
    if (state.selected && !graph.nodes.some((node) => node.id === state.selected)) clearSelection();
  } catch (error) {
    showToast(error.message);
    dom["graph-empty"].hidden = false;
    dom["graph-empty"].querySelector("strong").textContent = "The graph sheet could not open.";
    dom["graph-empty"].querySelector("p").textContent = error.message;
  } finally {
    if (token === state.loadingToken) dom["graph-loading"].hidden = true;
  }
}

function clearSelection() {
  state.selected = null;
  state.selectedDetail = null;
  graphCanvas.setSelected(null);
  dom["inspector-empty"].hidden = false;
  dom["inspector-content"].hidden = true;
  setInspectorOpen(false);
  renderAccessibleNodes();
}

async function selectNode(id) {
  const node = state.graph.nodes.find((item) => item.id === id);
  if (!node) return;
  state.selected = id;
  graphCanvas.setSelected(id);
  renderAccessibleNodes();
  dom["inspector-empty"].hidden = true;
  dom["inspector-content"].hidden = false;
  dom["fold-node"].hidden = false;
  dom["node-number"].textContent = String(Math.max(1, state.graph.nodes.indexOf(node) + 1)).padStart(2, "0");
  dom["node-title"].textContent = node.label;
  dom["node-kinds"].replaceChildren(...node.labels.map((label) => {
    const span = document.createElement("span");
    span.className = "node-kind";
    span.textContent = label;
    return span;
  }));
  setInspectorOpen(true);
  try {
    const detail = await api(`/api/node/${encodeURIComponent(id)}`);
    if (state.selected !== id) return;
    state.selectedDetail = detail;
    renderDetail(detail);
  } catch (error) {
    showToast(error.message);
  }
}

function setTraceMode(enabled) {
  state.mode = enabled ? "trace" : "atlas";
  dom["trace-sheet"].hidden = !enabled;
  dom["paper-sheet"].hidden = enabled;
  dom.legend.hidden = enabled;
  dom["open-trace"].classList.toggle("active", enabled);
  dom["open-trace"].setAttribute("aria-pressed", String(enabled));
  document.querySelectorAll(".view-mode button, #unfold, #fit-graph, #refresh-graph").forEach((button) => {
    button.disabled = enabled;
  });
  if (enabled) {
    const selected = state.graph.nodes.find((node) => node.id === state.selected);
    const suggestion = selected?.labels?.some((label) => ["Page", "Class", "CodeFile"].includes(label)) ? selected.label : state.search;
    if (suggestion) dom["trace-view"].value = suggestion.replace(/^\//, "");
    setTimeout(() => dom["trace-view"].focus(), 30);
  } else {
    document.querySelectorAll(".view-mode button, #fit-graph, #refresh-graph").forEach((button) => { button.disabled = false; });
    dom.unfold.disabled = state.historyIndex === 0;
    setTimeout(() => graphCanvas.resize(), 20);
  }
}

function traceDepths(trace) {
  const depths = new Map(trace.nodes.map((node) => [node.id, node.labels.includes("Page") ? 0 : 1]));
  for (let pass = 0; pass < 12; pass += 1) {
    let changed = false;
    for (const link of trace.links) {
      const candidate = (depths.get(link.source) || 0) + 1;
      if (candidate > (depths.get(link.target) || 0) && candidate < 30) {
        depths.set(link.target, candidate);
        changed = true;
      }
    }
    if (!changed) break;
  }
  return depths;
}

function inspectTraceNode(node) {
  highlightTracePath(node.id);
  state.selected = node.id;
  dom["inspector-empty"].hidden = true;
  dom["inspector-content"].hidden = false;
  dom["fold-node"].hidden = true;
  dom["node-number"].textContent = "TR";
  dom["node-title"].textContent = node.label;
  dom["node-kinds"].replaceChildren(...node.labels.map((label) => {
    const span = document.createElement("span");
    span.className = "node-kind";
    span.textContent = label;
    return span;
  }));
  const connected = state.trace.links.filter((link) => link.source === node.id || link.target === node.id);
  renderDetail({
    properties: node.properties,
    neighbors: connected.map((link) => {
      const otherId = link.source === node.id ? link.target : link.source;
      const other = state.trace.nodes.find((item) => item.id === otherId);
      return {
        direction: link.source === node.id ? "out" : "in",
        relationship: link.type,
        node_id: otherId,
        labels: other?.labels || [],
        label: other?.label || otherId,
      };
    }),
  });
  setInspectorOpen(true);
}

function highlightTracePath(nodeId) {
  if (!state.trace) return;
  const active = new Set([nodeId]);
  for (let pass = 0; pass < 12; pass += 1) {
    let changed = false;
    for (const link of state.trace.links) {
      if (active.has(link.target) && !active.has(link.source)) { active.add(link.source); changed = true; }
    }
    if (!changed) break;
  }
  const descendants = new Set([nodeId]);
  for (let pass = 0; pass < 12; pass += 1) {
    let changed = false;
    for (const link of state.trace.links) {
      if (descendants.has(link.source) && !descendants.has(link.target)) { descendants.add(link.target); changed = true; }
    }
    if (!changed) break;
  }
  descendants.forEach((id) => active.add(id));
  dom["trace-lanes"].querySelectorAll(".trace-node").forEach((button) => {
    button.classList.toggle("trace-muted", !active.has(button.dataset.traceId));
    button.classList.toggle("trace-active", button.dataset.traceId === nodeId);
  });
  dom["trace-links"].querySelectorAll(".trace-link").forEach((path) => {
    path.classList.toggle("trace-muted", !(active.has(path.dataset.source) && active.has(path.dataset.target)));
  });
}

function drawTraceLinks() {
  if (!state.trace || dom["trace-sheet"].hidden) return;
  const svg = dom["trace-links"];
  const boardRect = dom["trace-board"].getBoundingClientRect();
  const width = Math.max(dom["trace-board"].scrollWidth, boardRect.width);
  const height = Math.max(dom["trace-board"].scrollHeight, boardRect.height);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.replaceChildren();
  const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
  const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
  marker.setAttribute("id", "trace-arrow");
  marker.setAttribute("viewBox", "0 0 10 10");
  marker.setAttribute("refX", "8");
  marker.setAttribute("refY", "5");
  marker.setAttribute("markerWidth", "6");
  marker.setAttribute("markerHeight", "6");
  marker.setAttribute("orient", "auto-start-reverse");
  const arrow = document.createElementNS("http://www.w3.org/2000/svg", "path");
  arrow.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
  arrow.setAttribute("fill", "context-stroke");
  marker.append(arrow);
  defs.append(marker);
  svg.append(defs);
  for (const link of state.trace.links) {
    const source = dom["trace-lanes"].querySelector(`[data-trace-id="${escapeSelector(link.source)}"]`);
    const target = dom["trace-lanes"].querySelector(`[data-trace-id="${escapeSelector(link.target)}"]`);
    if (!source || !target) continue;
    const a = source.getBoundingClientRect();
    const b = target.getBoundingClientRect();
    const x1 = a.right - boardRect.left;
    const y1 = a.top + a.height / 2 - boardRect.top;
    const x2 = b.left - boardRect.left;
    const y2 = b.top + b.height / 2 - boardRect.top;
    const sameLane = Math.abs(x2 - x1) < 40;
    const bend = Math.max(28, Math.abs(x2 - x1) * .45);
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("class", `trace-link${link.alternative ? " alternative" : ""}`);
    path.dataset.source = link.source;
    path.dataset.target = link.target;
    path.setAttribute("d", sameLane
      ? `M ${a.left + a.width / 2 - boardRect.left} ${a.bottom - boardRect.top} C ${a.left + a.width / 2 + 30 - boardRect.left} ${a.bottom + 18 - boardRect.top}, ${b.left + b.width / 2 + 30 - boardRect.left} ${b.top - 18 - boardRect.top}, ${b.left + b.width / 2 - boardRect.left} ${b.top - boardRect.top}`
      : `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`);
    svg.append(path);
    const detail = link.properties?.condition || link.properties?.name || (link.properties?.is_default ? "default" : "");
    if (detail) {
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("class", "trace-link-label");
      label.setAttribute("x", String((x1 + x2) / 2));
      label.setAttribute("y", String((y1 + y2) / 2 - 5));
      label.textContent = shortLabel(detail, 54);
      svg.append(label);
    }
  }
}

function renderTrace(trace) {
  state.trace = trace;
  dom["trace-state"].hidden = true;
  dom["trace-result"].hidden = false;
  dom["trace-title"].textContent = trace.seed?.classes?.[0] || trace.view;
  dom["trace-title"].tabIndex = -1;
  dom["trace-stats"].textContent = `${trace.stats.nodes} evidence forms · ${trace.stats.links} connections · ${trace.stats.alternatives} alternate paths`;
  dom["trace-warnings"].replaceChildren(...trace.warnings.map((warning) => {
    const item = document.createElement("div");
    item.className = "trace-warning";
    item.textContent = warning;
    return item;
  }));
  const depths = traceDepths(trace);
  dom["trace-lanes"].style.setProperty("--lane-count", trace.lanes.length);
  dom["trace-lanes"].replaceChildren(...trace.lanes.map((lane) => {
    const section = document.createElement("section");
    section.className = "trace-lane";
    section.dataset.traceLane = lane.name;
    const heading = document.createElement("h4");
    heading.textContent = `${lane.name} · ${lane.nodes.length}`;
    section.append(heading);
    const nodes = lane.nodes.map((id) => trace.nodes.find((node) => node.id === id)).filter(Boolean)
      .sort((a, b) => (depths.get(a.id) || 0) - (depths.get(b.id) || 0) || a.label.localeCompare(b.label));
    for (const node of nodes) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "trace-node";
      button.dataset.traceId = node.id;
      const meta = node.properties.source_file_id || node.properties.process_key || node.properties.system || node.properties.repo_name || "";
      button.innerHTML = '<span class="trace-node-kind"></span><span class="trace-node-title"></span><span class="trace-node-meta"></span>';
      button.querySelector(".trace-node-kind").textContent = node.labels.join(" · ");
      button.querySelector(".trace-node-title").textContent = node.label;
      button.querySelector(".trace-node-meta").textContent = meta;
      button.addEventListener("click", () => inspectTraceNode(node));
      section.append(button);
    }
    return section;
  }));
  dom["trace-lane-nav"].replaceChildren(...trace.lanes.map((lane) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${lane.name} ${lane.nodes.length}`;
    button.addEventListener("click", () => {
      const target = [...dom["trace-lanes"].children].find((section) => section.dataset.traceLane === lane.name);
      if (target) dom["trace-result"].querySelector(".trace-scroll").scrollTo({ left: target.offsetLeft, behavior: "smooth" });
    });
    return button;
  }));
  dom["trace-access-list"].replaceChildren(...trace.links.map((link) => {
    const item = document.createElement("li");
    const source = trace.nodes.find((node) => node.id === link.source)?.label || link.source;
    const target = trace.nodes.find((node) => node.id === link.target)?.label || link.target;
    const detail = link.properties?.condition || link.properties?.name || (link.properties?.is_default ? "default" : "");
    item.textContent = `${source} ${link.type.toLowerCase().replaceAll("_", " ")} ${target}${detail ? ` when ${detail}` : ""}`;
    return item;
  }));
  requestAnimationFrame(() => requestAnimationFrame(() => {
    drawTraceLinks();
    dom["trace-title"].focus({ preventScroll: true });
  }));
}

async function runTrace(view) {
  const requested = view.trim();
  if (!requested) return;
  dom["trace-result"].hidden = true;
  dom["trace-state"].hidden = false;
  dom["trace-state"].classList.add("loading");
  dom["trace-state"].innerHTML = "<strong>Following the evidence chain</strong><span>Resolving UI actions, Java calls, routes, workflow branches, and systems.</span>";
  try {
    const params = new URLSearchParams({ view: requested, repository: state.repository || "sample-web" });
    const trace = await api(`/api/trace?${params}`);
    if (!trace.found) throw new Error(trace.warnings?.[0] || "Entry point not found");
    renderTrace(trace);
  } catch (error) {
    dom["trace-state"].innerHTML = "<strong>That entry point could not be traced.</strong><span></span>";
    dom["trace-state"].querySelector("span").textContent = error.message;
  } finally {
    dom["trace-state"].classList.remove("loading");
  }
}

function renderDetail(detail) {
  dom["node-properties"].replaceChildren();
  const properties = Object.entries(detail.properties || {}).sort(([a], [b]) => a.localeCompare(b));
  for (const [key, value] of properties) {
    const row = document.createElement("div");
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = key.replaceAll("_", " ");
    dd.textContent = safeText(value);
    row.append(dt, dd);
    dom["node-properties"].append(row);
  }
  if (!properties.length) {
    const row = document.createElement("div");
    row.innerHTML = "<dt>State</dt><dd>No stored properties</dd>";
    dom["node-properties"].append(row);
  }
  const neighbors = detail.neighbors || [];
  dom["neighbor-count"].textContent = String(neighbors.length);
  dom["neighbor-list"].replaceChildren();
  for (const neighbor of neighbors) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "neighbor-item";
    button.innerHTML = `<span class="neighbor-direction">${neighbor.direction === "out" ? "out" : "in"}</span><span><span class="neighbor-title"></span><small class="neighbor-type"></small></span><span class="neighbor-arrow">›</span>`;
    button.querySelector(".neighbor-title").textContent = neighbor.label;
    button.querySelector(".neighbor-type").textContent = `${neighbor.relationship} · ${(neighbor.labels || []).join(", ")}`;
    button.addEventListener("click", () => {
      if (state.mode === "trace") {
        const traceNode = state.trace?.nodes.find((item) => item.id === neighbor.node_id);
        if (traceNode) inspectTraceNode(traceNode);
        return;
      }
      const present = state.graph.nodes.some((node) => node.id === neighbor.node_id);
      if (present) selectNode(neighbor.node_id);
      else foldTo(neighbor.node_id, neighbor.label);
    });
    dom["neighbor-list"].append(button);
  }
}

function renderHistory() {
  dom["fold-history"].replaceChildren();
  state.history.forEach((step, index) => {
    const li = document.createElement("li");
    li.classList.toggle("active", index === state.historyIndex);
    const marker = document.createElement("i");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${String(index + 1).padStart(2, "0")}  ${shortLabel(step.label, 28)}`;
    button.addEventListener("click", () => {
      state.historyIndex = index;
      renderHistory();
      loadGraph({ animate: true });
    });
    li.append(marker, button);
    dom["fold-history"].append(li);
  });
}

function foldTo(id, label) {
  if (!id || state.history[state.historyIndex]?.id === id) return;
  state.history = state.history.slice(0, state.historyIndex + 1);
  state.history.push({ id, label });
  state.historyIndex = state.history.length - 1;
  renderHistory();
  loadGraph({ animate: true });
}

function unfold() {
  if (state.historyIndex === 0) return;
  state.historyIndex -= 1;
  renderHistory();
  loadGraph({ animate: true });
}

function setControlsOpen(open, returnFocus = false) {
  dom["scope-rail"].classList.toggle("open", open);
  dom["open-controls"].setAttribute("aria-expanded", String(open));
  if (innerWidth <= 860) dom["scope-rail"].setAttribute("aria-hidden", String(!open));
  else dom["scope-rail"].removeAttribute("aria-hidden");
  if (open) dom["close-controls"].focus();
  else if (returnFocus) dom["open-controls"].focus();
}

function setInspectorOpen(open, returnFocus = false) {
  dom.inspector.classList.toggle("open", open);
  if (innerWidth <= 860) dom.inspector.setAttribute("aria-hidden", String(!open));
  else dom.inspector.removeAttribute("aria-hidden");
  if (!open && returnFocus) dom["graph-canvas"].focus();
}

function resetFilters() {
  state.search = "";
  dom.search.value = "";
  state.repository = state.meta?.default_repository || "";
  dom.repository.value = state.repository;
  state.labels = new Set(state.meta?.labels.map((item) => item.label) || []);
  state.relationships = new Set(state.meta?.relationships.map((item) => item.relationship) || []);
  document.querySelectorAll(".filter-row input").forEach((input) => { input.checked = true; });
  state.history = [{ id: "", label: "Full sheet" }];
  state.historyIndex = 0;
  renderHistory();
  loadGraph();
}

function debounce(callback, delay = 260) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => callback(...args), delay); };
}

function bindUI() {
  dom.repository.addEventListener("change", () => { state.repository = dom.repository.value; state.history = [{ id: "", label: "Full sheet" }]; state.historyIndex = 0; renderHistory(); loadGraph(); });
  dom.search.addEventListener("input", debounce(() => { state.search = dom.search.value.trim(); loadGraph(); }, 320));
  dom["node-limit"].addEventListener("input", () => { state.limit = Number(dom["node-limit"].value); dom["node-limit-value"].textContent = `${state.limit} forms`; });
  dom["node-limit"].addEventListener("change", () => loadGraph());
  dom["label-filters"].addEventListener("change", () => { state.labels = new Set([...dom["label-filters"].querySelectorAll("input:checked")].map((input) => input.value)); loadGraph(); });
  dom["relationship-filters"].addEventListener("change", () => { state.relationships = new Set([...dom["relationship-filters"].querySelectorAll("input:checked")].map((input) => input.value)); loadGraph({ fit: false }); });
  dom["toggle-labels"].addEventListener("click", () => toggleFilterGroup("label-filters", "labels"));
  dom["toggle-relations"].addEventListener("click", () => toggleFilterGroup("relationship-filters", "relationships"));
  dom["clear-filters"].addEventListener("click", resetFilters);
  dom["refresh-graph"].addEventListener("click", () => loadGraph({ fit: false }));
  dom["fit-graph"].addEventListener("click", () => graphCanvas.fit());
  dom.unfold.addEventListener("click", unfold);
  dom["open-trace"].addEventListener("click", () => setTraceMode(true));
  dom["close-trace"].addEventListener("click", () => setTraceMode(false));
  dom["trace-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    runTrace(dom["trace-view"].value);
  });
  dom["copy-mermaid"].addEventListener("click", async () => {
    if (!state.trace?.mermaid) return;
    try {
      await navigator.clipboard.writeText(state.trace.mermaid);
      showToast("Mermaid diagram copied");
    } catch {
      const area = document.createElement("textarea");
      area.value = state.trace.mermaid;
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
      showToast("Mermaid diagram copied");
    }
  });
  dom["fold-node"].addEventListener("click", () => { const node = state.graph.nodes.find((item) => item.id === state.selected); if (node) foldTo(node.id, node.label); });
  document.querySelectorAll("[data-layout]").forEach((button) => button.addEventListener("click", () => {
    state.layout = button.dataset.layout;
    document.querySelectorAll("[data-layout]").forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    graphCanvas.setLayout(state.layout);
  }));
  dom["open-controls"].addEventListener("click", () => setControlsOpen(true));
  dom["close-controls"].addEventListener("click", () => setControlsOpen(false, true));
  dom["close-inspector"].addEventListener("click", () => setInspectorOpen(false, true));
  let dialogReturnFocus = null;
  const closeKeys = () => {
    dom["key-dialog"].hidden = true;
    dom["keyboard-help"].setAttribute("aria-expanded", "false");
    (dialogReturnFocus || dom["keyboard-help"]).focus();
  };
  dom["keyboard-help"].addEventListener("click", () => {
    dialogReturnFocus = document.activeElement;
    dom["key-dialog"].hidden = false;
    dom["keyboard-help"].setAttribute("aria-expanded", "true");
    dom["key-dialog"].querySelector(".dialog-close").focus();
  });
  dom["key-dialog"].querySelector(".dialog-close").addEventListener("click", closeKeys);
  dom["key-dialog"].querySelector(".dialog-backdrop").addEventListener("click", closeKeys);
  document.addEventListener("keydown", (event) => {
    if (!dom["key-dialog"].hidden && event.key === "Tab") {
      const focusable = [...dom["key-dialog"].querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])")].filter((item) => !item.disabled);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    if (dom["key-dialog"].hidden && event.key === "/" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) { event.preventDefault(); dom.search.focus(); }
    if (event.key === "Escape") {
      if (!dom["key-dialog"].hidden) closeKeys();
      else if (state.mode === "trace") setTraceMode(false);
      else if (dom["scope-rail"].classList.contains("open")) setControlsOpen(false, true);
      else if (dom.inspector.classList.contains("open") && innerWidth <= 860) setInspectorOpen(false, true);
      else unfold();
    }
  });
  const syncDrawerSemantics = () => {
    if (innerWidth <= 860) {
      dom["scope-rail"].setAttribute("aria-hidden", String(!dom["scope-rail"].classList.contains("open")));
      dom.inspector.setAttribute("aria-hidden", String(!dom.inspector.classList.contains("open")));
    } else {
      dom["scope-rail"].removeAttribute("aria-hidden");
      dom.inspector.removeAttribute("aria-hidden");
    }
  };
  addEventListener("resize", () => { syncDrawerSemantics(); drawTraceLinks(); });
  syncDrawerSemantics();
}

function toggleFilterGroup(containerId, stateKey) {
  const inputs = [...dom[containerId].querySelectorAll("input")];
  const select = !inputs.every((input) => input.checked);
  inputs.forEach((input) => { input.checked = select; });
  state[stateKey] = new Set(select ? inputs.map((input) => input.value) : []);
  loadGraph();
}

async function init() {
  bindUI();
  renderHistory();
  try {
    const meta = await api("/api/meta");
    populateMeta(meta);
    await loadGraph();
  } catch (error) {
    dom["graph-loading"].hidden = true;
    dom["graph-empty"].hidden = false;
    dom["graph-empty"].querySelector("strong").textContent = "Neo4j is out of reach.";
    dom["graph-empty"].querySelector("p").textContent = error.message;
    showToast(error.message);
  }
}

init();
