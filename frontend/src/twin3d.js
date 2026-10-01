// 3D refinery digital twin engine (refinery_3d_digital_twin guide), vanilla Three.js r128 (vendored,
// no CDN). The layout mirrors the real model; every level, flow and colour comes from solver output
// passed to setState() - see backend/sovereign/twin.py for the mapping.
const T = () => window.THREE;

const COLORS = { cyan: 0x00e5ff, green: 0x00ff9d, amber: 0xffb300, red: 0xff1744, off: 0x33414d, crude: 0xc49a6c };

function statusHex(dual, th) {
  if (dual >= th.red) return COLORS.red;
  if (dual > th.amber) return COLORS.amber;
  if (dual > 1e-6) return COLORS.green;
  return COLORS.cyan;
}

function labelSprite(text, color = "#dfe7ec") {
  const THREE = T();
  const c = document.createElement("canvas");
  c.width = 512; c.height = 96;
  const g = c.getContext("2d");
  g.font = "600 40px Segoe UI, system-ui, sans-serif";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillStyle = "rgba(5,11,20,0.72)";
  const w = Math.min(500, g.measureText(text).width + 36);
  g.beginPath();
  if (g.roundRect) g.roundRect(256 - w / 2, 12, w, 72, 18); else g.rect(256 - w / 2, 12, w, 72);
  g.fill();
  g.fillStyle = color;
  g.fillText(text, 256, 50);
  const tex = new THREE.CanvasTexture(c);
  tex.minFilter = THREE.LinearFilter;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
  s.scale.set(6.6, 1.24, 1);
  return s;
}

export function webglAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!(window.WebGLRenderingContext && (c.getContext("webgl") || c.getContext("experimental-webgl")));
  } catch { return false; }
}

export class Refinery3D {
  constructor(container, layout, { onSelect } = {}) {
    const THREE = T();
    this.container = container;
    this.layout = layout;
    this.th = layout.thresholds;
    this.onSelect = onSelect;
    this.units = {};
    this.pipes = [];
    this.pickables = [];
    this.selected = null;
    this.autoRotate = false;
    this.disposed = false;

    const scene = (this.scene = new THREE.Scene());
    scene.fog = new THREE.Fog(0x050b14, 40, 95);
    const w = container.clientWidth || 800, h = container.clientHeight || 480;
    const camera = (this.camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 300));
    // frame the whole plant: pull back on narrow (portrait / phone) viewports
    const k = Math.max(1, 1.55 / (w / h));
    this.home = new THREE.Vector3(-2 * k, 17 * k, 29 * k);
    camera.position.copy(this.home);
    const renderer = (this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }));
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setSize(w, h);
    renderer.setClearColor(0x050b14, 1);
    container.appendChild(renderer.domElement);

    scene.add(new THREE.HemisphereLight(0x88aacc, 0x0a0f14, 0.7));
    const sun = new THREE.DirectionalLight(0xffffff, 0.75);
    sun.position.set(12, 30, 18);
    scene.add(sun);
    const rim = new THREE.PointLight(0x00e5ff, 0.6, 80);
    rim.position.set(-20, 12, -20);
    scene.add(rim);

    const ground = new THREE.Mesh(new THREE.CircleGeometry(48, 64),
      new THREE.MeshStandardMaterial({ color: 0x07121c, roughness: 0.95, metalness: 0.1 }));
    ground.rotation.x = -Math.PI / 2;
    scene.add(ground);
    const grid = new THREE.GridHelper(96, 48, 0x0d3b4d, 0x0a2230);
    grid.position.y = 0.01;
    scene.add(grid);

    const controls = (this.controls = new THREE.OrbitControls(camera, renderer.domElement));
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.target.set(0, 3, 0);
    controls.maxPolarAngle = Math.PI * 0.48;
    controls.minDistance = 12;
    controls.maxDistance = 90;
    controls.autoRotateSpeed = 0.7;

    this._build();
    this._bindPicking();
    this.clock = new THREE.Clock();
    this._loop = this._loop.bind(this);
    this._raf = requestAnimationFrame(this._loop);
    this._onResize = () => this.resize();
    window.addEventListener("resize", this._onResize);
  }

  // ------------------------------------------------------------------ scene construction
  _position(n) {
    switch (n.kind) {
      case "crude": return [-22, -10 + n.slot * 5];
      case "cdu": return [-11, 0];
      case "unit": return [0, [-10, -3.5, 3.5, 10][n.slot] ?? 0];
      case "product": {
        const col = n.slot < 4 ? 0 : 1, row = n.slot < 4 ? n.slot : n.slot - 4;
        return [12 + col * 7, -10 + row * (col === 0 ? 6.7 : 7.5)];
      }
      default: return [0, 0];
    }
  }

  _addNode(n) {
    const THREE = T();
    const group = new THREE.Group();
    const [x, z] = this._position(n);
    group.position.set(x, 0, z);
    let r = 1.5, h = 5, shape = "tank";
    if (n.kind === "crude") { r = 1.7; h = 3.6; }
    if (n.kind === "cdu") { r = 1.35; h = 12; shape = "column"; }
    if (n.kind === "unit") { if (n.shape === "column") { r = 1.1; h = n.id === "FCC" ? 10 : 7.5; shape = "column"; } else { shape = "reactor"; h = 4.6; } }
    if (n.kind === "product" && n.product === "LPG") shape = "sphere";

    const shellMat = new THREE.MeshStandardMaterial({ color: 0x113344, transparent: true, opacity: 0.28,
      emissive: new THREE.Color(COLORS.cyan), emissiveIntensity: 0.18, metalness: 0.6, roughness: 0.3, depthWrite: false });
    const liqMat = new THREE.MeshStandardMaterial({ color: new THREE.Color(COLORS.cyan), emissive: new THREE.Color(COLORS.cyan),
      emissiveIntensity: 0.55, transparent: true, opacity: 0.9 });
    let shell, liquid, glowMat = liqMat;
    if (shape === "sphere") {
      shell = new THREE.Mesh(new THREE.SphereGeometry(2.1, 28, 20), shellMat);
      shell.position.y = 2.6;
      liquid = new THREE.Mesh(new THREE.SphereGeometry(1.9, 28, 20), liqMat);
      liquid.position.y = 2.6;
      liquid.scale.setScalar(0.01);
      for (let i = 0; i < 4; i++) {  // legs
        const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.1, 1.2, 6), new THREE.MeshStandardMaterial({ color: 0x3a4a56 }));
        leg.position.set(Math.cos(i * Math.PI / 2) * 1.3, 0.6, Math.sin(i * Math.PI / 2) * 1.3);
        group.add(leg);
      }
      h = 4.8;
    } else if (shape === "reactor") {
      shell = new THREE.Mesh(new THREE.BoxGeometry(3.2, h, 3.2), shellMat);
      shell.position.y = h / 2;
      liquid = new THREE.Mesh(new THREE.BoxGeometry(2.9, h, 2.9), liqMat);
      liquid.geometry.translate(0, h / 2, 0);
      liquid.scale.y = 0.01;
      const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.5, 1.6, 12), new THREE.MeshStandardMaterial({ color: 0x2c3a46, metalness: 0.7 }));
      cap.position.set(0.8, h + 0.8, 0.8);
      group.add(cap);
    } else {
      shell = new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, 32, 1, true), shellMat);
      shell.position.y = h / 2;
      liquid = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.9, r * 0.9, h, 32), liqMat);
      liquid.geometry.translate(0, h / 2, 0);
      liquid.scale.y = 0.01;
      const roof = new THREE.Mesh(new THREE.ConeGeometry(r * 1.02, shape === "column" ? 0.9 : 0.6, 32),
        new THREE.MeshStandardMaterial({ color: 0x1c2b36, metalness: 0.6, roughness: 0.4 }));
      roof.position.y = h + (shape === "column" ? 0.45 : 0.3);
      group.add(roof);
      if (shape === "column") {  // distillation trays
        const trays = n.kind === "cdu" ? 8 : 5;
        for (let i = 1; i <= trays; i++) {
          const ring = new THREE.Mesh(new THREE.TorusGeometry(r * 1.02, 0.05, 6, 32), new THREE.MeshBasicMaterial({ color: 0x00e5ff, transparent: true, opacity: 0.55 }));
          ring.rotation.x = Math.PI / 2;
          ring.position.y = (h / (trays + 1)) * i;
          group.add(ring);
        }
      }
      if (n.kind === "crude") liqMat.color.set(COLORS.crude);
    }
    shell.userData.id = n.id;
    liquid.userData.id = n.id;
    group.add(shell);
    group.add(liquid);
    // base plinth + status light
    const plinth = new THREE.Mesh(new THREE.CylinderGeometry(Math.max(r, 1.8) + 0.4, Math.max(r, 1.8) + 0.6, 0.25, 32),
      new THREE.MeshStandardMaterial({ color: 0x0d1a24, metalness: 0.4, roughness: 0.7 }));
    plinth.position.y = 0.12;
    group.add(plinth);
    const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.22, 12, 10), new THREE.MeshBasicMaterial({ color: COLORS.cyan }));
    lamp.position.y = h + 1.3;
    group.add(lamp);
    const label = labelSprite(n.short);
    label.position.y = h + 2.4;
    group.add(label);
    this.scene.add(group);
    this.pickables.push(shell, liquid);
    this.units[n.id] = { node: n, group, shellMat, liqMat, glowMat, liquid, lamp, shape, h, level: 0, target: 0,
      dual: 0, on: true, info: null, sphere: shape === "sphere" };
  }

  _addPipe(e) {
    const THREE = T();
    const a = this.units[e.from], b = this.units[e.to];
    if (!a || !b) return;
    const p0 = a.group.position.clone().add(new THREE.Vector3(0, Math.min(a.h * 0.55, 4), 0));
    const p2 = b.group.position.clone().add(new THREE.Vector3(0, Math.min(b.h * 0.55, 3.5), 0));
    const mid = p0.clone().lerp(p2, 0.5);
    mid.y += 2.2 + p0.distanceTo(p2) * 0.08;
    const curve = new THREE.QuadraticBezierCurve3(p0, mid, p2);
    const tubeMat = new THREE.MeshBasicMaterial({ color: COLORS.cyan, transparent: true, opacity: 0.12 });
    const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 40, 0.07, 6, false), tubeMat);
    this.scene.add(tube);
    const N = 12;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(N * 3), 3));
    const ptsMat = new THREE.PointsMaterial({ color: COLORS.cyan, size: 0.42, transparent: true, opacity: 0.95,
      blending: THREE.AdditiveBlending, depthWrite: false });
    const pts = new THREE.Points(geo, ptsMat);
    pts.visible = false;
    this.scene.add(pts);
    this.pipes.push({ ...e, curve, tube, tubeMat, pts, ptsMat, N, phase: Math.random(), speed: 0, flow: 0 });
  }

  _build() {
    this.layout.nodes.forEach((n) => this._addNode(n));
    this.layout.edges.forEach((e) => this._addPipe(e));
  }

  // ------------------------------------------------------------------ data
  setState(state) {
    if (!state) return;
    const nodes = state.nodes || {};
    for (const id in nodes) {
      const u = this.units[id];
      if (!u) continue;
      const s = nodes[id];
      u.target = Math.max(0, Math.min(1, s.level ?? 0));
      u.dual = s.dual || 0;
      u.on = s.on !== false;
      u.info = s;
    }
    const flows = state.flows || {};
    const fmax = Math.max(1, ...Object.values(flows));
    this.pipes.forEach((p) => {
      const f = flows[p.id] ?? 0;
      p.flow = f;
      p.speed = f > 1e-6 ? 0.05 + 0.45 * (f / fmax) : 0;
      p.pts.visible = f > 1e-6;
      p.tubeMat.opacity = f > 1e-6 ? 0.18 + 0.4 * (f / fmax) : 0.06;
    });
    if (this.selected && this.onSelect) this.onSelect(this.describe(this.selected));
  }

  describe(id) {
    const u = this.units[id];
    if (!u) return null;
    const s = u.info || {};
    const inflow = this.pipes.filter((p) => p.to === id).reduce((a, p) => a + p.flow, 0);
    const outflow = this.pipes.filter((p) => p.from === id).reduce((a, p) => a + p.flow, 0);
    const bottleneck = u.dual > this.th.amber;
    return { id, label: u.node.label, kind: u.node.kind, level: u.level, target: u.target, value: s.value,
      unit: s.unit, capacity: u.node.capacity, dual: u.dual, bottleneck, binding: s.binding, on: u.on,
      parcels: s.parcels, quality: s.quality_limited || [], inflow, outflow,
      color: "#" + statusHex(u.dual, this.th).toString(16).padStart(6, "0") };
  }

  _bindPicking() {
    const THREE = T();
    const ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
    const pick = (e) => {
      const r = this.renderer.domElement.getBoundingClientRect();
      mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(mouse, this.camera);
      const hit = ray.intersectObjects(this.pickables)[0];
      return hit ? hit.object.userData.id : null;
    };
    let down = null;
    this.renderer.domElement.addEventListener("pointerdown", (e) => { down = [e.clientX, e.clientY]; });
    this.renderer.domElement.addEventListener("pointerup", (e) => {
      if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;  // was a drag
      const id = pick(e);
      this.selected = id;
      if (this.onSelect) this.onSelect(id ? this.describe(id) : null);
    });
    this.renderer.domElement.addEventListener("pointermove", (e) => {
      this.renderer.domElement.style.cursor = pick(e) ? "pointer" : "grab";
    });
  }

  // ------------------------------------------------------------------ animation
  _loop() {
    if (this.disposed) return;
    this._raf = requestAnimationFrame(this._loop);
    if (!this.container.clientWidth) return;  // tab hidden
    const THREE = T();
    const dt = Math.min(0.05, this.clock.getDelta()), t = this.clock.elapsedTime;
    for (const id in this.units) {
      const u = this.units[id];
      u.level += (u.target - u.level) * Math.min(1, dt * 3);
      const lv = Math.max(0.01, u.level);
      if (u.sphere) u.liquid.scale.setScalar(Math.cbrt(lv)); else u.liquid.scale.y = lv;
      const hex = u.on ? statusHex(u.dual, this.th) : COLORS.off;
      const c = new THREE.Color(hex);
      if (u.node.kind !== "crude") { u.liqMat.color.lerp(c, 0.12); u.liqMat.emissive.lerp(c, 0.12); }
      u.shellMat.emissive.lerp(c, 0.1);
      u.lamp.material.color.lerp(c, 0.2);
      const hot = u.dual >= this.th.red, warm = u.dual > this.th.amber;
      u.shellMat.emissiveIntensity = hot ? 0.45 + 0.4 * Math.sin(t * 6) : warm ? 0.32 : 0.16;
      u.liqMat.emissiveIntensity = u.on ? (hot ? 0.7 + 0.3 * Math.sin(t * 6) : 0.5) : 0.05;
      u.liqMat.opacity = u.on ? 0.9 : 0.25;
      u.lamp.scale.setScalar(hot ? 1.3 + 0.35 * Math.sin(t * 6) : 1);
      u.group.scale.setScalar(this.selected === id ? 1.06 : 1);
    }
    this.pipes.forEach((p) => {
      if (!p.pts.visible) return;
      const pos = p.pts.geometry.attributes.position;
      p.phase = (p.phase + p.speed * dt) % 1;
      for (let i = 0; i < p.N; i++) {
        const v = p.curve.getPoint((p.phase + i / p.N) % 1);
        pos.setXYZ(i, v.x, v.y, v.z);
      }
      pos.needsUpdate = true;
    });
    this.controls.autoRotate = this.autoRotate;
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  resetCamera() {
    this.camera.position.copy(this.home);
    this.controls.target.set(0, 3, 0);
  }

  resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    if (!w || !h) return;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  }

  dispose() {
    this.disposed = true;
    cancelAnimationFrame(this._raf);
    window.removeEventListener("resize", this._onResize);
    this.controls.dispose();
    this.scene.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); }
    });
    this.renderer.dispose();
    if (this.renderer.domElement.parentNode) this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
  }
}
